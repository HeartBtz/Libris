#!/usr/bin/env python3
"""Memory quality of the analysis modes on a synthetic serial with known ground truth: no network.

Runs the analysis of the same synthetic serial (backend/tests/analysis_world.py) in each mode and
scores the memory it leaves against the ground truth:

* attribution: which characters each passage involves (pronoun-only passages included);
* pronoun recall: pronoun-only passages whose referent the analysis found;
* identities: pairs of names the registry of each volume keeps together (aliases introduced late, a
  masked figure revealed mid-book, a name change in the second volume);
* relationships and glossary proposals;
* spoilers: identity links shown to (or stored for) a passage before the text reveals them.

The simulated analyst answers from its prompt alone (see analysis_world): the scores measure what each
mode puts in front of each call, not a real model. Modes: `strict`, `parallel` (every passage
reconciled), `parallel-flagged` (only ambiguous passages reconciled), `parallel-unreconciled` (no
reconciliation at all, for reference).

Usage (from the repository root, with the backend's development environment):

    python scripts/evaluate_analysis_modes.py --chapters 60 --volumes 2
    python scripts/evaluate_analysis_modes.py --seeds 1,2,3 --json
"""

import argparse
import asyncio
import json
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "backend"), str(ROOT / "backend" / "tests")]
workspace = tempfile.TemporaryDirectory(prefix="libris-evaluate-")
os.environ.update(
    DATABASE_URL=f"sqlite:///{workspace.name}/evaluate.db",
    DATA_DIR=workspace.name,
    SECRET_KEY="evaluation-only-secret-key-with-more-than-32-characters",
    BOOTSTRAP_PASSWORD="evaluation-password-123456",
    EPUBCHECK_JAR="",
    FINAL_REVIEW_ENABLED="false",
)

import analysis_world as w
import respx
from app import models  # noqa: F401
from app.config import settings
from app.db import Base, engine
from app.engines.memory import timeline

MODES = ("strict", "parallel", "parallel-flagged", "parallel-unreconciled")


async def evaluate(mode: str, world: w.World, threads: int) -> dict:
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    settings().prepare()
    settings().analysis_reconciliation = "flagged" if mode != "parallel" else "all"
    ambiguous = timeline.Timeline.ambiguous
    if mode == "parallel-unreconciled":
        timeline.Timeline.ambiguous = lambda self, analysis: False
    try:
        ids, _ = w.create_world(world, capacity=threads)
        with respx.mock:
            respx.post("https://llm.world/v1/chat/completions").mock(
                side_effect=w.simulated_provider(w.Analyst())
            )
            await w.analyse(
                ids, {"analysis_mode": "strict" if mode == "strict" else "parallel"}
            )
        return {**w.score(world, ids), **w.usage(ids)}
    finally:
        timeline.Timeline.ambiguous = ambiguous


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--chapters", type=int, default=60, help="chapters per volume")
    parser.add_argument("--volumes", type=int, default=2)
    parser.add_argument(
        "--seeds", default="7", help="comma-separated seeds; the scores are summed"
    )
    parser.add_argument("--threads", type=int, default=8)
    parser.add_argument("--modes", default=",".join(MODES))
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    results: dict[str, list[dict]] = {}
    for seed in [int(s) for s in args.seeds.split(",")]:
        world = w.build_world(args.chapters, args.volumes, seed=seed)
        for mode in args.modes.split(","):
            results.setdefault(mode, []).append(
                asyncio.run(evaluate(mode, world, args.threads))
            )
    summary = {mode: average(runs) for mode, runs in results.items()}
    if args.json:
        print(json.dumps(summary, indent=2))
        return
    keys = [k for k in next(iter(summary.values())) if k != "calls_by_operation"]
    print(f"{'':28}" + "".join(f"{mode:>24}" for mode in summary))
    for key in keys:
        print(f"{key:28}" + "".join(f"{summary[mode][key]:>24}" for mode in summary))


def average(runs: list[dict]) -> dict:
    result = {}
    for key, value in runs[0].items():
        if isinstance(value, float):
            result[key] = round(sum(r[key] for r in runs) / len(runs), 4)
        elif isinstance(value, int):
            result[key] = sum(r[key] for r in runs)
        else:
            result[key] = value
    return result


if __name__ == "__main__":
    main()
