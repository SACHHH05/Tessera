"""Try the Main AI on a real idea (uses your Token Factory key, and Tavily if set).

    python scripts/try_intake.py "an invoicing app for freelance designers"
    python scripts/try_intake.py "a booking app for a small clinic" --ask
    python scripts/try_intake.py "inventory tracker for a bakery" --answers "3 staff, track ingredients"
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tessera import db  # noqa: E402
from tessera.agents import main_ai  # noqa: E402
from tessera.config import Settings  # noqa: E402
from tessera.llm import LLMClient, LLMResult  # noqa: E402
from tessera.tavily import TavilyClient  # noqa: E402


def show_call(r: LLMResult) -> None:
    print(f"  [{r.role}] {r.model}: {r.latency_s:.1f}s, {r.total_tokens} tokens")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("idea")
    ap.add_argument("--ask", action="store_true", help="ask clarifying questions interactively first")
    ap.add_argument("--answers", default="", help="client answers, as plain text")
    args = ap.parse_args()

    s = Settings.from_env()
    llm = LLMClient(s, on_call=show_call)
    tavily = TavilyClient(s.tavily_api_key) if s.tavily_api_key else None
    print("Tavily:", "on" if tavily else "off (no TAVILY_API_KEY)")

    answers = args.answers
    if args.ask:
        qs = main_ai.clarifying_questions(llm, args.idea)
        pairs = []
        for q in qs:
            a = input(f"\nMain AI: {q}\n> ")
            pairs.append(f"Q: {q}\nA: {a}")
        answers = "\n".join(pairs)

    conn = db.connect(":memory:")
    pid = db.create_project(conn, args.idea)
    out = main_ai.run_intake(conn, llm, tavily, pid, answers)

    print(f"\nSources used: {len(out['sources'])}")
    for src in out["sources"][:5]:
        print("  -", src.get("title"), src.get("url"))
    print("\nRequirements:")
    for r in db.list_requirements(conn, pid):
        print(f"\n{r['code']}  {r['title']}\n   accept: {r['acceptance']}")
    print("\nAssumptions:")
    for a in out["assumptions"]:
        print("  -", a)
    print("\nUsage by role:", {k: dict(v) for k, v in llm.tracker.by_role.items()})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())