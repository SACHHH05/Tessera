"""The dashboard's API, with the scripted agency standing in for the models."""

import time

from fastapi.testclient import TestClient

from tessera import db
from tessera.config import Settings
from tessera.llm import LLMClient
from tessera.main import create_app
from tessera.replay import export_replay, save_replay
from test_orchestrator import Agency


def make(tmp_path, models=True, code=""):
    s = Settings("k", "http://x/v1/", "u" if models else "", "s" if models else "", "n" if models else "", "", str(tmp_path / "t.db"), 3, 1_000_000)
    agency = Agency()
    app = create_app(
        settings=s, llm_factory=lambda st: LLMClient(st, client=agency), workspaces=tmp_path / "ws",
        replays=tmp_path / "replays", web=tmp_path / "web", live_code=code,
    )
    return TestClient(app), s


def wait_until_finished(client, pid, timeout=120):
    end = time.time() + timeout
    while time.time() < end:
        data = client.get(f"/api/projects/{pid}/events").json()
        if data["finished"]:
            return data
        time.sleep(0.5)
    raise AssertionError("run did not finish")


def test_config_reports_roles_and_models(tmp_path):
    client, _ = make(tmp_path)
    cfg = client.get("/api/config").json()
    assert cfg["live_enabled"] and not cfg["needs_code"]
    assert cfg["roles"]["tester"] == {"tier": "super", "model": "s"}


def test_live_runs_are_refused_when_models_are_not_set_up(tmp_path):
    client, _ = make(tmp_path, models=False)
    r = client.post("/api/projects", json={"idea": "a notes app"})
    assert r.status_code == 503 and "recorded run" in r.json()["detail"]


def test_access_code_is_enforced(tmp_path):
    client, _ = make(tmp_path, code="letmein")
    assert client.post("/api/projects", json={"idea": "a notes app"}).status_code == 403
    assert client.post("/api/projects", json={"idea": "a notes app", "code": "nope"}).status_code == 403
    assert client.get("/api/config").json()["needs_code"] is True


def test_idea_must_be_reasonable(tmp_path):
    client, _ = make(tmp_path)
    assert client.post("/api/projects", json={"idea": "x"}).status_code == 422
    assert client.post("/api/projects", json={"idea": "y" * 401}).status_code == 422


def test_live_run_streams_events_and_exposes_the_built_files(tmp_path):
    client, s = make(tmp_path)
    r = client.post("/api/projects", json={"idea": "a notes app"})
    pid = r.json()["project_id"]
    again = client.post("/api/projects", json={"idea": "another"})  # only one live run at a time
    assert again.status_code == 409

    data = wait_until_finished(client, pid)
    kinds = {e["kind"] for e in data["events"]}
    assert {"phase", "requirements", "plan", "llm_call", "req", "test", "done"} <= kinds
    assert data["summary"] == {"total": 4, "passing": 4, "failing": 0}
    assert data["project"]["status"] == "verified"
    first_phase = data["events"][0]
    assert first_phase["payload"]["stage"] == "start"

    calls = [e for e in data["events"] if e["kind"] == "llm_call"]
    assert {c["actor"] for c in calls} >= {"main", "tl", "dev", "tester"} and all(c["payload"]["tokens"] for c in calls)

    later = client.get(f"/api/projects/{pid}/events", params={"after": data["events"][-3]["id"]}).json()
    assert len(later["events"]) == 2  # polling with `after` returns only what is new

    files = client.get(f"/api/projects/{pid}/files").json()
    assert "app/routers/notes.py" in files and "tests/test_req_004.py" in files
    assert not any(p.endswith("000_users.sql") for p in files)
    assert client.get("/api/projects").json()[0]["summary"]["passing"] == 4
    assert client.get("/api/projects/999/events").status_code == 404


def test_recorded_runs_can_be_exported_listed_and_played(tmp_path):
    client, s = make(tmp_path)
    pid = client.post("/api/projects", json={"idea": "a notes app"}).json()["project_id"]
    wait_until_finished(client, pid)

    conn = db.connect(s.db_path)
    rep = export_replay(conn, pid, tmp_path / "ws" / f"project-{pid}", "notes-demo", "A notes app")
    save_replay(rep, tmp_path / "replays")

    assert rep["events"][0]["ts"] == 0  # times are relative to the start
    listed = client.get("/api/replays").json()
    assert listed[0]["name"] == "notes-demo" and listed[0]["summary"]["passing"] == 4
    played = client.get("/api/replays/notes-demo").json()
    assert len(played["events"]) == len(rep["events"]) and "app/routers/notes.py" in played["files"]
    assert client.get("/api/replays/missing").status_code == 404
    assert client.get("/api/replays/..%2Fsecret").status_code == 404


def test_index_page_is_served_when_present(tmp_path):
    client, _ = make(tmp_path)
    assert client.get("/").status_code == 404
    (tmp_path / "web").mkdir()
    (tmp_path / "web" / "index.html").write_text("<h1>hi</h1>")
    assert client.get("/").text == "<h1>hi</h1>"