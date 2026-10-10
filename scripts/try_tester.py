"""Try the Tester AI on a project the Developer AI has already built.

    python scripts/try_tester.py --db dev.db --project 2

The Tester never reads workspaces/project-N/app. It writes tests from the requirements and the API
contract only, then they run against the built app and each requirement gets a verdict.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tessera import db  # noqa: E402
from tessera.agents import tester_ai  # noqa: E402
from tessera.config import Settings  # noqa: E402
from tessera.llm import LLMClient, LLMResult  # noqa: E402
from tessera.sandbox import LocalRunner, Workspace  # noqa: E402


def show_call(r: LLMResult) -> None:
    print(f"  [{r.role}] {r.model}: {r.latency_s:.1f}s, {r.total_tokens} tokens")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default="dev.db")
    ap.add_argument("--project", type=int, default=2)
    ap.add_argument("--workspaces", default="workspaces")
    args = ap.parse_args()

    conn = db.connect(args.db)
    llm = LLMClient(Settings.from_env(), on_call=show_call)
    ws = Workspace(Path(args.workspaces) / f"project-{args.project}")

    print("Tester AI: writing and running tests...")
    out = tester_ai.verify_project(conn, llm, ws, LocalRunner(), args.project)

    print()
    for r in db.list_requirements(conn, args.project):
        v = out["verdicts"][r["code"]]
        print(f"{v['status'].upper():9} {r['code']}  {r['title']}")
        if v["status"] != "passing":
            print(f"          {v['detail'][:300]}")
    s = out["summary"]
    print(f"\n{s['passing']} of {s['total']} requirements verified passing")
    print("Usage by role:", {k: dict(v) for k, v in llm.tracker.by_role.items()})
    print(f"Tests are in {ws.root / 'tests'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())