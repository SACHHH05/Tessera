"""Status AI: a small, fast model that writes short plain-English updates for the client.

It only reports; it never decides anything. If the model is unavailable or over budget, a plain
template message is posted instead, so a status failure can never stop a build.
"""

from __future__ import annotations

import re
import sqlite3
from typing import Any

from .. import db
from ..llm import LLMClient

STATUS_SYSTEM = """You are the project manager at a small software agency, writing a short client update.
The client is not technical. Write 1 or 2 plain sentences, at most 45 words, in the first person plural ("we").
Use only the facts you are given. Never invent numbers, features or dates. Do not mention models, tokens,
endpoints, tests frameworks or code. Say what happened and, if it matters, what happens next.
If some requirements are not verified, say so honestly and name them. Reply with the update text only."""

MOMENTS = {
    "planned": "The plan is ready; building starts next.",
    "built": "The code is written; independent testing starts next.",
    "verified": "Independent testing just finished for the first time.",
    "fix_round": "A fix round just finished: failing requirements were sent back to the developer and re-tested.",
    "done": "The project is finished.",
}


def _facts(conn: sqlite3.Connection, project_id: int, moment: str, extra: dict[str, Any]) -> tuple[str, str]:
    project = db.get_project(conn, project_id)
    reqs = db.list_requirements(conn, project_id)
    summary = db.verified_summary(conn, project_id)
    failing = [f"{r['code']} {r['title']}" for r in reqs if r["status"] == "failing"]
    lines = [
        f"Client's idea: {project['idea']}",
        f"Moment: {MOMENTS[moment]}",
        f"Requirements: {summary['total']} in total, {summary['passing']} verified passing, {summary['failing']} failing.",
    ]
    if failing:
        lines.append("Not verified: " + "; ".join(failing))
    for key, value in extra.items():
        lines.append(f"{key}: {value}")
    fallback = {
        "planned": f"We turned your idea into {summary['total']} requirements and a plan. Building starts now.",
        "built": "The first version of the app is written. Independent testing starts now.",
        "verified": f"First test results: {summary['passing']} of {summary['total']} requirements verified.",
        "fix_round": f"After a fix round, {summary['passing']} of {summary['total']} requirements are verified.",
        "done": f"Finished: {summary['passing']} of {summary['total']} requirements verified."
        + (" The rest are listed with their reasons." if summary["failing"] else ""),
    }[moment]
    return "\n".join(lines), fallback


def clean(text: str) -> str:
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.S).strip().strip('"').strip()
    text = re.sub(r"\s+", " ", text)
    return text[:400]


def post_update(conn: sqlite3.Connection, llm: LLMClient, project_id: int, moment: str, **extra: Any) -> str:
    """Write one client update to the event log and return its text."""
    facts, fallback = _facts(conn, project_id, moment, extra)
    message = ""
    try:
        res = llm.chat(
            "status",
            [{"role": "system", "content": STATUS_SYSTEM}, {"role": "user", "content": facts}],
            temperature=0.4,
            max_tokens=200,
            retries=1,
        )
        message = clean(res.text)
    except Exception:  # a status update is never worth stopping a build for
        message = ""
    db.log_event(conn, project_id, "update", "status", {"message": message or fallback, "moment": moment})
    return message or fallback