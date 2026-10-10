"""Tester AI: proves each requirement, without ever seeing the Developer's code.

It receives only what a client-side QA engineer would have: the requirement with its acceptance
criteria, the API contract, and the shared response conventions. It writes one black-box test file
per requirement (tests/test_req_NNN.py) that talks to the app over HTTP.

The separation is enforced in code: the prompt builder is only handed requirements and the
contract, never a workspace, and generated tests are rejected if they import the app's code.
"""

from __future__ import annotations

import ast
import json
import re
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from typing import Any

from .. import db
from ..fileblocks import parse_file_blocks
from ..llm import LLMClient
from ..sandbox import TEST_PATTERNS, Runner, RunResult, Workspace
from ..scaffold_spec import API_CONVENTIONS, TESTER_FIXTURES

MAX_REPLY_TOKENS = 6000
WORKERS = 4

TESTER_SYSTEM = f"""\
You are an independent QA engineer. You have never seen the implementation and you must not guess at
it. You write pytest tests that check ONE requirement of a web API strictly through its HTTP interface.

{TESTER_FIXTURES}
{API_CONVENTIONS}
Rules:
- Black-box only. Never import anything from `app`. Use only pytest and the `client` / `login` fixtures.
- Create every record you need through the API itself (for example create a client before an invoice),
  using only fields and rules from the contract.
- Cover the acceptance criteria and every error case of the endpoints listed for this requirement:
  the happy path with real values, each validation failure, 401 without a token, 404 for a missing
  or someone else's record. Use clearly invalid input to trigger validation errors, nothing borderline.
- Assert status codes and fields the contract or conventions guarantee. Do not assert error wording,
  field order, or response fields nobody promised.
- Each test is independent and starts from an empty database. Keep tests short and readable.
- For data isolation, create data as one user and check a second user cannot see, change or delete it.

Output exactly one file, in this format and nothing else outside it:
=== FILE: tests/test_req_NNN.py ===
<complete file content>
=== END FILE ===
"""


def test_path(code: str) -> str:
    """REQ-006 -> tests/test_req_006.py"""
    return f"tests/test_req_{code.split('-')[-1].lower()}.py"


def code_from_path(text: str) -> str | None:
    m = re.search(r"test_req_(\d+)", text)
    return f"REQ-{m.group(1)}" if m else None


def endpoints_for_requirement(contract: dict[str, Any], code: str) -> list[dict[str, Any]]:
    return [e for e in contract["endpoints"] if code in e.get("requirement_codes", [])]


def build_prompt(requirement: dict[str, Any], contract: dict[str, Any]) -> str:
    code = requirement["code"]
    primary = endpoints_for_requirement(contract, code)
    if code in contract.get("scaffold_provided", []) and not primary:
        focus = "This requirement is delivered by the scaffold's signup/login API described above."
    else:
        focus = "ENDPOINTS FOR THIS REQUIREMENT:\n" + json.dumps(primary, indent=1)
    others = [f"{e['method']} {e['path']}" for e in contract["endpoints"] if e not in primary]
    return (
        f"REQUIREMENT {code}: {requirement['title']}\n"
        f"ACCEPTANCE CRITERIA: {requirement['acceptance']}\n\n"
        f"{focus}\n\n"
        f"OTHER ENDPOINTS YOU MAY CALL TO SET UP DATA: {', '.join(others) or '(none)'}\n"
        f"FULL REQUEST DETAILS FOR THOSE: {json.dumps([e for e in contract['endpoints'] if e not in primary], indent=1)}\n\n"
        f"DATA MODEL:\n{json.dumps(contract['entities'], indent=1)}\n\n"
        f"Write {test_path(code)}."
    )


def check_test_file(path: str, content: str) -> list[str]:
    problems: list[str] = []
    try:
        tree = ast.parse(content)
    except SyntaxError as e:
        return [f"SyntaxError on line {e.lineno}: {e.msg}"]
    for node in ast.walk(tree):
        names = []
        if isinstance(node, ast.Import):
            names = [a.name for a in node.names]
        elif isinstance(node, ast.ImportFrom):
            names = [node.module or ""]
        if any(n == "app" or n.startswith("app.") for n in names):
            problems.append("do not import from `app`; test only through the HTTP API with the fixtures")
    tests = [n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name.startswith("test_")]
    if not tests:
        problems.append("no test functions (def test_...)")
    if not any(isinstance(n, ast.Assert) for n in ast.walk(tree)):
        problems.append("no assert statements; tests must assert on status codes or fields")
    if "client" not in content:
        problems.append("tests must call the API through the `client` fixture")
    return problems


def write_test(llm: LLMClient, requirement: dict[str, Any], contract: dict[str, Any]) -> dict[str, Any]:
    """One requirement -> one validated test file. Returns {"code","path","content","problems"}."""
    code = requirement["code"]
    path = test_path(code)
    messages = [
        {"role": "system", "content": TESTER_SYSTEM},
        {"role": "user", "content": build_prompt(requirement, contract)},
    ]
    problems: list[str] = []
    content = ""
    for attempt in range(2):
        res = llm.chat("tester", messages, temperature=0.2, max_tokens=MAX_REPLY_TOKENS)
        files = parse_file_blocks(res.text)
        content = next(iter(files.values()), "") if len(files) == 1 else files.get(path, "")
        if res.finish_reason == "length":
            problems = ["your reply was cut off; write fewer, shorter tests"]
        elif not content:
            problems = [f"no file block found; reply with exactly one file, {path}"]
        else:
            problems = check_test_file(path, content)
        if not problems:
            return {"code": code, "path": path, "content": content, "problems": []}
        messages += [
            {"role": "assistant", "content": res.text if res.finish_reason != "length" else "(cut off)"},
            {"role": "user", "content": "Problems:\n- " + "\n- ".join(problems) + f"\nSend the corrected {path}."},
        ]
    return {"code": code, "path": path, "content": "", "problems": problems}


def grade(requirements: list[dict[str, Any]], written: dict[str, dict[str, Any]], result: RunResult) -> dict[str, dict[str, str]]:
    """Turn one pytest run into a verdict per requirement: passing | failing | untested."""
    by_code: dict[str, list[tuple[str, str]]] = {}
    for key, outcome in result.tests.items():
        code = code_from_path(key)
        if code:
            by_code.setdefault(code, []).append((key, outcome))
    verdicts: dict[str, dict[str, str]] = {}
    for r in requirements:
        code = r["code"]
        w = written.get(code)
        if not w or w["problems"]:
            verdicts[code] = {"status": "untested", "detail": "; ".join(w["problems"]) if w else "no test written"}
            continue
        outcomes = by_code.get(code, [])
        bad = [(k, o) for k, o in outcomes if o != "passed"]
        if not outcomes:
            tail = (result.stdout + result.stderr).strip().splitlines()[-6:]
            verdicts[code] = {"status": "failing", "detail": "tests did not run: " + " | ".join(tail)[:400]}
        elif bad:
            key, outcome = bad[0]
            detail = result.details.get(key, outcome)
            verdicts[code] = {
                "status": "failing",
                "detail": f"{len(bad)} of {len(outcomes)} tests {outcome}; first: {key.split('::')[-1]}: {detail}"[:700],
            }
        else:
            verdicts[code] = {"status": "passing", "detail": f"{len(outcomes)} tests passed"}
    return verdicts


def verify_project(
    conn: sqlite3.Connection, llm: LLMClient, ws: Workspace, runner: Runner, project_id: int
) -> dict[str, Any]:
    """Write a test per requirement (in parallel), run them all once, record a verdict per requirement."""
    reqs = db.list_requirements(conn, project_id)
    contract = db.get_artifact(conn, project_id, "contract")
    if not contract:
        raise ValueError("No contract yet. Run the TL AI first.")
    db.set_project_status(conn, project_id, "testing")
    db.log_event(conn, project_id, "status", "tester", {"message": f"Writing tests for {len(reqs)} requirements."})

    with ThreadPoolExecutor(max_workers=WORKERS) as pool:
        written_list = list(pool.map(lambda r: write_test(llm, r, contract), reqs))
    written = {w["code"]: w for w in written_list}

    ws.write_files({w["path"]: w["content"] for w in written_list if not w["problems"]}, patterns=TEST_PATTERNS)
    db.save_artifact(conn, project_id, "tests", {w["path"]: w["content"] for w in written_list if not w["problems"]})

    result = runner.run_pytest(ws.root)
    verdicts = grade(reqs, written, result)
    for r in reqs:
        v = verdicts[r["code"]]
        db.set_requirement_status(conn, project_id, r["code"], v["status"], v["detail"])
        db.log_event(conn, project_id, "test", "tester", {"code": r["code"], **v})
    summary = db.verified_summary(conn, project_id)
    db.log_event(conn, project_id, "status", "tester", {"message": f"{summary['passing']} of {summary['total']} requirements verified passing.", **summary})
    return {"verdicts": verdicts, "summary": summary, "run": result}