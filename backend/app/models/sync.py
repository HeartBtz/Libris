"""`Project.series_name` stays in step with `Project.series_id`, whichever one a code path sets.

`series_id` is authoritative. Code written for 0.5 (the project settings API, archives, scripts) only
knows the name: setting it attaches the book to the owner's series of that normalized name, created
if needed, so that no book ends up with a name but no series.
"""

from sqlalchemy import event, inspect, select
from sqlalchemy.orm import Session

from app.models.books import Project
from app.models.common import uid
from app.models.series import Series


def _normalized(name: str) -> str:
    return " ".join((name or "").split()).casefold()


@event.listens_for(Session, "before_flush")
def sync_series(session: Session, _context, _instances) -> None:
    pending: dict[tuple[str, str], Series] = {}
    for obj in [*session.new, *session.dirty]:
        if not isinstance(obj, Project):
            continue
        state = inspect(obj)
        id_changed = state.attrs.series_id.history.has_changes()
        name_changed = state.attrs.series_name.history.has_changes()
        if id_changed or (obj in session.new and obj.series_id):
            with session.no_autoflush:
                series = session.get(Series, obj.series_id) if obj.series_id else None
            obj.series_name = series.name if series else ""
            continue
        if not name_changed:
            continue
        key = _normalized(obj.series_name)
        if not key:
            obj.series_id = None
            continue
        with session.no_autoflush:
            series = pending.get((obj.owner_id, key)) or session.scalar(
                select(Series).where(Series.owner_id == obj.owner_id, Series.normalized_name == key)
            )
        if series is None:
            series = Series(
                id=uid(),
                owner_id=obj.owner_id,
                name=" ".join(obj.series_name.split())[:500],
                normalized_name=key[:500],
                kind="books",
                authors=[],
                bible={},
            )
            session.add(series)
            pending[(obj.owner_id, key)] = series
        obj.series_id = series.id
        obj.series_name = series.name
