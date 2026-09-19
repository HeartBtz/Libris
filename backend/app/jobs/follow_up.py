"""Follow-up of a volume: chapters added after earlier ones were translated (a webnovel sent over time).

Analysis and translation already skip finished passages. The final review and the consistency check of
the whole-book pipeline would read the whole volume again, and could rewrite chapters already
delivered. A job that follows up a volume carries `follow_up_chapters`: the chapters those stages
cover, i.e. the new or replaced ones plus any chapter still missing a translation. Earlier chapters
are neither translated nor reviewed again: they only give context.
"""

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Chapter, Job, Project, Segment

OPTION = "follow_up_chapters"


def follow_up_scope(db: Session, project: Project, chapter_ids: list[str]) -> list[str] | None:
    """The chapters a follow-up job works on; None when that is the whole volume anyway."""
    unfinished = db.scalars(
        select(Segment.chapter_id)
        .where(
            Segment.project_id == project.id,
            Segment.translation == "",
            Segment.retained_source.is_(False),
        )
        .distinct()
    )
    scope = set(chapter_ids) | set(unfinished)
    every = set(db.scalars(select(Chapter.id).where(Chapter.project_id == project.id)))
    if every and every <= scope:
        return None
    ordered = db.scalars(
        select(Chapter.id)
        .where(Chapter.project_id == project.id, Chapter.id.in_(scope))
        .order_by(Chapter.position)
    )
    return list(ordered)


def follow_up_options(db: Session, project: Project, chapter_ids: list[str]) -> dict:
    scope = follow_up_scope(db, project, chapter_ids)
    return {} if scope is None else {OPTION: scope}


def scoped_chapters(job: Job) -> set[str] | None:
    """The chapters the review stages of this job cover; None: the whole volume."""
    if OPTION not in (job.options or {}):
        return None
    return set(job.options[OPTION] or [])
