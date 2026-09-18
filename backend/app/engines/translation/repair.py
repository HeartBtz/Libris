"""Recover a structurally invalid response with bounded, checkpointed four-unit batches."""

from app.db import SessionLocal
from app.engines.context.builder import build_context
from app.engines.quality.checks import checks, locked_term_error, validate_translation
from app.jobs.execution import execution
from app.jobs.queue import checkpoint, fence
from app.models import Segment
from app.providers.llm import InvalidResponseExhausted, llm
from app.schemas import TranslationResult


async def repair_translation(project, segment, operation, job, extra, terms):
    scope = execution.get()
    if not scope or len(segment.units) <= 1 or len(segment.units) > 128:
        raise InvalidResponseExhausted("Réparation par petits groupes impossible pour ce passage.")
    jid, owner = scope
    key = f"{segment.id}:{segment.revision}:{operation}"
    completed = checkpoint(jid, owner).checkpoint.get("repair", {})
    completed = completed.get(key, {})
    merged, uncertainties, events, new_terms = [], [], [], []
    for start in range(0, len(segment.units), 4):
        group = segment.units[start : start + 4]
        allowed = {unit["id"] for unit in group}

        def validate(result):
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

        data = completed.get(str(start))
        if data:
            result = TranslationResult.model_validate(data)
            validate(result)
        else:
            with SessionLocal() as db:
                current = db.get(Segment, segment.id)
                if current.revision != segment.revision or current.human:
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
            with SessionLocal() as db:
                current_job = fence(db, jid, owner)
                repair = dict(current_job.checkpoint.get("repair", {}))
                batches = dict(repair.get(key, {}))
                batches[str(start)] = result.model_dump()
                repair[key] = batches
                current_job.checkpoint = {
                    **current_job.checkpoint,
                    "repair": repair,
                    "repair_progress": {
                        "segment_id": segment.id,
                        "units_done": min(start + 4, len(segment.units)),
                        "units_total": len(segment.units),
                    },
                }
                db.commit()
        merged.extend(result.units)
        uncertainties.extend(result.uncertainties)
        events.extend(result.events)
        new_terms.extend(result.new_terms)
    result = TranslationResult(
        units=merged, uncertainties=list(dict.fromkeys(uncertainties)), events=events, new_terms=new_terms
    )
    validate_translation(segment.units, result)
    return result
