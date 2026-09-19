"""What a job would cost before it is started, without calling any model.

The estimate multiplies the passages that the job would still process by the calls each step makes
and by the tokens of one call. Both come, step by step, from the owner's past books on the same
provider when there are enough of them; otherwise from defaults calibrated on averages measured on
real books in production (about 14 000 input tokens per call, prompts dominated by instructions and
context rather than by the passage itself).
"""

import math
from collections import defaultdict
from dataclasses import dataclass
from typing import Literal

from fastapi import APIRouter, Query
from sqlalchemy import func, select

from app.config import settings
from app.engines.translation.fused_review import review_mode
from app.models import Chapter, Entity, Glossary, Memory, Project, Provider, RequestLog, Segment
from app.security import DB, CurrentUser, access

router = APIRouter(prefix="/api/projects")

MIN_HISTORY_PASSAGES = 5
CHARS_PER_TOKEN = 4
# Instructions, JSON schema, glossary, characters and neighbouring passages sent with every call.
PROMPT_OVERHEAD = 12_500
# New attempts after invalid or failed answers; real books showed 6.5 to 15 % of failed calls.
RETRY_FACTOR = 1.15
# Share of passages that get a conditional step: a revision only follows a critique with issues,
# the final review only reads the passages flagged by the checks.
DEFAULT_SHARE = {"translation_revision": 0.6, "final_review": 0.5, "chapter_reconciliation": 0.5}
FAMILIES = {
    "analyze": ("chapter_analysis", "chapter_extraction", "chapter_reconciliation", "book_analysis"),
    "translate": (
        "translation",
        "translation_review",
        "translation_revision",
        "review_revision",
        "polishing",
        "final_review",
        "consistency_check",
    ),
}
FAMILIES["review"] = FAMILIES["translate"]
PASSAGE_STEPS = {"chapter_analysis", "translation", "translation_review", "translation_revision",
                 "review_revision", "polishing", "final_review", "chapter_extraction", "chapter_reconciliation"}


def analysis_mode_of(project: Project) -> str:
    """The volume's analysis mode, else ANALYSIS_MODE (a launch may still choose another)."""
    chosen = (project.config or {}).get("analysis_mode")
    return chosen if chosen in {"parallel", "strict"} else settings().analysis_mode


@dataclass
class Step:
    operation: str
    count: int
    conditional: bool = False


@dataclass
class Observed:
    calls: int = 0
    passages: int = 0
    successes: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    family_passages: int = 0


def default_tokens(operation: str, passage: float) -> tuple[float, float]:
    """Input and output tokens of one call, for a passage of `passage` tokens."""
    rewritten = 1.3 * passage + 300  # translated text, a little longer in French, wrapped in JSON
    return {
        "chapter_analysis": (PROMPT_OVERHEAD + passage, 800),
        # Parallel analysis: an extraction, then a reconciliation that also reads the extraction and
        # the memory of the passages before (app.engines.translation.parallel_analysis).
        "chapter_extraction": (PROMPT_OVERHEAD + passage, 800),
        "chapter_reconciliation": (PROMPT_OVERHEAD + passage + 2500, 800),
        "book_analysis": (PROMPT_OVERHEAD + 4 * 800, 1000),
        "translation": (PROMPT_OVERHEAD + passage, rewritten),
        "translation_review": (PROMPT_OVERHEAD + 2 * passage, 500),
        "translation_revision": (PROMPT_OVERHEAD + 2.5 * passage, rewritten),
        # Findings always, the corrected units only for the passages with findings.
        "review_revision": (PROMPT_OVERHEAD + 2 * passage, 500 + DEFAULT_SHARE["translation_revision"] * rewritten),
        "polishing": (PROMPT_OVERHEAD + 2 * passage, rewritten),
        "final_review": (PROMPT_OVERHEAD + 2 * passage, rewritten),
        "consistency_check": (PROMPT_OVERHEAD + 8 * passage, 500),
    }[operation]


def plan(db, project: Project, operation: str) -> tuple[list[Step], int, int]:
    """The steps the job would run, how many passages it would touch and their size in characters."""
    in_book = Segment.project_id == project.id
    if operation == "analyze":
        analyzed = select(Memory.segment_id).where(
            Memory.project_id == project.id, Memory.kind == "analysis", Memory.segment_id.is_not(None)
        )
        passages, chars = db.execute(
            select(func.count(), func.coalesce(func.sum(func.length(Segment.source)), 0)).where(
                in_book, Segment.id.not_in(analyzed)
            )
        ).one()
        parallel = analysis_mode_of(project) == "parallel"
        if parallel:
            # Every passage extracted, then reconciled (ANALYSIS_RECONCILIATION=flagged: the ambiguous ones).
            flagged = settings().analysis_reconciliation != "all"
            steps = [Step("chapter_extraction", passages), Step("chapter_reconciliation", passages, flagged)]
        else:
            steps = [Step("chapter_analysis", passages)]
        if not project.bible_validated:
            sizes = list(db.scalars(
                select(func.count(Segment.id))
                .join(Chapter, Segment.chapter_id == Chapter.id)
                .where(in_book, Chapter.analyzed.is_(False))
                .group_by(Chapter.id)
            ))  # fmt: skip
            syntheses = sum(math.ceil(size / 4) for size in sizes)
            if parallel:
                # The Book Bible as a tree: chapter syntheses merged four by four, level by level.
                nodes = len(sizes)
                while nodes > 1:
                    nodes = math.ceil(nodes / 4)
                    syntheses += nodes
            steps.append(Step("book_analysis", syntheses))
        return steps, passages, chars

    # Same selection as the pipeline: a translation skips what is done or human, a review
    # reads every passage again; neither touches what a person has validated.
    if operation == "translate":
        scope = [in_book, Segment.human.is_(False), Segment.stage != "done"]
    else:
        scope = [in_book, Segment.validated.is_(False)]
    untranslated = Segment.translation == ""
    passages, chars, missing, translated, reviewed, unpolished, editable = db.execute(
        select(
            func.count(),
            func.coalesce(func.sum(func.length(Segment.source)), 0),
            func.count().filter(untranslated),
            func.count().filter(Segment.stage == "translated"),
            func.count().filter(Segment.stage == "reviewed"),
            func.count().filter(Segment.stage != "polished", Segment.human.is_(False)),
            func.count().filter(Segment.human.is_(False)),
        ).where(*scope)
    ).one()
    quality = project.quality
    steps = [Step("translation", missing)]
    if operation == "review":
        steps.append(Step("translation_review", passages))
        revisable = editable
    elif quality != "fast":
        steps.append(Step("translation_review", missing + translated))
        revisable = missing + translated + reviewed
    else:
        revisable = 0
    if quality in {"high", "maximum"} and operation == "translate" and review_mode(project) == "fused":
        # One call reviews and revises what this job translates; a passage already reviewed is revised.
        steps = [step for step in steps if step.operation != "translation_review"]
        steps.append(Step("review_revision", missing + translated))
        steps.append(Step("translation_revision", reviewed, conditional=True))
    elif quality in {"high", "maximum"}:
        steps.append(Step("translation_revision", revisable, conditional=True))
    if quality == "maximum":
        steps.append(Step("polishing", unpolished))
    if settings().final_review_enabled:
        steps.append(Step("final_review", passages, conditional=True))
        if operation == "translate":
            # Finished passages still flagged by the checks are read again by the final review.
            flagged = db.scalar(
                select(func.count()).where(
                    in_book,
                    Segment.stage == "done",
                    Segment.status == "check",
                    Segment.translation != "",
                    Segment.human.is_(False),
                    Segment.validated.is_(False),
                    Segment.retained_source.is_(False),
                )
            )
            steps.append(Step("final_review", flagged))
    if quality in {"high", "maximum"}:
        subjects = db.scalar(
            select(func.count()).where(Glossary.project_id == project.id, Glossary.accepted.is_(True))
        ) + db.scalar(
            select(func.count()).where(Entity.project_id == project.id, Entity.merged_into_id.is_(None))
        )
        steps.append(Step("consistency_check", subjects))
    return steps, passages, chars


def history(db, project: Project, operations: tuple[str, ...]) -> tuple[dict[str, Observed], int, int]:
    """Calls of the owner's books on this provider, excluding cache hits that cost nothing."""
    if not project.provider_id:
        return {}, 0, 0
    books = select(Project.id).where(Project.owner_id == project.owner_id)
    scope = (
        RequestLog.project_id.in_(books),
        RequestLog.provider_id == project.provider_id,
        RequestLog.cached.is_(False),
        RequestLog.status != "running",
        RequestLog.operation.in_(operations),
    )
    touched = dict(
        db.execute(
            select(RequestLog.project_id, func.count(func.distinct(RequestLog.segment_id)))
            .where(*scope)
            .group_by(RequestLog.project_id)
        ).all()
    )
    observed: dict[str, Observed] = defaultdict(Observed)
    for project_id, operation, calls, passages, successes, tokens_in, tokens_out in db.execute(
        select(
            RequestLog.project_id,
            RequestLog.operation,
            func.count(),
            func.count(func.distinct(RequestLog.segment_id)),
            func.count().filter(RequestLog.status == "success"),
            func.coalesce(func.sum(RequestLog.prompt_tokens), 0),
            func.coalesce(func.sum(RequestLog.completion_tokens), 0),
        )
        .where(*scope)
        .group_by(RequestLog.project_id, RequestLog.operation)
    ):
        item = observed[operation]
        item.calls += calls
        item.passages += passages
        item.successes += successes
        item.input_tokens += tokens_in
        item.output_tokens += tokens_out
        item.family_passages += touched[project_id]
    return observed, sum(1 for count in touched.values() if count), sum(touched.values())


def price_note(provider: Provider | None) -> str:
    if not provider:
        return "Aucun fournisseur n’est associé à ce livre : coût non calculé."
    if not (provider.input_cost or provider.output_cost):
        return f"Aucun prix n’est saisi pour le fournisseur « {provider.name} » : coût affiché à 0."
    return (
        f"Prix actuels du fournisseur « {provider.name} » par million de tokens, dans la devise où ils "
        "ont été saisis ; remises de cache et forfaits d’abonnement non pris en compte."
    )


@router.get("/{pid}/estimate")
def estimate(
    pid: str,
    user: CurrentUser,
    db: DB,
    operation: Literal["analyze", "translate", "review"] = Query(),
):
    from app.engines.budget import estimate_view

    project = access(db, pid, user)
    result = forecast(db, project, operation)
    # The book's budget, when it has one: what remains of it against this estimate.
    return {**result, "budget": estimate_view(db, project, result["cost"])}


def forecast(db, project: Project, operation: str) -> dict:
    """The estimate of `operation` on the book (also read by the budgets before a launch)."""
    provider = db.get(Provider, project.provider_id) if project.provider_id else None
    steps, passages, chars = plan(db, project, operation)
    passage_tokens = chars / passages / CHARS_PER_TOKEN if passages else 0
    observed, books, touched = history(db, project, FAMILIES[operation])
    enough = touched >= MIN_HISTORY_PASSAGES
    totals: dict[str, list] = {}
    for step in steps:
        if not step.count:
            continue
        seen = observed.get(step.operation) if enough else None
        if seen and seen.calls and (seen.passages or step.operation not in PASSAGE_STEPS):
            if step.operation not in PASSAGE_STEPS:
                rate = seen.calls / seen.successes if seen.successes else RETRY_FACTOR
            elif step.conditional:
                rate = seen.calls / seen.family_passages
            else:
                rate = seen.calls / seen.passages
            tokens_in, tokens_out = seen.input_tokens / seen.calls, seen.output_tokens / seen.calls
            source = "history"
        else:
            share = DEFAULT_SHARE.get(step.operation, 1) if step.conditional else 1
            rate = share * RETRY_FACTOR
            tokens_in, tokens_out = default_tokens(step.operation, passage_tokens)
            source = "default"
        calls = step.count * rate
        entry = totals.setdefault(step.operation, [0.0, 0.0, 0.0, set()])
        entry[0] += calls
        entry[1] += calls * tokens_in
        entry[2] += calls * tokens_out
        entry[3].add(source)
    breakdown = [
        {
            "operation": name,
            "requests": round(calls),
            "input_tokens": round(tokens_in),
            "output_tokens": round(tokens_out),
            "source": next(iter(sources)) if len(sources) == 1 else "mixed",
        }
        for name, (calls, tokens_in, tokens_out, sources) in totals.items()
    ]
    input_tokens = sum(item["input_tokens"] for item in breakdown)
    output_tokens = sum(item["output_tokens"] for item in breakdown)
    cost = (
        (input_tokens * provider.input_cost + output_tokens * provider.output_cost) / 1_000_000
        if provider
        else 0
    )
    defaults = sorted(item["operation"] for item in breakdown if item["source"] != "history")
    observed_text = (
        f"historique de {books} livre{'s' if books > 1 else ''} avec ce fournisseur "
        f"({touched} passages traités)"
    )
    if not breakdown:
        kind, basis = "none", "Aucun passage ne reste à traiter pour cette opération."
    elif not defaults:
        kind, basis = "history", observed_text[0].upper() + observed_text[1:] + "."
    elif len(defaults) < len(breakdown):
        kind = "mixed"
        basis = (
            observed_text[0].upper() + observed_text[1:]
            + " ; estimation par défaut pour : " + ", ".join(defaults) + "."
        )
    else:
        kind = "default"
        basis = (
            "Estimation par défaut, sans historique suffisant avec ce fournisseur : environ "
            f"{PROMPT_OVERHEAD} tokens de contexte par appel plus le passage ({CHARS_PER_TOKEN} caractères "
            f"par token), {round((RETRY_FACTOR - 1) * 100)} % de nouvelles tentatives, étapes selon la "
            "qualité du livre."
        )
    return {
        "operation": operation,
        "passages": passages,
        "requests": sum(item["requests"] for item in breakdown),
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "cost": round(cost, 4),
        "currency_note": price_note(provider),
        "basis": basis,
        "basis_kind": kind,
        "history_books": books if kind in {"history", "mixed"} else 0,
        "breakdown": breakdown,
    }
