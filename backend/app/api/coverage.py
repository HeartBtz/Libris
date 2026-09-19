from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import and_, case, func, or_, select

from app.api.common import row
from app.api.segments import get_segment
from app.engines.memory.identities import upsert_profiles
from app.engines.memory.relations import collect
from app.engines.memory.store import remember
from app.engines.quality.score import repair as repair_scores
from app.engines.quality.score import summary as quality_summary
from app.engines.translation.versions import save_version
from app.jobs import segment_state as state
from app.jobs.queue import HELD, emit, lock_live_jobs
from app.models import Chapter, Issue, Job, Memory, Segment
from app.schemas import ChapterAnalysis
from app.security import DB, CurrentUser, access

router = APIRouter(prefix="/api")


RECOVERY_LIMIT = 500


@router.get("/projects/{pid}/completion")
def completion(pid: str, user: CurrentUser, db: DB):
    access(db, pid, user)
    if repair_scores(db, [pid]):
        db.commit()
    # Counted by the database: this report refreshes with every burst of job events, and loading each
    # passage of a long book (units, translations, critiques) to count them cost close to 1 MB a call.
    owned = Segment.project_id == pid

    def count(*conditions):
        return func.coalesce(func.sum(case((and_(*conditions), 1), else_=0)), 0)

    untranslated = Segment.translation == ""
    total, translated, missing, retained, flagged, protected = db.execute(
        select(
            func.count(),
            count(~untranslated, Segment.retained_source.is_(False)),
            count(untranslated),
            count(Segment.retained_source.is_(True)),
            count(Segment.status == "check"),
            count(or_(Segment.human.is_(True), Segment.validated.is_(True))),
        ).where(owned)
    ).one()
    issues = db.scalar(
        select(func.count()).select_from(Issue).where(Issue.project_id == pid, Issue.resolved.is_(False))
    )
    last_status = db.scalar(
        select(Job.status).where(Job.project_id == pid).order_by(Job.created_at.desc()).limit(1)
    )
    processing = db.scalar(select(Job.id).where(Job.project_id == pid, Job.status.in_(HELD)).limit(1))
    # Passages kept in the original are listed too: another provider may translate them now.
    stuck = (
        owned,
        or_(
            untranslated,
            Segment.retained_source.is_(True),
            Segment.status.in_(("error", "refused", "blocked")),
        ),
    )
    chapters = {c.id: c.title for c in db.scalars(select(Chapter).where(Chapter.project_id == pid))}
    rows = db.execute(
        select(
            Segment.id,
            Segment.position,
            Segment.chapter_id,
            Segment.status,
            Segment.error,
            func.substr(Segment.source, 1, 260),
            Segment.human,
            Segment.validated,
        )
        .where(*stuck)
        .order_by(Segment.position)
        .limit(RECOVERY_LIMIT)
    ).all()
    return {
        "total": total,
        "translated": translated,
        "missing": missing,
        "retained": retained,
        "coverage_complete": bool(total) and not missing and not retained,
        "flagged": flagged,
        "issues": issues,
        "protected": protected,
        "processing": processing is not None,
        "last_job_status": last_status or "none",
        "epubcheck": "checked_on_export",
        "recovery_total": db.scalar(select(func.count()).select_from(Segment).where(*stuck)),
        "recovery": [
            {
                "id": sid,
                "position": position,
                "chapter": chapters.get(chapter_id, ""),
                "status": status,
                "error": error,
                "excerpt": excerpt,
                "eligible": not (human or validated),
            }
            for sid, position, chapter_id, status, error, excerpt, human, validated in rows
        ],
        "quality": quality_summary(db, [pid]),
    }


class RevisionInput(BaseModel):
    revision: int = Field(ge=0)


@router.post("/segments/{sid}/retain-source")
def retain_source(sid: str, body: RevisionInput, user: CurrentUser, db: DB):
    segment, project = get_segment(db, sid, user, write=True)
    units = [{"id": u["id"], "text": u["text"]} for u in segment.units]
    live_jobs = lock_live_jobs(db, project.id)
    if not save_version(db, sid, units, "source_retained", body.revision, author_id=user.id, stage="done"):
        db.rollback()
        raise HTTPException(409, "Le passage a changé. Rechargez sa version.")
    segment.status, segment.error = "source_retained", ""
    # A resumed forced job must respect this explicit decision instead of repeating the refused call.
    for job_id in live_jobs:
        state.mark(db, job_id, state.FINISHED, sid)
    for issue in db.scalars(select(Issue).where(Issue.segment_id == sid, Issue.code == "content_refusal")):
        if not issue.message.startswith("analyze"):
            issue.resolved = True
    db.add(
        Issue(
            project_id=project.id,
            segment_id=sid,
            severity="warning",
            code="source_retained",
            message="Texte original conservé par décision humaine ; ce passage n’est pas traduit.",
        )
    )
    emit(db, project.id, segment_id=sid, status="source_retained")
    db.commit()
    db.refresh(segment)
    return row(segment)


@router.put("/segments/{sid}/analysis")
def manual_analysis(sid: str, body: ChapterAnalysis, user: CurrentUser, db: DB):
    segment, project = get_segment(db, sid, user, write=True)
    if not body.summary.strip():
        raise HTTPException(422, "Un résumé humain non vide est nécessaire.")
    content = body.model_dump() | {"human_analysis": True}
    remember(db, project, segment, content, "analysis", validated=True)
    upsert_profiles(db, project.id, [c.model_dump() for c in body.characters], segment.position, human=True)
    collect(db, project.id, [r.model_dump() for r in body.relationships], segment)
    chapter = db.get(Chapter, segment.chapter_id)
    addenda = [a for a in chapter.summary.get("human_addenda", []) if a.get("segment_id") != sid]
    chapter.summary = {
        **chapter.summary,
        "human_addenda": [
            *addenda,
            {"segment_id": sid, "position": segment.position, "summary": body.summary},
        ],
    }
    chapter.analyzed = False
    project.memory_revision += 1
    for issue in db.scalars(select(Issue).where(Issue.segment_id == sid, Issue.code == "content_refusal")):
        if issue.message.startswith("analyze"):
            issue.resolved = True
    if segment.status == "refused":
        segment.status, segment.error = "pending", ""
    emit(db, project.id, segment_id=sid, status="human_analysis_saved")
    db.commit()
    return {"saved": True, "message": "Analyse humaine enregistrée. Le travail peut être repris."}


@router.get("/projects/{pid}/coverage")
def coverage(pid: str, user: CurrentUser, db: DB):
    access(db, pid, user)
    segments = list(db.scalars(select(Segment).where(Segment.project_id == pid).order_by(Segment.position)))
    analyzed = set(
        db.scalars(select(Memory.segment_id).where(Memory.project_id == pid, Memory.kind == "analysis"))
    )
    return {
        "total": len(segments),
        "translated": sum(bool(s.translation) and not s.retained_source for s in segments),
        "retained_source": [s.id for s in segments if s.retained_source],
        "untranslated": [s.id for s in segments if not s.translation],
        "analysis_missing": [s.id for s in segments if s.id not in analyzed],
        "attention": [
            {
                "segment_id": s.id,
                "position": s.position,
                "chapter_id": s.chapter_id,
                "status": s.status,
                "error": s.error,
            }
            for s in segments
            if s.status in {"refused", "error", "source_retained"}
        ],
    }
