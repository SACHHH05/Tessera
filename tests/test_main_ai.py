"""Main AI tests with scripted fake model and fake Tavily (no network, no keys)."""

import json
from types import SimpleNamespace

import httpx
import pytest
from openai import BadRequestError

from tessera import db
from tessera.agents import main_ai
from tessera.config import Settings
from tessera.llm import LLMClient

REQS = {
    "requirements": [
        {"title": "Sign up", "acceptance": "POST /api/signup returns 201"},
        {"title": "Create invoice", "acceptance": "POST /api/invoices returns 201 and appears in GET"},
    ],
    "assumptions": ["Single currency"],
}


def settings() -> Settings:
    return Settings("k", "http://x/v1/", "u", "s", "n", "", ":memory:", 3, 100000)


class ScriptedOpenAI:
    """Returns queued replies; an Exception in the queue is raised instead."""

    def __init__(self, *replies):
        self.replies = list(replies)
        self.calls = []
        outer = self

        class Completions:
            def create(self, **kwargs):
                outer.calls.append(kwargs)
                item = outer.replies.pop(0)
                if isinstance(item, Exception):
                    raise item
                msg = SimpleNamespace(content=item)
                usage = SimpleNamespace(prompt_tokens=10, completion_tokens=10)
                return SimpleNamespace(choices=[SimpleNamespace(message=msg)], usage=usage)

        self.chat = SimpleNamespace(completions=Completions())


class FakeTavily:
    api_key = "tvly-x"

    def search(self, query, max_results=5):
        return [{"title": "Rival", "url": "https://rival.example", "content": "recurring invoices"}]


def bad_request():
    req = httpx.Request("POST", "http://x/v1/chat/completions")
    resp = httpx.Response(400, request=req, json={"error": "response_format not supported"})
    return BadRequestError("response_format not supported", response=resp, body=None)


def test_run_intake_stores_requirements_and_logs_events():
    conn = db.connect(":memory:")
    pid = db.create_project(conn, "invoicing app")
    llm = LLMClient(settings(), client=ScriptedOpenAI(json.dumps(REQS)))
    out = main_ai.run_intake(conn, llm, FakeTavily(), pid)
    reqs = db.list_requirements(conn, pid)
    assert [r["code"] for r in reqs] == ["REQ-001", "REQ-002"]
    assert db.get_project(conn, pid)["status"] == "planned"
    kinds = [e["kind"] for e in db.events_since(conn, pid)]
    assert kinds == ["status", "research", "status", "requirements"]
    assert out["sources"][0]["url"] == "https://rival.example"


def test_research_is_passed_to_the_model():
    fake = ScriptedOpenAI(json.dumps(REQS))
    llm = LLMClient(settings(), client=fake)
    main_ai.draft_requirements(llm, "invoicing app", main_ai.research_market(FakeTavily(), "x"))
    assert "Rival" in fake.calls[0]["messages"][1]["content"]


def test_falls_back_to_prompt_only_json_when_schema_rejected():
    fake = ScriptedOpenAI(bad_request(), "```json\n" + json.dumps(REQS) + "\n```")
    llm = LLMClient(settings(), client=fake)
    out = main_ai.draft_requirements(llm, "invoicing app", [])
    assert len(out["requirements"]) == 2
    assert "response_format" in fake.calls[0] and "response_format" not in fake.calls[1]


def test_works_without_tavily():
    assert main_ai.research_market(None, "x") == []


def test_empty_reply_gives_actionable_error():
    llm = LLMClient(settings(), client=ScriptedOpenAI(""))
    with pytest.raises(RuntimeError, match="max_tokens"):
        main_ai.draft_requirements(llm, "x", [])


def test_clarifying_questions_capped_at_three():
    qs = json.dumps({"questions": ["a?", "b?", "c?", "d?"]})
    llm = LLMClient(settings(), client=ScriptedOpenAI(qs))
    assert main_ai.clarifying_questions(llm, "x") == ["a?", "b?", "c?"]