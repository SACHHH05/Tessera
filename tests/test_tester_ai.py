"""Tester AI tests: scripted fake model, real workspace and real pytest run."""

from types import SimpleNamespace

from tessera import db
from tessera.agents import tester_ai
from tessera.config import Settings
from tessera.llm import LLMClient
from tessera.sandbox import LocalRunner, Workspace

ROUTER = '''\
import sqlite3

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app.auth import get_current_user
from app.db import get_conn

router = APIRouter(prefix="/api/notes")


class NoteIn(BaseModel):
    title: str = Field(min_length=1, max_length=100)


@router.post("", status_code=201)
def create_note(body: NoteIn, user: dict = Depends(get_current_user), conn: sqlite3.Connection = Depends(get_conn)) -> dict:
    cur = conn.execute("INSERT INTO notes (user_id, title) VALUES (?, ?)", (user["id"], body.title))
    conn.commit()
    return {"id": cur.lastrowid, "title": body.title}


@router.get("/{note_id}")
def get_note(note_id: int, user: dict = Depends(get_current_user), conn: sqlite3.Connection = Depends(get_conn)) -> dict:
    row = conn.execute("SELECT id, title FROM notes WHERE id = ? AND user_id = ?", (note_id, user["id"])).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="not found")
    return dict(row)
'''
MIGRATION = "CREATE TABLE IF NOT EXISTS notes (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL REFERENCES users(id), title TEXT NOT NULL);\n"

EP = {"summary": "", "request_body": "", "error_cases": [], "auth_required": True}
CONTRACT = {
    "scaffold_provided": ["REQ-001"],
    "entities": [{"name": "notes", "fields": [{"name": "title", "type": "string", "notes": ""}]}],
    "endpoints": [
        {**EP, "method": "POST", "path": "/api/notes", "request_body": "title: str 1-100", "success_status": 201,
         "requirement_codes": ["REQ-002"]},
        {**EP, "method": "GET", "path": "/api/notes/{id}", "success_status": 200, "requirement_codes": ["REQ-003"]},
    ],
}
GOOD_002 = '''\
def test_create_note(client, login):
    r = client.post("/api/notes", json={"title": "hello"}, headers=login())
    assert r.status_code == 201 and r.json()["title"] == "hello"


def test_create_requires_token_and_valid_title(client, login):
    assert client.post("/api/notes", json={"title": "x"}).status_code == 401
    assert client.post("/api/notes", json={"title": ""}, headers=login()).status_code == 422
'''
GOOD_003 = '''\
def test_other_users_note_is_404(client, login):
    nid = client.post("/api/notes", json={"title": "mine"}, headers=login("a@example.com")).json()["id"]
    assert client.get(f"/api/notes/{nid}", headers=login("a@example.com")).status_code == 200
    assert client.get(f"/api/notes/{nid}", headers=login("b@example.com")).status_code == 404
'''
WRONG_003 = '''\
def test_other_users_note_is_forbidden(client, login):
    nid = client.post("/api/notes", json={"title": "mine"}, headers=login("a@example.com")).json()["id"]
    assert client.get(f"/api/notes/{nid}", headers=login("b@example.com")).status_code == 403
'''
GOOD_001 = '''\
def test_signup_and_login(client):
    body = {"email": "z@example.com", "password": "password123"}
    assert client.post("/api/auth/signup", json=body).status_code == 201
    assert client.post("/api/auth/login", json=body).json()["token_type"] == "bearer"
'''


def block(path, content):
    return f"=== FILE: {path} ===\n{content}=== END FILE ===\n"


class Scripted:
    def __init__(self, replies):
        self.replies, self.calls = dict(replies), []
        outer = self

        class C:
            def create(self, **kw):
                outer.calls.append(kw)
                user = kw["messages"][1]["content"]
                key = next(k for k in outer.replies if f"REQUIREMENT {k}" in user)
                queue = outer.replies[key]
                msg = SimpleNamespace(content=queue.pop(0) if len(queue) > 1 else queue[0])
                return SimpleNamespace(
                    choices=[SimpleNamespace(message=msg, finish_reason="stop")],
                    usage=SimpleNamespace(prompt_tokens=5, completion_tokens=5),
                )

        self.chat = SimpleNamespace(completions=C())


def llm_for(replies):
    fake = Scripted(replies)
    s = Settings("k", "http://x/v1/", "u", "s", "n", "", ":memory:", 3, 100000)
    return LLMClient(s, client=fake), fake


def project(tmp_path):
    conn = db.connect(":memory:")
    pid = db.create_project(conn, "notes")
    db.add_requirements(conn, pid, [
        {"title": "Auth", "acceptance": "users can sign up and log in"},
        {"title": "Create note", "acceptance": "a logged in user can create a note with a title"},
        {"title": "Isolation", "acceptance": "users never see each other's notes"},
    ])
    db.save_artifact(conn, pid, "contract", CONTRACT)
    ws = Workspace.create(tmp_path, "p")
    ws.write_files({"app/migrations/001_notes.sql": MIGRATION, "app/routers/notes.py": ROUTER})
    return conn, pid, ws


def test_prompt_has_acceptance_and_contract_but_no_implementation(tmp_path):
    conn, pid, ws = project(tmp_path)
    req = db.list_requirements(conn, pid)[1]
    prompt = tester_ai.build_prompt(req, CONTRACT)
    assert "a logged in user can create a note" in prompt and "POST" in prompt and "tests/test_req_002.py" in prompt
    assert "def create_note" not in prompt and "INSERT INTO notes" not in prompt  # never sees the Developer's code
    assert "app.routers" not in prompt


def test_verify_project_grades_each_requirement_from_a_real_run(tmp_path):
    conn, pid, ws = project(tmp_path)
    model, _ = llm_for({
        "REQ-001": [block("tests/test_req_001.py", GOOD_001)],
        "REQ-002": [block("tests/test_req_002.py", GOOD_002)],
        "REQ-003": [block("tests/test_req_003.py", WRONG_003)],  # the tester guessed 403; the app answers 404
    })
    out = tester_ai.verify_project(conn, model, ws, LocalRunner(), pid)
    assert {c: v["status"] for c, v in out["verdicts"].items()} == {
        "REQ-001": "passing", "REQ-002": "passing", "REQ-003": "failing",
    }
    assert "403" in out["verdicts"]["REQ-003"]["detail"] or "assert" in out["verdicts"]["REQ-003"]["detail"]
    assert out["summary"] == {"total": 3, "passing": 2, "failing": 1}
    assert db.get_project(conn, pid)["status"] == "testing"
    assert db.events_since(conn, pid)[-1]["payload"]["message"] == "2 of 3 requirements verified passing."
    assert "tests/test_req_002.py" in db.get_artifact(conn, pid, "tests")


def test_correct_tests_all_pass(tmp_path):
    conn, pid, ws = project(tmp_path)
    model, _ = llm_for({
        "REQ-001": [block("tests/test_req_001.py", GOOD_001)],
        "REQ-002": [block("tests/test_req_002.py", GOOD_002)],
        "REQ-003": [block("tests/test_req_003.py", GOOD_003)],
    })
    out = tester_ai.verify_project(conn, model, ws, LocalRunner(), pid)
    assert out["summary"] == {"total": 3, "passing": 3, "failing": 0}


def test_test_that_imports_the_app_is_rejected_and_repaired():
    cheat = "from app.db import connect\n\ndef test_x(client):\n    assert connect()\n"
    req = {"code": "REQ-002", "title": "t", "acceptance": "a"}
    model, fake = llm_for({"REQ-002": [block("tests/test_req_002.py", cheat), block("tests/test_req_002.py", GOOD_002)]})
    out = tester_ai.write_test(model, req, CONTRACT)
    assert out["problems"] == [] and "from app" not in out["content"]
    assert "do not import from `app`" in fake.calls[1]["messages"][-1]["content"]


def test_unusable_tests_leave_the_requirement_untested(tmp_path):
    conn, pid, ws = project(tmp_path)
    junk = block("tests/test_req_003.py", "def helper():\n    return 1\n")
    model, _ = llm_for({
        "REQ-001": [block("tests/test_req_001.py", GOOD_001)],
        "REQ-002": [block("tests/test_req_002.py", GOOD_002)],
        "REQ-003": [junk],
    })
    out = tester_ai.verify_project(conn, model, ws, LocalRunner(), pid)
    assert out["verdicts"]["REQ-003"]["status"] == "untested"
    assert out["summary"]["passing"] == 2 and out["summary"]["failing"] == 0