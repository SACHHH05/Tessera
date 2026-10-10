"""Developer AI tests with a scripted fake model (no network) and a real workspace."""

import json
from types import SimpleNamespace

from tessera import db
from tessera.agents import dev_ai
from tessera.config import Settings
from tessera.llm import LLMClient
from tessera.sandbox import Workspace

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

CONTRACT = {
    "scaffold_provided": ["REQ-001"],
    "entities": [{"name": "notes", "fields": [{"name": "title", "type": "string", "notes": ""}]}],
    "endpoints": [
        {"method": "POST", "path": "/api/notes", "summary": "create", "request_body": "title", "success_status": 201,
         "error_cases": [], "auth_required": True, "requirement_codes": ["REQ-002"]},
        {"method": "GET", "path": "/api/notes/{id}", "summary": "get", "request_body": "", "success_status": 200,
         "error_cases": ["404"], "auth_required": True, "requirement_codes": ["REQ-002", "REQ-003"]},
        {"method": "GET", "path": "/api/reports/notes.csv", "summary": "csv", "request_body": "", "success_status": 200,
         "error_cases": [], "auth_required": True, "requirement_codes": ["REQ-004"]},
    ],
}
TASK = {
    "title": "Notes", "description": "Notes API", "requirement_codes": ["REQ-002", "REQ-003"],
    "files": ["app/migrations/001_notes.sql", "app/routers/notes.py"],
}


def blocks(files: dict[str, str]) -> str:
    return "".join(f"=== FILE: {p} ===\n{c}=== END FILE ===\n" for p, c in files.items())


GOOD = blocks({"app/migrations/001_notes.sql": MIGRATION, "app/routers/notes.py": ROUTER})


class Scripted:
    def __init__(self, *replies):
        self.replies, self.calls = list(replies), []
        outer = self

        class C:
            def create(self, **kw):
                outer.calls.append(kw)
                msg = SimpleNamespace(content=outer.replies.pop(0))
                return SimpleNamespace(choices=[SimpleNamespace(message=msg)],
                                       usage=SimpleNamespace(prompt_tokens=5, completion_tokens=5))

        self.chat = SimpleNamespace(completions=C())


def llm(*replies):
    fake = Scripted(*replies)
    return LLMClient(Settings("k", "http://x/v1/", "u", "s", "n", "", ":memory:", 3, 100000), client=fake), fake


REQS = [{"code": c, "title": f"t{c}", "acceptance": f"acc {c}"} for c in ("REQ-001", "REQ-002", "REQ-003", "REQ-004")]


def test_endpoints_are_assigned_by_router_name():
    eps = dev_ai.endpoints_for_task(CONTRACT, TASK)
    assert [e["path"] for e in eps] == ["/api/notes", "/api/notes/{id}"]  # the reports endpoint is another task's


def test_prompt_carries_acceptance_files_and_existing_migrations(tmp_path):
    ws = Workspace.create(tmp_path, "p")
    ws.write_files({"app/migrations/001_other.sql": "CREATE TABLE IF NOT EXISTS other (id INTEGER);"})
    prompt = dev_ai.build_prompt(TASK, CONTRACT, REQS, ws)
    assert "acc REQ-002" in prompt and "app/routers/notes.py" in prompt and "CREATE TABLE IF NOT EXISTS other" in prompt
    assert "/api/reports/notes.csv" not in prompt


def test_good_reply_is_written_and_the_app_starts(tmp_path):
    ws = Workspace.create(tmp_path, "p")
    model, fake = llm(GOOD)
    out = dev_ai.build_task(model, ws, TASK, CONTRACT, REQS)
    assert out["ok"] and out["attempts"] == 1 and len(fake.calls) == 1
    assert "def create_note" in ws.read("app/routers/notes.py")


def test_syntax_error_is_fed_back_and_fixed(tmp_path):
    ws = Workspace.create(tmp_path, "p")
    bad = blocks({"app/migrations/001_notes.sql": MIGRATION, "app/routers/notes.py": "router = (\n"})
    model, fake = llm(bad, GOOD)
    out = dev_ai.build_task(model, ws, TASK, CONTRACT, REQS)
    assert out["ok"] and out["attempts"] == 2
    assert "SyntaxError" in fake.calls[1]["messages"][-1]["content"]


def test_extra_and_missing_files_are_rejected(tmp_path):
    ws = Workspace.create(tmp_path, "p")
    sneaky = blocks({"app/main.py": "x = 1\n", "app/routers/notes.py": ROUTER})
    model, fake = llm(sneaky, GOOD)
    out = dev_ai.build_task(model, ws, TASK, CONTRACT, REQS)
    msg = fake.calls[1]["messages"][-1]["content"]
    assert out["ok"] and "app/main.py was not assigned" in msg and "001_notes.sql is missing" in msg
    assert "x = 1" not in ws.read("app/main.py")  # the scaffold's file was never touched


def test_app_that_fails_to_start_is_rolled_back_then_reported(tmp_path):
    ws = Workspace.create(tmp_path, "p")
    broken = blocks({"app/migrations/001_notes.sql": MIGRATION, "app/routers/notes.py": "import nonexistent_pkg\nrouter = 1\n"})
    model, fake = llm(broken, broken, broken)
    out = dev_ai.build_task(model, ws, TASK, CONTRACT, REQS)
    assert not out["ok"] and out["attempts"] == 3 and "nonexistent_pkg" in out["problems"][0]
    assert not ws.exists("app/routers/notes.py") and not ws.exists("app/migrations/001_notes.sql")


def test_build_all_updates_the_board(tmp_path):
    conn = db.connect(":memory:")
    pid = db.create_project(conn, "notes app")
    db.add_requirements(conn, pid, [{"title": r["title"], "acceptance": r["acceptance"]} for r in REQS])
    db.save_artifact(conn, pid, "contract", CONTRACT)
    db.save_artifact(conn, pid, "plan", {"tasks": [TASK]})
    db.add_tasks(conn, pid, [TASK])
    ws = Workspace.create(tmp_path, "p")
    model, _ = llm(GOOD)
    out = dev_ai.build_all(conn, model, ws, pid)
    assert out["ok"] and db.get_project(conn, pid)["status"] == "built"
    assert [t["status"] for t in db.list_tasks(conn, pid)] == ["done"]
    status = {r["code"]: r["status"] for r in db.list_requirements(conn, pid)}
    assert status["REQ-002"] == "built" and status["REQ-003"] == "built" and status["REQ-004"] == "pending"
    assert db.events_since(conn, pid)[-1]["kind"] == "build"