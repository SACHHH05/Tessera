"""Signup, login and the get_current_user dependency. Part of the scaffold: agents do not edit this.

Standard library only: PBKDF2 password hashing and HMAC-signed bearer tokens.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import re
import secrets
import sqlite3
import time

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel, field_validator

from app.db import get_conn

TOKEN_TTL_S = 24 * 3600
_ITERATIONS = 100_000
_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")

router = APIRouter(prefix="/api/auth", tags=["auth"])


def _secret() -> bytes:
    return os.environ.get("TESSERA_SECRET", "dev-secret-change-me").encode()


def hash_password(password: str) -> str:
    salt = secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), _ITERATIONS).hex()
    return f"{salt}${digest}"


def verify_password(password: str, stored: str) -> bool:
    salt, _, digest = stored.partition("$")
    candidate = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), _ITERATIONS).hex()
    return hmac.compare_digest(candidate, digest)


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode().rstrip("=")


def _unb64(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def make_token(user_id: int, ttl_s: int = TOKEN_TTL_S) -> str:
    body = _b64(json.dumps({"sub": user_id, "exp": int(time.time()) + ttl_s}).encode())
    sig = _b64(hmac.new(_secret(), body.encode(), hashlib.sha256).digest())
    return f"{body}.{sig}"


def decode_token(token: str) -> int | None:
    """Return the user id for a valid, unexpired token, else None."""
    try:
        body, sig = token.split(".")
        expected = _b64(hmac.new(_secret(), body.encode(), hashlib.sha256).digest())
        if not hmac.compare_digest(sig, expected):
            return None
        payload = json.loads(_unb64(body))
        return int(payload["sub"]) if payload["exp"] > time.time() else None
    except (ValueError, KeyError, TypeError, json.JSONDecodeError):
        return None


class Credentials(BaseModel):
    email: str
    password: str

    @field_validator("email")
    @classmethod
    def _valid_email(cls, v: str) -> str:
        v = v.strip().lower()
        if not _EMAIL.match(v):
            raise ValueError("invalid email address")
        return v

    @field_validator("password")
    @classmethod
    def _valid_password(cls, v: str) -> str:
        if len(v) < 8:
            raise ValueError("password must be at least 8 characters")
        return v


@router.post("/signup", status_code=201)
def signup(body: Credentials, conn: sqlite3.Connection = Depends(get_conn)) -> dict:
    try:
        cur = conn.execute(
            "INSERT INTO users (email, password_hash) VALUES (?, ?)",
            (body.email, hash_password(body.password)),
        )
        conn.commit()
    except sqlite3.IntegrityError:
        raise HTTPException(status_code=409, detail="email already registered")
    return {"user_id": cur.lastrowid}


@router.post("/login")
def login(body: Credentials, conn: sqlite3.Connection = Depends(get_conn)) -> dict:
    row = conn.execute("SELECT id, password_hash FROM users WHERE email = ?", (body.email,)).fetchone()
    if row is None or not verify_password(body.password, row["password_hash"]):
        raise HTTPException(status_code=401, detail="invalid email or password")
    return {"access_token": make_token(row["id"]), "token_type": "bearer"}


def get_current_user(authorization: str | None = Header(default=None), conn: sqlite3.Connection = Depends(get_conn)) -> dict:
    """FastAPI dependency. Returns {"id": int, "email": str} or answers 401."""
    scheme, _, token = (authorization or "").partition(" ")
    user_id = decode_token(token) if scheme.lower() == "bearer" else None
    row = conn.execute("SELECT id, email FROM users WHERE id = ?", (user_id,)).fetchone() if user_id else None
    if row is None:
        raise HTTPException(status_code=401, detail="not authenticated", headers={"WWW-Authenticate": "Bearer"})
    return {"id": row["id"], "email": row["email"]}
