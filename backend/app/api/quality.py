"""Quality dashboard of a volume or a series: passage scores (see app.engines.quality.score)."""

from fastapi import APIRouter, Query
from sqlalchemy import select

from app.api.series import readable_projects, series_access
from app.engines.quality.score import dashboard, repair, volumes
from app.models import Project
from app.models.quality import PassageQuality
from app.security import DB, CurrentUser, access

router = APIRouter(prefix="/api")


@router.get("/projects/{pid}/quality")
def project_quality(pid: str, user: CurrentUser, db: DB):
    project = access(db, pid, user)
    return {"project_id": project.id, **dashboard(db, [project.id])}


@router.get("/projects/{pid}/quality/passages")
def passage_scores(pid: str, user: CurrentUser, db: DB, chapter_id: str | None = Query(default=None)):
    """Score and signals of each scored passage of the volume (or of one chapter), for the editor."""
    access(db, pid, user)
    if repair(db, [pid]):
        db.commit()
    query = select(
        PassageQuality.segment_id, PassageQuality.score, PassageQuality.band, PassageQuality.signals
    ).where(PassageQuality.project_id == pid)
    if chapter_id:
        query = query.where(PassageQuality.chapter_id == chapter_id)
    return {
        sid: {"score": score, "band": band, "signals": signals}
        for sid, score, band, signals in db.execute(query)
    }


@router.get("/series/{sid}/quality")
def series_quality(sid: str, user: CurrentUser, db: DB):
    """The series' readable volumes, archived ones excepted."""
    series = series_access(db, sid, user)
    ids = list(
        db.scalars(
            readable_projects(db, user, include_archived=False)
            .where(Project.series_id == series.id)
            .with_only_columns(Project.id)
        )
    )
    return {"series_id": series.id, **dashboard(db, ids), "volumes": volumes(db, ids)}
