"""Shared fixtures. `client` is a TestClient on a fresh database; `login` signs a user up and in."""

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("TESSERA_DB", str(tmp_path / "test.db"))
    from app.main import create_app

    with TestClient(create_app()) as c:
        yield c


@pytest.fixture
def login(client):
    """login(email) -> Authorization headers for that user (signs up on first use)."""

    def _login(email: str = "user@example.com", password: str = "password123") -> dict:
        client.post("/api/auth/signup", json={"email": email, "password": password})
        token = client.post("/api/auth/login", json={"email": email, "password": password}).json()["access_token"]
        return {"Authorization": f"Bearer {token}"}

    return _login
