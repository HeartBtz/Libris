import re

from sqlalchemy import select

from app.db import SessionLocal
from app.engines.context.builder import build_context
from app.engines.epub.text import restore_missing_codes
from app.engines.quality.checks import validate_translation
from app.engines.translation.versions import save_version
from app.jobs.queue import checkpoint, emit, fence
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


def restore_accepted_revision(target: dict, result: TranslationResult) -> None:
    if len(result.units) != 1 or result.units[0].id != target["id"]:
        return
    repaired = restore_missing_codes(target["text"], result.units[0].text)
    if repaired is not None:
        result.units[0].text = repaired


def _unqueue(segment: Segment, item: dict) -> None:
    segment.critique = [
        {key: field for key, field in value.items() if key != "queued"}
        if matches_acceptance(value, item)
        else value
        for value in segment.critique
    ]


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
        # 1. Short read. No session or transaction may stay open while the model is working.
        with SessionLocal() as db:
            segment = db.get(Segment, item["segment_id"])
            project = db.get(Project, job.project_id)
            critique = next(
                (value for value in (segment.critique if segment else []) if matches_acceptance(value, item)),
                None,
            )
            if not segment or not critique:
                index += 1
                continue
            target = next((unit for unit in segment.translated_units if unit["id"] == item["unit_id"]), None)
            source = next((unit for unit in segment.units if unit["id"] == item["unit_id"]), None)
            if not target or not source:
                _unqueue(segment, item)
                db.commit()
                index += 1
                continue
            project_id, segment_id, revision = project.id, segment.id, segment.revision
            provider_id = job.provider_id or project.provider_id
            translated = [dict(unit) for unit in segment.translated_units]
            target, source, critique = dict(target), dict(source), dict(critique)

        # 2. Network calls, without any database resource.
        failure = ""
        try:
            built = await build_context(
                project_id,
                segment_id,
                "translation_revision",
                extra={
                    "TARGET_TEXT": [{"id": item["unit_id"], "text": source["text"]}],
                    "CURRENT_TRANSLATION": [target],
                    "REVIEW": [critique],
                    "APPLICATION_SCOPE": "Apply this accepted editorial advice to this one unit. Return its complete corrected text with every immutable marker. Never insert the advice itself as prose.",
                },
                provider_id=provider_id,
            )

            def validate(value: TranslationResult, target=target, source=source) -> None:
                restore_accepted_revision(target, value)
                validate_accepted_revision([source], value)

            result = await llm.complete(
                project_id=project_id,
                provider_id=provider_id,
                segment_id=segment_id,
                operation="translation_revision",
                messages=built.messages,
                context=built.inspector,
                response_model=TranslationResult,
                validator=validate,
                temperature=0.1,
            )
            next(unit for unit in translated if unit["id"] == item["unit_id"])["text"] = result.units[0].text
        except (ProviderUnavailable, ProviderAuthenticationRequired):
            raise
        except (LLMError, ValueError) as exc:
            failure = str(exc)[:500]

        # 3. Write, only if this worker still owns the job (a pause or cancel during the call wins).
        with SessionLocal() as db:
            fence(db, job.id, owner)
            segment = db.get(Segment, segment_id)
            if segment is None:
                index += 1
                continue
            if not failure and not save_version(
                db,
                segment_id,
                translated,
                "human",
                revision,
                author_id=item["author_id"],
                validated=False,
                stage="done",
            ):
                db.rollback()
                fence(db, job.id, owner)
                segment = db.get(Segment, segment_id)
                failure = "La traduction a été modifiée avant le traitement de la file."
            if failure:
                _unqueue(segment, item)
                db.add(
                    Issue(
                        project_id=project_id,
                        segment_id=segment_id,
                        severity="warning",
                        code="queued_critique_failed",
                        message=f"Proposition acceptée non appliquée : {failure}",
                    )
                )
            else:
                db.refresh(segment)
                segment.critique = [value for value in segment.critique if not matches_acceptance(value, item)]
                if not segment.critique:
                    for issue in db.scalars(
                        select(Issue).where(
                            Issue.segment_id == segment_id,
                            Issue.code == "queued_critique_failed",
                            Issue.resolved.is_(False),
                        )
                    ):
                        issue.resolved = True
                    db.flush()
                unresolved = db.scalar(
                    select(Issue.id).where(Issue.segment_id == segment_id, Issue.resolved.is_(False)).limit(1)
                )
                segment.status = "check" if segment.critique or unresolved else "ok"
                emit(db, project_id, segment_id=segment_id, status="ai_suggestion_accepted")
            db.commit()
        index += 1
