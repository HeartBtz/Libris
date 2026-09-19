#!/usr/bin/env python3
"""Wall time, model calls and tokens of the analysis of a long synthetic serial, strict against parallel.

The whole analysis of one volume runs in-process (SQLite, the real worker code) against the simulated
analyst of backend/tests/analysis_world.py, which answers every call after a fixed latency. The strict
mode is run once (it is sequential by design); the parallel mode at each number of threads.

Usage (from the repository root, with the backend's development environment):

    python scripts/benchmark_analysis.py --chapters 400 --latency 0.05 --threads 1,4,8,16
    python scripts/benchmark_analysis.py --chapters 40 --json

The latency stands for a model call; with a real provider (several seconds per call) the model time
dominates and the ratios between modes stay close to the measured ones, bounded by what the provider
lets run at once.
"""

import argparse
import asyncio
import json
import os
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "backend"), str(ROOT / "backend" / "tests")]
workspace = tempfile.TemporaryDirectory(prefix="libris-benchmark-")
os.environ.update(
    DATABASE_URL=f"sqlite:///{workspace.name}/benchmark.db",
    DATA_DIR=workspace.name,
    SECRET_KEY="benchmark-only-secret-key-with-more-than-32-characters",
    BOOTSTRAP_PASSWORD="benchmark-password-123456",
    EPUBCHECK_JAR="",
    FINAL_REVIEW_ENABLED="false",
)

import analysis_world as w
import respx
from app import models  # noqa: F401
from app.config import settings
from app.db import Base, engine


async def measure(world: w.World, mode: str, threads: int, latency: float, capacity: int) -> dict:
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    settings().prepare()
    ids, _ = w.create_world(world, capacity=capacity)
    with respx.mock:
        respx.post("https://llm.world/v1/chat/completions").mock(
            side_effect=w.simulated_provider(w.Analyst(), latency=latency)
        )
        started = time.monotonic()
        await w.analyse(ids, {"analysis_mode": mode, "threads": threads})
        elapsed = time.monotonic() - started
    return {
        "mode": mode,
        "threads": threads if mode == "parallel" else 1,
        "wall_seconds": round(elapsed, 1),
        **w.usage(ids),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--chapters", type=int, default=400)
    parser.add_argument("--per-chapter", default="2,2", help="passages per chapter: min,max")
    parser.add_argument("--latency", type=float, default=0.05, help="seconds per simulated model call")
    parser.add_argument("--threads", default="1,4,8,16")
    parser.add_argument("--capacity", type=int, default=16, help="the provider's max_concurrency")
    parser.add_argument("--skip-strict", action="store_true")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    low, high = (int(v) for v in args.per_chapter.split(","))
    world = w.build_world(args.chapters, 1, per_chapter=(low, high), seed=7)
    runs = [] if args.skip_strict else [("strict", 1)]
    runs += [("parallel", int(t)) for t in args.threads.split(",")]
    results = []
    for mode, threads in runs:
        results.append(asyncio.run(measure(world, mode, threads, args.latency, args.capacity)))
        if not args.json:
            r = results[-1]
            print(
                f"{r['mode']:>8} threads={r['threads']:<3} wall={r['wall_seconds']:>8}s calls={r['calls']:<6} "
                f"prompt_tokens={r['prompt_tokens']:<10} completion_tokens={r['completion_tokens']:<8} "
                f"{r['calls_by_operation']}",
                flush=True,
            )
    if args.json:
        print(json.dumps({"passages": len(world.passages()), "latency": args.latency, "runs": results}, indent=2))


if __name__ == "__main__":
    main()
