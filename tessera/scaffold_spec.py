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

# The contract lists requests and status codes but not response bodies. Developer and Tester both
# read these conventions, so the independent tests and the code agree on response shapes.
API_CONVENTIONS = """\
RESPONSE CONVENTIONS (the Developer implements them, the Tester relies on them)
- Every record is returned as a JSON object with an integer `id` plus all of its entity fields
  (user_id is not returned). Dates are ISO strings (YYYY-MM-DD), money is a JSON number.
- POST that creates a record: 201 and the created record. GET one: 200 and the record.
- PATCH: 200 and the updated record. DELETE: 204 with an empty body.
- GET list: 200 and a plain JSON array of records (never wrapped in an object), newest id last.
  Pagination or filter query parameters only limit or filter that array.
- If a request body holds a list of child objects (for example line_items), the returned record
  holds the same list under the same name, each child with its own integer `id`.
- Errors: JSON object with a `detail` field. Tests never depend on the wording of `detail`.
- A record owned by another user is answered exactly like a missing record: 404.
- A CSV endpoint answers 200 with Content-Type text/csv: first line a header row, then one line
  per record. A .json report endpoint answers with a JSON array or object.
"""

TESTER_FIXTURES = """\
TEST FIXTURES (provided by the project, in tests/conftest.py)
- `client`: a fastapi TestClient on a fresh empty database for each test.
- `login(email="user@example.com")`: signs that user up (if new) and logs in; returns the
  headers dict {"Authorization": "Bearer ..."} to pass as `headers=`. Call it with two different
  emails to get two different users.
Scaffold-provided API: POST /api/auth/signup and POST /api/auth/login exactly as in the contract
notes (signup 201 {"user_id"}, 409 duplicate email, 422 invalid email or password shorter than 8;
login 200 {"access_token","token_type":"bearer"}, 401 wrong credentials).
"""