"""TL AI: the tech lead.

Turns the requirements into (1) an API contract, the shared agreement the developer and the
independent tester both work from, and (2) a task list tied to requirement IDs.

Both are validated in code, not trusted: the contract must define entities and give every
requirement an endpoint, and the plan must cover every requirement with allowed files only.
Each is repaired once if it fails; anything still wrong is reported, never hidden.
"""

from __future__ import annotations

import json
import re
import sqlite3
from typing import Any

from .. import db
from ..llm import LLMClient
from ..scaffold_spec import FILE_PATTERNS, SCAFFOLD_SPEC

VALID_METHODS = {"GET", "POST", "PUT", "PATCH", "DELETE"}

_STR = {"type": "string"}
_STRS = {"type": "array", "items": _STR}

CONTRACT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "scaffold_provided": _STRS,
        "entities": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "name": _STR,
                    "fields": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {"name": _STR, "type": _STR, "notes": _STR},
                            "required": ["name", "type", "notes"],
                            "additionalProperties": False,
                        },
                    },
                },
                "required": ["name", "fields"],
                "additionalProperties": False,
            },
        },
        "endpoints": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "method": _STR,
                    "path": _STR,
                    "summary": _STR,
                    "request_body": _STR,
                    "success_status": {"type": "integer"},
                    "error_cases": _STRS,
                    "auth_required": {"type": "boolean"},
                    "requirement_codes": _STRS,
                },
                "required": [
                    "method",
                    "path",
                    "summary",
                    "request_body",
                    "success_status",
                    "error_cases",
                    "auth_required",
                    "requirement_codes",
                ],
                "additionalProperties": False,
            },
        },
    },
    "required": ["scaffold_provided", "entities", "endpoints"],
    "additionalProperties": False,
}

TASKS_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "tasks": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "title": _STR,
                    "description": _STR,
                    "requirement_codes": _STRS,
                    "files": _STRS,
                },
                "required": ["title", "description", "requirement_codes", "files"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["tasks"],
    "additionalProperties": False,
}

CONTRACT_SYSTEM = (
    "You are the tech lead of a small AI software agency. Design the API contract for the app "
    "so that a developer and an independent tester can each work from it alone, without seeing "
    "each other's work.\n\n"
    + SCAFFOLD_SPEC
    + "\nRules for the contract:\n"
    "- JSON API, all paths start with /api/. Use {id} for path parameters.\n"
    "- The whole /api/auth/ namespace belongs to the scaffold. Do NOT add any endpoint under it "
    "(no register, refresh, logout, me), and add no /api/me or /api/users endpoints.\n"
    "- scaffold_provided: the requirement IDs completely satisfied by the scaffold's signup and "
    "login alone (normally just the sign-up and login requirements). List nothing else there.\n"
    "- entities: define one entity for every table the app needs (real tables only: not reports, "
    "views or the users table, which the scaffold owns), with every field, its type, and notes "
    "(constraints, defaults, allowed values).\n"
    "- endpoints: define an endpoint for EVERY behavior in EVERY other requirement's acceptance "
    "criterion. Each requirement that is not in scaffold_provided must appear in the "
    "requirement_codes of at least one endpoint. Completeness matters more than brevity: a tester "
    "writes tests from this contract alone.\n"
    "- Be exact for each endpoint: field names, types, constraints (required, min, max, allowed "
    "values), the success status, and error cases written as 'status: condition'.\n"
    "- Set auth_required true on every endpoint.\n\n"
    "Example of the level of detail wanted (a different app, for illustration only):\n"
    '{"method": "POST", "path": "/api/notes", "summary": "Create a note owned by the current user", '
    '"request_body": "title: str (1-100 chars, required); body: str (optional, max 5000 chars)", '
    '"success_status": 201, "error_cases": ["422: title missing or empty", "401: no valid token"], '
    '"auth_required": true, "requirement_codes": ["REQ-004"]}\n\n'
    "Respond only as JSON matching the schema."
)

TASKS_SYSTEM = (
    "You are the tech lead of a small AI software agency. Break the build into 4 to 8 developer "
    "tasks, in dependency order, from the requirements and the API contract.\n\n"
    + SCAFFOLD_SPEC
    + "\nRules for the plan:\n"
    "- Each task has a title, a description (what to build, referring to contract endpoints), "
    "requirement_codes, and files.\n"
    "- files may ONLY be app/routers/<name>.py or app/migrations/NNN_<name>.sql as described "
    "above. Migration numbers must be unique across tasks.\n"
    "- A file belongs to exactly one task. A task must NOT modify a file owned by another task: "
    "put each endpoint in the task that owns its router, or give it a new router file.\n"
    "- Never create routers named auth*. Authentication belongs to the scaffold.\n"
    "- Group by feature. Put each entity's migration in the same task as its router.\n"
    "- The contract's scaffold_provided requirements need no tasks. Every other requirement must "
    "appear in at least one task.\n"
    "Respond only as JSON matching the schema."
)


def _req_text(requirements: list[dict[str, Any]]) -> str:
    return "\n".join(f"{r['code']} | {r['title']} | {r['acceptance']}" for r in requirements)


def design_contract(llm: LLMClient, requirements: list[dict[str, Any]]) -> dict[str, Any]:
    return llm.chat_json(
        "tl",
        [
            {"role": "system", "content": CONTRACT_SYSTEM},
            {"role": "user", "content": f"Requirements:\n{_req_text(requirements)}"},
        ],
        CONTRACT_SCHEMA,
        "contract",
        max_tokens=10000,
    )


def repair_contract(
    llm: LLMClient,
    requirements: list[dict[str, Any]],
    contract: dict[str, Any],
    problems: list[str],
) -> dict[str, Any]:
    issues = "\n".join(f"- {p}" for p in problems)
    return llm.chat_json(
        "tl",
        [
            {"role": "system", "content": CONTRACT_SYSTEM},
            {
                "role": "user",
                "content": f"Requirements:\n{_req_text(requirements)}\n\n"
                f"Your previous contract:\n{json.dumps(contract, indent=1)}\n\n"
                f"It has these problems:\n{issues}\n\nReturn the full corrected contract.",
            },
        ],
        CONTRACT_SCHEMA,
        "contract",
        max_tokens=10000,
    )


def break_into_tasks(
    llm: LLMClient, requirements: list[dict[str, Any]], contract: dict[str, Any]
) -> dict[str, Any]:
    return llm.chat_json(
        "tl",
        [
            {"role": "system", "content": TASKS_SYSTEM},
            {
                "role": "user",
                "content": f"Requirements:\n{_req_text(requirements)}\n\n"
                f"API contract:\n{json.dumps(contract, indent=1)}",
            },
        ],
        TASKS_SCHEMA,
        "plan",
        max_tokens=8000,
    )


def repair_plan(
    llm: LLMClient,
    requirements: list[dict[str, Any]],
    contract: dict[str, Any],
    plan: dict[str, Any],
    problems: list[str],
) -> dict[str, Any]:
    issues = "\n".join(f"- {p}" for p in problems)
    return llm.chat_json(
        "tl",
        [
            {"role": "system", "content": TASKS_SYSTEM},
            {
                "role": "user",
                "content": f"Requirements:\n{_req_text(requirements)}\n\n"
                f"API contract:\n{json.dumps(contract, indent=1)}\n\n"
                f"Your previous plan:\n{json.dumps(plan, indent=1)}\n\n"
                f"It has these problems:\n{issues}\n\nReturn the full corrected plan.",
            },
        ],
        TASKS_SCHEMA,
        "plan",
        max_tokens=8000,
    )


def validate_contract(requirements: list[dict[str, Any]], contract: dict[str, Any]) -> list[str]:
    """Return a list of problems (empty means the contract is usable)."""
    problems: list[str] = []
    known = {r["code"] for r in requirements}
    endpoints = contract.get("endpoints", [])

    if not endpoints:
        problems.append("the contract has no endpoints")
    if endpoints and not contract.get("entities"):
        problems.append("the contract defines no entities (tables and their fields)")
    for entity in contract.get("entities", []):
        if entity.get("name", "").lower() in {"user", "users"}:
            problems.append("entity 'users': the users table belongs to the scaffold, remove it")
        elif not entity.get("fields"):
            problems.append(
                f"entity '{entity.get('name')}' has no fields; remove it (reports and exports are "
                "queries over real tables, not entities)"
            )

    covered: set[str] = set()
    for code in contract.get("scaffold_provided", []):
        if code not in known:
            problems.append(f"scaffold_provided has unknown requirement code {code}")
        covered.add(code)

    for ep in endpoints:
        method, path = ep.get("method", ""), ep.get("path", "")
        label = f"endpoint {method} {path}"
        if method not in VALID_METHODS:
            problems.append(f"{label}: invalid method")
        if not path.startswith("/api/"):
            problems.append(f"{label}: path must start with /api/")
        if path == "/api/auth" or path.startswith("/api/auth/"):
            problems.append(f"{label}: the /api/auth/ namespace belongs to the scaffold, remove it")
        if path == "/api/me" or path == "/api/users" or path.startswith("/api/users/"):
            problems.append(f"{label}: user and profile endpoints are not available, remove it")
        for code in ep.get("requirement_codes", []):
            if code not in known:
                problems.append(f"{label}: unknown requirement code {code}")
            covered.add(code)

    for code in sorted(known - covered):
        problems.append(f"{code} has no endpoint in the contract and is not in scaffold_provided")
    return problems


def validate_plan(
    requirements: list[dict[str, Any]], contract: dict[str, Any], plan: dict[str, Any]
) -> list[str]:
    """Return a list of problems (empty means the plan is sound)."""
    problems: list[str] = []
    known = {r["code"] for r in requirements}
    covered: set[str] = set(contract.get("scaffold_provided", []))

    owners: dict[str, str] = {}
    for t in plan.get("tasks", []):
        title = t.get("title", "?")
        if not t.get("files"):
            problems.append(f"task '{title}': no files")
        if not t.get("requirement_codes"):
            problems.append(f"task '{title}': no requirement codes; merge it into the task that needs it or remove it")
        for code in t.get("requirement_codes", []):
            if code not in known:
                problems.append(f"task '{title}': unknown requirement code {code}")
            covered.add(code)
        for f in t.get("files", []):
            if not any(re.fullmatch(p, f) for p in FILE_PATTERNS):
                problems.append(f"task '{title}': file {f} is not an allowed path")
            if f.startswith("app/routers/auth"):
                problems.append(f"task '{title}': routers named auth* are reserved for the scaffold")
            if re.fullmatch(r"app/migrations/\d{3}_users?(_.*)?\.sql", f):
                problems.append(f"task '{title}': the users table belongs to the scaffold, do not migrate it")
            if f in owners and owners[f] != title:
                problems.append(f"file {f} is assigned to two tasks ('{owners[f]}' and '{title}')")
            owners.setdefault(f, title)

    for code in sorted(known - covered):
        problems.append(f"{code} is not covered by any task")
    return problems


def plan_project(conn: sqlite3.Connection, llm: LLMClient, project_id: int) -> dict[str, Any]:
    """Design the contract, plan the tasks, validate and repair, store on the board, log events."""
    reqs = db.list_requirements(conn, project_id)
    if not reqs:
        raise ValueError("Project has no requirements yet. Run the Main AI intake first.")

    db.log_event(conn, project_id, "status", "tl", {"message": "Designing the API contract."})
    contract = design_contract(llm, reqs)
    contract_problems = validate_contract(reqs, contract)
    if contract_problems:
        db.log_event(
            conn,
            project_id,
            "status",
            "tl",
            {"message": f"Contract had {len(contract_problems)} issues, revising."},
        )
        contract = repair_contract(llm, reqs, contract, contract_problems)
        contract_problems = validate_contract(reqs, contract)

    db.log_event(conn, project_id, "status", "tl", {"message": "Breaking the work into tasks."})
    plan = break_into_tasks(llm, reqs, contract)
    plan_problems = validate_plan(reqs, contract, plan)
    if plan_problems:
        db.log_event(
            conn, project_id, "status", "tl", {"message": f"Plan had {len(plan_problems)} issues, revising."}
        )
        plan = repair_plan(llm, reqs, contract, plan, plan_problems)
        plan_problems = validate_plan(reqs, contract, plan)

    problems = contract_problems + plan_problems
    db.save_artifact(conn, project_id, "contract", contract)
    db.save_artifact(conn, project_id, "plan", plan)
    db.add_tasks(conn, project_id, plan["tasks"])
    db.set_project_status(conn, project_id, "ready_to_build")
    db.log_event(
        conn,
        project_id,
        "standup",
        "tl",
        {
            "message": f"Plan ready: {len(plan['tasks'])} tasks, {len(contract['endpoints'])} endpoints, "
            f"{len(contract['scaffold_provided'])} requirements provided by the scaffold.",
            "problems": problems,
        },
    )
    return {"contract": contract, "plan": plan, "problems": problems}