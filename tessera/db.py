"""SQLite shared state: the task board every agent reads and writes."""

from __future__ import annotations

import json
import sqlite3
import time
from typing import Any

SCHEMA = """
CREATE TABLE IF NOT EXISTS projects (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    idea TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'intake',
    created_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS requirements (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id INTEGER NOT NULL REFERENCES projects(id),
    code TEXT NOT NULL,                 -- e.g. REQ-001
    title TEXT NOT NULL,
    acceptance TEXT NOT NULL,           -- acceptance criterion the tester must prove
    status TEXT NOT NULL DEFAULT 'pending',  -- pending | building | built | passing | failing
    detail TEXT NOT NULL DEFAULT '',    -- failure reason or test output summary
    UNIQUE(project_id, code)
);
CREATE TABLE IF NOT EXISTS tasks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id INTEGER NOT NULL REFERENCES projects(id),
    requirement_code TEXT,
    owner TEXT NOT NULL,                -- agent role
    title TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'todo' -- todo | doing | done | blocked
);
CREATE TABLE IF NOT EXISTS artifacts (
    project_id INTEGER NOT NULL REFERENCES projects(id),
    kind TEXT NOT NULL,                 -- contract | plan | ...
    content TEXT NOT NULL,              -- JSON
    updated_at REAL NOT NULL,
    PRIMARY KEY (project_id, kind)
);
CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id INTEGER NOT NULL REFERENCES projects(id),
    ts REAL NOT NULL,
    kind TEXT NOT NULL,                 -- standup | llm_call | status | test | change_request ...
    actor TEXT NOT NULL,
    payload TEXT NOT NULL               -- JSON
);
"""


def connect(path: str = "tessera.db") -> sqlite3.Connection:
    conn = sqlite3.connect(path, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    return conn


def create_project(conn: sqlite3.Connection, idea: str) -> int:
    cur = conn.execute("INSERT INTO projects (idea, created_at) VALUES (?, ?)", (idea, time.time()))
    conn.commit()
    return int(cur.lastrowid)


def get_project(conn: sqlite3.Connection, project_id: int) -> dict[str, Any] | None:
    row = conn.execute("SELECT * FROM projects WHERE id = ?", (project_id,)).fetchone()
    return dict(row) if row else None


def set_project_status(conn: sqlite3.Connection, project_id: int, status: str) -> None:
    conn.execute("UPDATE projects SET status = ? WHERE id = ?", (status, project_id))
    conn.commit()


def add_requirements(conn: sqlite3.Connection, project_id: int, reqs: list[dict[str, str]]) -> None:
    start = conn.execute(
        "SELECT COUNT(*) FROM requirements WHERE project_id = ?", (project_id,)
    ).fetchone()[0]
    for i, r in enumerate(reqs, start=start + 1):
        conn.execute(
            "INSERT INTO requirements (project_id, code, title, acceptance) VALUES (?, ?, ?, ?)",
            (project_id, f"REQ-{i:03d}", r["title"], r["acceptance"]),
        )
    conn.commit()


def list_requirements(conn: sqlite3.Connection, project_id: int) -> list[dict[str, Any]]:
    rows = conn.execute(
        "SELECT * FROM requirements WHERE project_id = ? ORDER BY code", (project_id,)
    ).fetchall()
    return [dict(r) for r in rows]


def set_requirement_status(
    conn: sqlite3.Connection, project_id: int, code: str, status: str, detail: str = ""
) -> None:
    conn.execute(
        "UPDATE requirements SET status = ?, detail = ? WHERE project_id = ? AND code = ?",
        (status, detail, project_id, code),
    )
    conn.commit()


def save_artifact(conn: sqlite3.Connection, project_id: int, kind: str, content: Any) -> None:
    conn.execute(
        "INSERT INTO artifacts (project_id, kind, content, updated_at) VALUES (?, ?, ?, ?) "
        "ON CONFLICT(project_id, kind) DO UPDATE SET content = excluded.content, "
        "updated_at = excluded.updated_at",
        (project_id, kind, json.dumps(content), time.time()),
    )
    conn.commit()


def get_artifact(conn: sqlite3.Connection, project_id: int, kind: str) -> Any | None:
    row = conn.execute(
        "SELECT content FROM artifacts WHERE project_id = ? AND kind = ?", (project_id, kind)
    ).fetchone()
    return json.loads(row["content"]) if row else None


def add_tasks(conn: sqlite3.Connection, project_id: int, tasks: list[dict[str, Any]]) -> None:
    for t in tasks:
        conn.execute(
            "INSERT INTO tasks (project_id, requirement_code, owner, title) VALUES (?, ?, 'dev', ?)",
            (project_id, ",".join(t.get("requirement_codes", [])), t["title"]),
        )
    conn.commit()


def set_task_status(conn: sqlite3.Connection, task_id: int, status: str) -> None:
    conn.execute("UPDATE tasks SET status = ? WHERE id = ?", (status, task_id))
    conn.commit()


def list_tasks(conn: sqlite3.Connection, project_id: int) -> list[dict[str, Any]]:
    rows = conn.execute("SELECT * FROM tasks WHERE project_id = ? ORDER BY id", (project_id,)).fetchall()
    return [dict(r) for r in rows]


def log_event(
    conn: sqlite3.Connection, project_id: int, kind: str, actor: str, payload: dict[str, Any]
) -> int:
    cur = conn.execute(
        "INSERT INTO events (project_id, ts, kind, actor, payload) VALUES (?, ?, ?, ?, ?)",
        (project_id, time.time(), kind, actor, json.dumps(payload)),
    )
    conn.commit()
    return int(cur.lastrowid)


def events_since(conn: sqlite3.Connection, project_id: int, last_id: int = 0) -> list[dict[str, Any]]:
    rows = conn.execute(
        "SELECT * FROM events WHERE project_id = ? AND id > ? ORDER BY id", (project_id, last_id)
    ).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        d["payload"] = json.loads(d["payload"])
        out.append(d)
    return out


def verified_summary(conn: sqlite3.Connection, project_id: int) -> dict[str, int]:
    """The headline number: how many requirements are verified passing."""
    reqs = list_requirements(conn, project_id)
    return {
        "total": len(reqs),
        "passing": sum(1 for r in reqs if r["status"] == "passing"),
        "failing": sum(1 for r in reqs if r["status"] == "failing"),
    }