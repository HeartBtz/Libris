import hashlib
import time
from pathlib import Path
from typing import Literal

from fastapi import APIRouter, HTTPException, UploadFile
from pydantic import BaseModel
from sqlalchemy import delete, func, or_, select, update
from starlette.concurrency import run_in_threadpool

from app.api.common import row
from app.config import settings
from app.engines.epub import parse_book
from app.engines.epub.check import epubcheck
from app.engines.memory.identities import canonical_bible
from app.jobs.queue import ACTIVE, HELD, emit, enqueue
from app.models import (
    Chapter,
    Entity,
    Glossary,
    Job,
    Membership,
    Memory,
    Outbox,
    Project,
    Provider,
    Segment,
    User,
)
from app.models.common import uid
from app.schemas import InstructionInput, JobInput, ProjectConfig
from app.security import DB, CurrentUser, access

router = APIRouter(prefix="/api/projects")


def stats(db, project: Project) -> dict:
    total, done, validated, flagged, errors, refused = db.execute(
        select(
            func.count(Segment.id),
            func.count(Segment.id).filter(Segment.translation != "", Segment.retained_source.is_(False)),
            func.count(Segment.id).filter(Segment.validated.is_(True)),
            func.count(Segment.id).filter(Segment.status == "check"),
            func.count(Segment.id).filter(Segment.status == "error"),
            func.count(Segment.id).filter(Segment.status == "refused"),
        ).where(Segment.project_id == project.id)
    ).one()
    review_job = next(
        (
            candidate
            for candidate in db.scalars(
                select(Job)
                .where(
                    Job.project_id == project.id,
                    Job.operation.in_(["translate", "resolve_validations"]),
                )
                .order_by(Job.created_at.desc())
            )
            if candidate.checkpoint.get("step") == "final_review"
            or candidate.checkpoint.get("final_review_targets")
            or candidate.checkpoint.get("final_review_done")
        ),
        None,
    )
    review_checkpoint = review_job.checkpoint if review_job else {}
    review_done = len(set(review_checkpoint.get("final_review_done", [])))
    review_total = int(
        review_checkpoint.get("total")
        or len(review_checkpoint.get("final_review_targets", []))
        or flagged
        or total
    )
    return {
        "reviewed_segments": review_done,
        "review_total": review_total,
        "analyzed_segments": db.scalar(
            select(func.count(func.distinct(Memory.segment_id))).where(
                Memory.project_id == project.id, Memory.kind == "analysis"
            )
        ),
        "synthesized_chapters": db.scalar(
            select(func.count(Chapter.id)).where(Chapter.project_id == project.id, Chapter.analyzed.is_(True))
        ),
        "total": total,
        "retained_source": db.scalar(
            select(func.count(Segment.id)).where(
                Segment.project_id == project.id, Segment.retained_source.is_(True)
            )
        ),
        "translated": done,
        "validated": validated,
        "flagged": flagged,
        "errors": errors,
        "refused": refused,
        "chapters": db.scalar(
            select(func.count()).select_from(Chapter).where(Chapter.project_id == project.id)
        ),
        "glossary": db.scalar(
            select(func.count()).select_from(Glossary).where(Glossary.project_id == project.id)
        ),
    }


def project_view(db, project: Project) -> dict:
    return dict(
        row(project, ("original_path",)), stats=stats(db, project), bible=canonical_bible(db, project)
    )


def import_book(db, owner_id: str, data: bytes) -> Project:
    parsed = parse_book(data)
    project_id = uid()
    book_path = settings().data_dir / "books" / f"{project_id}.epub"
    project = Project(
        id=project_id,
        owner_id=owner_id,
        title=parsed["title"],
        author=parsed["author"],
        source_language=parsed["language"],
        original_hash=hashlib.sha256(data).hexdigest(),
        original_path=str(book_path),
        book_info=parsed["info"],
    )
    db.add(project)
    db.flush()
    position = 0
    for number, item in enumerate(parsed["chapters"]):
        chapter = Chapter(
            project_id=project.id, position=number, title=item["title"], resource=item["resource"]
        )
        db.add(chapter)
        db.flush()
        for group in item["groups"]:
            db.add(
                Segment(
                    project_id=project.id,
                    chapter_id=chapter.id,
                    position=position,
                    units=group,
                    source="\n\n".join(u["text"] for u in group),
                    section=group[0]["section"][:100],
                )
            )
            position += 1
    if not position:
        raise ValueError("Aucun texte traduisible trouvé dans l’EPUB.")
    book_path.write_bytes(data)
    return project


@router.get("")
def projects(user: CurrentUser, db: DB):
    member = select(Membership.project_id).where(Membership.user_id == user.id)
    books = db.scalars(
        select(Project)
        .where(or_(Project.owner_id == user.id, Project.id.in_(member)))
        .order_by(Project.updated_at.desc())
    )
    return [project_view(db, p) for p in books]


@router.post("", status_code=201)
async def upload(file: UploadFile, user: CurrentUser, db: DB):
    data = await file.read(settings().max_upload_mb * 1024**2 + 1)
    project = await run_in_threadpool(import_book, db, user.id, data)
    project.book_info = dict(project.book_info, validation=await run_in_threadpool(epubcheck, data))
    emit(db, project.id, status="imported", step="parsing")
    db.commit()
    return project_view(db, project)


@router.get("/{project_id}")
def get_project(project_id: str, user: CurrentUser, db: DB):
    return project_view(db, access(db, project_id, user))


@router.get("/{project_id}/final-review")
def final_review_info(project_id: str, user: CurrentUser, db: DB):
    from app.providers.search import search_config

    access(db, project_id, user)
    return {
        "automatic": settings().final_review_enabled,
        "web_enabled": bool(search_config()["enabled"]),
        "eligible": db.scalar(select(func.count(Segment.id)).where(
            Segment.project_id == project_id, Segment.status == "check",
            Segment.translation != "", Segment.human.is_(False),
            Segment.validated.is_(False), Segment.retained_source.is_(False),
        )),
    }


@router.put("/{project_id}")
def configure(project_id: str, body: ProjectConfig, user: CurrentUser, db: DB):
    project = access(db, project_id, user, write=True)
    if db.scalar(select(Job.id).where(Job.project_id == project_id, Job.status.in_(ACTIVE))):
        raise HTTPException(409, "Mettez le travail en pause avant de modifier sa configuration.")
    if body.provider_id and not db.get(Provider, body.provider_id):
        raise HTTPException(422, "Provider inconnu.")
    for key, value in body.model_dump().items():
        setattr(project, key, value)
    for job in db.scalars(select(Job).where(Job.project_id == project_id, Job.status.in_(HELD))):
        if not job.options.get("provider_id"):
            job.provider_id = body.provider_id
    db.commit()
    return project_view(db, project)


@router.delete("/{project_id}")
def remove(project_id: str, user: CurrentUser, db: DB, stop_jobs: bool = False):
    project = access(db, project_id, user, owner=True)
    if not stop_jobs and db.scalar(
        select(Job.id).where(Job.project_id == project_id, Job.status.in_(ACTIVE))
    ):
        raise HTTPException(409, "Annulez le travail actif avant de supprimer le projet.")
    db.execute(
        update(Job)
        .where(Job.project_id == project_id, Job.status.in_(HELD))
        .values(
            status="cancelled", lease_owner="", lease_until=0, next_attempt=0, stop_reason="project_deleted"
        )
    )
    # Clear self references before the project-level cascade; PostgreSQL otherwise may try to
    # SET NULL on an entity already deleted by the same cascade.
    db.execute(update(Entity).where(Entity.project_id == project_id).values(merged_into_id=None))
    path = Path(project.original_path)
    db.delete(project)
    db.commit()
    path.unlink(missing_ok=True)
    return {
        "ok": True,
        "message": "Projet local supprimé. La mémoire OpenViking distante se gère séparément.",
    }


@router.get("/{project_id}/chapters")
def chapters(project_id: str, user: CurrentUser, db: DB):
    access(db, project_id, user)
    return [
        row(c)
        for c in db.scalars(
            select(Chapter).where(Chapter.project_id == project_id).order_by(Chapter.position)
        )
    ]


@router.put("/{project_id}/chapters/{chapter_id}/instructions")
def chapter_instructions(project_id: str, chapter_id: str, body: InstructionInput, user: CurrentUser, db: DB):
    project = access(db, project_id, user, write=True)
    chapter = db.get(Chapter, chapter_id)
    if not chapter or chapter.project_id != project_id:
        raise HTTPException(404, "Chapitre introuvable.")
    chapter.instructions = body.instructions
    project.memory_revision += 1
    db.commit()
    return row(chapter)


@router.post("/{project_id}/jobs", status_code=202)
def start_job(project_id: str, body: JobInput, user: CurrentUser, db: DB):
    project = access(db, project_id, user, write=True)
    if body.operation == "analyze" and not body.force:
        held = db.scalar(select(Job).where(Job.project_id == project_id, Job.status.in_(HELD)))
        if held and held.operation == "analyze":
            return {**row(held), "message": "Ce travail existe déjà. Utilisez Reprendre s’il est en pause."}
        if not held:
            coverage = stats(db, project)
            if (
                coverage["total"]
                and coverage["analyzed_segments"] == coverage["total"]
                and coverage["synthesized_chapters"] == coverage["chapters"]
            ):
                prior = db.scalar(
                    select(Job)
                    .where(
                        Job.project_id == project_id, Job.operation == "analyze", Job.status == "completed"
                    )
                    .order_by(Job.created_at.desc())
                    .limit(1)
                )
                if not prior:
                    prior = Job(
                        project_id=project_id,
                        operation="analyze",
                        status="completed",
                        stop_reason="already_analyzed",
                        checkpoint={"step": "already_analyzed"},
                    )
                    db.add(prior)
                    db.commit()
                return {**row(prior), "message": "Analyse déjà terminée ; aucun nouvel appel au modèle."}
    if not (body.provider_id or project.provider_id) and body.operation != "sync_memory":
        raise HTTPException(422, "Configurez un provider LLM pour ce projet.")
    if body.provider_id and not db.get(Provider, body.provider_id):
        raise HTTPException(404, "Provider de reprise introuvable.")
    if body.refused_only and body.operation != "translate":
        raise HTTPException(422, "Le filtre des refus est réservé à la traduction.")
    if body.segment_ids is not None:
        if body.operation != "translate" or body.segment_id or body.chapter_id or body.refused_only:
            raise HTTPException(422, "La sélection de récupération doit être une traduction ciblée seule.")
        selected = list(db.scalars(select(Segment).where(Segment.project_id == project_id,
                                                         Segment.id.in_(body.segment_ids))))
        if len(selected) != len(set(body.segment_ids)):
            raise HTTPException(422, "La sélection contient un passage inconnu de ce livre.")
        if any(s.human or s.validated or s.retained_source or
               (s.translation and s.status not in {"refused", "error", "blocked"}) for s in selected):
            raise HTTPException(409, "Un passage sélectionné est protégé ou n’a plus besoin de récupération.")
        body.force = True
    if body.operation == "translate" and not project.bible:
        raise HTTPException(409, "Lancez l’analyse du livre avant sa traduction.")
    if body.chapter_id:
        chapter = db.get(Chapter, body.chapter_id)
        if not chapter or chapter.project_id != project_id:
            raise HTTPException(404, "Chapitre introuvable.")
    if body.segment_id:
        segment = db.get(Segment, body.segment_id)
        if not segment or segment.project_id != project_id:
            raise HTTPException(404, "Passage introuvable.")
    job = enqueue(db, project, body.operation, body.model_dump(exclude={"operation"}))
    if body.operation == "analyze" and body.force:
        previous = list(
            db.scalars(
                select(Memory).where(
                    Memory.project_id == project_id, Memory.kind == "analysis", Memory.validated.is_(False)
                )
            )
        )
        db.execute(
            delete(Outbox).where(
                Outbox.project_id == project_id, Outbox.event_key.in_([m.id for m in previous])
            )
        )
        for memory in previous:
            db.delete(memory)
        for chapter in db.scalars(select(Chapter).where(Chapter.project_id == project_id)):
            chapter.analyzed, chapter.summary = False, {}
    db.commit()
    return row(job)


@router.get("/{project_id}/jobs")
def jobs(project_id: str, user: CurrentUser, db: DB):
    access(db, project_id, user)
    return [
        row(j)
        for j in db.scalars(
            select(Job).where(Job.project_id == project_id).order_by(Job.created_at.desc()).limit(50)
        )
    ]


@router.post("/{project_id}/jobs/{job_id}/{action}")
def control(
    project_id: str,
    job_id: str,
    action: Literal["pause", "resume", "cancel", "retry"],
    user: CurrentUser,
    db: DB,
):
    project = access(db, project_id, user, write=True)
    job = db.scalar(select(Job).where(Job.id == job_id).with_for_update())
    if not job or job.project_id != project_id:
        raise HTTPException(404, "Travail introuvable.")
    if action in {"resume", "retry"} and job.status not in {
        "paused",
        "failed",
        "waiting",
        "blocked",
        "cancelled",
    }:
        raise HTTPException(409, "Seul un travail en pause, en attente, bloqué ou échoué peut être repris.")
    if action in {"resume", "retry"} and db.scalar(
        select(Job.id).where(Job.project_id == project_id, Job.id != job_id, Job.status.in_(HELD))
    ):
        raise HTTPException(409, "Un autre travail est déjà actif pour ce livre.")
    if action in {"pause", "cancel"} and job.status not in (*HELD, "failed"):
        raise HTTPException(409, "Ce travail est déjà terminé.")
    job.status = {"pause": "paused", "resume": "pending", "retry": "pending", "cancel": "cancelled"}[action]
    if action in {"resume", "retry"} and not job.options.get("provider_id"):
        job.provider_id = project.provider_id
    job.lease_owner, job.lease_until, job.error = "", 0, ""
    if action in {"resume", "retry"}:
        job.checkpoint = {**job.checkpoint, "consecutive_failures": 0}
    job.next_attempt, job.outage_count = 0, 0
    job.stop_reason = (
        "user_pause" if action == "pause" else "user_cancel" if action == "cancel" else "manual_resume"
    )
    project.status = job.status
    project.updated_at = time.time()
    emit(db, project_id, job_id=job.id, status=job.status)
    db.commit()
    return row(job)


class ShareInput(BaseModel):
    username: str
    role: Literal["reader", "editor"]


@router.get("/{project_id}/members")
def members(project_id: str, user: CurrentUser, db: DB):
    access(db, project_id, user, owner=True)
    return [
        {"user_id": m.user_id, "username": u.username, "role": m.role}
        for m, u in db.execute(
            select(Membership, User)
            .join(User, User.id == Membership.user_id)
            .where(Membership.project_id == project_id)
        )
    ]


@router.post("/{project_id}/members")
def share(project_id: str, body: ShareInput, user: CurrentUser, db: DB):
    access(db, project_id, user, owner=True)
    target = db.scalar(select(User).where(User.username == body.username))
    if not target:
        raise HTTPException(404, "Utilisateur introuvable.")
    member = db.get(Membership, (project_id, target.id))
    if member:
        member.role = body.role
    else:
        db.add(Membership(project_id=project_id, user_id=target.id, role=body.role))
    db.commit()
    return {"ok": True}


@router.delete("/{project_id}/members/{user_id}")
def unshare(project_id: str, user_id: str, user: CurrentUser, db: DB):
    access(db, project_id, user, owner=True)
    db.execute(delete(Membership).where(Membership.project_id == project_id, Membership.user_id == user_id))
    db.commit()
    return {"ok": True}
