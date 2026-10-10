"""FastAPI orchestrator. Agents plug in later; this is the API + live event stream."""

from __future__ import annotations

import asyncio
import json

from fastapi import FastAPI, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from . import db
from .config import Settings

settings = Settings.from_env()
conn = db.connect(settings.db_path)
app = FastAPI(title="Tessera", version="0.1.0")


class NewProject(BaseModel):
    idea: str


@app.get("/health")
def health() -> dict[str, object]:
    return {
        "ok": True,
        "token_factory_key_set": bool(settings.nebius_api_key),
        "models_configured": all([settings.model_ultra, settings.model_super, settings.model_nano]),
        "tavily_key_set": bool(settings.tavily_api_key),
    }


@app.post("/api/projects")
def create_project(body: NewProject) -> dict[str, int]:
    if not body.idea.strip():
        raise HTTPException(400, "idea must not be empty")
    pid = db.create_project(conn, body.idea.strip())
    db.log_event(conn, pid, "status", "main", {"message": "Project created. Intake starting."})
    return {"project_id": pid}


@app.get("/api/projects/{project_id}")
def get_project(project_id: int) -> dict[str, object]:
    project = db.get_project(conn, project_id)
    if not project:
        raise HTTPException(404, "project not found")
    return {
        "project": project,
        "requirements": db.list_requirements(conn, project_id),
        "summary": db.verified_summary(conn, project_id),
    }


@app.get("/api/projects/{project_id}/events")
async def stream_events(project_id: int) -> StreamingResponse:
    if not db.get_project(conn, project_id):
        raise HTTPException(404, "project not found")

    async def gen():
        last = 0
        while True:
            for ev in db.events_since(conn, project_id, last):
                last = ev["id"]
                yield f"data: {json.dumps(ev)}\n\n"
            await asyncio.sleep(0.5)

    return StreamingResponse(gen(), media_type="text/event-stream")
