"""Main AI: the account manager. Talks to the client and owns the requirements.

Flow: (optional) clarifying questions -> Tavily market research -> numbered requirements
with acceptance criteria that the Tester AI can verify automatically.
"""

from __future__ import annotations

import sqlite3
from typing import Any

from .. import db
from ..llm import LLMClient
from ..tavily import TavilyClient

QUESTIONS_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {"questions": {"type": "array", "items": {"type": "string"}}},
    "required": ["questions"],
    "additionalProperties": False,
}

REQUIREMENTS_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "requirements": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {"title": {"type": "string"}, "acceptance": {"type": "string"}},
                "required": ["title", "acceptance"],
                "additionalProperties": False,
            },
        },
        "assumptions": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["requirements", "assumptions"],
    "additionalProperties": False,
}

QUESTIONS_SYSTEM = (
    "You are the account manager of a small AI software agency. A client describes an idea for a "
    "small multi-user SaaS web app. Ask the 2 or 3 most important clarifying questions a good "
    "account manager would ask (who the users are, the core data, any must-have feature). Keep "
    "each question short and in plain language. Respond as JSON: {\"questions\": [...]}."
)

REQUIREMENTS_SYSTEM = (
    "You are the account manager of a small AI software agency. Turn the client's idea into 8 to 12 "
    "numbered requirements for a small multi-user SaaS web app (Python FastAPI + SQLite, JSON API, "
    "simple frontend).\n"
    "Rules:\n"
    "- Each requirement has a short title and an acceptance criterion.\n"
    "- Every acceptance criterion must be an observable behavior that an automated test can check "
    "over HTTP, for example: 'POST /api/invoices with a valid body returns 201 and the invoice "
    "appears in GET /api/invoices'. No vague criteria such as 'is user friendly'.\n"
    "- Cover: sign up and login, data isolation between users, the core create/read/update/delete "
    "flows for the main entity, input validation, and one report or export when it fits the idea.\n"
    "- The app can only use FastAPI, SQLite and the Python standard library. Do NOT require email "
    "sending, PDF generation, payments, file uploads, background jobs, or any third-party service. "
    "Use CSV or JSON for exports and reports.\n"
    "- If market research is provided, use it to include features users expect, but do not copy "
    "competitors.\n"
    "- List the assumptions you made about anything the client did not say.\n"
    "Respond as JSON: {\"requirements\": [{\"title\": ..., \"acceptance\": ...}], \"assumptions\": [...]}."
)


def _call_json(
    llm: LLMClient,
    role: str,
    messages: list[dict[str, Any]],
    schema: dict[str, Any],
    name: str,
    max_tokens: int = 4000,
) -> Any:
    """Schema-constrained JSON with fallbacks (see LLMClient.chat_json)."""
    return llm.chat_json(role, messages, schema, name, max_tokens)


def clarifying_questions(llm: LLMClient, idea: str) -> list[str]:
    data = _call_json(
        llm,
        "main",
        [{"role": "system", "content": QUESTIONS_SYSTEM}, {"role": "user", "content": idea}],
        QUESTIONS_SCHEMA,
        "questions",
        max_tokens=2000,
    )
    return [q for q in data.get("questions", []) if q.strip()][:3]


def research_market(tavily: TavilyClient | None, idea: str) -> list[dict[str, Any]]:
    """Best-effort grounding. Returns [] if Tavily is not configured or fails."""
    if tavily is None or not tavily.api_key:
        return []
    results: list[dict[str, Any]] = []
    for q in (f"{idea} software key features competitors", f"best {idea} app must-have features"):
        try:
            results.extend(tavily.search(q, max_results=4))
        except Exception:
            continue
    seen, unique = set(), []
    for r in results:
        if r.get("url") and r["url"] not in seen:
            seen.add(r["url"])
            unique.append(r)
    return unique


def _research_text(research: list[dict[str, Any]]) -> str:
    lines = [f"- {r.get('title')}: {(r.get('content') or '')[:300]}" for r in research[:8]]
    return "\n".join(lines)


def draft_requirements(
    llm: LLMClient, idea: str, research: list[dict[str, Any]], answers: str = ""
) -> dict[str, Any]:
    user = f"Client idea:\n{idea}\n"
    if answers.strip():
        user += f"\nClient answers to clarifying questions:\n{answers.strip()}\n"
    if research:
        user += f"\nMarket research (similar products):\n{_research_text(research)}\n"
    data = _call_json(
        llm,
        "main",
        [{"role": "system", "content": REQUIREMENTS_SYSTEM}, {"role": "user", "content": user}],
        REQUIREMENTS_SCHEMA,
        "requirements",
    )
    reqs = [
        {"title": r["title"].strip(), "acceptance": r["acceptance"].strip()}
        for r in data.get("requirements", [])
        if r.get("title") and r.get("acceptance")
    ]
    if not reqs:
        raise RuntimeError("Main AI produced no usable requirements.")
    return {"requirements": reqs, "assumptions": data.get("assumptions", [])}


def run_intake(
    conn: sqlite3.Connection,
    llm: LLMClient,
    tavily: TavilyClient | None,
    project_id: int,
    answers: str = "",
) -> dict[str, Any]:
    """Research, draft requirements, store them on the board, and log events for the dashboard."""
    project = db.get_project(conn, project_id)
    if not project:
        raise ValueError(f"project {project_id} not found")
    idea = project["idea"]

    db.log_event(conn, project_id, "status", "main", {"message": "Researching similar products."})
    research = research_market(tavily, idea)
    db.log_event(
        conn,
        project_id,
        "research",
        "main",
        {"sources": [{"title": r.get("title"), "url": r.get("url")} for r in research]},
    )

    db.log_event(conn, project_id, "status", "main", {"message": "Drafting requirements."})
    out = draft_requirements(llm, idea, research, answers)
    db.add_requirements(conn, project_id, out["requirements"])
    db.set_project_status(conn, project_id, "planned")
    db.log_event(
        conn,
        project_id,
        "requirements",
        "main",
        {"count": len(out["requirements"]), "assumptions": out["assumptions"]},
    )
    return {**out, "sources": research}