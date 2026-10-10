"""TL AI tests with a scripted fake model (no network, no keys)."""

import json
from types import SimpleNamespace

import pytest

from tessera import db
from tessera.agents import tl_ai
from tessera.config import Settings
from tessera.llm import LLMClient

ENDPOINT = {
    "method": "POST",
    "path": "/api/invoices",
    "summary": "create an invoice",
    "request_body": "client_name: str",
    "success_status": 201,
    "error_cases": ["422: missing client_name"],
    "auth_required": True,
    "requirement_codes": ["REQ-003", "REQ-004"],
}
CONTRACT = {
    "scaffold_provided": ["REQ-001", "REQ-002"],
    "entities": [{"name": "invoice", "fields": [{"name": "total", "type": "number", "notes": ""}]}],
    "endpoints": [ENDPOINT],
}
# What the real Super model returned on the first live run: one invented auth endpoint, no entities.
HOLLOW_CONTRACT = {
    "scaffold_provided": ["REQ-002"],
    "entities": [],
    "endpoints": [
        {**ENDPOINT, "method": "POST", "path": "/api/auth/register", "requirement_codes": ["REQ-001"]}
    ],
}
GOOD_PLAN = {
    "tasks": [
        {
            "title": "Invoices",
            "description": "Build POST /api/invoices",
            "requirement_codes": ["REQ-003", "REQ-004"],
            "files": ["app/migrations/001_invoices.sql", "app/routers/invoices.py"],
        }
    ]
}
INCOMPLETE_PLAN = {"tasks": [{**GOOD_PLAN["tasks"][0], "requirement_codes": ["REQ-003"]}]}


def settings() -> Settings:
    return Settings("k", "http://x/v1/", "u", "s", "n", "", ":memory:", 3, 100000)


class ScriptedOpenAI:
    def __init__(self, *replies):
        self.replies = [r if isinstance(r, str) else json.dumps(r) for r in replies]
        self.calls = []
        outer = self

        class Completions:
            def create(self, **kwargs):
                outer.calls.append(kwargs)
                msg = SimpleNamespace(content=outer.replies.pop(0))
                usage = SimpleNamespace(prompt_tokens=10, completion_tokens=10)
                return SimpleNamespace(choices=[SimpleNamespace(message=msg)], usage=usage)

        self.chat = SimpleNamespace(completions=Completions())


def seeded():
    conn = db.connect(":memory:")
    pid = db.create_project(conn, "invoicing app")
    db.add_requirements(
        conn,
        pid,
        [{"title": t, "acceptance": "a"} for t in ("Sign up", "Login", "Create invoice", "Isolation")],
    )
    return conn, pid


def reqs(conn, pid):
    return db.list_requirements(conn, pid)


def test_plan_project_stores_everything():
    conn, pid = seeded()
    llm = LLMClient(settings(), client=ScriptedOpenAI(CONTRACT, GOOD_PLAN))
    out = tl_ai.plan_project(conn, llm, pid)
    assert out["problems"] == []
    assert db.get_artifact(conn, pid, "contract")["endpoints"][0]["path"] == "/api/invoices"
    assert [t["requirement_code"] for t in db.list_tasks(conn, pid)] == ["REQ-003,REQ-004"]
    assert db.get_project(conn, pid)["status"] == "ready_to_build"
    last = db.events_since(conn, pid)[-1]
    assert last["kind"] == "standup" and "Plan ready" in last["payload"]["message"]
    assert all(c["model"] == "s" for c in llm._client.calls)  # TL runs on the Super tier


def test_hollow_contract_is_caught():
    conn, pid = seeded()
    text = "\n".join(tl_ai.validate_contract(reqs(conn, pid), HOLLOW_CONTRACT))
    assert "defines no entities" in text
    assert "/api/auth/ namespace belongs to the scaffold" in text
    assert "REQ-003 has no endpoint" in text and "REQ-004 has no endpoint" in text


def test_hollow_contract_is_repaired_before_planning():
    conn, pid = seeded()
    fake = ScriptedOpenAI(HOLLOW_CONTRACT, CONTRACT, GOOD_PLAN)
    out = tl_ai.plan_project(conn, LLMClient(settings(), client=fake), pid)
    assert out["problems"] == []
    assert len(fake.calls) == 3
    assert "REQ-004 has no endpoint" in fake.calls[1]["messages"][1]["content"]
    assert db.get_artifact(conn, pid, "contract")["entities"]  # the repaired one was stored


def test_contract_validation_flags_bad_paths_methods_and_unknown_codes():
    conn, pid = seeded()
    contract = {
        **CONTRACT,
        "scaffold_provided": ["REQ-001", "REQ-002", "REQ-099"],
        "endpoints": [
            {**ENDPOINT, "path": "/invoices", "method": "FETCH", "requirement_codes": ["REQ-003", "REQ-004", "REQ-077"]}
        ],
    }
    text = "\n".join(tl_ai.validate_contract(reqs(conn, pid), contract))
    assert "path must start with /api/" in text
    assert "invalid method" in text
    assert "unknown requirement code REQ-099" in text and "unknown requirement code REQ-077" in text


def test_plan_validation_flags_uncovered_bad_file_conflict_and_auth_router():
    conn, pid = seeded()
    bad = {
        "tasks": [
            {"title": "A", "description": "", "requirement_codes": ["REQ-003"], "files": ["app/main.py"]},
            {"title": "B", "description": "", "requirement_codes": ["REQ-009"], "files": ["app/routers/x.py"]},
            {"title": "C", "description": "", "requirement_codes": ["REQ-003"], "files": ["app/routers/x.py"]},
            {"title": "D", "description": "", "requirement_codes": ["REQ-003"], "files": ["app/routers/auth_register.py"]},
        ]
    }
    text = "\n".join(tl_ai.validate_plan(reqs(conn, pid), CONTRACT, bad))
    assert "app/main.py is not an allowed path" in text
    assert "unknown requirement code REQ-009" in text
    assert "assigned to two tasks" in text
    assert "reserved for the scaffold" in text
    assert "REQ-004 is not covered by any task" in text


def test_incomplete_plan_is_repaired_once():
    conn, pid = seeded()
    fake = ScriptedOpenAI(CONTRACT, INCOMPLETE_PLAN, GOOD_PLAN)
    out = tl_ai.plan_project(conn, LLMClient(settings(), client=fake), pid)
    assert out["problems"] == []
    assert len(fake.calls) == 3
    assert "REQ-004 is not covered" in fake.calls[2]["messages"][1]["content"]


def test_still_broken_after_repair_is_reported_not_hidden():
    conn, pid = seeded()
    fake = ScriptedOpenAI(CONTRACT, INCOMPLETE_PLAN, INCOMPLETE_PLAN)
    out = tl_ai.plan_project(conn, LLMClient(settings(), client=fake), pid)
    assert any("REQ-004" in p for p in out["problems"])
    assert db.events_since(conn, pid)[-1]["payload"]["problems"] == out["problems"]


def test_users_table_and_profile_endpoints_are_rejected():
    conn, pid = seeded()
    contract = {
        **CONTRACT,
        "entities": CONTRACT["entities"] + [{"name": "users", "fields": []}],
        "endpoints": [ENDPOINT, {**ENDPOINT, "method": "GET", "path": "/api/me"}],
    }
    text = "\n".join(tl_ai.validate_contract(reqs(conn, pid), contract))
    assert "users table belongs to the scaffold" in text
    assert "user and profile endpoints are not available" in text
    plan = {
        "tasks": [
            {
                "title": "U",
                "description": "",
                "requirement_codes": ["REQ-003", "REQ-004"],
                "files": ["app/migrations/001_users_fullname.sql"],
            }
        ]
    }
    assert "do not migrate it" in "\n".join(tl_ai.validate_plan(reqs(conn, pid), CONTRACT, plan))


def test_empty_entity_and_task_without_requirements_are_rejected():
    conn, pid = seeded()
    contract = {**CONTRACT, "entities": CONTRACT["entities"] + [{"name": "reports", "fields": []}]}
    assert "entity 'reports' has no fields" in "\n".join(tl_ai.validate_contract(reqs(conn, pid), contract))
    plan = {"tasks": [*GOOD_PLAN["tasks"], {"title": "R", "description": "", "requirement_codes": [], "files": ["app/migrations/003_reports.sql"]}]}
    assert "task 'R': no requirement codes" in "\n".join(tl_ai.validate_plan(reqs(conn, pid), CONTRACT, plan))


def test_plan_requires_requirements():
    conn = db.connect(":memory:")
    pid = db.create_project(conn, "x")
    with pytest.raises(ValueError, match="no requirements"):
        tl_ai.plan_project(conn, LLMClient(settings(), client=ScriptedOpenAI()), pid)


def test_chat_json_retries_once_on_invalid_json():
    fake = ScriptedOpenAI("not json at all", {"a": 1})
    llm = LLMClient(settings(), client=fake)
    out = llm.chat_json("tl", [{"role": "user", "content": "x"}], {"type": "object"}, "t")
    assert out == {"a": 1}
    assert len(fake.calls) == 2
    assert "not valid JSON" in fake.calls[1]["messages"][-1]["content"]