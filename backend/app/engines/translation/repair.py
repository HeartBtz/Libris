"""Translate a passage in bounded, checkpointed batches: after invalid answers, or for small windows."""

from app.db import SessionLocal
from app.engines.context.builder import ContextTooLarge, build_context
from app.engines.epub.text import split_unit
from app.engines.quality.checks import checks, locked_term_error, validate_translation
from app.jobs import segment_state as state
from app.jobs.concurrency import blocking
from app.jobs.execution import execution
from app.jobs.queue import checkpoint, fence
from app.models import Segment
from app.providers.llm import InvalidResponseExhausted, llm
from app.schemas import TextUnit, TranslationResult

MINIMUM_PART = 400  # tokens: below this, a part is too short to be translated with any context


def _repaired(job_id: str, segment_id: str) -> dict[str, dict]:
    with SessionLocal() as db:
        return state.batches(db, job_id, state.REPAIR, segment_id)


def _changed(segment: Segment) -> bool:
    with SessionLocal() as db:
        current = db.get(Segment, segment.id)
        return current.revision != segment.revision or current.human


def _keep_batch(job_id: str, owner: str, segment_id: str, key: str, result: TranslationResult) -> None:
    with SessionLocal() as db:
        fence(db, job_id, owner)
        state.mark(db, job_id, state.REPAIR, segment_id, key=key, data=result.model_dump())
        db.commit()


async def repair_translation(project, segment, operation, job, extra, terms):
    if not execution.get() or len(segment.units) <= 1 or len(segment.units) > 128:
        raise InvalidResponseExhausted("Réparation par petits groupes impossible pour ce passage.")
    groups = [segment.units[start : start + 4] for start in range(0, len(segment.units), 4)]
    result = await translate_groups(project, segment, operation, job, extra, terms, groups, "")
    validate_translation(segment.units, result)
    return result


async def translate_in_parts(project, segment, operation, job, extra, terms, too_large: ContextTooLarge):
    """A passage too long for a small window is translated in consecutive parts, then reassembled.

    Half of what the window leaves after the fixed rules goes to the text, the other half to its
    neighbourhood. Only a first translation may cut inside a paragraph: a revision compares whole units.
    """
    allowance = (too_large.available - too_large.fixed) // 2
    ratio = too_large.target / max(1, sum(len(u["text"]) for u in segment.units))
    limit = int(allowance / max(ratio, 1))
    if not execution.get() or allowance < MINIMUM_PART or limit < MINIMUM_PART // 4:
        raise too_large
    if operation == "translation":
        parts = [
            dict(part, of=unit["id"]) for unit in segment.units for part in split_unit(dict(unit), limit)
        ]
    elif all(len(unit["text"]) <= limit for unit in segment.units):
        parts = [dict(unit, of=unit["id"]) for unit in segment.units]
    else:
        raise too_large
    groups, size = [[]], 0
    for part in parts:
        if groups[-1] and size + len(part["text"]) > limit:
            groups.append([])
            size = 0
        groups[-1].append(part)
        size += len(part["text"])
    result = await translate_groups(project, segment, operation, job, extra, terms, groups, ":parts")
    joined: dict[str, str] = {}
    for part, translated in zip(parts, result.units, strict=True):
        joined[part["of"]] = joined.get(part["of"], "") + translated.text
    result.units = [TextUnit(id=unit["id"], text=joined[unit["id"]]) for unit in segment.units]
    validate_translation(segment.units, result)
    return result


async def translate_groups(project, segment, operation, job, extra, terms, groups, suffix):
    jid, owner = execution.get()
    prefix = f"{segment.revision}:{operation}{suffix}:"
    await blocking(checkpoint, jid, owner)
    completed = await blocking(_repaired, jid, segment.id)
    merged, uncertainties, events, new_terms = [], [], [], []
    done = 0  # unit offset of the batch: its checkpoint key
    for group in groups:
        allowed = {unit["id"] for unit in group}

        def validate(result, group=group):
            validate_translation(group, result)
            findings = checks(
                group,
                [u.model_dump() for u in result.units],
                terms,
                project.source_language,
                project.target_language,
            )
            if error := locked_term_error(findings):
                raise ValueError(error)

        data = completed.get(prefix + str(done))
        if data:
            result = TranslationResult.model_validate(data)
            validate(result)
        else:
            if await blocking(_changed, segment):
                raise InvalidResponseExhausted("Le passage a changé pendant la réparation.")
            batch_extra = dict(extra or {})
            batch_extra["TARGET_TEXT"] = [{"id": u["id"], "text": u["text"]} for u in group]
            if "CURRENT_TRANSLATION" in batch_extra:
                batch_extra["CURRENT_TRANSLATION"] = [
                    u for u in batch_extra["CURRENT_TRANSLATION"] if u["id"] in allowed
                ]
            batch_extra["REPAIR_SCOPE"] = (
                "Return only the TARGET_TEXT units. Keep all IDs and inline markers unchanged."
            )
            built = await build_context(
                project.id, segment.id, operation, extra=batch_extra, provider_id=job.provider_id
            )
            result = await llm.complete(
                project_id=project.id,
                provider_id=job.provider_id,
                segment_id=segment.id,
                operation=operation,
                messages=built.messages,
                response_model=TranslationResult,
                context=built.inspector,
                validator=validate,
                temperature=0.1,
            )
            await blocking(_keep_batch, jid, owner, segment.id, prefix + str(done), result)
        done += len(group)
        merged.extend(result.units)
        uncertainties.extend(result.uncertainties)
        events.extend(result.events)
        new_terms.extend(result.new_terms)
    return TranslationResult(
        units=merged, uncertainties=list(dict.fromkeys(uncertainties)), events=events, new_terms=new_terms
    )
