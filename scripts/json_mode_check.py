"""Does wrapping code in JSON damage it? Compare three reply formats on the same coding task."""

from __future__ import annotations

import argparse
import ast
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tessera.config import Settings  # noqa: E402
from tessera.fileblocks import parse_file_blocks  # noqa: E402
from tessera.llm import LLMClient, LLMRequestError, parse_json  # noqa: E402

TASK = (
    "Write the file app/routers/notes.py for a FastAPI app. It needs an APIRouter named `router` "
    "with prefix /api/notes and these endpoints: POST (create a note: title str 1-100 chars, body "
    "optional str, returns 201), GET (list the current user's notes), GET /{note_id} (404 if "
    "missing), DELETE /{note_id} (returns 204). Use sqlite3 through "
    "`from app.db import get_conn` and `from app.auth import get_current_user`, Pydantic models, "
    "and filter every query by the current user's id. Add type hints and a short docstring to "
    "every function.\n"
    'Respond only as JSON: {"files": [{"path": "...", "content": "..."}]}'
)

BLOCK_TASK = TASK.rsplit("Respond only", 1)[0] + (
    "Respond with the file only, in exactly this format and nothing else:\n"
    "=== FILE: app/routers/notes.py ===\n<file content>\n=== END FILE ==="
)

SCHEMA = {
    "type": "object",
    "properties": {
        "files": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {"path": {"type": "string"}, "content": {"type": "string"}},
                "required": ["path", "content"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["files"],
    "additionalProperties": False,
}


def check_blocks(text: str) -> tuple[bool, str]:
    files = parse_file_blocks(text)
    if not files:
        return False, f"no file blocks; reply starts: {text[:80]!r}"
    for path, content in files.items():
        try:
            ast.parse(content)
        except SyntaxError as e:
            return False, f"{path}: SyntaxError line {e.lineno}: {e.msg}"
    return True, ""


def check(text: str) -> tuple[bool, str]:
    try:
        data = parse_json(text)
    except Exception as e:
        return False, f"invalid JSON: {e}; reply starts: {text[:80]!r}"
    files = data.get("files") if isinstance(data, dict) else None
    if not files:
        return False, "no files in reply"
    for f in files:
        try:
            ast.parse(f["content"])
        except SyntaxError as e:
            return False, f"{f.get('path')}: SyntaxError line {e.lineno}: {e.msg}"
    return True, ""


def one_call(llm: LLMClient, role: str, mode: str) -> dict:
    messages = [{"role": "user", "content": BLOCK_TASK if mode == "blocks" else TASK}]
    t0 = time.perf_counter()
    try:
        if mode == "json_schema":
            res = llm.chat(role, messages, json_schema=SCHEMA, schema_name="files", max_tokens=6000)
        else:
            res = llm.chat(role, messages, max_tokens=6000)
    except (LLMRequestError, RuntimeError) as e:
        return {"ok": False, "detail": f"call failed: {e}", "latency": 0.0, "tokens": 0}
    ok, detail = (check_blocks if mode == "blocks" else check)(res.text)
    return {"ok": ok, "detail": detail, "latency": time.perf_counter() - t0, "tokens": res.total_tokens}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=8, help="calls per mode")
    ap.add_argument("--role", default="dev")
    ap.add_argument("--workers", type=int, default=4)
    args = ap.parse_args()

    llm = LLMClient(Settings.from_env())
    print(f"model for role '{args.role}': {llm.settings.model_for(args.role)}")
    for label in ("json_schema", "prompt-only", "blocks"):
        with ThreadPoolExecutor(max_workers=args.workers) as pool:
            results = list(pool.map(lambda _: one_call(llm, args.role, label), range(args.n)))
        ok = sum(r["ok"] for r in results)
        avg_t = sum(r["latency"] for r in results) / args.n
        avg_tok = sum(r["tokens"] for r in results) / args.n
        print(f"\n{label:12} {ok}/{args.n} files parse   avg {avg_t:.1f}s   avg {avg_tok:.0f} tokens")
        for r in [r for r in results if not r["ok"]][:3]:
            print("   failure:", r["detail"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
