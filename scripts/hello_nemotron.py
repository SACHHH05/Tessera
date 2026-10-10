"""Day-1 check: can we reach Token Factory and call each Nemotron tier?

    python scripts/hello_nemotron.py --list     # show Nemotron model IDs your key can use
    python scripts/hello_nemotron.py            # one call per configured tier
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from openai import OpenAI  # noqa: E402

from tessera.config import ConfigError, Settings  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--list", action="store_true", help="list Nemotron models and exit")
    args = ap.parse_args()

    s = Settings.from_env()
    if not s.nebius_api_key:
        print("NEBIUS_API_KEY is not set. Copy .env.example to .env and fill it in.")
        return 1
    client = OpenAI(base_url=s.base_url, api_key=s.nebius_api_key)

    if args.list:
        ids = sorted(m.id for m in client.models.list().data)
        nemo = [i for i in ids if "nemotron" in i.lower() or i.lower().startswith("nvidia/")]
        print(f"{len(ids)} models available. NVIDIA / Nemotron ones:")
        for i in nemo or ["(none matched; full list below)"] + ids:
            print("  ", i)
        return 0

    status = 0
    for tier, model in [("ultra", s.model_ultra), ("super", s.model_super), ("nano", s.model_nano)]:
        if not model:
            print(f"[{tier}] TESSERA_MODEL_{tier.upper()} not set, skipping")
            status = 1
            continue
        t0 = time.perf_counter()
        try:
            r = client.chat.completions.create(
                model=model,
                messages=[{"role": "user", "content": "Reply with exactly one word: ready"}],
                max_tokens=512,
            )
        except ConfigError as e:
            print(f"[{tier}] {e}")
            status = 1
            continue
        except Exception as e:
            print(f"[{tier}] {model}: FAILED: {e}")
            status = 1
            continue
        dt = time.perf_counter() - t0
        text = (r.choices[0].message.content or "").strip()
        tokens = getattr(r.usage, "total_tokens", "?")
        print(f"[{tier}] {model}: {dt:.2f}s, {tokens} tokens -> {text!r}")
    return status


if __name__ == "__main__":
    raise SystemExit(main())
