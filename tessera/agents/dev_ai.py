"""Developer AI: writes the code for one TL task at a time, inside the scaffold.

Safeguards, all in code rather than hoped for:
  * It may only write the files the TL assigned to the task.
  * It returns plain-text file blocks (JSON-wrapped code broke far too often in our experiment).
  * Python files must parse, and the whole app (every migration included) must start and build its OpenAPI
    schema. Anything wrong goes back to the model with the real error, up to 2 times.
  * Files are only kept if all of that passes; otherwise the workspace is restored.
"""

from __future__ import annotations

import ast
import json
import re
import sqlite3
from typing import Any

from .. import db
from ..fileblocks import parse_file_blocks
from ..llm import LLMClient
from ..sandbox import Workspace
from ..scaffold_spec import SCAFFOLD_SPEC

MAX_FIX_ROUNDS = 2
MAX_REPLY_TOKENS = 8000

DEV_SYSTEM = f"""\
You are the Developer on a small software team. You write production-quality Python for one task.

{SCAFFOLD_SPEC}
Coding rules:
- Write ONLY the files assigned to you. Complete files, no placeholders, no TODOs.
- Use only the standard library, fastapi and pydantic (v2). No other packages.
- Follow the API contract exactly: paths, methods, status codes, field names, validation limits and
  error cases. When the contract lists an error case, implement it.
- Use `conn = Depends(get_conn)` and `user = Depends(get_current_user)`. Use parameterised SQL (`?`).
  Never build SQL by string formatting. Call conn.commit() after writes.
- Always filter per-user data by user["id"]. A record that belongs to someone else is a 404, never a 403.
- Store dates as ISO text (YYYY-MM-DD). Store money as REAL and round to 2 decimals.
- A router file defines `router = APIRouter(...)`. Put the full path prefix in the router or the decorators
  so the final URL matches the contract exactly (for example /api/invoices/{{invoice_id}}).
- Migrations: CREATE TABLE IF NOT EXISTS, user_id INTEGER NOT NULL REFERENCES users(id) on per-user tables,
  and reference tables from earlier migrations by their real names.
- Do not write tests.

Output format. Each file exactly like this, nothing else outside the blocks:
=== FILE: <path> ===
<complete file content>
=== END FILE ===
"""


def _stem(path: str) -> str:
    return path.rsplit("/", 1)[-1].removesuffix(".py")


def endpoints_for_task(contract: dict[str, Any], task: dict[str, Any]) -> list[dict[str, Any]]:
    """The contract endpoints this task's router files own.

    An endpoint belongs to the router whose name equals the path's first segment after /api/
    (/api/invoices/{id}/payments -> invoices.py). If a task has no router that matches anything,
    fall back to the endpoints that share a requirement code with it.
    """
    stems = {_stem(f) for f in task["files"] if f.startswith("app/routers/")}
    owned = [e for e in contract["endpoints"] if e["path"].split("/")[2:3] and e["path"].split("/")[2].split(".")[0] in stems]
    if owned:
        return owned
    codes = set(task["requirement_codes"])
    return [e for e in contract["endpoints"] if codes & set(e.get("requirement_codes", []))]


def _json(obj: Any) -> str:
    return json.dumps(obj, indent=1)


def build_prompt(
    task: dict[str, Any],
    contract: dict[str, Any],
    requirements: list[dict[str, Any]],
    ws: Workspace,
) -> str:
    by_code = {r["code"]: r for r in requirements}
    reqs = "\n".join(
        f"- {c}: {by_code[c]['title']}. Acceptance: {by_code[c]['acceptance']}"
        for c in task["requirement_codes"]
        if c in by_code
    )
    existing = ws.files_under("app/migrations", ".sql")
    existing_txt = "\n\n".join(f"-- {p}\n{c}" for p, c in existing.items()) or "(none yet)"
    return (
        f"TASK: {task['title']}\n{task['description']}\n\n"
        f"FILES YOU MUST WRITE (exactly these, no others):\n" + "\n".join(f"- {f}" for f in task["files"]) + "\n\n"
        f"REQUIREMENTS THIS TASK SATISFIES:\n{reqs}\n\n"
        f"ENDPOINTS YOUR ROUTER FILES MUST IMPLEMENT:\n{_json(endpoints_for_task(contract, task))}\n\n"
        f"DATA MODEL (all tables in the project):\n{_json(contract['entities'])}\n\n"
        f"MIGRATIONS ALREADY IN THE PROJECT (tables you can reference):\n{existing_txt}\n"
    )


def check_files(task: dict[str, Any], files: dict[str, str]) -> list[str]:
    """Static checks on a reply, before anything touches the workspace."""
    problems: list[str] = []
    wanted = set(task["files"])
    for extra in sorted(set(files) - wanted):
        problems.append(f"{extra} was not assigned to you; write only the assigned files")
    for missing in sorted(wanted - set(files)):
        problems.append(f"{missing} is missing from your reply")
    for path, content in files.items():
        if path not in wanted:
            continue
        if path.endswith(".py"):
            try:
                ast.parse(content)
            except SyntaxError as e:
                problems.append(f"{path}: SyntaxError on line {e.lineno}: {e.msg}")
            if "router" not in content:
                problems.append(f"{path}: must define `router = APIRouter(...)`")
    return problems


def _save_debug(ws: Workspace, task: dict[str, Any], attempt: int, res: Any) -> None:
    """Keep every raw reply in the workspace so a failed task can be inspected afterwards."""
    slug = re.sub(r"[^a-z0-9]+", "_", task["title"].lower()).strip("_")[:40]
    d = ws.root / ".dev_debug"
    d.mkdir(exist_ok=True)
    (d / f"{slug}_attempt{attempt}.txt").write_text(
        f"finish_reason={res.finish_reason!r} completion_tokens={res.completion_tokens}\n\n{res.text}"
    )


def build_task(
    llm: LLMClient,
    ws: Workspace,
    task: dict[str, Any],
    contract: dict[str, Any],
    requirements: list[dict[str, Any]],
) -> dict[str, Any]:
    """Write one task's files. Returns {"ok", "files", "problems", "attempts"}; workspace is untouched on failure."""
    messages: list[dict[str, str]] = [
        {"role": "system", "content": DEV_SYSTEM},
        {"role": "user", "content": build_prompt(task, contract, requirements, ws)},
    ]
    problems: list[str] = []
    for attempt in range(1, MAX_FIX_ROUNDS + 2):
        res = llm.chat("dev", messages, temperature=0.2, max_tokens=MAX_REPLY_TOKENS)
        _save_debug(ws, task, attempt, res)
        files = parse_file_blocks(res.text)
        truncated = res.finish_reason == "length"
        if truncated:
            problems = [
                "your reply was cut off because it was too long. Write shorter, simpler code: "
                "fewer helper functions, no long comments or docstrings, no unrequested extras"
            ]
        else:
            problems = check_files(task, files) if files else ["no file blocks found in your reply; use the exact format"]
        if not problems:
            mine = {p: files[p] for p in task["files"]}
            snap = ws.snapshot(list(mine))
            ws.write_files(mine)
            ok, err = ws.smoke_check()
            if ok:
                return {"ok": True, "files": sorted(mine), "problems": [], "attempts": attempt}
            ws.restore(snap)
            problems = [f"the app failed to start with your files:\n{err}"]
        if attempt <= MAX_FIX_ROUNDS:
            if not truncated:  # a cut-off reply is garbage; do not feed it back
                messages.append({"role": "assistant", "content": res.text})
            messages += [
                {
                    "role": "user",
                    "content": "Problems found:\n- " + "\n- ".join(problems)
                    + "\n\nFix them and send ALL assigned files again in the same format.",
                },
            ]
    return {"ok": False, "files": [], "problems": problems, "attempts": MAX_FIX_ROUNDS + 1}


def build_all(conn: sqlite3.Connection, llm: LLMClient, ws: Workspace, project_id: int) -> dict[str, Any]:
    """Build every task from the TL's plan, in order. Updates the task board and requirement statuses."""
    reqs = db.list_requirements(conn, project_id)
    contract = db.get_artifact(conn, project_id, "contract")
    plan = db.get_artifact(conn, project_id, "plan")
    if not contract or not plan:
        raise ValueError("No plan yet. Run the TL AI first.")
    rows = db.list_tasks(conn, project_id)
    db.set_project_status(conn, project_id, "building")

    results = []
    for row, task in zip(rows, plan["tasks"]):
        db.set_task_status(conn, row["id"], "doing")
        for code in task["requirement_codes"]:
            db.set_requirement_status(conn, project_id, code, "building")
        db.log_event(conn, project_id, "status", "dev", {"message": f"Building: {task['title']}"})
        out = build_task(llm, ws, task, contract, reqs)
        results.append({"task": task["title"], **out})
        if out["ok"]:
            db.set_task_status(conn, row["id"], "done")
            for code in task["requirement_codes"]:
                db.set_requirement_status(conn, project_id, code, "built")
            db.log_event(
                conn, project_id, "build", "dev",
                {"task": task["title"], "files": out["files"], "attempts": out["attempts"]},
            )
        else:
            db.set_task_status(conn, row["id"], "blocked")
            for code in task["requirement_codes"]:
                db.set_requirement_status(conn, project_id, code, "failing", "; ".join(out["problems"])[:500])
            db.log_event(
                conn, project_id, "build_failed", "dev", {"task": task["title"], "problems": out["problems"]}
            )
    all_ok = all(r["ok"] for r in results)
    db.set_project_status(conn, project_id, "built" if all_ok else "build_incomplete")
    return {"ok": all_ok, "results": results}