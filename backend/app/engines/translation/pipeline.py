import asyncio
import hashlib
import json
import weakref

from sqlalchemy import delete, func, or_, select

from app.db import SessionLocal
from app.engines.autopilot.degrade import degradable, note, reason_of
from app.engines.context.builder import ContextTooLarge, build_context
from app.engines.context.series import enforced_glossary
from app.engines.memory.store import propose_terms, remember
from app.engines.quality.checks import checks, locked_term_error, validate_translation
from app.engines.translation.fused_review import review_and_revise
from app.engines.translation.memory import remembered_translation
from app.engines.translation.versions import save_version
from app.jobs import segment_state as state
from app.jobs.concurrency import blocking, in_parallel, job_lock, job_share
from app.jobs.follow_up import scoped_chapters
from app.jobs.queue import JobStopped, checkpoint, fence, finish_segment
from app.models import Entity, Glossary, Issue, Job, Project, Segment
from app.providers.llm import (
    MAX_INVALID_ATTEMPTS,
    InvalidResponseExhausted,
    LLMError,
    ProviderAuthenticationRequired,
    ProviderContentRefused,
    ProviderUnavailable,
    llm,
    load_prompt,
)
from app.schemas import ContextNeeds, ReviewResult, TranslationResult


def restore_project_provider(job_id: str, owner: str, provider_id: str | None) -> Job:
    with SessionLocal() as db:
        job = fence(db, job_id, owner)
        options = dict(job.options)
        recovery_provider_id = options.pop("provider_id", None)
        if recovery_provider_id:
            options["recovery_provider_id"] = recovery_provider_id
        job.options = options
        job.provider_id = provider_id
        db.commit()
        return job


def accepted_terms(project_id: str) -> list:
    with SessionLocal() as db:
        return enforced_glossary(db, db.get(Project, project_id))


async def translation_call(
    project: Project,
    segment: Segment,
    operation: str,
    job: Job,
    extra: dict | None = None,
    needs: list[str] | None = None,
) -> TranslationResult:
    glossary = await blocking(accepted_terms, project.id)
    try:
        built = await build_context(
            project.id,
            segment.id,
            operation,
            deep=job.options.get("deep", False),
            instruction=job.options.get("instruction", ""),
            extra=extra,
            needs=needs,
            provider_id=job.provider_id,
        )
    except ContextTooLarge as too_large:
        from app.engines.translation.repair import translate_in_parts

        return await translate_in_parts(project, segment, operation, job, extra, glossary, too_large)

    def validate(result: TranslationResult):
        validate_translation(segment.units, result)
        findings = checks(
            segment.units,
            [u.model_dump() for u in result.units],
            glossary,
            project.source_language,
            project.target_language,
        )
        if error := locked_term_error(findings):
            raise ValueError(error)

    try:
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
            use_cache=not job.options.get("force"),
        )
    except InvalidResponseExhausted:
        from app.engines.translation.repair import repair_translation

        return await repair_translation(project, segment, operation, job, extra, glossary)


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
            if origin in {"translation", "translation_memory"}:
                state.mark(db, current_job.id, state.STARTED, segment.id)
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
            state.mark(db, current_job.id, state.FINISHED, segment.id)
        db.commit()
        return applied


def _translation_plan(job: Job) -> tuple[list[str], set[str], bool, bool]:
    with SessionLocal() as db:
        recovery_pass = bool(
            db.scalar(state.segments(job.id, state.RECOVERY_TARGET).limit(1))
            and job.checkpoint.get("automatic_recovery_started")
            and not job.checkpoint.get("automatic_recovery_completed")
        )
        force = bool(job.options.get("force") or recovery_pass)
        query = select(Segment.id).where(Segment.project_id == job.project_id).order_by(Segment.position)
        if recovery_pass:
            query = query.where(Segment.id.in_(state.segments(job.id, state.RECOVERY_TARGET)))
        elif job.options.get("chapter_id"):
            query = query.where(Segment.chapter_id == job.options["chapter_id"])
        if job.options.get("segment_id"):
            query = query.where(Segment.id == job.options["segment_id"])
        if job.options.get("segment_ids"):
            query = query.where(Segment.id.in_(job.options["segment_ids"]))
        if job.options.get("refused_only"):
            query = query.where(Segment.status == "refused", Segment.retained_source.is_(False))
        ids = list(db.scalars(query))
        # A resumed job skips what is done without a checkpoint write and an event per passage; the
        # checks of each passage still catch a human edit made while the job runs.
        settled = state.marked(db, job.id, state.FINISHED)
        if not force and job.operation != "review":
            settled.update(
                db.scalars(
                    select(Segment.id).where(
                        Segment.project_id == job.project_id,
                        Segment.human.is_(True) | (Segment.stage == "done"),
                    )
                )
            )
    return ids, settled, force, recovery_pass


async def translate(job: Job, owner: str) -> None:
    job = await blocking(checkpoint, job.id, owner)
    scoped = any(
        job.options.get(key)
        for key in ("segment_id", "chapter_id", "refused_only", "segment_ids")
    )
    continue_pipeline = job.options.get("continue_pipeline") or not scoped
    ids, settled, force, recovery_pass = await blocking(_translation_plan, job)

    async def launch(item: tuple[int, str]):
        number, sid = item
        if sid in settled:
            return None
        current = await blocking(
            checkpoint,
            job.id,
            owner,
            {"step": "translation", "current": number + 1, "total": len(ids), "segment_id": sid},
        )
        return translate_passage(current, owner, sid, force)

    # Passages overlap: a passage's context shows the translation of the neighbours already done and
    # only the source of those still in flight (see docs/architecture.md).
    await in_parallel(enumerate(ids), job_share(job, owner), launch)
    job = await blocking(checkpoint, job.id, owner)
    await after_translation(job, owner, scoped, continue_pipeline, recovery_pass)


def _passage(job: Job, sid: str, force: bool) -> tuple[Project, Segment, bool] | None:
    with SessionLocal() as db:
        project = db.get(Project, job.project_id)
        segment = db.get(Segment, sid)
        if segment.human and not force and job.operation != "review":
            return None
        if segment.stage == "done" and not force and job.operation != "review":
            return None
        # A passage kept in the original is translated again only on an explicit, forced request.
        if segment.retained_source and not force:
            return None
        # A forced rerun is checkpointed per job, including proposal-only runs on human text.
        if state.is_marked(db, job.id, state.FINISHED, sid):
            return None
        return project, segment, state.is_marked(db, job.id, state.STARTED, sid)


def _reload(sid: str) -> Segment:
    with SessionLocal() as db:
        return db.get(Segment, sid)


def _store_review(job: Job, owner: str, segment: Segment, critique: list[dict]) -> None:
    with SessionLocal() as db:
        fence(db, job.id, owner)
        current = db.get(Segment, segment.id)
        if current.revision == segment.revision:
            current.critique = critique
            if not current.human:
                current.stage = "reviewed"
        db.commit()


def _complete_passage(job: Job, owner: str, project: Project, sid: str) -> None:
    with job_lock(job.id), SessionLocal() as db:
        current_job = fence(db, job.id, owner)
        segment = db.get(Segment, sid)
        if segment.human:
            return
        terms = enforced_glossary(db, project)
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
        segment.status = "check" if findings or segment.critique else "ok"
        segment.stage = "done"
        segment.error = ""
        current_job.checkpoint = {**current_job.checkpoint, "consecutive_failures": 0}
        current_job.outage_count = 0
        state.mark(db, job.id, state.FINISHED, sid)
        db.commit()


def _skip_failed_passage(job: Job, owner: str, sid: str, refused: bool, error: str) -> bool:
    """Records a passage given up after refusals or invalid answers; True when the job must stop."""
    with job_lock(job.id), SessionLocal() as db:
        current_job = fence(db, job.id, owner)
        current = db.get(Segment, sid)
        if current and not current.human:
            # A passage kept in the original stays so when another attempt fails; the error is recorded.
            if not current.retained_source:
                current.status = "refused" if refused else "error"
            current.error = error[:1500]
        code = "content_refusal" if refused else "invalid_response"
        db.execute(delete(Issue).where(Issue.segment_id == sid, Issue.code == code))
        db.add(
            Issue(
                project_id=job.project_id,
                segment_id=sid,
                severity="error",
                code=code,
                message=(
                    "Traduction refusée deux fois ; passage ignoré."
                    if refused
                    else f"Réponses invalides (jusqu’à {MAX_INVALID_ATTEMPTS} essais) ; passage ignoré, "
                    "à reprendre ultérieurement."
                ),
            )
        )
        current_job.checkpoint = {
            **current_job.checkpoint,
            "consecutive_failures": current_job.checkpoint.get("consecutive_failures", 0) + 1,
        }
        state.mark(db, job.id, state.FINISHED, sid)
        failures = current_job.checkpoint["consecutive_failures"]
        stop = failures >= 10 and not (job.options.get("automatic_recovery") or job.options.get("autopilot"))
        if stop:
            current_job.stop_reason = "consecutive_failures"
        db.commit()
        return stop


def _flag_passage(job: Job, owner: str, sid: str, exc: Exception) -> None:
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
        if segment.retained_source and segment.status in {"error", "refused"}:
            segment.status = "source_retained"  # the original stays in place; the error is recorded
        segment.error = str(exc)[:1500]
        db.commit()


def same_source(job_id: str, key: str) -> asyncio.Lock:
    """Identical passages of one job run one after the other: the second reuses the first's translation
    instead of racing it to the model and ending up translated differently."""
    lock = _same_source.get((job_id, key))
    if lock is None:
        lock = _same_source[(job_id, key)] = asyncio.Lock()
    return lock


_same_source: weakref.WeakValueDictionary[tuple[str, str], asyncio.Lock] = weakref.WeakValueDictionary()


async def translate_passage(job: Job, owner: str, sid: str, force: bool) -> None:
    loaded = await blocking(_passage, job, sid, force)
    if loaded is None:
        return
    project, segment, restarted = loaded
    if force or segment.translation or not segment.source_key:
        return await _translate_passage(job, owner, project, segment, force, restarted, None)
    async with same_source(job.id, segment.source_key):
        # Looked up once the identical passage before it, if any, is finished.
        reused = await blocking(remembered_translation, project, segment)
        return await _translate_passage(job, owner, project, segment, force, restarted, reused)


async def _translate_passage(job, owner, project, segment, force, restarted, reused) -> None:
    sid = segment.id
    needs = None
    if job.options.get("deep") and reused is None:
        try:
            built = await build_context(project.id, sid, "context_planner", provider_id=job.provider_id)
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
        except Exception as exc:
            if not degradable(job, exc):
                raise
            await blocking(
                note,
                job,
                owner,
                stage="translation",
                kind="context_planner",
                action="skipped",
                reason=f"Plan de contexte abandonné ({reason_of(exc)}) ; contexte standard utilisé.",
                segment_id=sid,
            )
    try:
        if not segment.translation or (force and not restarted):
            result = reused or await translation_call(project, segment, "translation", job, needs=needs)
            origin = "translation_memory" if reused else "translation"
            if not await blocking(persist, job, owner, segment, result, origin, "translated"):
                await blocking(finish_segment, job.id, owner, sid)
                return  # Human/stale version: proposal is in history, never overwrites active text.
        if project.quality != "fast" or job.operation == "review":
            segment = await blocking(_reload, sid)
            if segment.human and job.operation != "review":
                await blocking(finish_segment, job.id, owner, sid)
                return
            fused = await _improvement(
                job, owner, sid, "review_revision", lambda: review_and_revise(job, owner, project, segment, needs)
            )
            if not fused and (segment.stage == "translated" or job.operation == "review"):

                async def review_call(segment=segment):
                    built = await build_context(
                        project.id,
                        sid,
                        "translation_review",
                        extra={"CURRENT_TRANSLATION": segment.translated_units},
                        provider_id=job.provider_id,
                    )
                    return await llm.complete(
                        project_id=project.id,
                        provider_id=job.provider_id,
                        segment_id=sid,
                        operation="translation_review",
                        messages=built.messages,
                        response_model=ReviewResult,
                        context=built.inspector,
                        temperature=0.1,
                    )

                review = await _improvement(job, owner, sid, "translation_review", review_call)
                if review is not None:
                    allowed = {u["id"] for u in segment.units}
                    critique = [i.model_dump() for i in review.issues if i.unit_id in allowed]
                    await blocking(_store_review, job, owner, segment, critique)
            segment = await blocking(_reload, sid)
            if segment.human:
                await blocking(finish_segment, job.id, owner, sid)
                return
            if (
                project.quality in {"high", "maximum"}
                and segment.critique
                and segment.stage == "reviewed"
            ):
                result = await _improvement(
                    job,
                    owner,
                    sid,
                    "translation_revision",
                    lambda segment=segment: translation_call(
                        project,
                        segment,
                        "translation_revision",
                        job,
                        {"CURRENT_TRANSLATION": segment.translated_units, "REVIEW": segment.critique},
                        needs=needs,
                    ),
                )
                if result is not None:
                    await blocking(persist, job, owner, segment, result, "revision", "revised")
            if project.quality == "maximum":
                segment = await blocking(_reload, sid)
                if segment.stage != "polished":
                    result = await _improvement(
                        job,
                        owner,
                        sid,
                        "polishing",
                        lambda segment=segment: translation_call(
                            project,
                            segment,
                            "polishing",
                            job,
                            {"CURRENT_TRANSLATION": segment.translated_units},
                            needs=needs,
                        ),
                    )
                    if result is not None:
                        await blocking(persist, job, owner, segment, result, "polishing", "polished")
        await blocking(_complete_passage, job, owner, project, sid)
    except (ProviderContentRefused, InvalidResponseExhausted) as exc:
        refused = isinstance(exc, ProviderContentRefused)
        if await blocking(_skip_failed_passage, job, owner, sid, refused, str(exc)):
            raise LLMError(
                "Arrêt après 10 passages consécutifs en échec. Vérifiez le provider avant de reprendre."
            ) from None
    except JobStopped:
        raise
    except Exception as exc:
        if degradable(job, exc):
            # Autopilot: the recovery ladder takes this passage up once the book is translated.
            await blocking(_skip_failed_passage, job, owner, sid, False, reason_of(exc))
            return
        await blocking(_flag_passage, job, owner, sid, exc)
        raise


async def _improvement(job: Job, owner: str, sid: str, operation: str, call):
    """A review, revision or polish of a passage already translated. Under the autopilot, its failure
    keeps the translation it had instead of making the passage a failure (None is returned)."""
    try:
        return await call()
    except Exception as exc:
        if not degradable(job, exc):
            raise
        await blocking(
            note,
            job,
            owner,
            stage="translation",
            kind=operation,
            action="skipped",
            reason=f"Étape abandonnée ({reason_of(exc)}) ; la traduction existante est conservée.",
            segment_id=sid,
        )
        return None


def _recovery_count(job: Job) -> int:
    with SessionLocal() as db:
        return db.scalar(
            select(func.count())
            .select_from(Segment)
            .where(
                Segment.project_id == job.project_id,
                or_(
                    Segment.status.in_(("error", "refused", "blocked")),
                    (Segment.translation == "") & Segment.retained_source.is_(False),
                ),
            )
        )


def _start_recovery(job: Job, owner: str) -> None:
    with job_lock(job.id), SessionLocal() as db:
        targets = list(
            db.scalars(
                select(Segment.id)
                .where(
                    Segment.project_id == job.project_id,
                    Segment.human.is_(False),
                    Segment.validated.is_(False),
                    Segment.retained_source.is_(False),
                    or_(
                        Segment.status.in_(("error", "refused", "blocked")),
                        Segment.translation == "",
                    ),
                )
                .order_by(Segment.position)
            )
        )
        current_job = fence(db, job.id, owner)
        state.mark_all(db, job.id, state.RECOVERY_TARGET, targets)
        state.forget(db, job.id, (state.FINISHED, state.STARTED), targets)
        current_job.checkpoint = {
            **current_job.checkpoint,
            "step": "automatic_recovery",
            "automatic_recovery_started": True,
            "current": 0,
            "total": len(targets),
            "segment_id": None,
        }
        db.commit()


def _project(project_id: str) -> Project:
    with SessionLocal() as db:
        return db.get(Project, project_id)


async def after_translation(
    job: Job, owner: str, scoped: bool, continue_pipeline: bool, recovery_pass: bool
) -> None:
    if job.options.get("autopilot") and continue_pipeline and not scoped and not recovery_pass:
        from app.engines.autopilot.loop import converge

        # Recovery, consistency, final review and every decision a person used to take.
        await converge(job, owner)
        return
    recovery_count = await blocking(_recovery_count, job)
    if recovery_pass:
        await blocking(
            checkpoint,
            job.id,
            owner,
            {
                "step": "automatic_recovery",
                "automatic_recovery_completed": True,
                "automatic_recovery_remaining": recovery_count,
                "segment_id": None,
            },
        )
    elif (
        recovery_count
        and job.options.get("automatic_recovery")
        and not scoped
        and not job.checkpoint.get("automatic_recovery_started")
    ):
        await blocking(_start_recovery, job, owner)
        await translate(job, owner)
        return
    elif recovery_count and not (
        job.options.get("automatic_recovery")
        and job.checkpoint.get("automatic_recovery_completed")
    ):
        await blocking(
            checkpoint,
            job.id,
            owner,
            {
                "step": "recovery_required",
                "recovery_required": recovery_count,
                "segment_id": None,
            },
        )
        return
    project = await blocking(_project, job.project_id)
    if job.options.get("continue_pipeline") and job.options.get("segment_ids"):
        job = await blocking(restore_project_provider, job.id, owner, project.provider_id)
    if (
        project.quality in {"high", "maximum"}
        and continue_pipeline
    ):
        await consistency(job, owner)
    from app.config import settings
    from app.engines.translation.final_review import resolve_validations

    # An automation request can leave the final review out (`final_review: false` in its job options).
    if (
        settings().final_review_enabled
        and continue_pipeline
        and job.options.get("final_review", True)
    ):
        await resolve_validations(job, owner)


def _consistency_samples(job: Job) -> tuple[Project, list[dict]]:
    """Every sample still to check, with its key; a scan of the whole book, kept off the event loop."""
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
        checked = set(state.batches(db, job.id, state.CONSISTENCY))
    subjects = [
        {"source": t.source, "translation": t.translation, "locked": t.locked, "kind": "terminology"}
        for t in terms
    ]
    subjects += [{"source": e.name, "profile": e.data, "kind": "character"} for e in entities]
    sources = [s.source.casefold() for s in segments]
    samples: dict[str, dict] = {}
    scope = scoped_chapters(job)
    # Evidence windows sample occurrences throughout the novel for each term; bounded, grounded output.
    for subject in subjects:
        names = [n.casefold() for n in [subject["source"], *subject.get("profile", {}).get("aliases", [])]]
        evidence = [s for s, source in zip(segments, sources, strict=True) if any(n in source for n in names)]
        if len(evidence) < 2:
            continue
        # Sample across the entire narrative, capped at three calls per entity. Structural/locked-term
        # checks still cover every unit. Coverage is persisted and not represented as exhaustive LLM QA.
        pool, offsets = evidence, sorted({1, max(1, len(evidence) // 2), max(1, len(evidence) - 3)})
        if scope is not None:
            # A follow-up: only the new chapters are sampled, against the first occurrence.
            pool = [s for s in evidence if s.chapter_id in scope]
            offsets = sorted({0, len(pool) // 2, max(0, len(pool) - 3)}) if pool else []
        for offset in offsets:
            batch = list({s.id: s for s in [evidence[0], *pool[offset : offset + 3]]}.values())
            if len(batch) < 2:
                continue
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
            key = hashlib.sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
            if key not in checked and key not in samples:
                samples[key] = {
                    "key": key,
                    "payload": payload,
                    "occurrences": len(evidence),
                    "mapping": {u["id"]: s.id for s in batch for u in s.units},
                }
    return project, list(samples.values())


def _store_consistency(job: Job, owner: str, project_id: str, sample: dict, review: ReviewResult) -> None:
    with SessionLocal() as db:
        current_job = fence(db, job.id, owner)
        mapping = sample["mapping"]
        for issue in review.issues:
            if issue.unit_id in mapping:
                db.add(
                    Issue(
                        project_id=project_id,
                        segment_id=mapping[issue.unit_id],
                        code="global_consistency",
                        severity=issue.severity,
                        message=issue.description + " → " + issue.suggestion,
                    )
                )
        state.mark(db, job.id, state.CONSISTENCY, key=sample["key"])
        current_job.outage_count = 0
        db.commit()


async def consistency(job: Job, owner: str) -> None:
    project, samples = await blocking(_consistency_samples, job)
    system, _ = await blocking(load_prompt, "consistency_check", project.source_language, project.target_language)

    async def check(sample: dict) -> None:
        try:
            review = await llm.complete(
                project_id=project.id,
                provider_id=job.provider_id,
                operation="consistency_check",
                messages=[
                    {"role": "system", "content": system},
                    {"role": "user", "content": json.dumps(sample["payload"], ensure_ascii=False)},
                ],
                response_model=ReviewResult,
                temperature=0.1,
            )
        except Exception as exc:
            if not degradable(job, exc):
                raise
            # A sampled check only looks for improvements: its failure costs the book nothing else.
            subject = sample["payload"]["subject"]["source"]
            await blocking(
                note,
                job,
                owner,
                stage="consistency",
                kind="consistency_sample",
                action="skipped",
                reason=f"Échantillon de cohérence « {subject} » abandonné ({reason_of(exc)}).",
                mark=lambda db: state.mark(
                    db, job.id, state.CONSISTENCY, key=sample["key"], outcome="skipped"
                ),
            )
            return
        await blocking(_store_consistency, job, owner, project.id, sample, review)

    async def launch(sample: dict):
        await blocking(
            checkpoint,
            job.id,
            owner,
            {
                "step": "consistency",
                "subject": sample["payload"]["subject"]["source"],
                "coverage": "sampled",
                "occurrences": sample["occurrences"],
            },
        )
        return check(sample)

    await in_parallel(samples, job_share(job, owner), launch)
