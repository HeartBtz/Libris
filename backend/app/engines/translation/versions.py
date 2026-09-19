from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.engines.context.series import human_choices
from app.engines.memory.store import remember
from app.engines.quality.checks import validate_translation
from app.models import Issue, Project, Segment, TranslationVersion
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
    # A human correction: protected from machine output until a person changes it again. Keeping the
    # original (`source_retained`) is not a translation anyone chose, so a later successful machine
    # translation may replace it; the automatic passes skip retained passages on their own.
    human = origin in {"human", "restore"}
    retained = origin == "source_retained"
    version = TranslationVersion(
        segment_id=segment_id,
        units=units,
        origin=origin,
        base_revision=base_revision,
        author_id=author_id,
        applied=False,
    )
    db.add(version)
    was_retained = segment.retained_source  # read before the UPDATE refreshes the loaded row
    # Only a correction of machine output is a reusable choice; revising one's own decision, or
    # translating a passage kept in the original, is not.
    previous_units = [] if segment.human or was_retained else list(segment.translated_units or [])
    # A person's own action (author_id) may replace a human correction; machine output never does.
    permitted = segment.revision == base_revision and (human or author_id is not None or not segment.human)
    if permitted:
        changed = db.execute(
            update(Segment)
            .where(Segment.id == segment_id, Segment.revision == base_revision)
            .values(
                translated_units=units,
                translation="\n\n".join(u["text"] for u in units),
                revision=base_revision + 1,
                human=human,
                retained_source=retained,
                validated=validated if human else False,
                stage=stage,
                # An untranslated passage kept on purpose is neither "ok" nor merely "to check".
                status="source_retained" if retained else "ok" if validated else "check",
                error="",
            )
        )
        permitted = changed.rowcount == 1
        if permitted and not retained and was_retained:
            # A real translation replaces the retained original: its standing alert is settled.
            db.execute(
                update(Issue)
                .where(Issue.segment_id == segment_id, Issue.code == "source_retained", Issue.resolved.is_(False))
                .values(resolved=True)
            )
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
                # A whole passage only helps where it recurs verbatim; the edits themselves travel.
                **human_choices(segment.units, previous_units, units),
            },
            "human_decision",
            validated=True,
        )
        project.memory_revision += 1
    return permitted
