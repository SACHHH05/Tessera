"""End to end with a scripted model: the Developer ships a data-isolation bug, the independent
tests catch it, the Developer is sent the failure, and the second version passes. Real workspace,
real app, real pytest runs; only the language model is faked."""

import json
from types import SimpleNamespace

from tessera import db
from tessera.config import Settings
from tessera.llm import LLMClient
from tessera.orchestrator import run_project
from tessera.sandbox import LocalRunner
from test_tester_ai import GOOD_001, GOOD_002, GOOD_003, MIGRATION, ROUTER, block

BUGGY_ROUTER = ROUTER.replace(
    'conn.execute("SELECT id, title FROM notes WHERE id = ? AND user_id = ?", (note_id, user["id"]))',
    'conn.execute("SELECT id, title FROM notes WHERE id = ?", (note_id,))',
)
assert BUGGY_ROUTER != ROUTER

REQUIREMENTS = {
    "requirements": [
        {"title": "Sign up", "acceptance": "signup returns 201"},
        {"title": "Login", "acceptance": "login returns a token"},
        {"title": "Create note", "acceptance": "POST /api/notes returns 201"},
        {"title": "Isolation", "acceptance": "other users get 404 for my note"},
    ],
    "assumptions": [],
}
EP = {"summary": "s", "request_body": "title: str", "error_cases": ["401: no token"], "auth_required": True}
CONTRACT = {
    "scaffold_provided": ["REQ-001", "REQ-002"],
    "entities": [{"name": "notes", "fields": [{"name": "id", "type": "int", "notes": ""}, {"name": "title", "type": "str", "notes": ""}]}],
    "endpoints": [
        {**EP, "method": "POST", "path": "/api/notes", "success_status": 201, "requirement_codes": ["REQ-003", "REQ-004"]},
        {**EP, "method": "GET", "path": "/api/notes/{id}", "success_status": 200, "requirement_codes": ["REQ-004"]},
    ],
}
PLAN = {
    "tasks": [
        {"title": "Notes", "description": "notes api", "requirement_codes": ["REQ-003", "REQ-004"],
         "files": ["app/migrations/001_notes.sql", "app/routers/notes.py"]}
    ]
}


class Agency:
    """Answers each agent the way a model would, by recognising its system prompt."""

    def __init__(self):
        self.dev_replies = [
            block("app/migrations/001_notes.sql", MIGRATION) + block("app/routers/notes.py", BUGGY_ROUTER),
            block("app/migrations/001_notes.sql", MIGRATION) + block("app/routers/notes.py", ROUTER),
        ]
        self.tests = {"REQ-001": GOOD_001, "REQ-002": GOOD_001, "REQ-003": GOOD_002, "REQ-004": GOOD_003}
        self.calls = []
        outer = self

        class C:
            def create(self, **kw):
                outer.calls.append(kw)
                system, user = kw["messages"][0]["content"], kw["messages"][1]["content"]
                if "account manager" in system:
                    text = json.dumps(REQUIREMENTS)
                elif "Design the API contract" in system:
                    text = json.dumps(CONTRACT)
                elif "Break the build into" in system:
                    text = json.dumps(PLAN)
                elif system.startswith("You are the Developer"):
                    text = outer.dev_replies.pop(0)
                elif "who wrote the test file" in system:
                    text = "VERDICT: APP_WRONG\nREASON: another user's note must be a 404 per the acceptance criteria"
                elif "independent QA engineer" in system:
                    code = next(c for c in outer.tests if f"REQUIREMENT {c}" in user)
                    text = block(f"tests/test_req_{code[-3:]}.py", outer.tests[code])
                else:
                    raise AssertionError("unrecognised prompt: " + system[:60])
                return SimpleNamespace(
                    choices=[SimpleNamespace(message=SimpleNamespace(content=text), finish_reason="stop")],
                    usage=SimpleNamespace(prompt_tokens=5, completion_tokens=5),
                )

        self.chat = SimpleNamespace(completions=C())


def test_bug_is_caught_sent_back_and_fixed(tmp_path):
    agency = Agency()
    settings = Settings("k", "http://x/v1/", "u", "s", "n", "", ":memory:", 3, 1_000_000)
    llm = LLMClient(settings, client=agency)
    conn = db.connect(":memory:")

    out = run_project(conn, llm, None, LocalRunner(), tmp_path, idea="a notes app", say=lambda _: None)

    pid = out["project_id"]
    assert out["plan_problems"] == []
    assert len(out["fix_rounds"]) == 1
    assert out["fix_rounds"][0]["failing_before"] == ["REQ-004"]
    assert out["summary"] == {"total": 4, "passing": 4, "failing": 0}
    assert db.get_project(conn, pid)["status"] == "verified"
    kinds = [e["kind"] for e in db.events_since(conn, pid)]
    assert "test_review" in kinds and "fix" in kinds and kinds[-1] == "done"
    assert "AND user_id = ?" in (tmp_path / f"project-{pid}" / "app/routers/notes.py").read_text()


def test_an_unfixable_bug_is_reported_not_hidden(tmp_path):
    agency = Agency()
    agency.dev_replies = [agency.dev_replies[0]] * 3  # the Developer never fixes it
    settings = Settings("k", "http://x/v1/", "u", "s", "n", "", ":memory:", 3, 1_000_000)
    conn = db.connect(":memory:")
    out = run_project(conn, LLMClient(settings, client=agency), None, LocalRunner(), tmp_path, idea="a notes app", say=lambda _: None)
    assert out["summary"] == {"total": 4, "passing": 3, "failing": 1}
    assert len(out["fix_rounds"]) == 2
    assert db.get_project(conn, out["project_id"])["status"] == "needs_attention"