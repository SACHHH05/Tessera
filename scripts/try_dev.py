"""Try the Developer AI on a project whose plan is already in dev.db (made by try_tl.py).

    python scripts/try_dev.py --db dev.db --project 1

Builds every task into workspaces/project-<id>/ and then runs that app's own tests (the scaffold's
auth tests), so you can see whether what the Developer wrote at least starts and does not break login.
Tests for the new features come from the Tester AI later.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tessera import db  # noqa: E402
from tessera.agents import dev_ai  # noqa: E402
from tessera.config import Settings  # noqa: E402
from tessera.llm import LLMClient, LLMResult  # noqa: E402
from tessera.sandbox import LocalRunner, Workspace  # noqa: E402


def show_call(r: LLMResult) -> None:
    print(f"  [{r.role}] {r.model}: {r.latency_s:.1f}s, {r.total_tokens} tokens")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default="dev.db")
    ap.add_argument("--project", type=int, default=1)
    ap.add_argument("--workspaces", default="workspaces")
    args = ap.parse_args()

    conn = db.connect(args.db)
    llm = LLMClient(Settings.from_env(), on_call=show_call)
    ws = Workspace.create(Path(args.workspaces), f"project-{args.project}")

    print("Developer AI: building...")
    out = dev_ai.build_all(conn, llm, ws, args.project)

    print()
    for r in out["results"]:
        mark = "OK  " if r["ok"] else "FAIL"
        print(f"{mark} {r['task']}  (attempts: {r['attempts']})")
        for f in r["files"]:
            print(f"       wrote {f}")
        for p in r["problems"]:
            print(f"       problem: {p.splitlines()[0]}")

    result = LocalRunner().run_pytest(ws.root)
    print("\nApp's own tests:", "pass" if result.ok else "FAIL", f"({len(result.tests)} tests)")
    print("Usage by role:", {k: dict(v) for k, v in llm.tracker.by_role.items()})
    print(f"Code is in {ws.root}")
    return 0 if out["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())