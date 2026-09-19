"""AI arbitration: the open points of a passage are decided by the model instead of waiting for a person.

Open points are the reviewer's critiques, the doubts (uncertainties) and the unresolved issues of the
passage (consistency remarks, check warnings, a failed accepted proposal…). One call per passage decides
all of them at once, and only passages with something to decide are sent. An accepted proposal is applied
only when the corrected text passes the same validation as a translation (units, markers, locked
glossary); when the model's answers never pass it, the proposals are rejected with that reason. A refused
or failed call leaves them open for the next round; the last round settles them on the current text.
"""

from sqlalchemy import exists, or_, select, update

from app.db import SessionLocal
from app.engines.autopilot.decisions import record
from app.engines.autopilot.degrade import degradable, note, reason_of
from app.engines.autopilot.recovery import FAILURE_CODES, reduced_messages
from app.engines.context.builder import ContextTooLarge, build_context
from app.engines.context.series import enforced_glossary
from app.engines.quality.checks import checks, locked_term_error, validate_translation
from app.engines.translation.versions import save_version
from app.jobs import segment_state as state
from app.jobs.concurrency import blocking, in_parallel, job_lock, job_share
from app.jobs.queue import checkpoint, emit, fence
from app.models import Issue, Job, JobSegmentState, Project, Provider, Segment
from app.providers.llm import InvalidResponseExhausted, llm
from app.schemas import ArbitrationDecision, ArbitrationResult, TranslationResult


def open_condition(project_id: str):
    """Translated machine passages with something left to decide."""
    unresolved = exists().where(Issue.segment_id == Segment.id, Issue.resolved.is_(False))
    return (
        Segment.project_id == project_id,
        Segment.translation != "",
        Segment.human.is_(False),
        Segment.validated.is_(False),
        Segment.retained_source.is_(False),
        or_(Segment.status == "check", unresolved),
    )


def _targets(job: Job, round_no: int) -> list[str]:
    with SessionLocal() as db:
        done = set(
            db.scalars(
                select(JobSegmentState.segment_id).where(
                    JobSegmentState.job_id == job.id,
                    JobSegmentState.step == state.ARBITRATED,
                    JobSegmentState.key == f"r{round_no}",
                )
            )
        )
        ids = db.scalars(select(Segment.id).where(*open_condition(job.project_id)).order_by(Segment.position))
        return [sid for sid in ids if sid not in done]


async def arbitrate(job: Job, owner: str, round_no: int) -> None:
    ids = await blocking(_targets, job, round_no)

    async def launch(item: tuple[int, str]):
        index, sid = item
        await blocking(
            checkpoint,
            job.id,
            owner,
            {"step": "arbitration", "current": index + 1, "total": len(ids), "segment_id": sid},
        )
        return decide_passage(job, owner, sid, round_no)

    await in_parallel(enumerate(ids), job_share(job, owner), launch)


def _inputs(job: Job, sid: str):
    with SessionLocal() as db:
        segment = db.get(Segment, sid)
        project = db.get(Project, job.project_id)
        if segment.human or segment.validated or segment.retained_source or not segment.translation:
            return None
        issues = list(
            db.scalars(
                select(Issue)
                .where(Issue.segment_id == sid, Issue.resolved.is_(False))
                .order_by(Issue.created_at)
            )
        )
        proposals = [
            {
                "id": f"c{index}",
                "kind": "critique",
                "unit_id": item.get("unit_id", ""),
                "description": item.get("description", ""),
                "suggestion": item.get("suggestion", ""),
            }
            for index, item in enumerate(segment.critique or [])
            if isinstance(item, dict)
        ]
        proposals += [
            {"id": f"u{index}", "kind": "uncertainty", "description": str(item)}
            for index, item in enumerate(segment.uncertainties or [])
        ]
        # A failure record on a passage translated since then is outdated, not a proposal.
        proposals += [
            {"id": f"i{index}", "kind": issue.code, "description": issue.message, "issue_id": issue.id}
            for index, issue in enumerate(issues)
            if issue.code not in FAILURE_CODES
        ]
        outdated = [issue.id for issue in issues if issue.code in FAILURE_CODES]
        return project, segment, enforced_glossary(db, project), proposals, outdated


def merged_units(segment: Segment, changed: list) -> list[dict]:
    replacement = {unit.id: unit.text for unit in changed}
    return [
        {"id": unit["id"], "text": replacement.get(unit["id"], unit["text"])}
        for unit in segment.translated_units
    ]


def arbitration_validator(project: Project, segment: Segment, glossary: list, proposals: list[dict]):
    known = {proposal["id"] for proposal in proposals}
    allowed = {unit["id"] for unit in segment.units}

    def validate(result: ArbitrationResult) -> None:
        if any(decision.id not in known for decision in result.decisions):
            raise ValueError("Décision sur une proposition inconnue.")
        ids = [unit.id for unit in result.units]
        if len(ids) != len(set(ids)) or any(value not in allowed for value in ids):
            raise ValueError("Paragraphes inconnus ou répétés dans les unités corrigées.")
        if result.units and not any(decision.accept for decision in result.decisions):
            raise ValueError("Texte modifié alors qu’aucune proposition n’est retenue.")
        if result.units:
            units = merged_units(segment, result.units)
            validate_translation(segment.units, TranslationResult(units=units))
            findings = checks(
                segment.units, units, glossary, project.source_language, project.target_language
            )
            if error := locked_term_error(findings):
                raise ValueError(error)

    return validate


async def decide_passage(job: Job, owner: str, sid: str, round_no: int) -> None:
    loaded = await blocking(_inputs, job, sid)
    if loaded is None:
        return
    project, segment, glossary, proposals, outdated = loaded
    verdict, failure = None, ""
    if proposals:
        # Only what the model needs to decide: the ids it answers with, never internal row ids.
        shown = [{k: v for k, v in proposal.items() if k != "issue_id"} for proposal in proposals]
        extra = {"CURRENT_TRANSLATION": segment.translated_units, "PROPOSALS": shown}
        validate = arbitration_validator(project, segment, glossary, proposals)
        try:
            try:
                built = await build_context(
                    project.id, sid, "autopilot_arbitration", extra=extra, provider_id=job.provider_id
                )
                messages, context = built.messages, built.inspector
            except ContextTooLarge:
                messages = await blocking(
                    reduced_messages, project, segment, "autopilot_arbitration", glossary, extra
                )
                context = {"reduced_context": True}
            verdict = await llm.complete(
                project_id=project.id,
                provider_id=job.provider_id,
                segment_id=sid,
                operation="autopilot_arbitration",
                messages=messages,
                response_model=ArbitrationResult,
                context=context,
                validator=validate,
                temperature=0.1,
            )
        except Exception as exc:
            if not degradable(job, exc):
                raise
            if isinstance(exc, InvalidResponseExhausted | ValueError):
                # The corrections never passed validation: the translation stays as it is.
                why = f"Correction rejetée : elle ne passe pas la validation ({reason_of(exc)[:300]})."
                verdict = ArbitrationResult(
                    decisions=[ArbitrationDecision(id=p["id"], accept=False, reason=why) for p in proposals]
                )
            else:
                failure = reason_of(exc)
    await blocking(
        _apply, job, owner, project, segment, glossary, proposals, outdated, verdict, failure, round_no
    )


def _apply(job, owner, project, segment, glossary, proposals, outdated, verdict, failure, round_no) -> None:
    sid = segment.id
    with job_lock(job.id), SessionLocal() as db:
        current_job = fence(db, job.id, owner)
        provider = db.get(Provider, current_job.provider_id) if current_job.provider_id else None
        current = db.scalar(select(Segment).where(Segment.id == sid).with_for_update())
        if current.revision != segment.revision or current.human or current.validated:
            state.mark(db, job.id, state.ARBITRATED, sid, key=f"r{round_no}", outcome="protected")
            db.commit()
            return
        if failure:
            # Proposals stay open: the next round tries again, the last one settles them.
            state.mark(db, job.id, state.ARBITRATED, sid, key=f"r{round_no}", outcome="failed")
            record(
                db,
                job.project_id,
                job_id=job.id,
                segment_id=sid,
                stage="arbitration",
                kind="passage",
                action="deferred",
                reason=f"Arbitrage impossible ({failure}) ; nouvel essai au tour suivant.",
                provider=provider,
            )
            db.commit()
            return
        decisions = {decision.id: decision for decision in (verdict.decisions if verdict else [])}
        accepted = [p for p in proposals if p["id"] in decisions and decisions[p["id"]].accept]
        applied = False
        if verdict and verdict.units:
            units = merged_units(current, verdict.units)
            applied = save_version(db, sid, units, "arbitration", segment.revision, stage="done")
            if not applied:
                db.rollback()
                fence(db, job.id, owner)
                current = db.get(Segment, sid)
            else:
                db.refresh(current)
        for proposal in proposals:
            decision = decisions.get(proposal["id"])
            if decision is None:
                action, why = "rejected", "Aucune décision rendue : traduction actuelle conservée."
            elif decision.accept:
                action = "applied" if applied else "accepted"
                why = decision.reason or "Proposition retenue."
                if not applied and verdict.units:
                    action, why = "rejected", "Correction non applicable : le passage a changé entre-temps."
            else:
                action, why = "rejected", decision.reason or "Proposition écartée."
            label = proposal.get("suggestion") or proposal["description"]
            record(
                db,
                job.project_id,
                job_id=job.id,
                segment_id=sid,
                stage="arbitration",
                kind=proposal["kind"],
                action=action,
                reason=f"{label[:600]} → {why}",
                provider=provider,
            )
        decided = [proposal["issue_id"] for proposal in proposals if "issue_id" in proposal]
        if decided or outdated:
            db.execute(update(Issue).where(Issue.id.in_([*decided, *outdated])).values(resolved=True))
        current.critique, current.uncertainties = [], []
        # Checks run again on the new text: a warning already decided is not raised a second time.
        known = {
            (issue.code, issue.message) for issue in db.scalars(select(Issue).where(Issue.segment_id == sid))
        }
        findings = checks(
            current.units,
            current.translated_units,
            glossary,
            project.source_language,
            project.target_language,
        )
        for finding in findings:
            if (finding["code"], finding["message"]) not in known:
                db.add(Issue(project_id=job.project_id, segment_id=sid, **finding))
        db.flush()
        remaining = db.scalar(
            select(Issue.id).where(Issue.segment_id == sid, Issue.resolved.is_(False)).limit(1)
        )
        current.status = "check" if remaining else "ok"
        state.mark(
            db, job.id, state.ARBITRATED, sid, key=f"r{round_no}", outcome="applied" if applied else "decided"
        )
        emit(
            db,
            job.project_id,
            job_id=job.id,
            segment_id=sid,
            status="arbitration",
            applied=applied,
            accepted=len(accepted),
            rejected=len(proposals) - len(accepted),
        )
        db.commit()


def settle_open(job: Job, owner: str, reason: str) -> int:
    """After the last round: what is still open is closed on the current translation, with the reason."""
    with job_lock(job.id), SessionLocal() as db:
        current_job = fence(db, job.id, owner)
        provider = db.get(Provider, current_job.provider_id) if current_job.provider_id else None
        segments = list(db.scalars(select(Segment).where(*open_condition(job.project_id))))
        for segment in segments:
            points = [
                *(item.get("description", "") for item in segment.critique or [] if isinstance(item, dict)),
                *(str(item) for item in segment.uncertainties or []),
                *db.scalars(
                    select(Issue.message).where(Issue.segment_id == segment.id, Issue.resolved.is_(False))
                ),
            ]
            db.execute(update(Issue).where(Issue.segment_id == segment.id).values(resolved=True))
            segment.critique, segment.uncertainties, segment.status = [], [], "ok"
            record(
                db,
                job.project_id,
                job_id=job.id,
                segment_id=segment.id,
                stage="settle",
                kind="open_points",
                action="kept_translation",
                reason=f"{reason} Points clos : " + " | ".join(p[:200] for p in points if p)[:3000],
                provider=provider,
            )
        db.commit()
        return len(segments)


def note_round(job: Job, owner: str, round_no: int, summary: str) -> None:
    note(job, owner, stage="convergence", kind="round", action=f"round_{round_no}", reason=summary)
