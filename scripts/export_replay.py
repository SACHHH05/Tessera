"""Save a finished project as a replay the dashboard can play back.

    python scripts/export_replay.py --db dev.db --project 1 --name invoicing --title "Invoicing app for freelance designers"
Writes replays/<name>.json (commit it, so judges can watch it without a live run).
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tessera import db  # noqa: E402
from tessera.replay import export_replay, save_replay  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default="dev.db")
    ap.add_argument("--project", type=int, required=True)
    ap.add_argument("--name", required=True)
    ap.add_argument("--title", default="")
    ap.add_argument("--workspaces", default="workspaces")
    ap.add_argument("--out", default="replays")
    args = ap.parse_args()

    conn = db.connect(args.db)
    replay = export_replay(conn, args.project, Path(args.workspaces) / f"project-{args.project}", args.name, args.title)
    path = save_replay(replay, Path(args.out))
    s = replay["meta"]["summary"]
    print(f"Saved {path}: {len(replay['events'])} events, {len(replay['files'])} files, "
          f"{s['passing']} of {s['total']} verified, {replay['meta']['duration_s']:.0f}s long")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())