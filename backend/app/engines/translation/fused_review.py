"""Review and revision of a passage in one model call (REVIEW_MODE=fused, or a volume's `review_mode`).

At high and maximum quality a passage costs a review call and, when the review finds something, a
revision call; each carries the whole context again (book context, glossary, characters,
neighbours) for a few hundred tokens of findings. Fused, one call returns the findings and, only if
there are any, the corrected units: one context instead of two for a flagged passage.

Only used where it is safe: a first review of a machine translation (never a human text, never a
review-only job, whose findings are proposals). Anything unusual — a window too small for the
passage, answers that keep failing validation — falls back to the separate review and revision,
with the reason logged: fusion saves tokens, it never costs a passage.
"""

import logging

from app.config import settings
from app.engines.context.builder import ContextTooLarge, build_context
from app.engines.quality.checks import checks, locked_term_error, validate_translation
from app.jobs.concurrency import blocking
from app.models import Project, Segment
from app.providers.llm import InvalidResponseExhausted, llm
from app.schemas import ReviewRevisionResult, TranslationResult

logger = logging.getLogger("epub.review")
OPERATION = "review_revision"
REVISED_QUALITIES = {"high", "maximum"}


def review_mode(project: Project) -> str:
    return (project.config or {}).get("review_mode") or settings().review_mode


def fusable(project: Project, segment: Segment, job) -> bool:
    """A review that a revision would follow, on a passage the machine translated in this pipeline."""
    return (
        review_mode(project) == "fused"
        and project.quality in REVISED_QUALITIES
        and job.operation != "review"
        and segment.stage == "translated"
        and not segment.human
    )


def revision_of(result: ReviewRevisionResult) -> TranslationResult:
    return TranslationResult(
        units=result.units,
        new_terms=result.new_terms,
        events=result.events,
        uncertainties=result.uncertainties,
    )


async def review_and_revise(job, owner: str, project: Project, segment: Segment, needs=None) -> bool:
    """True when the passage was reviewed (and revised if needed) in one call; False to use the two calls."""
    if not fusable(project, segment, job):
        return False
    from app.engines.translation.pipeline import _reload, _store_review, accepted_terms, persist

    glossary = await blocking(accepted_terms, project.id)
    allowed = [u["id"] for u in segment.units]

    def validate(result: ReviewRevisionResult):
        if any(issue.unit_id not in allowed for issue in result.issues):
            raise ValueError("Problème signalé sur un paragraphe absent de TARGET_TEXT.")
        if not result.issues:
            if result.units:
                raise ValueError("Aucun problème signalé : units doit rester vide.")
            return
        revised = revision_of(result)
        validate_translation(segment.units, revised)
        found = checks(
            segment.units,
            [u.model_dump() for u in revised.units],
            glossary,
            project.source_language,
            project.target_language,
        )
        if error := locked_term_error(found):
            raise ValueError(error)

    try:
        built = await build_context(
            project.id,
            segment.id,
            OPERATION,
            extra={"CURRENT_TRANSLATION": segment.translated_units},
            needs=needs,
            provider_id=job.provider_id,
        )
        result = await llm.complete(
            project_id=project.id,
            provider_id=job.provider_id,
            segment_id=segment.id,
            operation=OPERATION,
            messages=built.messages,
            response_model=ReviewRevisionResult,
            context=built.inspector,
            validator=validate,
            temperature=0.15,
            use_cache=not job.options.get("force"),
        )
    except (ContextTooLarge, InvalidResponseExhausted) as exc:
        logger.warning(
            "segment=%s review_mode=fused fallback=separate reason=%s", segment.id, type(exc).__name__
        )
        return False
    critique = [issue.model_dump() for issue in result.issues]
    await blocking(_store_review, job, owner, segment, critique)
    if critique:
        current = await blocking(_reload, segment.id)
        await blocking(persist, job, owner, current, revision_of(result), "revision", "revised")
    return True
