"""Recorded runs: a finished project saved as one JSON file the dashboard can play back.

A replay is just the project's event log (the same events the live view reads) plus the files the
agents wrote, so watching a replay shows exactly what a live run showed.
"""

from __future__ import annotations

import json
import re
import sqlite3
from pathlib import Path
from typing import Any

from . import db
from .sandbox import Workspace

NAME = re.compile(r"^[a-z0-9][a-z0-9_-]{0,40}$")


def export_replay(conn: sqlite3.Connection, project_id: int, workspace_root: Path, name: str, title: str = "") -> dict[str, Any]:
    project = db.get_project(conn, project_id)
    if not project:
        raise ValueError(f"project {project_id} not found")
    if not NAME.match(name):
        raise ValueError("name must be lowercase letters, digits, - or _")
    events = db.events_since(conn, project_id, 0)
    t0 = events[0]["ts"] if events else 0
    for e in events:
        e["ts"] = round(e["ts"] - t0, 2)  # seconds since the start, so a replay has no wall-clock dates
        e.pop("project_id", None)
    summary = db.verified_summary(conn, project_id)
    return {
        "meta": {
            "name": name,
            "title": title or project["idea"],
            "idea": project["idea"],
            "summary": summary,
            "duration_s": events[-1]["ts"] if events else 0,
        },
        "events": events,
        "files": Workspace(workspace_root).generated_files(),
    }


def save_replay(replay: dict[str, Any], directory: Path) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{replay['meta']['name']}.json"
    path.write_text(json.dumps(replay, indent=1))
    return path


def list_replays(directory: Path) -> list[dict[str, Any]]:
    out = []
    for p in sorted(directory.glob("*.json")) if directory.exists() else []:
        try:
            out.append(json.loads(p.read_text())["meta"])
        except (ValueError, KeyError):
            continue
    return out


def load_replay(directory: Path, name: str) -> dict[str, Any] | None:
    if not NAME.match(name):
        return None
    path = directory / f"{name}.json"
    return json.loads(path.read_text()) if path.exists() else None