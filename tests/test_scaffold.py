"""The scaffold, workspace and runner work together (no network, no model)."""

import pytest

from tessera.sandbox import TEST_PATTERNS, LocalRunner, PathNotAllowed, Workspace

NOTES_ROUTER = '''\
from fastapi import APIRouter, Depends

from app.auth import get_current_user

router = APIRouter(prefix="/api/notes")


@router.get("")
def list_notes(user: dict = Depends(get_current_user)) -> list:
    return []
'''

NOTES_TEST = '''\
def test_notes_need_login_and_list_empty(client, login):
    assert client.get("/api/notes").status_code == 401
    assert client.get("/api/notes", headers=login()).json() == []
'''


def test_scaffold_passes_its_own_tests(tmp_path):
    ws = Workspace.create(tmp_path, "p1")
    result = LocalRunner().run_pytest(ws.root)
    assert result.ok, result.stdout
    assert result.tests and all(v == "passed" for v in result.tests.values())


def test_agent_router_is_registered_and_tested(tmp_path):
    ws = Workspace.create(tmp_path, "p2")
    ws.write_files({"app/routers/notes.py": NOTES_ROUTER})
    ws.write_files({"tests/test_req_001.py": NOTES_TEST}, patterns=TEST_PATTERNS)
    result = LocalRunner().run_pytest(ws.root)
    assert result.ok, result.stdout
    assert result.tests["tests/test_req_001.py::test_notes_need_login_and_list_empty"] == "passed"


def test_failing_test_is_reported_per_test(tmp_path):
    ws = Workspace.create(tmp_path, "p3")
    ws.write_files({"tests/test_req_002.py": "def test_bad():\n    assert 1 == 2\n"}, patterns=TEST_PATTERNS)
    result = LocalRunner().run_pytest(ws.root)
    assert not result.ok
    assert result.tests["tests/test_req_002.py::test_bad"] == "failed"


def test_migration_runs_at_startup(tmp_path):
    ws = Workspace.create(tmp_path, "p4")
    ws.write_files({"app/migrations/001_notes.sql": "CREATE TABLE IF NOT EXISTS notes (id INTEGER PRIMARY KEY);"})
    ws.write_files(
        {"tests/test_req_003.py": "def test_table(client):\n    from app.db import connect\n"
         "    assert connect().execute('select count(*) from notes').fetchone()[0] == 0\n"},
        patterns=TEST_PATTERNS,
    )
    assert LocalRunner().run_pytest(ws.root).ok


@pytest.mark.parametrize(
    "path",
    ["app/main.py", "app/auth.py", "app/routers/../auth.py", "../evil.py", "/etc/passwd", "app/routers/Bad.py"],
)
def test_paths_outside_the_allowed_set_are_refused(tmp_path, path):
    ws = Workspace.create(tmp_path, "p5")
    with pytest.raises(PathNotAllowed):
        ws.write_files({"app/routers/ok.py": "x = 1\n", path: "x = 1\n"})
    assert not (ws.root / "app/routers/ok.py").exists()  # nothing written when any path is bad


def test_relative_workspace_path_still_reports_per_test_results(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    ws = Workspace.create(__import__("pathlib").Path("workspaces"), "project-1")  # relative, like try_dev.py
    result = LocalRunner().run_pytest(__import__("pathlib").Path("workspaces/project-1"))
    assert result.ok and len(result.tests) >= 4


def test_failures_carry_the_last_http_response(tmp_path):
    ws = Workspace.create(tmp_path, "p6")
    ws.write_files(
        {"tests/test_req_009.py": "def test_x(client):\n    r = client.get('/api/nothing-here')\n    assert r.status_code == 200\n"},
        patterns=TEST_PATTERNS,
    )
    result = LocalRunner().run_pytest(ws.root)
    detail = result.details["tests/test_req_009.py::test_x"]
    assert "LAST RESPONSE: GET /api/nothing-here -> 404" in detail


def test_existing_workspace_receives_scaffold_updates(tmp_path):
    ws = Workspace.create(tmp_path, "p7")
    (ws.root / "tests/conftest.py").write_text("# stale\n")
    again = Workspace.create(tmp_path, "p7", fresh=False)
    assert "LAST RESPONSE" in again.read("tests/conftest.py")