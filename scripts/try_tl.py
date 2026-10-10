"""Try the TL AI: runs the Main AI intake, then the TL plan, and prints both.

    python scripts/try_tl.py "an invoicing app for freelance designers"

To avoid paying for the intake again while you tune the TL, keep a file database:

    python scripts/try_tl.py "an invoicing app for freelance designers" --db dev.db
    python scripts/try_tl.py "ignored" --db dev.db --project 1     # reuse its requirements
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tessera import db  # noqa: E402
from tessera.agents import main_ai, tl_ai  # noqa: E402
from tessera.config import Settings  # noqa: E402
from tessera.llm import LLMClient, LLMResult  # noqa: E402
from tessera.tavily import TavilyClient  # noqa: E402


def show_call(r: LLMResult) -> None:
    print(f"  [{r.role}] {r.model}: {r.latency_s:.1f}s, {r.total_tokens} tokens")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("idea")
    ap.add_argument("--db", default=":memory:", help="sqlite file to keep results in")
    ap.add_argument("--project", type=int, help="reuse requirements of this project id in --db")
    ap.add_argument("--json", action="store_true", help="also print the full contract as JSON")
    args = ap.parse_args()

    s = Settings.from_env()
    llm = LLMClient(s, on_call=show_call)
    conn = db.connect(args.db)

    if args.project:
        pid = args.project
        print(f"Reusing project {pid} ({len(db.list_requirements(conn, pid))} requirements)")
    else:
        pid = db.create_project(conn, args.idea)
        tavily = TavilyClient(s.tavily_api_key) if s.tavily_api_key else None
        print("Main AI: drafting requirements...")
        main_ai.run_intake(conn, llm, tavily, pid)

    print("TL AI: planning...")
    out = tl_ai.plan_project(conn, llm, pid)

    print("\nEntities:")
    for e in out["contract"]["entities"]:
        print(f"  {e['name']}: " + ", ".join(f"{f['name']}:{f['type']}" for f in e["fields"]))

    print("\nEndpoints:")
    for ep in out["contract"]["endpoints"]:
        auth = "auth" if ep["auth_required"] else "public"
        print(f"  {ep['method']:6} {ep['path']}  -> {ep['success_status']}  [{auth}]  {ep['requirement_codes']}")
        print(f"         body: {ep['request_body']}")
        for err in ep["error_cases"]:
            print(f"         error {err}")

    print("\nTasks:")
    for i, t in enumerate(out["plan"]["tasks"], 1):
        print(f"\n  {i}. {t['title']}  {t['requirement_codes']}")
        print(f"     {t['description']}")
        for f in t["files"]:
            print(f"     file: {f}")
    print("\nProvided by scaffold:", out["contract"]["scaffold_provided"])
    if args.json:
        print("\nFull contract JSON:\n" + json.dumps(out["contract"], indent=1))

    print("\nPlan problems:", out["problems"] or "none")
    print("Usage by role:", {k: dict(v) for k, v in llm.tracker.by_role.items()})
    print(f"(project id {pid} in {args.db})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())