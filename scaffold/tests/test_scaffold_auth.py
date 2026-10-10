"""Scaffold self-checks. These always run with the project's tests."""

from fastapi import Depends

from app.auth import get_current_user, make_token


def test_signup_then_duplicate(client):
    r = client.post("/api/auth/signup", json={"email": "a@example.com", "password": "password123"})
    assert r.status_code == 201 and isinstance(r.json()["user_id"], int)
    again = client.post("/api/auth/signup", json={"email": "A@example.com", "password": "password123"})
    assert again.status_code == 409


def test_signup_validation(client):
    assert client.post("/api/auth/signup", json={"email": "nope", "password": "password123"}).status_code == 422
    assert client.post("/api/auth/signup", json={"email": "a@example.com", "password": "short"}).status_code == 422


def test_login(client):
    client.post("/api/auth/signup", json={"email": "a@example.com", "password": "password123"})
    ok = client.post("/api/auth/login", json={"email": "a@example.com", "password": "password123"})
    assert ok.status_code == 200 and ok.json()["token_type"] == "bearer" and ok.json()["access_token"]
    assert client.post("/api/auth/login", json={"email": "a@example.com", "password": "wrongpass1"}).status_code == 401
    assert client.post("/api/auth/login", json={"email": "x@example.com", "password": "password123"}).status_code == 401


def test_get_current_user_needs_a_valid_token(client, login):
    client.app.add_api_route("/api/_who", lambda user=Depends(get_current_user): user)
    assert client.get("/api/_who").status_code == 401
    assert client.get("/api/_who", headers={"Authorization": "Bearer garbage"}).status_code == 401
    assert client.get("/api/_who", headers={"Authorization": f"Bearer {make_token(999)}"}).status_code == 401
    me = client.get("/api/_who", headers=login("a@example.com")).json()
    assert me["email"] == "a@example.com" and isinstance(me["id"], int)
