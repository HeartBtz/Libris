import re

from fastapi import APIRouter, HTTPException, Query
from sqlalchemy import Text, cast, select

from app.api.common import row
from app.engines.context.builder import build_context
from app.engines.translation.versions import save_version
from app.jobs import segment_state as state
from app.jobs.queue import HELD, emit, enqueue, lock_live_jobs
from app.models import Issue, Job, RequestLog, Segment, TranslationVersion
from app.providers.llm import llm
from app.schemas import (
    AcceptCritiqueInput,
    AskInput,
    AskResult,
    EditInput,
    InstructionInput,
)
from app.security import DB, CurrentUser, access

router = APIRouter(prefix="/api")


def explicit_replacement(target: str, suggestion: str) -> str | None:
    match = re.search(
        r"(?:remplacer|corriger)\s+[«\"](?P<old>.+?)[»\"]\s+(?:par|avec)\s+[«\"](?P<new>.+?)[»\"]",
        suggestion,
        flags=re.IGNORECASE | re.DOTALL,
    )
    if not match:
        return None
    old, new = match["old"].strip(), match["new"].strip()
    if not old or old not in target:
        return None
    return target.replace(old, new, 1)


def queue_critique(db, project, segment, critique, author_id):
    item = {
        "segment_id": segment.id,
        "unit_id": str(critique.get("unit_id", "")),
        "suggestion": str(critique.get("suggestion", "")),
        "author_id": author_id,
    }
    # Job row first, then the passage's critique: the worker's order (see lock_live_jobs).
    lock_live_jobs(db, project.id)
    active = db.scalar(
        select(Job)
        .where(Job.project_id == project.id, Job.status.in_(HELD))
        .order_by(Job.created_at.desc())
        .limit(1)
    )
    if active and active.operation != "accept_critiques":
        raise HTTPException(409, "Un autre travail occupe ce livre ; réessayez une fois terminé.")
    job = active or enqueue(db, project, "accept_critiques", {})
    queued = list(job.options.get("critique_acceptances", []))
    if item not in queued:
        queued.append(item)
        job.options = {**job.options, "critique_acceptances": queued}
    segment.critique = [
        {**value, "queued": True} if value == critique else value for value in segment.critique
    ]
    return job


def get_segment(db, sid, user, write=False):
    segment = db.get(Segment, sid)
    if not segment:
        raise HTTPException(404, "Passage introuvable.")
    project = access(db, segment.project_id, user, write=write)
    return segment, project


@router.get("/projects/{project_id}/segments")
def segments(
    project_id: str,
    user: CurrentUser,
    db: DB,
    chapter_id: str | None = None,
    status: str | None = None,
    offset: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=250),
):
    access(db, project_id, user)
    query = select(Segment).where(Segment.project_id == project_id).order_by(Segment.position)
    if chapter_id:
        query = query.where(Segment.chapter_id == chapter_id)
    if status == "uncertain":
        query = query.where(cast(Segment.uncertainties, Text) != "[]")
    elif status:
        query = query.where(Segment.status == status)
    return [row(s) for s in db.scalars(query.offset(offset).limit(limit))]


@router.get("/segments/{sid}")
def get(sid: str, user: CurrentUser, db: DB):
    segment, _ = get_segment(db, sid, user)
    return row(segment)


@router.put("/segments/{sid}")
def edit(sid: str, body: EditInput, user: CurrentUser, db: DB):
    segment, _ = get_segment(db, sid, user, write=True)
    if segment.revision != body.revision:
        raise HTTPException(409, "Le passage a été modifié. Rechargez sa version avant d’enregistrer.")
    live_jobs = lock_live_jobs(db, segment.project_id, analysis=False)
    if not save_version(
        db,
        sid,
        [u.model_dump() for u in body.units],
        "human",
        body.revision,
        author_id=user.id,
        validated=body.validated,
        stage="done",
    ):
        db.rollback()
        raise HTTPException(409, "Modification concurrente détectée.")
    emit(db, segment.project_id, segment_id=sid, status="human_saved")
    for issue in db.scalars(select(Issue).where(Issue.segment_id == sid, Issue.code == "content_refusal")):
        if not issue.message.startswith("analyze"):
            issue.resolved = True
    for job_id in live_jobs:
        state.mark(db, job_id, state.FINISHED, sid)
    db.commit()
    db.refresh(segment)
    return row(segment)


@router.post("/segments/{sid}/critique/{index}/accept")
async def accept_critique(sid: str, index: int, body: AcceptCritiqueInput, user: CurrentUser, db: DB):
    segment, project = get_segment(db, sid, user, write=True)
    if segment.revision != body.revision:
        raise HTTPException(409, "Le passage a été modifié. Rechargez-le avant d’accepter la proposition.")
    if index < 0 or index >= len(segment.critique):
        raise HTTPException(404, "Proposition IA introuvable.")
    critique = segment.critique[index]
    unit_id = str(critique.get("unit_id", ""))
    suggestion = str(critique.get("suggestion", "")).strip()
    if not suggestion:
        raise HTTPException(409, "Cette remarque IA ne contient pas de remplacement applicable.")
    explicit_text = bool(
        re.match(r"^(?:écrire(?:\s+(?:plutôt|par exemple))?|proposition)\s*:\s*", suggestion, re.I)
        or (suggestion.startswith("«") and suggestion.endswith("»"))
        or (len(suggestion) >= 2 and suggestion[0] == suggestion[-1] in {'"', "'"})
    )
    suggestion = re.sub(
        r"^(?:écrire(?:\s+(?:plutôt|par exemple))?|proposition)\s*:\s*",
        "",
        suggestion,
        flags=re.IGNORECASE,
    ).strip()
    if suggestion.startswith("«") and suggestion.endswith("»"):
        suggestion = suggestion[1:-1].strip()
    elif len(suggestion) >= 2 and suggestion[0] == suggestion[-1] in {'"', "'"}:
        suggestion = suggestion[1:-1].strip()

    units = [dict(unit) for unit in segment.translated_units]
    target = next((unit for unit in units if unit["id"] == unit_id), None)
    if not target:
        raise HTTPException(409, "L’unité visée par la proposition n’existe plus.")
    replacement = explicit_replacement(target["text"], suggestion)
    if replacement:
        suggestion = replacement
        explicit_text = True
    current_codes = re.findall(r"⟦[^⟧]+⟧", target["text"])
    suggestion_codes = re.findall(r"⟦[^⟧]+⟧", suggestion)
    if current_codes and not suggestion_codes:
        stripped = target["text"].strip()
        if (
            len(current_codes) == 2
            and stripped.startswith(current_codes[0])
            and stripped.endswith(current_codes[1])
        ):
            suggestion = f"{current_codes[0]}{suggestion}{current_codes[1]}"
        else:
            suggestion_codes = []
    if re.findall(r"⟦[^⟧]+⟧", suggestion) != current_codes or not explicit_text:
        # Provider calls run in the worker so several proposals can be queued without blocking.
        job = queue_critique(db, project, segment, critique, user.id)
        db.commit()
        return {"queued": True, "job_id": job.id}
    target["text"] = suggestion
    if not save_version(
        db,
        sid,
        units,
        "human",
        body.revision,
        author_id=user.id,
        validated=False,
        stage="done",
    ):
        db.rollback()
        raise HTTPException(409, "Modification concurrente détectée.")
    db.refresh(segment)
    segment.critique = [item for item_index, item in enumerate(segment.critique) if item_index != index]
    unresolved_issue = db.scalar(
        select(Issue.id).where(Issue.segment_id == sid, Issue.resolved.is_(False)).limit(1)
    )
    segment.status = "check" if segment.critique or unresolved_issue else "ok"
    emit(db, segment.project_id, segment_id=sid, status="ai_suggestion_accepted")
    db.commit()
    db.refresh(segment)
    return row(segment)


@router.post("/projects/{project_id}/critiques/accept-all")
def accept_all_critiques(project_id: str, user: CurrentUser, db: DB):
    project = access(db, project_id, user, write=True)
    queued = 0
    for segment in db.scalars(select(Segment).where(Segment.project_id == project.id, Segment.status == "check")):
        for critique in segment.critique:
            if critique.get("queued") or not critique.get("suggestion"):
                continue
            queue_critique(db, project, segment, critique, user.id)
            queued += 1
    db.commit()
    return {"queued": queued}


@router.post("/segments/{sid}/critique/{index}/reject")
def reject_critique(sid: str, index: int, body: AcceptCritiqueInput, user: CurrentUser, db: DB):
    segment, _ = get_segment(db, sid, user, write=True)
    if segment.revision != body.revision:
        raise HTTPException(409, "Le passage a été modifié. Rechargez-le avant de refuser la proposition.")
    if index < 0 or index >= len(segment.critique):
        raise HTTPException(404, "Proposition IA introuvable.")
    if not segment.translated_units:
        raise HTTPException(409, "Aucune traduction à conserver pour ce passage.")
    if not save_version(
        db,
        sid,
        [dict(unit) for unit in segment.translated_units],
        "human",
        body.revision,
        author_id=user.id,
        validated=False,
        stage="done",
    ):
        db.rollback()
        raise HTTPException(409, "Modification concurrente détectée.")
    db.refresh(segment)
    segment.critique = [item for item_index, item in enumerate(segment.critique) if item_index != index]
    unresolved_issue = db.scalar(
        select(Issue.id).where(Issue.segment_id == sid, Issue.resolved.is_(False)).limit(1)
    )
    segment.status = "check" if segment.critique or unresolved_issue else "ok"
    emit(db, segment.project_id, segment_id=sid, status="ai_suggestion_rejected")
    db.commit()
    db.refresh(segment)
    return row(segment)


@router.put("/segments/{sid}/instructions")
def instruction(sid: str, body: InstructionInput, user: CurrentUser, db: DB):
    segment, project = get_segment(db, sid, user, write=True)
    segment.instructions = body.instructions
    project.memory_revision += 1
    db.commit()
    return row(segment)


@router.get("/segments/{sid}/versions")
def versions(sid: str, user: CurrentUser, db: DB):
    get_segment(db, sid, user)
    return [
        row(v)
        for v in db.scalars(
            select(TranslationVersion)
            .where(TranslationVersion.segment_id == sid)
            .order_by(TranslationVersion.created_at.desc())
        )
    ]


@router.post("/segments/{sid}/versions/{version_id}/restore")
def restore(sid: str, version_id: str, body: EditInput, user: CurrentUser, db: DB):
    segment, _ = get_segment(db, sid, user, write=True)
    version = db.get(TranslationVersion, version_id)
    if not version or version.segment_id != sid:
        raise HTTPException(404, "Version introuvable.")
    if not save_version(
        db,
        sid,
        version.units,
        "source_retained" if version.origin == "source_retained" else "restore",
        body.revision,
        author_id=user.id,
        validated=body.validated,
        stage="done",
    ):
        db.rollback()
        raise HTTPException(409, "Modification concurrente détectée.")
    db.commit()
    db.refresh(segment)
    return row(segment)


@router.get("/segments/{sid}/requests")
def requests(sid: str, user: CurrentUser, db: DB):
    get_segment(db, sid, user)
    return [
        row(r)
        for r in db.scalars(
            select(RequestLog)
            .where(RequestLog.segment_id == sid)
            .order_by(RequestLog.created_at.desc())
            .limit(30)
        )
    ]


@router.post("/segments/{sid}/ask")
async def ask(sid: str, body: AskInput, user: CurrentUser, db: DB):
    segment, project = get_segment(db, sid, user, write=True)
    built = await build_context(
        project.id,
        sid,
        "ask",
        extra={
            "CURRENT_TRANSLATION": segment.translated_units,
            "USER_QUESTION": body.question,
            "SELECTION": body.selection,
        },
    )
    result = await llm.complete(
        project_id=project.id,
        provider_id=project.provider_id,
        segment_id=sid,
        operation="ask",
        messages=built.messages,
        response_model=AskResult,
        context=built.inspector,
    )
    return result


@router.get("/projects/{project_id}/issues")
def issues(project_id: str, user: CurrentUser, db: DB):
    access(db, project_id, user)
    return [
        row(i)
        for i in db.scalars(
            select(Issue).where(Issue.project_id == project_id).order_by(Issue.created_at.desc()).limit(1000)
        )
    ]


@router.post("/issues/{issue_id}/resolve")
def resolve(issue_id: str, user: CurrentUser, db: DB):
    issue = db.get(Issue, issue_id)
    if not issue:
        raise HTTPException(404, "Problème introuvable.")
    access(db, issue.project_id, user, write=True)
    issue.resolved = True
    segment = db.get(Segment, issue.segment_id) if issue.segment_id else None
    if segment and segment.status == "check":
        # Same rule as accepting or rejecting a suggestion: nothing left to review means "ok".
        db.flush()
        pending = db.scalar(
            select(Issue.id).where(Issue.segment_id == segment.id, Issue.resolved.is_(False)).limit(1)
        )
        segment.status = "check" if segment.critique or pending else "ok"
        emit(db, issue.project_id, segment_id=segment.id, status="issue_resolved")
    db.commit()
    return row(issue)
