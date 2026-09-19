from sqlalchemy.orm import Session

from app.models import AuditEntry


def audit(
    db: Session,
    *,
    owner_id: str,
    actor_id: str | None,
    action: str,
    series_id: str | None = None,
    project_id: str | None = None,
    **detail,
) -> None:
    """Traceable decision; never carries a secret or book text beyond the terms concerned."""
    db.add(
        AuditEntry(
            owner_id=owner_id,
            actor_id=actor_id,
            action=action,
            series_id=series_id,
            project_id=project_id,
            detail=detail,
        )
    )
