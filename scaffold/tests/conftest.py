"""Shared fixtures. `client` is a TestClient on a fresh database; `login` signs a user up and in.

When a test fails, the last HTTP response (status and body) is added to the failure report, so
everyone reading it can see what the app actually answered.
"""

import pytest
from fastapi.testclient import TestClient


class RecordingClient(TestClient):
    last: str | None = None

    def request(self, method, url, **kwargs):
        response = super().request(method, url, **kwargs)
        self.last = f"{method} {url} -> {response.status_code} {response.text[:300]}"
        return response


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_makereport(item, call):
    outcome = yield
    report = outcome.get_result()
    client = getattr(item, "funcargs", {}).get("client")
    if report.when == "call" and report.failed and getattr(client, "last", None):
        report.longrepr = f"{report.longrepr}\n\nLAST RESPONSE: {client.last}"


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("TESSERA_DB", str(tmp_path / "test.db"))
    from app.main import create_app

    with RecordingClient(create_app()) as c:
        yield c


@pytest.fixture
def login(client):
    """login(email) -> Authorization headers for that user (signs up on first use)."""

    def _login(email: str = "user@example.com", password: str = "password123") -> dict:
        client.post("/api/auth/signup", json={"email": email, "password": password})
        token = client.post("/api/auth/login", json={"email": email, "password": password}).json()["access_token"]
        return {"Authorization": f"Bearer {token}"}

    return _login