"""What the project scaffold provides and the rules agents must follow.

Shared by the TL AI (planning), the Developer AI (writing code) and the scaffold itself.
If you change a convention here, change the scaffold to match.
"""

# Files agents are allowed to create or edit. Everything else in the scaffold is fixed.
FILE_PATTERNS = [
    r"app/routers/[a-z][a-z0-9_]*\.py",
    r"app/migrations/\d{3}_[a-z][a-z0-9_]*\.sql",
]

SCAFFOLD_SPEC = """\
PROJECT SCAFFOLD (already built; agents extend it, they never replace it)
Stack: Python 3.12, FastAPI, SQLite (sqlite3 module), Pydantic v2, JSON API under /api/.

Provided by the scaffold (do NOT redefine):
- POST /api/auth/signup  body {"email": str, "password": str} -> 201 {"user_id": int}; duplicate email -> 409.
- POST /api/auth/login   body {"email": str, "password": str} -> 200 {"access_token": str, "token_type": "bearer"}; wrong credentials -> 401.
- `from app.auth import get_current_user`: FastAPI dependency. Returns a dict with keys "id" and "email".
  Requests without a valid "Authorization: Bearer <token>" header get 401 automatically.
- `from app.db import get_conn`: FastAPI dependency yielding a sqlite3 connection (row_factory=sqlite3.Row).
  Call conn.commit() after writes.
- The users table (id, email, password_hash, created_at) is created and owned by the scaffold.
  Never create or alter it in a migration; reference it with a user_id column only.
  There are no profile or user-listing endpoints (no /api/me, no /api/users).

Where new code goes (the ONLY files agents may create):
- app/routers/<feature>.py : a FastAPI APIRouter named `router` (use prefix="/api/<feature>" or full paths).
  Every module in app/routers/ is auto-registered at startup. No edits to app/main.py are needed.
- app/migrations/NNN_<name>.sql : SQLite DDL, three-digit number, run at startup in filename order.
  Use CREATE TABLE IF NOT EXISTS. Every per-user table needs a user_id column.

Rules:
- Every query on per-user data must filter by the current user's id (data isolation between users).
- Validate input with Pydantic models and return proper status codes (201 create, 204 delete, 404 not found, 409 conflict, 422 invalid).
"""