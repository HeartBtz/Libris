"""Bounded final review: assess, optionally revise once, verify, then apply atomically."""

import httpx
from sqlalchemy import delete, select

from app.db import SessionLocal
from app.engines.context.builder import build_context
from app.engines.quality.checks import checks, validate_translation
from app.engines.translation.versions import save_version
from app.jobs.queue import checkpoint, emit, fence
from app.models import Glossary, Issue, Job, Project, Segment
from app.providers.llm import LLMError, ProviderAuthenticationRequired, ProviderUnavailable, llm
from app.providers.search import search_config
from app.schemas import FinalReviewResult

CHECK_CODES = ("unchanged", "length", "repetition", "locked_term")


async def web_evidence(queries: list[str]) -> dict:
    config = search_config()
    url = config["base_url"] if config["enabled"] else ""
    evidence: dict = {"enabled": bool(url), "queries": [], "sources": [], "unavailable": False}
    if not url:
        return evidence
    async with httpx.AsyncClient(timeout=15, follow_redirects=False) as client:
        for query in queries[:2]:
            query = query.strip()[:200]
            if not query:
                continue
            evidence["queries"].append(query)
            try:
                response = await client.get(
                    url.rstrip("/") + "/search", params={"q": query, "format": "json"}
                )
                response.raise_for_status()
                for item in response.json().get("results", [])[:3]:
                    link = str(item.get("url", ""))
                    if link.startswith(("https://", "http://")):
                        evidence["sources"].append(
                            {
                                "url": link[:1000],
                                "title": str(item.get("title", ""))[:200],
                                "snippet": str(item.get("content", ""))[:1500],
                            }
                        )
            except (httpx.HTTPError, ValueError, AttributeError, TypeError):
                evidence["unavailable"] = True
    return evidence


async def resolve_validations(job: Job, owner: str) -> None:
    from app.engines.translation.pipeline import translation_call

    with SessionLocal() as db:
        ids = list(
            db.scalars(
                select(Segment.id)
                .where(
                    Segment.project_id == job.project_id,
                    Segment.status == "check",
                    Segment.translation != "",
                    Segment.human.is_(False),
                    Segment.validated.is_(False),
                    Segment.retained_source.is_(False),
                )
                .order_by(Segment.position)
            )
        )
    stored = checkpoint(job.id, owner)
    if "final_review_targets" in stored.checkpoint:
        ids = stored.checkpoint["final_review_targets"]
    else:
        checkpoint(job.id, owner, {"final_review_targets": ids})
    for index, sid in enumerate(ids):
        current_job = checkpoint(
            job.id,
            owner,
            {"step": "final_review", "current": index + 1, "total": len(ids), "segment_id": sid},
        )
        if sid in current_job.checkpoint.get("final_review_done", []):
            continue
        with SessionLocal() as db:
            segment = db.get(Segment, sid)
            project = db.get(Project, job.project_id)
            if segment.human or segment.validated or segment.retained_source:
                current_job = fence(db, job.id, owner)
                outcomes = dict(current_job.checkpoint.get("final_review_outcomes", {}))
                outcomes[sid] = {"outcome": "protected", "revised": False}
                current_job.checkpoint = {
                    **current_job.checkpoint,
                    "final_review_done": [
                        *current_job.checkpoint.get("final_review_done", []),
                        sid,
                    ],
                    "final_review_outcomes": outcomes,
                }
                db.commit()
                continue
            terms = list(
                db.scalars(
                    select(Glossary).where(Glossary.project_id == project.id, Glossary.accepted.is_(True))
                )
            )
            old_issues = list(
                db.scalars(select(Issue).where(Issue.segment_id == sid, Issue.resolved.is_(False)))
            )
            technical = [{"code": issue.code, "message": issue.message} for issue in old_issues]
        evidence = {"enabled": False, "sources": []}

        async def assess(units, uncertainties):
            built = await build_context(
                project.id,
                sid,
                "final_review",
                extra={
                    "CURRENT_TRANSLATION": units,
                    "PREVIOUS_CRITIQUES": segment.critique,
                    "UNCERTAINTIES": uncertainties,
                    "TECHNICAL_CHECKS": technical,
                    "WEB_EVIDENCE_UNTRUSTED": evidence,
                },
            )

            def validate(result):
                allowed = {unit["id"] for unit in segment.units}
                if any(issue.unit_id not in allowed for issue in result.issues):
                    raise ValueError("Final review references an unknown unit.")

            return await llm.complete(
                project_id=project.id,
                provider_id=job.provider_id,
                segment_id=sid,
                operation="final_review",
                messages=built.messages,
                response_model=FinalReviewResult,
                context=built.inspector,
                validator=validate,
                temperature=0.1,
            )

        try:
            verdict = await assess(segment.translated_units, segment.uncertainties)
            candidate = None
            initial_findings = checks(
                segment.units,
                segment.translated_units,
                terms,
                project.source_language,
                project.target_language,
            )
            if verdict.issues or verdict.uncertainties or initial_findings:
                evidence = await web_evidence(verdict.search_queries)
                revised = await translation_call(
                    project,
                    segment,
                    "translation_revision",
                    job,
                    {
                        "CURRENT_TRANSLATION": segment.translated_units,
                        "REVIEW": verdict.model_dump(),
                        "TECHNICAL_CHECKS": technical,
                        "WEB_EVIDENCE_UNTRUSTED": evidence,
                    },
                )
                validate_translation(segment.units, revised)
                units = [unit.model_dump() for unit in revised.units]
                findings = checks(
                    segment.units, units, terms, project.source_language, project.target_language
                )
                verified = await assess(units, revised.uncertainties)
                if not verified.issues and not verified.uncertainties and not findings:
                    candidate, verdict = units, verified
            with SessionLocal() as db:
                current_job = fence(db, job.id, owner)
                current = db.scalar(select(Segment).where(Segment.id == sid).with_for_update())
                if current.revision != segment.revision or current.human or current.validated:
                    outcomes = dict(current_job.checkpoint.get("final_review_outcomes", {}))
                    outcomes[sid] = {"outcome": "protected", "revised": False}
                    current_job.checkpoint = {
                        **current_job.checkpoint,
                        "final_review_done": [
                            *current_job.checkpoint.get("final_review_done", []),
                            sid,
                        ],
                        "final_review_outcomes": outcomes,
                    }
                    db.commit()
                    continue
                if candidate is not None:
                    if not save_version(db, sid, candidate, "final_review", segment.revision, stage="done"):
                        db.rollback()
                        continue
                    db.refresh(current)
                findings = checks(
                    current.units,
                    current.translated_units,
                    terms,
                    project.source_language,
                    project.target_language,
                )
                # Only recomputed deterministic checks may be removed. Global/other issues remain.
                db.execute(delete(Issue).where(Issue.segment_id == sid, Issue.code.in_(CHECK_CODES)))
                for finding in findings:
                    db.add(Issue(project_id=project.id, segment_id=sid, **finding))
                remaining = db.scalar(
                    select(Issue.id).where(Issue.segment_id == sid, Issue.resolved.is_(False)).limit(1)
                )
                current.critique = [issue.model_dump() for issue in verdict.issues]
                current.uncertainties = verdict.uncertainties
                current.status = "check" if remaining or verdict.issues else "ok"
                outcome = "resolved" if current.status == "ok" else "needs_human"
                outcomes = dict(current_job.checkpoint.get("final_review_outcomes", {}))
                outcomes[sid] = {
                    "outcome": outcome,
                    "revised": candidate is not None,
                }
                current_job.checkpoint = {
                    **current_job.checkpoint,
                    "final_review_done": [*current_job.checkpoint.get("final_review_done", []), sid],
                    "final_review_outcomes": outcomes,
                }
                emit(
                    db,
                    project.id,
                    job_id=job.id,
                    segment_id=sid,
                    status="final_review",
                    outcome=outcome,
                    explanation=verdict.explanation,
                    evidence=evidence,
                    revised=candidate is not None,
                )
                db.commit()
        except (ProviderUnavailable, ProviderAuthenticationRequired):
            raise
        except (LLMError, ValueError) as exc:
            # A review refusal or invalid response must not stop the entire book or erase its translation.
            with SessionLocal() as db:
                current_job = fence(db, job.id, owner)
                outcomes = dict(current_job.checkpoint.get("final_review_outcomes", {}))
                outcomes[sid] = {
                    "outcome": "failed",
                    "revised": False,
                    "reason": type(exc).__name__,
                }
                current_job.checkpoint = {
                    **current_job.checkpoint,
                    "final_review_done": [*current_job.checkpoint.get("final_review_done", []), sid],
                    "final_review_outcomes": outcomes,
                }
                emit(
                    db,
                    project.id,
                    job_id=job.id,
                    segment_id=sid,
                    status="final_review",
                    outcome="needs_human",
                    reason=type(exc).__name__,
                )
                db.commit()
