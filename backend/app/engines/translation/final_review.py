"""Bounded final review: assess, optionally revise once, verify, then apply atomically."""

import json

import httpx
from sqlalchemy import delete, select

from app.db import SessionLocal
from app.engines.context.builder import build_context
from app.engines.quality.checks import checks, validate_translation
from app.engines.translation.versions import save_version
from app.jobs import segment_state as state
from app.jobs.concurrency import blocking, book_share, in_parallel, job_lock
from app.jobs.queue import checkpoint, emit, fence
from app.models import Glossary, Issue, Job, Project, Segment
from app.providers.llm import LLMError, ProviderAuthenticationRequired, ProviderUnavailable, llm
from app.providers.search import search_config
from app.schemas import FinalReviewResult

CHECK_CODES = ("unchanged", "length", "repetition", "locked_term")
SEARCH_RESPONSE_LIMIT = 2 * 1024**2


async def bounded_body(response: httpx.Response, limit: int) -> bytes:
    declared = response.headers.get("content-length", "")
    if declared.isdigit() and int(declared) > limit:
        raise ValueError("Réponse de recherche trop volumineuse.")
    body = bytearray()
    async for chunk in response.aiter_bytes():
        body += chunk
        if len(body) > limit:
            raise ValueError("Réponse de recherche trop volumineuse.")
    return bytes(body)


async def web_evidence(queries: list[str]) -> dict:
    config = search_config()
    url = config["base_url"] if config["enabled"] else ""
    evidence: dict = {"enabled": bool(url), "queries": [], "sources": [], "unavailable": False}
    if not url:
        return evidence
    # Proxy variables of the server must not reroute the queries, and an oversized answer is not read.
    async with httpx.AsyncClient(timeout=15, follow_redirects=False, trust_env=False) as client:
        for query in queries[:2]:
            query = query.strip()[:200]
            if not query:
                continue
            evidence["queries"].append(query)
            try:
                async with client.stream(
                    "GET", url.rstrip("/") + "/search", params={"q": query, "format": "json"}
                ) as response:
                    response.raise_for_status()
                    body = await bounded_body(response, SEARCH_RESPONSE_LIMIT)
                for item in json.loads(body).get("results", [])[:3]:
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


def _review_targets(job: Job, owner: str) -> tuple[list[str], set[str]]:
    with SessionLocal() as db:
        # The targets are frozen on the first run: a resumed review must not grow or shrink its scope.
        if "review_targets" in job.checkpoint:
            ids = state.in_book_order(db, job.id, state.REVIEW_TARGET)
        else:
            conditions = [
                Segment.project_id == job.project_id,
                Segment.translation != "",
                Segment.human.is_(False),
                Segment.validated.is_(False),
                Segment.retained_source.is_(False),
            ]
            if job.options.get("full_review"):
                conditions.append(~Segment.status.in_(("error", "refused", "blocked")))
            else:
                conditions.append(Segment.status == "check")
            ids = list(db.scalars(select(Segment.id).where(*conditions).order_by(Segment.position)))
            with job_lock(job.id):
                current = fence(db, job.id, owner)
                state.mark_all(db, job.id, state.REVIEW_TARGET, ids)
                current.checkpoint = {**current.checkpoint, "review_targets": len(ids)}
                db.commit()
        return ids, state.marked(db, job.id, state.REVIEWED)


async def resolve_validations(job: Job, owner: str) -> None:
    job = await blocking(checkpoint, job.id, owner)
    ids, reviewed = await blocking(_review_targets, job, owner)

    async def launch(item: tuple[int, str]):
        index, sid = item
        if sid in reviewed:  # resumed job: no checkpoint write and no event for what is already done
            return None
        await blocking(
            checkpoint,
            job.id,
            owner,
            {"step": "final_review", "current": index + 1, "total": len(ids), "segment_id": sid},
        )
        return review_passage(job, owner, sid)

    await in_parallel(enumerate(ids), book_share(job.provider_id), launch)


def _review_inputs(job: Job, owner: str, sid: str):
    with SessionLocal() as db:
        if state.is_marked(db, job.id, state.REVIEWED, sid):
            return None
        segment = db.get(Segment, sid)
        project = db.get(Project, job.project_id)
        if segment.human or segment.validated or segment.retained_source:
            fence(db, job.id, owner)
            state.mark(db, job.id, state.REVIEWED, sid, outcome="protected")
            db.commit()
            return None
        terms = list(
            db.scalars(select(Glossary).where(Glossary.project_id == project.id, Glossary.accepted.is_(True)))
        )
        old_issues = list(db.scalars(select(Issue).where(Issue.segment_id == sid, Issue.resolved.is_(False))))
        technical = [{"code": issue.code, "message": issue.message} for issue in old_issues]
        return project, segment, terms, technical


def _apply_review(job, owner, project, segment, terms, verdict, candidate, evidence) -> None:
    sid = segment.id
    with SessionLocal() as db:
        fence(db, job.id, owner)
        current = db.scalar(select(Segment).where(Segment.id == sid).with_for_update())
        if current.revision != segment.revision or current.human or current.validated:
            state.mark(db, job.id, state.REVIEWED, sid, outcome="protected")
            db.commit()
            return
        if candidate is not None:
            if not save_version(db, sid, candidate, "final_review", segment.revision, stage="done"):
                db.rollback()
                return
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
        state.mark(db, job.id, state.REVIEWED, sid, outcome=outcome, data={"revised": candidate is not None})
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


def _review_failed(job: Job, owner: str, sid: str, reason: str) -> None:
    with SessionLocal() as db:
        fence(db, job.id, owner)
        state.mark(db, job.id, state.REVIEWED, sid, outcome="failed", data={"reason": reason})
        emit(
            db,
            job.project_id,
            job_id=job.id,
            segment_id=sid,
            status="final_review",
            outcome="needs_human",
            reason=reason,
        )
        db.commit()


async def review_passage(job: Job, owner: str, sid: str) -> None:
    from app.engines.translation.pipeline import translation_call

    loaded = await blocking(_review_inputs, job, owner, sid)
    if loaded is None:
        return
    project, segment, terms, technical = loaded
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
            provider_id=job.provider_id,
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
            findings = checks(segment.units, units, terms, project.source_language, project.target_language)
            verified = await assess(units, revised.uncertainties)
            if not verified.issues and not verified.uncertainties and not findings:
                candidate, verdict = units, verified
        await blocking(_apply_review, job, owner, project, segment, terms, verdict, candidate, evidence)
    except (ProviderUnavailable, ProviderAuthenticationRequired):
        raise
    except (LLMError, ValueError) as exc:
        # A review refusal or invalid response must not stop the entire book or erase its translation.
        await blocking(_review_failed, job, owner, sid, type(exc).__name__)
