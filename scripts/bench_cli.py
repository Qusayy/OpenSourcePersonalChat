#!/usr/bin/env python3
"""Headless benchmark. Same suite the /bench page runs, JSON on stdout.

    python scripts/bench_cli.py            # human-readable progress + JSON
    python scripts/bench_cli.py --quiet    # JSON only, for piping into a file
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import bench, db  # noqa: E402
from app.llm import engine  # noqa: E402


async def main() -> int:
    ap = argparse.ArgumentParser(description="Run the Aurora benchmark suite.")
    ap.add_argument("--quiet", action="store_true", help="JSON only")
    ap.add_argument("--out", type=Path, help="also write the JSON here")
    args = ap.parse_args()

    def log(msg: str) -> None:
        if not args.quiet:
            print(msg, file=sys.stderr, flush=True)

    db.init()
    log("Loading model…")
    t0 = time.perf_counter()
    engine.load_blocking()
    if not engine.ready:
        print(json.dumps({"error": engine.state.error}), flush=True)
        return 1
    log(f"Loaded in {time.perf_counter() - t0:.1f}s — {engine.info()['model']}")

    summary = None
    async for event, data in bench.run_suite():
        if event == "stage":
            log(f"  · {data['text']}")
        elif event == "rep":
            log(f"    {data['case']} #{data['rep']}: {data['gen_tps']} tok/s, "
                f"{data['ttft_ms']} ms ttft")
        elif event == "case":
            log(f"  ✓ {data['label']}: median {data['gen_tps']} tok/s, "
                f"prefill {data['prefill_tps']} tok/s")
        elif event == "summary":
            summary = data
        elif event == "error":
            print(json.dumps({"error": data["message"]}), flush=True)
            return 1

    payload = json.dumps(summary, indent=2, default=str)
    print(payload, flush=True)
    if args.out:
        args.out.write_text(payload, encoding="utf-8")
        log(f"Wrote {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
