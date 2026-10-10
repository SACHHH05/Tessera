"""Runs the whole agency: intake -> plan -> build -> verify -> fix rounds.

Every stage reads and writes the shared board (db.py), so a project can be resumed or a single stage
re-run. Failing requirements go back to the Developer with the independent tests and their errors,
at most MAX_FIX_ROUNDS times; whatever still fails is reported honestly.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any, Callable

from . import db
from .agents import dev_ai, main_ai, tester_ai, tl_ai
from .llm import LLMClient
from .sandbox import Runner, Workspace
from .tavily import TavilyClient

STAGES = ["intake", "plan", "build", "verify", "fix"]
MAX_FIX_ROUNDS = 2


def fix_failing(
    conn: sqlite3.Connection,
    llm: LLMClient,
    ws: Workspace,
    runner: Runner,
    project_id: int,
    result: Any,
    rounds: int = MAX_FIX_ROUNDS,
    say: Callable[[str], None] = lambda _: None,
) -> list[dict[str, Any]]:
    """Developer repairs the tasks behind failing requirements; tests re-run after each round."""
    history: list[dict[str, Any]] = []
    contract = db.get_artifact(conn, project_id, "contract")
    plan = db.get_artifact(conn, project_id, "plan")
    for rnd in range(1, rounds + 1):
        tests = db.get_artifact(conn, project_id, "tests") or {}
        reqs = db.list_requirements(conn, project_id)
        failing = {r["code"] for r in reqs if r["status"] == "failing"}
        if not failing:
            break
        say(f"Fix round {rnd}: {len(failing)} requirement(s) failing ({', '.join(sorted(failing))})")
        db.log_event(conn, project_id, "status", "tl", {"message": f"Fix round {rnd}: sending {len(failing)} failing requirement(s) back to the developer."})
        changed: list[str] = []
        for task in plan["tasks"]:
            codes = [c for c in task["requirement_codes"] if c in failing]
            if not codes:
                continue
            failures = {c: tester_ai.failure_text(result["run"] if isinstance(result, dict) else result, c) for c in codes}
            srcs = {tester_ai.test_path(c): tests[tester_ai.test_path(c)] for c in codes if tester_ai.test_path(c) in tests}
            prompt = dev_ai.build_fix_prompt(task, contract, reqs, ws, failures, srcs)
            out = dev_ai.build_task(llm, ws, task, contract, reqs, prompt=prompt)
            db.log_event(conn, project_id, "fix", "dev", {"round": rnd, "task": task["title"], "ok": out["ok"], "codes": codes, "problems": out["problems"]})
            if out["ok"]:
                changed.append(task["title"])
        after = tester_ai.rerun(conn, ws, runner, project_id, llm)
        result = after
        history.append({"round": rnd, "failing_before": sorted(failing), "tasks_changed": changed, "summary": after["summary"]})
        say(f"  after round {rnd}: {after['summary']['passing']} of {after['summary']['total']} verified")
    return history


def run_project(
    conn: sqlite3.Connection,
    llm: LLMClient,
    tavily: TavilyClient | None,
    runner: Runner,
    workspaces: Path,
    *,
    idea: str | None = None,
    project_id: int | None = None,
    stages: list[str] | None = None,
    rounds: int = MAX_FIX_ROUNDS,
    say: Callable[[str], None] = print,
) -> dict[str, Any]:
    stages = stages or STAGES
    if project_id is None:
        if not idea:
            raise ValueError("Give either an idea or an existing project id.")
        project_id = db.create_project(conn, idea)
    out: dict[str, Any] = {"project_id": project_id}
    ws = Workspace.create(workspaces, f"project-{project_id}", fresh="build" in stages)
    verify_result: Any = None

    if "intake" in stages:
        say("Main AI: understanding the idea and drafting requirements...")
        main_ai.run_intake(conn, llm, tavily, project_id)
    if "plan" in stages:
        say("TL AI: designing the API and planning tasks...")
        out["plan_problems"] = tl_ai.plan_project(conn, llm, project_id)["problems"]
    if "build" in stages:
        say("Developer AI: building...")
        built = dev_ai.build_all(conn, llm, ws, project_id)
        out["build"] = [{"task": r["task"], "ok": r["ok"], "attempts": r["attempts"]} for r in built["results"]]
    if "verify" in stages:
        say("Tester AI: writing independent tests and running them...")
        verify_result = tester_ai.verify_project(conn, llm, ws, runner, project_id)
        s = verify_result["summary"]
        say(f"  {s['passing']} of {s['total']} requirements verified")
    if "fix" in stages:
        if verify_result is None:
            verify_result = tester_ai.rerun(conn, ws, runner, project_id, llm)
        out["fix_rounds"] = fix_failing(conn, llm, ws, runner, project_id, verify_result, rounds, say)

    out["summary"] = db.verified_summary(conn, project_id)
    complete = out["summary"]["passing"] == out["summary"]["total"] and out["summary"]["total"] > 0
    db.set_project_status(conn, project_id, "verified" if complete else "needs_attention")
    db.log_event(conn, project_id, "done", "main", {**out["summary"], "complete": complete})
    out["workspace"] = str(ws.root)
    return out