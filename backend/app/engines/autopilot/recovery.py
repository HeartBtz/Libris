"""The recovery ladder: a passage the translation gave up on climbs cheaper-first rungs until one works.

1. informed retry: the same provider is told why the previous answer failed;
2. batch repair: the passage in groups of four paragraphs;
3. sentence split: each paragraph cut at sentence boundaries, translated piece by piece, reassembled;
4. reduced context: only the passage and its mandatory rules, without neighbours or memory;
5. fallback providers (project, then global chain): an informed retry, then the sentence split, on each;
6. last resort: the original text is kept (`source_retained`) with the reason. The book is never blocked.

Every rung is bounded (one call, or one per group), and every outcome is logged.
"""

import json

from sqlalchemy import or_, select, update

from app.db import SessionLocal
from app.engines.autopilot.decisions import record
from app.engines.autopilot.degrade import reason_of
from app.engines.autopilot.providers import fallbacks
from app.engines.context.builder import section
from app.engines.context.series import enforced_glossary
from app.engines.epub.text import split_unit
from app.engines.quality.checks import checks, locked_term_error, validate_translation
from app.engines.translation.versions import save_version
from app.jobs import segment_state as state
from app.jobs.concurrency import blocking, book_share, in_parallel, job_lock
from app.jobs.queue import JobStopped, checkpoint, emit, fence
from app.models import Issue, Job, JobSegmentState, Project, Provider, Segment
from app.providers.llm import (
    InvalidResponseExhausted,
    LLMError,
    ProviderAuthenticationRequired,
    ProviderUnavailable,
    llm,
    load_prompt,
)
from app.schemas import TextUnit, TranslationResult

FAILED = ("error", "refused", "blocked", "waiting")
SENTENCE_CHARS = 400  # a piece this long still carries a whole sentence or two
FAILURE_CODES = ("content_refusal", "invalid_response")


def failed_condition(project_id: str):
    return (
        Segment.project_id == project_id,
        Segment.human.is_(False),
        Segment.validated.is_(False),
        Segment.retained_source.is_(False),
        or_(Segment.status.in_(FAILED), Segment.translation == ""),
    )


def _targets(job: Job, round_no: int) -> list[str]:
    with SessionLocal() as db:
        done = set(
            db.scalars(
                select(JobSegmentState.segment_id).where(
                    JobSegmentState.job_id == job.id,
                    JobSegmentState.step == state.LADDER,
                    JobSegmentState.key == f"r{round_no}",
                )
            )
        )
        ids = db.scalars(
            select(Segment.id).where(*failed_condition(job.project_id)).order_by(Segment.position)
        )
        return [sid for sid in ids if sid not in done]


async def recover_failed(job: Job, owner: str, round_no: int) -> None:
    ids = await blocking(_targets, job, round_no)

    async def launch(item: tuple[int, str]):
        index, sid = item
        await blocking(
            checkpoint,
            job.id,
            owner,
            {"step": "automatic_recovery", "current": index + 1, "total": len(ids), "segment_id": sid},
        )
        return climb(job, owner, sid, round_no)

    await in_parallel(enumerate(ids), book_share(job.provider_id), launch)


def _load(job: Job, sid: str):
    with SessionLocal() as db:
        project = db.get(Project, job.project_id)
        segment = db.get(Segment, sid)
        if segment.human or segment.validated or segment.retained_source:
            return None
        return project, segment, enforced_glossary(db, project)


def on_provider(job: Job, provider_id: str) -> Job:
    """A detached copy of the job that sends its calls to another provider (never saved)."""
    return Job(
        id=job.id,
        project_id=job.project_id,
        provider_id=provider_id,
        operation=job.operation,
        options={**job.options, "force": True},
        checkpoint=dict(job.checkpoint),
    )


def validator(project: Project, units: list[dict], glossary: list):
    def validate(result: TranslationResult) -> None:
        validate_translation(units, result)
        findings = checks(
            units,
            [u.model_dump() for u in result.units],
            glossary,
            project.source_language,
            project.target_language,
        )
        if error := locked_term_error(findings):
            raise ValueError(error)

    return validate


def reduced_messages(project: Project, segment: Segment, operation: str, glossary: list, extra: dict) -> list:
    """The passage and its mandatory rules only: no neighbours, memory or retrieval."""
    system, _ = load_prompt(operation, project.source_language, project.target_language)
    source = segment.source.casefold()
    mandatory = {
        "USER_RULES": {"book": project.instructions, "passage": segment.instructions},
        "LOCKED_GLOSSARY": [
            {"source": g.source, "translation": g.translation}
            for g in glossary
            if g.locked and g.source.casefold() in source
        ],
        "TARGET_TEXT": [{"id": u["id"], "text": u["text"]} for u in segment.units],
        **extra,
    }
    body = "\n\n".join(
        section(key, json.dumps(value, ensure_ascii=False)) for key, value in mandatory.items()
    )
    return [{"role": "system", "content": system}, {"role": "user", "content": body}]


def _informed(reason: str) -> dict:
    return {
        "PREVIOUS_FAILURE": (
            "An earlier attempt at this passage failed: "
            f"{reason[:600]} Translate it faithfully as literary fiction, keeping every unit ID and marker."
        )
    }


async def informed_retry(job, project, segment, glossary, reason):
    from app.engines.translation.pipeline import translation_call

    return await translation_call(project, segment, "translation", job, _informed(reason))


async def batch_repair(job, project, segment, glossary, reason):
    from app.engines.translation.repair import repair_translation

    return await repair_translation(project, segment, "translation", job, _informed(reason), glossary)


async def sentence_split(job, project, segment, glossary, reason):
    from app.engines.translation.repair import translate_groups

    parts = [
        dict(part, of=unit["id"]) for unit in segment.units for part in split_unit(dict(unit), SENTENCE_CHARS)
    ]
    if len(parts) > 256:
        raise LLMError("Passage trop long pour une traduction phrase par phrase.")
    groups = [[part] for part in parts]
    result = await translate_groups(
        project,
        segment,
        "translation",
        job,
        _informed(reason),
        glossary,
        groups,
        f":sentences:{job.provider_id}",
    )
    joined: dict[str, str] = {}
    for part, translated in zip(parts, result.units, strict=True):
        joined[part["of"]] = joined.get(part["of"], "") + translated.text
    result.units = [TextUnit(id=unit["id"], text=joined[unit["id"]]) for unit in segment.units]
    validate_translation(segment.units, result)
    return result


async def reduced_context(job, project, segment, glossary, reason):
    messages = await blocking(reduced_messages, project, segment, "translation", glossary, _informed(reason))
    return await llm.complete(
        project_id=project.id,
        provider_id=job.provider_id,
        segment_id=segment.id,
        operation="translation",
        messages=messages,
        response_model=TranslationResult,
        context={"reduced_context": True},
        validator=validator(project, segment.units, glossary),
        temperature=0.1,
        use_cache=False,
    )


def rungs(job: Job, segment: Segment) -> list[tuple[str, Job, object]]:
    steps = [("informed_retry", job, informed_retry)]
    if len(segment.units) > 1:
        steps.append(("batch_repair", job, batch_repair))
    steps += [("sentence_split", job, sentence_split), ("reduced_context", job, reduced_context)]
    for provider_id in fallbacks(job.project_id, job.provider_id):
        other = on_provider(job, provider_id)
        steps += [("fallback_provider", other, informed_retry), ("fallback_provider", other, sentence_split)]
    return steps


async def climb(job: Job, owner: str, sid: str, round_no: int) -> None:
    loaded = await blocking(_load, job, sid)
    if loaded is None:
        return
    project, segment, glossary = loaded
    reason = segment.error or "Passage non traduit."
    attempts: list[str] = []
    repaired = False
    for name, runner, rung in await blocking(rungs, job, segment):
        if name == "batch_repair" and repaired:
            continue  # the retry's invalid answers already went through the same groups
        label = name if runner is job else f"{name}:{await blocking(_provider_name, runner.provider_id)}"
        try:
            result = await rung(runner, project, segment, glossary, reason)
        except JobStopped:
            raise
        except (ProviderUnavailable, ProviderAuthenticationRequired) as exc:
            if runner is job:
                raise  # the job's own provider is down: the job-level fallback decides
            attempts.append(f"{label} : indisponible ({type(exc).__name__})")
            continue
        except (LLMError, ValueError) as exc:
            attempts.append(f"{label} : {reason_of(exc)[:200]}")
            reason = reason_of(exc)
            repaired = repaired or (name == "informed_retry" and isinstance(exc, InvalidResponseExhausted))
            continue
        # Applied, or the passage changed meanwhile (a person edited it): either way nothing more to do.
        await blocking(
            _recovered, job, owner, project, segment, result, runner.provider_id, label, attempts, round_no
        )
        return
    await blocking(retain_source, job, owner, sid, " ; ".join(attempts) or reason, round_no)


def _provider_name(provider_id: str | None) -> str:
    with SessionLocal() as db:
        provider = db.get(Provider, provider_id) if provider_id else None
        return provider.name if provider else "?"


def _recovered(job, owner, project, segment, result, provider_id, label, attempts, round_no) -> bool:
    from app.engines.translation.pipeline import _complete_passage, persist

    applied = persist(job, owner, segment, result, "recovery", "translated")
    with SessionLocal() as db:
        fence(db, job.id, owner)
        state.mark(
            db,
            job.id,
            state.LADDER,
            segment.id,
            key=f"r{round_no}",
            outcome="recovered" if applied else "protected",
        )
        if applied:
            record(
                db,
                job.project_id,
                job_id=job.id,
                segment_id=segment.id,
                stage="recovery",
                kind="failed_passage",
                action="recovered",
                reason=f"Traduit à l’étape « {label} »"
                + (f" après : {' ; '.join(attempts)}" if attempts else "")
                + ".",
                provider=db.get(Provider, provider_id) if provider_id else None,
            )
        db.commit()
    if applied:
        _complete_passage(job, owner, project, segment.id)
    return applied


def retain_source(job: Job, owner: str, sid: str, reason: str, round_no: int | None = None) -> bool:
    """Last resort: the passage keeps its original text, with the reason recorded (never left failed).

    A passage that failed a new attempt but still has a machine translation keeps that translation."""
    with job_lock(job.id), SessionLocal() as db:
        current_job = fence(db, job.id, owner)
        segment = db.get(Segment, sid)
        if segment.human or segment.validated or segment.retained_source:
            return False
        provider = db.get(Provider, current_job.provider_id) if current_job.provider_id else None
        if segment.translation:
            segment.status, segment.error, segment.stage = "ok", "", "done"
            db.execute(
                update(Issue)
                .where(Issue.segment_id == sid, Issue.code.in_(FAILURE_CODES), Issue.resolved.is_(False))
                .values(resolved=True)
            )
            state.mark(db, job.id, state.FINISHED, sid)
            if round_no is not None:
                state.mark(db, job.id, state.LADDER, sid, key=f"r{round_no}", outcome="kept_translation")
            record(
                db,
                job.project_id,
                job_id=job.id,
                segment_id=sid,
                stage="recovery",
                kind="failed_passage",
                action="kept_translation",
                reason=f"Traduction précédente conservée : {reason}",
                provider=provider,
            )
            db.commit()
            return True
        units = [{"id": u["id"], "text": u["text"]} for u in segment.units]
        if not save_version(db, sid, units, "source_retained", segment.revision, stage="done"):
            db.rollback()
            return False
        db.execute(
            update(Issue)
            .where(Issue.segment_id == sid, Issue.code.in_(FAILURE_CODES), Issue.resolved.is_(False))
            .values(resolved=True)
        )
        message = f"Texte original conservé automatiquement : {reason}"[:3000]
        db.add(
            Issue(
                project_id=job.project_id,
                segment_id=sid,
                severity="warning",
                code="source_retained",
                message=message,
            )
        )
        state.mark(db, job.id, state.FINISHED, sid)
        if round_no is not None:
            state.mark(db, job.id, state.LADDER, sid, key=f"r{round_no}", outcome="source_retained")
        record(
            db,
            job.project_id,
            job_id=job.id,
            segment_id=sid,
            stage="recovery",
            kind="failed_passage",
            action="source_retained",
            reason=reason,
            provider=provider,
        )
        emit(db, job.project_id, job_id=job.id, segment_id=sid, status="source_retained", automatic=True)
        db.commit()
        return True
