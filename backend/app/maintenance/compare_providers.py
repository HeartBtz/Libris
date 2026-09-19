"""Provider comparison (audit I-33): the same passages of a book through several providers, side by side.

    docker compose exec api python -m app.maintenance.compare_providers \\
        --project <book id> --providers <provider id>,<provider id> [--sample 5] [--output report.json]

Each provider translates the same sample (evenly spread over the book's narrative chapters) with the
prompt and context the pipeline would build for it, answer cache off. Nothing is written to the book:
translations stay in the report. The model calls are real and paid; they are recorded like any other
request under the operation `provider_comparison`, so they appear in the statistics but not in the
book's translation stages. The report gives, per provider: passages translated and failed (with the
reason), latency, tokens, cost at the price recorded with each call, the automatic checks' findings
(locked glossary, markers, untranslated text…), the length ratio, and the translations side by side.
"""

import argparse
import asyncio
import json
import statistics
import time
from collections import Counter

from sqlalchemy import func, select

from app.db import SessionLocal
from app.engines.context.builder import build_context
from app.engines.context.series import enforced_glossary
from app.engines.epub.text import plain
from app.engines.quality.checks import checks, validate_translation
from app.maintenance.usage import request_cost
from app.models import Chapter, Project, Provider, RequestLog, Segment
from app.providers.llm import LLMError, llm
from app.schemas import TranslationResult

OPERATION = "provider_comparison"


def sample_passages(db, project_id: str, size: int) -> list[Segment]:
    """Passages spread evenly over the narrative chapters, skipping the very short ones."""
    passages = list(
        db.scalars(
            select(Segment)
            .join(Chapter, Segment.chapter_id == Chapter.id)
            .where(
                Segment.project_id == project_id,
                Chapter.kind == "narrative",
                func.length(Segment.source) >= 200,
            )
            .order_by(Segment.position)
        )
    )
    if len(passages) <= size:
        return passages
    step = len(passages) / size
    return [passages[int(index * step + step / 2)] for index in range(size)]


async def translate(project: Project, provider: Provider, segment: Segment, glossary: list) -> dict:
    built = await build_context(project.id, segment.id, "translation", provider_id=provider.id)
    started = time.monotonic()
    try:
        result = await llm.complete(
            project_id=project.id,
            provider_id=provider.id,
            segment_id=segment.id,
            operation=OPERATION,
            messages=built.messages,
            response_model=TranslationResult,
            context=built.inspector,
            validator=lambda answer: validate_translation(segment.units, answer),
            use_cache=False,
        )
    except LLMError as exc:
        return {"segment_id": segment.id, "error": f"{type(exc).__name__}: {str(exc)[:300]}"}
    units = [unit.model_dump() for unit in result.units]
    findings = checks(segment.units, units, glossary, project.source_language, project.target_language)
    source = sum(len(plain(unit["text"])) for unit in segment.units)
    target = sum(len(plain(unit["text"])) for unit in units)
    return {
        "segment_id": segment.id,
        "seconds": round(time.monotonic() - started, 2),
        "findings": [finding["code"] for finding in findings],
        "length_ratio": round(target / source, 3) if source else None,
        "translation": "\n\n".join(plain(unit["text"]) for unit in units),
    }


def spent(db, project_id: str, provider_id: str, since: float) -> dict:
    tokens_in, tokens_out, cost, calls = db.execute(
        select(
            func.coalesce(func.sum(RequestLog.prompt_tokens), 0),
            func.coalesce(func.sum(RequestLog.completion_tokens), 0),
            func.coalesce(func.sum(request_cost()), 0),
            func.count(),
        )
        .outerjoin(Provider, RequestLog.provider_id == Provider.id)
        .where(
            RequestLog.project_id == project_id,
            RequestLog.provider_id == provider_id,
            RequestLog.operation == OPERATION,
            RequestLog.created_at >= since,
        )
    ).one()
    return {"calls": calls, "input_tokens": tokens_in, "output_tokens": tokens_out, "cost": round(cost, 6)}


async def compare(project_id: str, provider_ids: list[str], sample: int = 5) -> dict:
    with SessionLocal() as db:
        project = db.get(Project, project_id)
        if project is None:
            raise LLMError("Projet introuvable.")
        providers = [db.get(Provider, provider_id) for provider_id in provider_ids]
        if not all(providers):
            raise LLMError("Provider inconnu.")
        passages = sample_passages(db, project_id, sample)
        glossary = enforced_glossary(db, project)
    report = {
        "project_id": project_id,
        "title": project.title,
        "sample": [s.id for s in passages],
        "providers": [],
    }
    side_by_side = {segment.id: {"source": plain(segment.source)[:2000]} for segment in passages}
    for provider in providers:
        since = time.time() - 1
        results = [await translate(project, provider, segment, glossary) for segment in passages]
        done = [result for result in results if "error" not in result]
        for result in results:
            side_by_side[result["segment_id"]][provider.name] = result.get("translation") or result.get(
                "error"
            )
        seconds = [result["seconds"] for result in done]
        with SessionLocal() as db:
            usage = spent(db, project_id, provider.id, since)
        report["providers"].append(
            {
                "provider_id": provider.id,
                "name": provider.name,
                "model": provider.model,
                "passages": len(results),
                "translated": len(done),
                "failed": [
                    {"segment_id": r["segment_id"], "error": r["error"]} for r in results if "error" in r
                ],
                "seconds_mean": round(statistics.mean(seconds), 2) if seconds else None,
                "seconds_max": max(seconds) if seconds else None,
                **usage,
                "findings": dict(Counter(code for result in done for code in result["findings"])),
                "length_ratio_mean": (
                    round(statistics.mean(r["length_ratio"] for r in done if r["length_ratio"]), 3)
                    if done
                    else None
                ),
            }
        )
    report["side_by_side"] = side_by_side
    return report


def table(report: dict) -> str:
    header = f"{'provider':24} {'model':28} {'ok':>5} {'s/passage':>9} {'tokens in':>10} {'out':>8} {'cost':>9}  findings"
    lines = [header]
    for item in report["providers"]:
        findings = ", ".join(f"{code}×{count}" for code, count in item["findings"].items()) or "—"
        lines.append(
            f"{item['name'][:24]:24} {item['model'][:28]:28} {item['translated']:>2}/{item['passages']:<2} "
            f"{item['seconds_mean'] if item['seconds_mean'] is not None else '—':>9} {item['input_tokens']:>10} "
            f"{item['output_tokens']:>8} {item['cost']:>9.4f}  {findings}"
        )
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--project", required=True)
    parser.add_argument("--providers", required=True, help="provider ids, comma separated")
    parser.add_argument("--sample", type=int, default=5)
    parser.add_argument("--output", help="write the full report (with the translations) to this JSON file")
    arguments = parser.parse_args()
    report = asyncio.run(compare(arguments.project, arguments.providers.split(","), max(1, arguments.sample)))
    print(table(report))
    if arguments.output:
        with open(arguments.output, "w") as file:
            json.dump(report, file, ensure_ascii=False, indent=2)


if __name__ == "__main__":
    main()
