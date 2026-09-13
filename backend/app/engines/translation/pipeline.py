import hashlib
import json

from sqlalchemy import delete, select

from app.db import SessionLocal
from app.engines.context.builder import build_context
from app.engines.memory.store import propose_terms, remember
from app.engines.quality.checks import checks, validate_translation
from app.engines.translation.versions import save_version
from app.jobs.queue import checkpoint, fence, finish_segment
from app.models import Entity, Glossary, Issue, Job, Project, Segment
from app.providers.llm import (
    ProviderAuthenticationRequired,
    ProviderContentRefused,
    ProviderUnavailable,
    llm,
    load_prompt,
)
from app.schemas import ContextNeeds, ReviewResult, TranslationResult


async def translation_call(
    project: Project,
    segment: Segment,
    operation: str,
    job: Job,
    extra: dict | None = None,
    needs: list[str] | None = None,
) -> TranslationResult:
    built = await build_context(
        project.id,
        segment.id,
        operation,
        deep=job.options.get("deep", False),
        instruction=job.options.get("instruction", ""),
        extra=extra,
        needs=needs,
    )
    with SessionLocal() as db:
        glossary = list(
            db.scalars(select(Glossary).where(Glossary.project_id == project.id, Glossary.accepted.is_(True)))
        )

    def validate(result: TranslationResult):
        validate_translation(segment.units, result)
        findings = checks(
            segment.units,
            [u.model_dump() for u in result.units],
            glossary,
            project.source_language,
            project.target_language,
        )
        if any(i["code"] == "locked_term" for i in findings):
            raise ValueError("Glossaire verrouillé non respecté.")

    return await llm.complete(
        project_id=project.id,
        provider_id=job.provider_id,
        segment_id=segment.id,
        operation=operation,
        messages=built.messages,
        response_model=TranslationResult,
        context=built.inspector,
        validator=validate,
        temperature=0.15 if operation == "translation_revision" else None,
    )


def persist(
    job: Job, owner: str, segment: Segment, result: TranslationResult, origin: str, stage: str
) -> bool:
    with SessionLocal() as db:
        current_job = fence(db, job.id, owner)
        project = db.get(Project, job.project_id)
        applied = save_version(
            db, segment.id, [u.model_dump() for u in result.units], origin, segment.revision, stage=stage
        )
        if applied:
            if origin == "translation":
                current_job.checkpoint = {
                    **current_job.checkpoint,
                    "started_ids": list(
                        dict.fromkeys([*current_job.checkpoint.get("started_ids", []), segment.id])
                    ),
                }
            current = db.get(Segment, segment.id)
            current.uncertainties = result.uncertainties
            current.narrative = {
                "events": [e.model_dump() for e in result.events],
                "through_position": segment.position,
            }
            propose_terms(db, project, [t.model_dump() for t in result.new_terms])
            if result.events:
                remember(db, project, current, current.narrative, "narrative")
        else:
            current_job.checkpoint = {
                **current_job.checkpoint,
                "finished_ids": list(
                    dict.fromkeys([*current_job.checkpoint.get("finished_ids", []), segment.id])
                ),
            }
        db.commit()
        return applied


async def translate(job: Job, owner: str) -> None:
    with SessionLocal() as db:
        project = db.get(Project, job.project_id)
        query = select(Segment.id).where(Segment.project_id == job.project_id).order_by(Segment.position)
        if job.options.get("chapter_id"):
            query = query.where(Segment.chapter_id == job.options["chapter_id"])
        if job.options.get("segment_id"):
            query = query.where(Segment.id == job.options["segment_id"])
        if job.options.get("refused_only"):
            query = query.where(Segment.status == "refused", Segment.retained_source.is_(False))
        ids = list(db.scalars(query))
    for number, sid in enumerate(ids):
        job = checkpoint(
            job.id,
            owner,
            {"step": "translation", "current": number + 1, "total": len(ids), "segment_id": sid},
        )
        with SessionLocal() as db:
            project = db.get(Project, job.project_id)
            segment = db.get(Segment, sid)
            if segment.human and not job.options.get("force") and job.operation != "review":
                continue
            if segment.stage == "done" and not job.options.get("force") and job.operation != "review":
                continue
            # A forced rerun is checkpointed per job, including proposal-only runs on human text.
            finished = job.checkpoint.get("finished_ids", [])
            if job.options.get("force") and sid in finished:
                continue
        needs = None
        if job.options.get("deep"):
            built = await build_context(project.id, sid, "context_planner")
            plan = await llm.complete(
                project_id=project.id,
                provider_id=job.provider_id,
                segment_id=sid,
                operation="context_planner",
                messages=built.messages,
                response_model=ContextNeeds,
                context=built.inspector,
                temperature=0.1,
            )
            needs = plan.needs
        try:
            if not segment.translation or (
                job.options.get("force") and sid not in job.checkpoint.get("started_ids", [])
            ):
                result = await translation_call(project, segment, "translation", job, needs=needs)
                if not persist(job, owner, segment, result, "translation", "translated"):
                    finish_segment(job.id, owner, sid)
                    continue  # Human/stale version: proposal is in history, never overwrites active text.
            if project.quality != "fast" or job.operation == "review":
                with SessionLocal() as db:
                    segment = db.get(Segment, sid)
                if segment.human and job.operation != "review":
                    finish_segment(job.id, owner, sid)
                    continue
                if segment.stage == "translated" or job.operation == "review":
                    built = await build_context(
                        project.id,
                        sid,
                        "translation_review",
                        extra={"CURRENT_TRANSLATION": segment.translated_units},
                    )
                    review = await llm.complete(
                        project_id=project.id,
                        provider_id=job.provider_id,
                        segment_id=sid,
                        operation="translation_review",
                        messages=built.messages,
                        response_model=ReviewResult,
                        context=built.inspector,
                        temperature=0.1,
                    )
                    allowed = {u["id"] for u in segment.units}
                    critique = [i.model_dump() for i in review.issues if i.unit_id in allowed]
                    with SessionLocal() as db:
                        fence(db, job.id, owner)
                        current = db.get(Segment, sid)
                        if current.revision == segment.revision:
                            current.critique = critique
                            if not current.human:
                                current.stage = "reviewed"
                        db.commit()
                with SessionLocal() as db:
                    segment = db.get(Segment, sid)
                if segment.human:
                    finish_segment(job.id, owner, sid)
                    continue
                if (
                    project.quality in {"high", "maximum"}
                    and segment.critique
                    and segment.stage == "reviewed"
                ):
                    result = await translation_call(
                        project,
                        segment,
                        "translation_revision",
                        job,
                        {"CURRENT_TRANSLATION": segment.translated_units, "REVIEW": segment.critique},
                        needs=needs,
                    )
                    persist(job, owner, segment, result, "revision", "revised")
                if project.quality == "maximum":
                    with SessionLocal() as db:
                        segment = db.get(Segment, sid)
                    if segment.stage != "polished":
                        result = await translation_call(
                            project,
                            segment,
                            "polishing",
                            job,
                            {"CURRENT_TRANSLATION": segment.translated_units},
                            needs=needs,
                        )
                        persist(job, owner, segment, result, "polishing", "polished")
            with SessionLocal() as db:
                current_job = fence(db, job.id, owner)
                segment = db.get(Segment, sid)
                if segment.human:
                    continue
                terms = list(
                    db.scalars(
                        select(Glossary).where(Glossary.project_id == project.id, Glossary.accepted.is_(True))
                    )
                )
                findings = checks(
                    segment.units,
                    segment.translated_units,
                    terms,
                    project.source_language,
                    project.target_language,
                )
                db.execute(delete(Issue).where(Issue.segment_id == sid))
                for issue in findings:
                    db.add(Issue(project_id=project.id, segment_id=sid, **issue))
                segment.status = "check" if findings or segment.uncertainties or segment.critique else "ok"
                segment.stage = "done"
                segment.error = ""
                current_job.checkpoint = {
                    **current_job.checkpoint,
                    "finished_ids": list(
                        dict.fromkeys([*current_job.checkpoint.get("finished_ids", []), sid])
                    ),
                }
                current_job.outage_count = 0
                db.commit()
            finish_segment(job.id, owner, sid)
        except Exception as exc:
            from app.jobs.queue import JobStopped

            if isinstance(exc, JobStopped):
                raise
            if isinstance(exc, ProviderContentRefused):
                with SessionLocal() as db:
                    current_job = fence(db, job.id, owner)
                    current = db.get(Segment, sid)
                    if current and not current.human:
                        current.status, current.error = "refused", str(exc)[:1500]
                    db.execute(
                        delete(Issue).where(Issue.segment_id == sid, Issue.code == "content_refusal")
                    )
                    db.add(
                        Issue(
                            project_id=job.project_id,
                            segment_id=sid,
                            severity="error",
                            code="content_refusal",
                            message=(
                                "Traduction refusée deux fois par le provider. "
                                "Passage ignoré ; reprise ciblée avec un autre provider disponible."
                            ),
                        )
                    )
                    current_job.checkpoint = {
                        **current_job.checkpoint,
                        "finished_ids": list(
                            dict.fromkeys([*current_job.checkpoint.get("finished_ids", []), sid])
                        ),
                    }
                    db.commit()
                finish_segment(job.id, owner, sid)
                continue
            with SessionLocal() as db:
                fence(db, job.id, owner)
                segment = db.get(Segment, sid)
                segment.status = (
                    "waiting"
                    if isinstance(exc, ProviderUnavailable)
                    else "blocked"
                    if isinstance(exc, ProviderAuthenticationRequired)
                    else "error"
                )
                if isinstance(exc, ProviderContentRefused):
                    segment.status = "refused"
                segment.error = str(exc)[:1500]
                db.commit()
            raise
    if (
        project.quality in {"high", "maximum"}
        and not job.options.get("segment_id")
        and not job.options.get("refused_only")
    ):
        await consistency(job, owner)


async def consistency(job: Job, owner: str) -> None:
    with SessionLocal() as db:
        project = db.get(Project, job.project_id)
        segments = list(
            db.scalars(
                select(Segment)
                .where(Segment.project_id == project.id, Segment.translation != "")
                .order_by(Segment.position)
            )
        )
        terms = list(
            db.scalars(select(Glossary).where(Glossary.project_id == project.id, Glossary.accepted.is_(True)))
        )
        entities = list(
            db.scalars(select(Entity).where(Entity.project_id == project.id, Entity.merged_into_id.is_(None)))
        )
    subjects = [
        {"source": t.source, "translation": t.translation, "locked": t.locked, "kind": "terminology"}
        for t in terms
    ]
    subjects += [{"source": e.name, "profile": e.data, "kind": "character"} for e in entities]
    # Evidence windows sample occurrences throughout the novel for each term; bounded, grounded output.
    for subject in subjects:
        evidence = [
            s
            for s in segments
            if any(
                name.casefold() in s.source.casefold()
                for name in [subject["source"], *subject.get("profile", {}).get("aliases", [])]
            )
        ]
        if len(evidence) < 2:
            continue
        # Sample across the entire narrative, capped at three calls per entity. Structural/locked-term
        # checks still cover every unit. Coverage is persisted and not represented as exhaustive LLM QA.
        offsets = sorted({1, max(1, len(evidence) // 2), max(1, len(evidence) - 3)})
        for offset in offsets:
            current_job = checkpoint(
                job.id,
                owner,
                {
                    "step": "consistency",
                    "subject": subject["source"],
                    "coverage": "sampled",
                    "occurrences": len(evidence),
                },
            )
            batch = list({s.id: s for s in [evidence[0], *evidence[offset : offset + 3]]}.values())
            payload = {
                "subject": subject,
                "instructions": project.instructions,
                "passages": [
                    {
                        "position": s.position,
                        "units": [{"id": u["id"], "text": u["text"]} for u in s.units],
                        "translation": s.translated_units,
                        "narrative_state": s.narrative,
                    }
                    for s in batch
                ],
            }
            batch_key = hashlib.sha256(
                json.dumps(payload, sort_keys=True, ensure_ascii=False).encode()
            ).hexdigest()
            if batch_key in current_job.checkpoint.get("consistency_batches", []):
                continue
            system, _ = load_prompt("consistency_check", project.source_language, project.target_language)
            review = await llm.complete(
                project_id=project.id,
                provider_id=job.provider_id,
                operation="consistency_check",
                messages=[
                    {"role": "system", "content": system},
                    {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
                ],
                response_model=ReviewResult,
                temperature=0.1,
            )
            mapping = {u["id"]: s.id for s in batch for u in s.units}
            with SessionLocal() as db:
                current_job = fence(db, job.id, owner)
                for issue in review.issues:
                    if issue.unit_id in mapping:
                        db.add(
                            Issue(
                                project_id=project.id,
                                segment_id=mapping[issue.unit_id],
                                code="global_consistency",
                                severity=issue.severity,
                                message=issue.description + " → " + issue.suggestion,
                            )
                        )
                current_job.checkpoint = {
                    **current_job.checkpoint,
                    "consistency_batches": list(
                        dict.fromkeys([*current_job.checkpoint.get("consistency_batches", []), batch_key])
                    ),
                }
                current_job.outage_count = 0
                db.commit()
