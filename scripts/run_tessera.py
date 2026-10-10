"""Run the whole agency on an idea, or resume a project stage by stage.

    python scripts/run_tessera.py "an invoicing app for freelance designers" --db dev.db
    python scripts/run_tessera.py --db dev.db --project 1 --stages verify,fix     # reuse what exists
Stages, in order: intake, plan, build, verify, fix
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tessera import db  # noqa: E402
from tessera.config import Settings  # noqa: E402
from tessera.llm import LLMClient, LLMResult  # noqa: E402
from tessera.orchestrator import MAX_FIX_ROUNDS, STAGES, run_project  # noqa: E402
from tessera.sandbox import LocalRunner  # noqa: E402
from tessera.tavily import TavilyClient  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("idea", nargs="?", help="what the client wants built")
    ap.add_argument("--db", default="dev.db")
    ap.add_argument("--project", type=int, help="resume this project id instead of starting a new one")
    ap.add_argument("--stages", default=",".join(STAGES))
    ap.add_argument("--rounds", type=int, default=MAX_FIX_ROUNDS, help="max fix rounds")
    ap.add_argument("--workspaces", default="workspaces")
    args = ap.parse_args()
    if not args.idea and not args.project:
        ap.error("give an idea, or --project N to resume")

    s = Settings.from_env()
    calls = {"n": 0}

    def tick(r: LLMResult) -> None:
        calls["n"] += 1
        print(f"    [{r.role}] {r.latency_s:.0f}s {r.total_tokens} tok", flush=True)

    llm = LLMClient(s, on_call=tick)
    conn = db.connect(args.db)
    tavily = TavilyClient(s.tavily_api_key) if s.tavily_api_key else None
    out = run_project(
        conn, llm, tavily, LocalRunner(), Path(args.workspaces),
        idea=args.idea, project_id=args.project, stages=args.stages.split(","), rounds=args.rounds,
    )

    print("\n=== RESULT ===")
    for r in db.list_requirements(conn, out["project_id"]):
        print(f"{r['status'].upper():9} {r['code']}  {r['title']}")
        if r["status"] != "passing" and r["detail"]:
            print(f"          {r['detail'][:260]}")
    s2 = out["summary"]
    print(f"\n{s2['passing']} of {s2['total']} requirements verified passing")
    print("Fix rounds:", [(f['round'], f"{f['summary']['passing']}/{f['summary']['total']}") for f in out.get("fix_rounds", [])])
    print("Tokens by role:", {k: int(v["tokens"]) for k, v in llm.tracker.by_role.items()}, "total", llm.tracker.total_tokens)
    print(f"Project {out['project_id']} in {args.db}; the app is in {out['workspace']}")
    return 0 if s2["passing"] == s2["total"] else 1


if __name__ == "__main__":
    raise SystemExit(main())