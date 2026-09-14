import re

from sqlalchemy import select

from app.db import SessionLocal
from app.engines.context.builder import build_context
from app.engines.quality.checks import validate_translation
from app.engines.translation.versions import save_version
from app.jobs.queue import checkpoint, emit
from app.models import Issue, Job, Project, Segment
from app.providers.llm import LLMError, ProviderAuthenticationRequired, ProviderUnavailable, llm
from app.schemas import TranslationResult


def matches_acceptance(critique: dict, acceptance: dict) -> bool:
    return (
        critique.get("unit_id") == acceptance["unit_id"]
        and critique.get("suggestion") == acceptance["suggestion"]
    )


def validate_accepted_revision(source: list[dict], result: TranslationResult) -> None:
    validate_translation(source, result)
    for unit in result.units:
        text = re.sub(r"⟦[^⟧]+⟧", "", unit.text).strip()
        if re.match(r"^(?:traduire|remplacer|écrire|reformuler|corriger)\b", text, re.IGNORECASE):
            raise ValueError("La réponse contient une consigne éditoriale au lieu du texte corrigé.")


async def accept_queued_critiques(job: Job, owner: str) -> None:
    """Apply accepted AI proposals one at a time so a failed proposal never blocks the queue."""
    index = max(int(job.checkpoint.get("current", 1)) - 1, 0)
    while True:
        current = checkpoint(job.id, owner)
        queued = current.options.get("critique_acceptances", [])
        if index >= len(queued):
            return
        item = queued[index]
        checkpoint(job.id, owner, {"step": "critique_acceptance", "current": index + 1, "total": len(queued)})
        with SessionLocal() as db:
            segment = db.get(Segment, item["segment_id"])
            project = db.get(Project, job.project_id)
            critique = next(
                (
                    value
                    for value in (segment.critique if segment else [])
                    if matches_acceptance(value, item)
                ),
                None,
            )
            if not segment or not critique:
                index += 1
                continue
            target = next((unit for unit in segment.translated_units if unit["id"] == item["unit_id"]), None)
            source = next((unit for unit in segment.units if unit["id"] == item["unit_id"]), None)
            if not target or not source:
                segment.critique = [
                    {key: field for key, field in value.items() if key != "queued"}
                    if matches_acceptance(value, item)
                    else value
                    for value in segment.critique
                ]
                db.commit()
                index += 1
                continue
            try:
                built = await build_context(
                    project.id,
                    segment.id,
                    "translation_revision",
                    extra={
                        "TARGET_TEXT": [{"id": item["unit_id"], "text": source["text"]}],
                        "CURRENT_TRANSLATION": [target],
                        "REVIEW": [critique],
                        "APPLICATION_SCOPE": "Apply this accepted editorial advice to this one unit. Return its complete corrected text with every immutable marker. Never insert the advice itself as prose.",
                    },
                )
                result = await llm.complete(
                    project_id=project.id,
                    provider_id=job.provider_id or project.provider_id,
                    segment_id=segment.id,
                    operation="translation_revision",
                    messages=built.messages,
                    context=built.inspector,
                    response_model=TranslationResult,
                    validator=lambda value: validate_accepted_revision([source], value),
                    temperature=0.1,
                )
                units = [dict(unit) for unit in segment.translated_units]
                next(unit for unit in units if unit["id"] == item["unit_id"])["text"] = result.units[0].text
                if not save_version(
                    db,
                    segment.id,
                    units,
                    "human",
                    segment.revision,
                    author_id=item["author_id"],
                    validated=False,
                    stage="done",
                ):
                    raise ValueError("La traduction a été modifiée avant le traitement de la file.")
                db.refresh(segment)
                segment.critique = [
                    value for value in segment.critique if not matches_acceptance(value, item)
                ]
                if not segment.critique:
                    for issue in db.scalars(select(Issue).where(
                        Issue.segment_id == segment.id,
                        Issue.code == "queued_critique_failed",
                        Issue.resolved.is_(False),
                    )):
                        issue.resolved = True
                    db.flush()
                unresolved = db.scalar(
                    select(Issue.id).where(Issue.segment_id == segment.id, Issue.resolved.is_(False)).limit(1)
                )
                segment.status = "check" if segment.critique or unresolved else "ok"
                emit(db, project.id, segment_id=segment.id, status="ai_suggestion_accepted")
            except (ProviderUnavailable, ProviderAuthenticationRequired):
                raise
            except (LLMError, ValueError) as exc:
                segment.critique = [
                    {key: field for key, field in value.items() if key != "queued"}
                    if matches_acceptance(value, item)
                    else value
                    for value in segment.critique
                ]
                db.add(
                    Issue(
                        project_id=project.id,
                        segment_id=segment.id,
                        severity="warning",
                        code="queued_critique_failed",
                        message=f"Proposition acceptée non appliquée : {type(exc).__name__}.",
                    )
                )
            db.commit()
        index += 1
