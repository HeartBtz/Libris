#!/usr/bin/env python3
"""Tokens per passage on a synthetic book and a mock provider: no network, no real model.

Runs the translation pipeline of one volume (translation, review, revision, polishing — the passage
calls) against an in-process mock, then reads what Libris recorded for every request:

* calls and input tokens per passage (≈ characters / 4 of the messages sent, schema excluded);
* the prefix each request shares with an earlier request of the same operation: what a provider
  with prefix caching (OpenAI and compatible servers: ≥ 1024 tokens, by blocks of 128) can serve
  from its cache — Libris cannot see a provider's cache hits on a mock, only make them possible;
* the uncached input per passage: input minus that cacheable prefix.

Usage (from the repository root, with the backend's development environment):

    python scripts/measure_prompt_cost.py --quality high --passage-chars 3500 --review-issues 0.5
    python scripts/measure_prompt_cost.py --review-mode fused --json

The figures compare configurations of the same code; they are not a forecast of a real book's bill.
"""

import argparse
import asyncio
import hashlib
import json
import os
import random
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
workspace = tempfile.TemporaryDirectory(prefix="libris-measure-")
os.environ.update(
    DATABASE_URL=f"sqlite:///{workspace.name}/measure.db",
    DATA_DIR=workspace.name,
    SECRET_KEY="measurement-only-secret-key-with-more-than-32-characters",
    BOOTSTRAP_PASSWORD="measurement-password-123456",
    EPUBCHECK_JAR="",
    WORKER_BOOK_PARALLELISM="1",
    FINAL_REVIEW_ENABLED="false",
)

import httpx
import respx
from app import models  # noqa: F401
from app.config import settings
from app.db import Base, SessionLocal, engine
from app.engines.ingestion import ImportedVolume
from app.engines.ingestion.store import Files, create_volume
from app.engines.ingestion.text import text_chapter
from app.models import (
    Entity,
    Glossary,
    Job,
    Project,
    Provider,
    RequestLog,
    Segment,
    User,
)
from sqlalchemy import select

PASSAGE_OPERATIONS = (
    "translation",
    "translation_review",
    "translation_revision",
    "polishing",
    "review_revision",
)
NAMES = ["Alice", "Bob", "Carol", "Dmitri", "Elena", "Farid"]
TERMS = [
    f"Order of the {word}" for word in ("Silver", "Glass", "Ash", "Tide", "Crown", "Lantern", "Salt", "Iron")
]
WORDS = ["the", "tower", "wind", "river", "lantern", "stone", "quietly", "remembered", "promise", "shadow", "garden", "letter", "morning", "bridge", "silence", "answered", "city", "market", "hand", "winter", "door", "voice", "old", "road", "storm", "light", "ember", "harbor", "mirror"]


def prose(rng: random.Random, sentences: int) -> str:
    out = []
    for _ in range(sentences):
        words = [rng.choice(WORDS) for _ in range(rng.randint(8, 18))]
        if rng.random() < 0.4:
            words.insert(0, rng.choice(NAMES))
        if rng.random() < 0.15:
            words.append("near the " + rng.choice(TERMS))
        out.append(" ".join(words).capitalize() + ".")
    return " ".join(out)


def synthetic_book(chapters: int, paragraphs: int, passage_chars: int) -> ImportedVolume:
    rng = random.Random(7)
    items = []
    for number in range(1, chapters + 1):
        text = "\n\n".join(prose(rng, rng.randint(3, 7)) for _ in range(paragraphs))
        chapter, _ = text_chapter(
            text, title=f"Chapter {number}", resource=f"txt/measure-{number}", max_chars=passage_chars
        )
        chapter.number = number
        items.append(chapter)
    return ImportedVolume(title="Measured Book", author="Synthetic", language="en", chapters=items)


def bible() -> dict:
    guidelines = [
        f"Guideline {n}: keep the narrator's restraint and the period vocabulary." for n in range(25)
    ]
    return {
        "title": "Measured Book",
        "summary": " ".join(prose(random.Random(3), 30).split()[:600]),
        "tone": "Melancholic, restrained, precise.",
        "narrative_style": "Third person limited, past tense, long sentences in descriptions.",
        "translation_guidelines": guidelines,
    }


def mock_completion(review_issues: float):
    def respond(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        name = body.get("response_format", {}).get("json_schema", {}).get("name", "TranslationResult")
        text = "\n".join(m["content"] for m in body["messages"])
        target = None
        if "<TARGET_TEXT>" in text:
            target = json.loads(text.split("<TARGET_TEXT>\n", 1)[1].split("\n</TARGET_TEXT>", 1)[0])
        # Same passage, same verdict: the review finds something on a stable share of the passages.
        digest = int(hashlib.sha256(target[0]["id"].encode()).hexdigest(), 16) if target else 0
        flagged = target is not None and digest % 1000 < review_issues * 1000
        issue = {
            "unit_id": target[0]["id"] if target else "",
            "category": "terminology",
            "severity": "warning",
            "description": "Synthetic finding.",
            "suggestion": "Synthetic fix.",
        }
        units = [{"id": u["id"], "text": "FR " + u["text"]} for u in target or []]
        if name == "ReviewResult":
            result = {"issues": [issue] if flagged else []}
        elif name == "ReviewRevisionResult":
            result = {
                "issues": [issue] if flagged else [],
                "units": units if flagged else [],
                "new_terms": [],
                "events": [],
                "uncertainties": [],
            }
        else:
            result = {"units": units, "new_terms": [], "events": [], "uncertainties": []}
        return httpx.Response(
            200,
            json={
                "choices": [{"finish_reason": "stop", "message": {"content": json.dumps(result)}}],
                "usage": {"prompt_tokens": len(text) // 4, "completion_tokens": len(json.dumps(result)) // 4},
            },
        )

    return respond


def serialized(messages: list[dict]) -> str:
    return "".join(f"<|{m['role']}|>{m['content']}" for m in messages)


def common_prefix(a: str, b: str) -> int:
    size = min(len(a), len(b))
    low, high = 0, size
    while low < high:  # binary search on the prefix length: the prompts are long
        middle = (low + high + 1) // 2
        if a[:middle] == b[:middle]:
            low = middle
        else:
            high = middle - 1
    return low


def cacheable(prefix_chars: int, minimum_tokens: int = 1024, block: int = 128) -> int:
    tokens = prefix_chars // 4
    return 0 if tokens < minimum_tokens else tokens // block * block


async def run(args) -> dict:
    Base.metadata.create_all(engine)
    settings().prepare()
    with SessionLocal() as db:
        user = User(username="measure", password_hash="x", admin=True)
        provider = Provider(
            name="Mock", base_url="https://llm.measure/v1", model="mock", context_window=200000,
            max_output_tokens=8000, capabilities={"supports_json_schema": True},
        )  # fmt: skip
        db.add_all([user, provider])
        db.flush()
        volume = synthetic_book(args.chapters, args.paragraphs, args.passage_chars)
        project = create_volume(db, user.id, volume, Files(), source_format="txt")
        project.provider_id, project.quality, project.target_language = provider.id, args.quality, "fr"
        project.bible, project.bible_validated = bible(), True
        project.config = {"review_mode": args.review_mode} if args.review_mode else {}
        for index, term in enumerate(TERMS):
            db.add(Glossary(project_id=project.id, source=term, translation=f"Ordre {index}", accepted=True,
                            locked=index % 2 == 0, description="Knightly order of the capital."))  # fmt: skip
        for name in NAMES:
            db.add(Entity(project_id=project.id, name=name, category="character", validated=True,
                          data={"role": f"{name} is a recurring character of the book.", "gender": "unknown"}))  # fmt: skip
        db.commit()
        project_id = project.id
    from app.jobs.queue import claim, enqueue
    from app.jobs.worker import execute

    with respx.mock:
        respx.post("https://llm.measure/v1/chat/completions").mock(
            side_effect=mock_completion(args.review_issues)
        )
        with SessionLocal() as db:
            enqueue(db, db.get(Project, project_id), "translate", {"final_review": False})
            db.commit()
        await execute(*claim())
    with SessionLocal() as db:
        job = db.scalar(select(Job))
        passages = db.scalar(select(__import__("sqlalchemy").func.count(Segment.id)))
        logs = list(
            db.scalars(
                select(RequestLog)
                .where(RequestLog.operation.in_(PASSAGE_OPERATIONS), RequestLog.cached.is_(False))
                .order_by(RequestLog.created_at)
            )
        )
        source_chars = sum(len(s.source) for s in db.scalars(select(Segment)))
    seen: dict[str, list[str]] = {}
    total = uncached = 0
    by_operation: dict[str, int] = {}
    divergence: dict[str, int] = {}
    for log in logs:
        text = serialized(log.messages)
        earlier = seen.setdefault(log.operation, [])
        shared = max((common_prefix(text, other) for other in earlier[-8:]), default=0)
        if earlier:
            # The section in which this request first differs from the closest earlier one.
            opened = [
                part.split(">", 1)[0] for part in text[:shared].split("\n<")[1:] if not part.startswith("/")
            ]
            where = f"{log.operation}:{opened[-1] if opened else 'system prompt'}"
            divergence[where] = divergence.get(where, 0) + 1
        earlier.append(text)
        tokens = len(text) // 4
        total += tokens
        uncached += tokens - cacheable(shared)
        by_operation[log.operation] = by_operation.get(log.operation, 0) + 1
    return {
        "status": job.status,
        "quality": args.quality,
        "passage_chars": args.passage_chars,
        "review_mode": args.review_mode or "separate",
        "review_issue_share": args.review_issues,
        "passages": passages,
        "source_chars": source_chars,
        "calls": len(logs),
        "calls_by_operation": by_operation,
        "calls_per_passage": round(len(logs) / passages, 2),
        "input_tokens_per_passage": round(total / passages),
        "uncached_input_tokens_per_passage": round(uncached / passages),
        "input_tokens_per_1000_source_chars": round(total / source_chars * 1000),
        "uncached_tokens_per_1000_source_chars": round(uncached / source_chars * 1000),
        "first_difference_in": dict(sorted(divergence.items(), key=lambda item: -item[1])),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--quality", choices=("fast", "normal", "high", "maximum"), default="high")
    parser.add_argument("--passage-chars", type=int, default=3500)
    parser.add_argument("--review-issues", type=float, default=0.5, help="share of passages the review flags")
    parser.add_argument("--review-mode", choices=("separate", "fused"), default=None)
    parser.add_argument("--chapters", type=int, default=3)
    parser.add_argument("--paragraphs", type=int, default=40)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    result = asyncio.run(run(args))
    if args.json:
        print(json.dumps(result, indent=2))
        return
    for key, value in result.items():
        print(f"{key:40} {value}")


if __name__ == "__main__":
    main()
