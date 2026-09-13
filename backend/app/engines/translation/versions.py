from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.engines.memory.store import remember
from app.engines.quality.checks import validate_translation
from app.models import Project, Segment, TranslationVersion
from app.schemas import TranslationResult


def save_version(
    db: Session,
    segment_id: str,
    units: list[dict],
    origin: str,
    base_revision: int,
    *,
    author_id: str | None = None,
    validated: bool = False,
    stage: str = "translated",
) -> bool:
    segment = db.scalar(select(Segment).where(Segment.id == segment_id).with_for_update())
    validate_translation(segment.units, TranslationResult(units=units))
    human = origin in {"human", "restore", "source_retained"}
    version = TranslationVersion(
        segment_id=segment_id,
        units=units,
        origin=origin,
        base_revision=base_revision,
        author_id=author_id,
        applied=False,
    )
    db.add(version)
    permitted = segment.revision == base_revision and (human or not segment.human)
    if permitted:
        changed = db.execute(
            update(Segment)
            .where(Segment.id == segment_id, Segment.revision == base_revision)
            .values(
                translated_units=units,
                translation="\n\n".join(u["text"] for u in units),
                revision=base_revision + 1,
                human=human,
                retained_source=origin == "source_retained",
                validated=validated if human and origin != "source_retained" else False,
                stage=stage,
                status="ok" if validated else "check",
                error="",
            )
        )
        permitted = changed.rowcount == 1
    version.applied = permitted
    if permitted and human and validated:
        project = db.get(Project, segment.project_id)
        remember(
            db,
            project,
            segment,
            {
                "source": segment.source,
                "translation": "\n\n".join(u["text"] for u in units),
                "revision": base_revision + 1,
            },
            "human_decision",
            validated=True,
        )
        project.memory_revision += 1
    return permitted
