"""Web server: the live dashboard, the API behind it, and recorded replays.

    uvicorn tessera.main:app --reload

Live runs cost model tokens, so they are limited to one at a time and can be protected with a
shared code (TESSERA_LIVE_CODE). Replays cost nothing and are always available.
"""

from __future__ import annotations

import os
import threading
from pathlib import Path
from typing import Any, Callable

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, Field

from . import db, replay
from .config import ROLE_TIER, Settings
from .llm import LLMClient
from .orchestrator import run_project
from .sandbox import LocalRunner, Runner, Workspace
from .tavily import TavilyClient

ROOT = Path(__file__).resolve().parent.parent
FINISHED = {"verified", "needs_attention", "failed"}


class NewProject(BaseModel):
    idea: str = Field(min_length=3, max_length=400)
    code: str = ""


def create_app(
    *,
    settings: Settings | None = None,
    llm_factory: Callable[[Settings], LLMClient] | None = None,
    runner: Runner | None = None,
    workspaces: Path | None = None,
    replays: Path | None = None,
    web: Path | None = None,
    live_code: str | None = None,
) -> FastAPI:
    settings = settings or Settings.from_env()
    llm_factory = llm_factory or (lambda s: LLMClient(s))
    runner = runner or LocalRunner()
    workspaces = workspaces or ROOT / "workspaces"
    replays = replays or ROOT / "replays"
    web = web or ROOT / "web"
    live_code = os.environ.get("TESSERA_LIVE_CODE", "") if live_code is None else live_code
    busy = threading.Lock()
    app = FastAPI(title="Tessera", version="0.2.0")

    def conn():
        return db.connect(settings.db_path)

    def run_in_background(project_id: int) -> None:
        c = conn()
        try:
            tavily = TavilyClient(settings.tavily_api_key) if settings.tavily_api_key else None
            run_project(c, llm_factory(settings), tavily, runner, workspaces, project_id=project_id, say=lambda _: None)
        except Exception as e:  # shown on the dashboard instead of dying silently
            db.log_event(c, project_id, "error", "system", {"message": f"{type(e).__name__}: {e}"[:400]})
            db.set_project_status(c, project_id, "failed")
        finally:
            c.close()
            busy.release()

    def models_ready() -> bool:
        return bool(settings.nebius_api_key and settings.model_ultra and settings.model_super and settings.model_nano)

    @app.get("/health")
    def health() -> dict[str, object]:
        return {
            "ok": True,
            "token_factory_key_set": bool(settings.nebius_api_key),
            "models_configured": models_ready(),
            "tavily_key_set": bool(settings.tavily_api_key),
        }

    @app.get("/api/config")
    def config() -> dict[str, Any]:
        models = {"ultra": settings.model_ultra, "super": settings.model_super, "nano": settings.model_nano}
        return {
            "live_enabled": models_ready(),
            "needs_code": bool(live_code),
            "roles": {role: {"tier": tier, "model": models[tier]} for role, tier in ROLE_TIER.items()},
        }

    @app.post("/api/projects")
    def create_project(body: NewProject) -> dict[str, int]:
        if not models_ready():
            raise HTTPException(503, "Live runs are not set up on this server. Watch a recorded run instead.")
        if live_code and body.code != live_code:
            raise HTTPException(403, "That access code is not right.")
        if not busy.acquire(blocking=False):
            raise HTTPException(409, "A run is already in progress. Wait for it to finish, or watch a recorded run.")
        c = conn()
        try:
            pid = db.create_project(c, body.idea.strip())
            db.log_event(c, pid, "phase", "system", {"stage": "start", "idea": body.idea.strip()})
        except Exception:
            busy.release()
            raise
        finally:
            c.close()
        threading.Thread(target=run_in_background, args=(pid,), daemon=True).start()
        return {"project_id": pid}

    @app.get("/api/projects")
    def list_projects() -> list[dict[str, Any]]:
        c = conn()
        try:
            rows = c.execute("SELECT id FROM projects ORDER BY id DESC LIMIT 20").fetchall()
            return [{**db.get_project(c, r["id"]), "summary": db.verified_summary(c, r["id"])} for r in rows]
        finally:
            c.close()

    @app.get("/api/projects/{project_id}/events")
    def events(project_id: int, after: int = Query(0, ge=0)) -> dict[str, Any]:
        c = conn()
        try:
            project = db.get_project(c, project_id)
            if not project:
                raise HTTPException(404, "No project with that id.")
            return {
                "project": project,
                "finished": project["status"] in FINISHED,
                "summary": db.verified_summary(c, project_id),
                "events": db.events_since(c, project_id, after),
            }
        finally:
            c.close()

    @app.get("/api/projects/{project_id}/files")
    def files(project_id: int) -> dict[str, str]:
        return Workspace(workspaces / f"project-{project_id}").generated_files() if (workspaces / f"project-{project_id}").exists() else {}

    @app.get("/api/replays")
    def replay_list() -> list[dict[str, Any]]:
        return replay.list_replays(replays)

    @app.get("/api/replays/{name}")
    def replay_get(name: str) -> JSONResponse:
        data = replay.load_replay(replays, name)
        if data is None:
            raise HTTPException(404, "No recorded run with that name.")
        return JSONResponse(data)

    @app.get("/")
    def index() -> FileResponse:
        page = web / "index.html"
        if not page.exists():
            raise HTTPException(404, "Dashboard page not found (web/index.html).")
        return FileResponse(page)

    return app


app = create_app()