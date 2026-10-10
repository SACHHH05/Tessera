"""Smoke tests that need no API keys: DB, JSON parsing, budget guard, routing."""

from types import SimpleNamespace

import pytest

from tessera import db
from tessera.config import ConfigError, Settings
from tessera.llm import BudgetExceeded, LLMClient, UsageTracker, parse_json


def make_settings(**kw) -> Settings:
    base = dict(
        nebius_api_key="k",
        base_url="http://x/v1/",
        model_ultra="u",
        model_super="s",
        model_nano="n",
        tavily_api_key="",
        db_path=":memory:",
        max_rounds=3,
        max_tokens_per_project=100,
    )
    base.update(kw)
    return Settings(**base)


class FakeOpenAI:
    def __init__(self, text='{"ok": true}', tokens=(10, 5)):
        self.calls = []
        outer = self

        class Completions:
            def create(self, **kwargs):
                outer.calls.append(kwargs)
                msg = SimpleNamespace(content=text)
                usage = SimpleNamespace(prompt_tokens=tokens[0], completion_tokens=tokens[1])
                return SimpleNamespace(choices=[SimpleNamespace(message=msg)], usage=usage)

        self.chat = SimpleNamespace(completions=Completions())


def test_db_requirement_board():
    conn = db.connect(":memory:")
    pid = db.create_project(conn, "a booking app")
    db.add_requirements(
        conn, pid, [{"title": "Sign up", "acceptance": "user can register"}, {"title": "Book", "acceptance": "user can book"}]
    )
    db.set_requirement_status(conn, pid, "REQ-001", "passing")
    db.set_requirement_status(conn, pid, "REQ-002", "failing", "500 on POST /book")
    assert db.verified_summary(conn, pid) == {"total": 2, "passing": 1, "failing": 1}
    db.log_event(conn, pid, "standup", "tl", {"msg": "hi"})
    assert db.events_since(conn, pid, 0)[0]["payload"] == {"msg": "hi"}


def test_parse_json_tolerates_fences_and_prose():
    assert parse_json('```json\n{"a": 1}\n```') == {"a": 1}
    assert parse_json('Sure! Here you go: {"a": [1, 2]} hope that helps') == {"a": [1, 2]}


def test_role_routing_and_usage_tracking():
    fake = FakeOpenAI()
    llm = LLMClient(make_settings(), client=fake)
    llm.chat("main", [{"role": "user", "content": "hi"}])
    llm.chat("tester", [{"role": "user", "content": "hi"}])
    assert [c["model"] for c in fake.calls] == ["u", "n"]
    assert llm.tracker.total_tokens == 30
    assert llm.tracker.by_role["main"]["calls"] == 1


def test_budget_guard_stops_runaway_agents():
    fake = FakeOpenAI(tokens=(60, 60))
    llm = LLMClient(make_settings(max_tokens_per_project=100), client=fake)
    llm.chat("dev", [{"role": "user", "content": "hi"}])
    with pytest.raises(BudgetExceeded):
        llm.chat("dev", [{"role": "user", "content": "again"}])


def test_missing_model_gives_actionable_error():
    with pytest.raises(ConfigError, match="TESSERA_MODEL_ULTRA"):
        make_settings(model_ultra="").model_for("main")


def test_json_schema_is_sent_as_response_format():
    fake = FakeOpenAI()
    llm = LLMClient(make_settings(), client=fake)
    llm.chat("main", [{"role": "user", "content": "x"}], json_schema={"type": "object"}, schema_name="reqs")
    rf = fake.calls[0]["response_format"]
    assert rf["type"] == "json_schema" and rf["json_schema"]["name"] == "reqs"


def test_parse_file_blocks():
    from tessera.fileblocks import parse_file_blocks

    text = (
        "Here you go\n=== FILE: app/routers/a.py ===\n```python\nx = '\\n'\n```\n=== END FILE ===\n"
        "=== FILE: app/migrations/001_a.sql ===\nCREATE TABLE t(id INT);\n=== END FILE ===\n"
    )
    files = parse_file_blocks(text)
    assert files["app/routers/a.py"] == "x = '\\n'\n"
    assert files["app/migrations/001_a.sql"].startswith("CREATE TABLE")
    assert parse_file_blocks("no blocks here") == {}


def test_parse_file_blocks_tolerates_missing_end_marker():
    from tessera.fileblocks import parse_file_blocks

    text = "=== FILE: app/routers/a.py ===\nx = 1\n=== FILE: app/routers/b.py ===\ny = 2\n"
    assert parse_file_blocks(text) == {"app/routers/a.py": "x = 1\n", "app/routers/b.py": "y = 2\n"}