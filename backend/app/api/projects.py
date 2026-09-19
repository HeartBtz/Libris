import hashlib
import shutil
import time
from pathlib import Path
from typing import Literal

from fastapi import APIRouter, HTTPException, UploadFile
from pydantic import BaseModel
from sqlalchemy import delete, func, or_, select, update
from starlette.concurrency import run_in_threadpool

from app.api.common import row
from app.config import settings
from app.engines.budget import admit as budget_admission
from app.engines.budget import resume_refusal as budget_resume_refusal
from app.engines.epub.book import SEGMENTATION
from app.engines.epub.check import epubcheck
from app.engines.ingestion import EpubAdapter
from app.engines.ingestion.passages import passage_chars
from app.engines.ingestion.store import (
    Files,
    attach,
    create_volume,
    data_path,
    epub_duplicate,
    get_or_create_series,
    lock,
    safe_display_name,
)
from app.engines.memory.cleanup import queue_volume_cleanup
from app.engines.memory.identities import canonical_bible
from app.engines.series.bible import refresh_series
from app.engines.translation.memory import translation_memory_enabled
from app.jobs.fairness import QueueRefused, admit, requested_priority
from app.jobs.launch import AUTOPILOT, autopilot_default, pipeline_options
from app.jobs.queue import ACTIVE, HELD, emit, enqueue
from app.models import (
    Chapter,
    Entity,
    Job,
    Membership,
    Memory,
    Outbox,
    Project,
    Provider,
    Segment,
    Series,
    User,
)
from app.progress import book_facts, books_progress, project_progress, project_stats
from app.schemas import InstructionInput, JobInput, ProjectConfig, SeriesBatchInput
from app.security import DB, CurrentUser, access

router = APIRouter(prefix="/api/projects")
# ProjectConfig fields kept in Project.config rather than in columns.
CONFIG_KEYS = ("translation_memory", "passage_max_chars", "review_mode", "analysis_mode", "threads")


# Settings kept in Project.config, only changed when a client sends them.
PROJECT_SETTINGS = {"autopilot", "fallback_provider_ids"}


def project_views(db, projects: list[Project], *, bible: bool = True) -> list[dict]:
    facts = book_facts(db, [project.id for project in projects])
    progress = books_progress(db, projects, facts)
    return [
        dict(
            row(project, ("original_path", "bible")),
            stats=facts[project.id].stats,
            progress=progress[project.id],
            **({"bible": canonical_bible(db, project)} if bible else {}),
            translation_memory=translation_memory_enabled(project),
        )
        for project in projects
    ]


def project_view(db, project: Project) -> dict:
    return project_views(db, [project])[0]


def check_series_access(db, project: Project, user: User, series_name: str) -> None:
    """A shared editor may not pull the conventions of the owner's books they cannot read.

    The owner's other volumes of a series feed the prompts of this book (terms, decisions): joining
    a series is reading it.
    """
    wanted = " ".join(series_name.split()).casefold()
    if project.owner_id == user.id or not wanted:
        return
    readable = set(db.scalars(select(Membership.project_id).where(Membership.user_id == user.id)))
    for other_id, other_series in db.execute(
        select(Project.id, Project.series_name).where(
            Project.owner_id == project.owner_id, Project.id != project.id, Project.series_name != ""
        )
    ):
        if " ".join(other_series.split()).casefold() == wanted and other_id not in readable:
            raise HTTPException(
                403,
                "Seul le propriétaire peut rattacher ce livre à cette série : elle contient des livres "
                "que vous ne pouvez pas lire.",
            )


def discard_book_file(project: Project) -> None:
    """The import is rolled back or the project deleted: its stored sources must not stay behind."""
    for path in project_files(project):
        if path.is_dir():
            shutil.rmtree(path, ignore_errors=True)
        else:
            path.unlink(missing_ok=True)


def project_files(project: Project) -> list[Path]:
    paths = [data_path(f"books/{project.id}.epub"), data_path(f"sources/{project.id}")]
    if project.original_path:
        paths.insert(0, Path(project.original_path))
    return paths


def import_book(
    db,
    owner_id: str,
    data: bytes,
    segmentation: int = SEGMENTATION,
    *,
    name: str = "book.epub",
    series: Series | None = None,
    volume_number: int | None = None,
    files: Files | None = None,
    passage_max_chars: int | None = None,
) -> Project:
    original_hash = hashlib.sha256(data).hexdigest()
    lock(db, f"{owner_id}:{original_hash}")
    existing = epub_duplicate(db, owner_id, original_hash)
    if existing:
        if existing.archived_at:
            raise HTTPException(
                409,
                f"Cet EPUB est déjà importé dans le projet archivé « {existing.title} ». "
                "Restaurez-le depuis les archives.",
            )
        raise HTTPException(409, f"Cet EPUB est déjà importé dans « {existing.title} ».")
    volume = EpubAdapter().parse(
        name, data, segmentation=segmentation, max_chars=passage_chars(None, passage_max_chars)
    )
    files = files if files is not None else Files()
    try:
        return create_volume(
            db,
            owner_id,
            volume,
            files,
            series=series,
            source_format="epub",
            number=volume_number,
            meta={"original_name": safe_display_name(name), "segmentation": segmentation},
        )
    except BaseException:
        files.discard()
        raise


@router.get("")
def projects(user: CurrentUser, db: DB, include_archived: bool = False):
    member = select(Membership.project_id).where(Membership.user_id == user.id)
    query = select(Project).where(or_(Project.owner_id == user.id, Project.id.in_(member)))
    if not include_archived:
        query = query.where(Project.archived_at.is_(None))
    books = list(db.scalars(query.order_by(Project.updated_at.desc())))
    # Polled every few seconds by the interface: the bible stays on the book's own page.
    return project_views(db, books, bible=False)


@router.post("", status_code=201)
async def upload(file: UploadFile, user: CurrentUser, db: DB):
    """Direct EPUB import as a standalone volume (0.5 clients); the interface uses /api/imports."""
    data = await file.read(settings().max_upload_mb * 1024**2 + 1)
    project = await run_in_threadpool(import_book, db, user.id, data, name=file.filename or "book.epub")
    try:
        try:
            validation = await run_in_threadpool(epubcheck, data)
        except ValueError as exc:
            # The source report is informative: a validator hiccup must not refuse the book.
            validation = {"available": True, "valid": None, "message": str(exc)}
        project.book_info = dict(project.book_info, validation=validation)
        emit(db, project.id, status="imported", step="parsing")
        db.commit()
    except BaseException:
        discard_book_file(project)
        raise
    return project_view(db, project)


@router.put("/batch/series")
def configure_series(body: SeriesBatchInput, user: CurrentUser, db: DB):
    if len(set(body.project_ids)) != len(body.project_ids):
        raise HTTPException(422, "Chaque livre doit apparaître une seule fois.")
    series_name = body.series_name.strip()
    if body.mode != "clear" and not series_name:
        raise HTTPException(422, "Le nom de série est requis.")
    if body.mode == "sequential" and body.first_volume + len(body.project_ids) - 1 > 10000:
        raise HTTPException(422, "La numérotation dépasse le volume 10000.")
    projects = [access(db, project_id, user, write=True) for project_id in body.project_ids]
    if body.mode != "clear":
        for project in projects:
            if project.series_name != series_name:
                check_series_access(db, project, user, series_name)
    touched = {project.series_id for project in projects}
    for offset, project in enumerate(projects):
        if body.mode == "clear" and (project.project_kind == "serial" or project.source_format != "epub"):
            raise HTTPException(409, "Des chapitres TXT ou JSON appartiennent obligatoirement à une série.")
        series = None if body.mode == "clear" else get_or_create_series(db, project.owner_id, series_name)
        attach(project, series)
        touched.add(project.series_id)
        if body.mode == "sequential":
            project.volume_number = body.first_volume + offset
        elif body.mode == "clear":
            project.volume_number = None
    for series_id in touched - {None}:
        refresh_series(db, series_id)
    db.commit()
    return project_views(db, projects)


@router.get("/{project_id}")
def get_project(project_id: str, user: CurrentUser, db: DB):
    return project_view(db, access(db, project_id, user))


@router.get("/{project_id}/final-review")
def final_review_info(project_id: str, user: CurrentUser, db: DB):
    from app.providers.search import search_config

    project = access(db, project_id, user)
    return {
        "automatic": settings().final_review_enabled,
        "web_enabled": bool(search_config()["enabled"]),
        "eligible": db.scalar(select(func.count(Segment.id)).where(
            Segment.project_id == project_id, Segment.status == "check",
            Segment.translation != "", Segment.human.is_(False),
            Segment.validated.is_(False), Segment.retained_source.is_(False),
        )),
        "summary": project_progress(db, project)["review"],
    }


@router.put("/{project_id}")
def configure(project_id: str, body: ProjectConfig, user: CurrentUser, db: DB):
    project = access(db, project_id, user, write=True)
    values = body.model_dump(exclude={*CONFIG_KEYS, *PROJECT_SETTINGS})
    changed = {key for key, value in values.items() if getattr(project, key) != value}
    provider_selected = project.provider_id is None and body.provider_id is not None
    if changed - {"series_name", "volume_number"} and db.scalar(
        select(Job.id).where(Job.project_id == project_id, Job.status.in_(ACTIVE))
    ):
        raise HTTPException(409, "Mettez le travail en pause avant de modifier sa configuration.")
    if body.provider_id and not db.get(Provider, body.provider_id):
        raise HTTPException(422, "Provider inconnu.")
    if any(not db.get(Provider, value) for value in body.fallback_provider_ids or []):
        raise HTTPException(422, "Fournisseur de secours inconnu.")
    if "series_name" in changed:
        check_series_access(db, project, user, body.series_name)
        if project.project_kind == "serial" or (project.source_format != "epub" and not body.series_name.strip()):
            raise HTTPException(409, "Des chapitres TXT ou JSON appartiennent obligatoirement à une série.")
    previous_series = project.series_id
    for key, value in values.items():
        if key != "series_name":
            setattr(project, key, value)
    if "series_name" in changed:
        name = body.series_name.strip()
        attach(project, get_or_create_series(db, project.owner_id, name) if name else None)
        for series_id in {previous_series, project.series_id} - {None}:
            refresh_series(db, series_id)
    # Clients that predate the setting omit it: the stored choice is then kept.
    stored = {key: getattr(body, key) for key in CONFIG_KEYS if key in body.model_fields_set}
    for key in PROJECT_SETTINGS & body.model_fields_set:
        stored[key] = getattr(body, key)
    if stored:
        project.config = {**project.config, **stored}
    for job in db.scalars(select(Job).where(Job.project_id == project_id, Job.status.in_(HELD))):
        if "provider_id" in changed:
            job.provider_id = body.provider_id
            if job.options.get("provider_id"):
                job.options = {**job.options, "provider_id": body.provider_id}
        elif not job.options.get("provider_id"):
            job.provider_id = body.provider_id
    # An archived book is at rest: configuring it must not start paid model calls.
    if provider_selected and project.archived_at is None and not db.scalar(
        select(Job.id).where(Job.project_id == project_id, Job.status.in_(HELD))
    ):
        try:
            admit(db, project.owner_id)  # a full queue leaves the analysis to be started later
        except QueueRefused:
            pass
        else:
            enqueue(db, project, "analyze", pipeline_options(project))
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
            status="cancelled",
            lease_owner="",
            lease_until=0,
            next_attempt=0,
            stop_reason="project_deleted",
            finished_at=time.time(),
        )
    )
    # Clear self references before the project-level cascade; PostgreSQL otherwise may try to
    # SET NULL on an entity already deleted by the same cascade.
    db.execute(update(Entity).where(Entity.project_id == project_id).values(merged_into_id=None))
    series_id = project.series_id
    # Opt-in: queued with the deletion itself, removed later by the worker (never blocks this request).
    cleanup = queue_volume_cleanup(db, project, user.id)
    db.delete(project)
    db.commit()
    discard_book_file(project)
    if series_id:
        refresh_series(db, series_id)
        db.commit()
    return {
        "ok": True,
        "message": "Projet local supprimé. Ses documents OpenViking seront effacés par le worker."
        if cleanup
        else "Projet local supprimé. La mémoire OpenViking distante se gère séparément.",
        "openviking_cleanup_id": cleanup.id if cleanup else None,
    }


@router.post("/{project_id}/archive")
def archive(project_id: str, user: CurrentUser, db: DB):
    project = access(db, project_id, user, owner=True)
    if db.scalar(select(Job.id).where(Job.project_id == project_id, Job.status.in_(HELD))):
        raise HTTPException(409, "Terminez ou annulez le travail avant d’archiver ce projet.")
    project.archived_at = project.archived_at or time.time()
    db.commit()
    return project_view(db, project)


@router.post("/{project_id}/restore")
def restore(project_id: str, user: CurrentUser, db: DB):
    project = access(db, project_id, user, owner=True)
    project.archived_at = None
    db.commit()
    return project_view(db, project)


@router.get("/{project_id}/chapters")
def chapters(project_id: str, user: CurrentUser, db: DB):
    access(db, project_id, user)
    return [
        # The layout of a text chapter is only needed to export it; it can be long.
        {**row(c, ("import_meta",)), "import_meta": {k: v for k, v in c.import_meta.items() if k != "layout"}}
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


class StaleInput(BaseModel):
    stale: bool


@router.put("/{project_id}/chapters/{chapter_id}/stale")
def chapter_stale(project_id: str, chapter_id: str, body: StaleInput, user: CurrentUser, db: DB):
    """An earlier chapter's source changed: a person checked this one, or asks for it to be checked."""
    access(db, project_id, user, write=True)
    chapter = db.get(Chapter, chapter_id)
    if not chapter or chapter.project_id != project_id:
        raise HTTPException(404, "Chapitre introuvable.")
    chapter.context_stale = body.stale
    db.commit()
    return row(chapter)


@router.post("/{project_id}/jobs", status_code=202)
def start_job(project_id: str, body: JobInput, user: CurrentUser, db: DB):
    project = access(db, project_id, user, write=True)
    if project.archived_at is not None:
        raise HTTPException(409, "Restaurez ce projet avant de lancer un travail.")
    if body.operation == "analyze" and not body.force:
        held = db.scalar(select(Job).where(Job.project_id == project_id, Job.status.in_(HELD)))
        if held and held.operation == "analyze":
            return {**row(held), "message": "Ce travail existe déjà. Utilisez Reprendre s’il est en pause."}
        if not held:
            coverage = project_stats(db, project)
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
                        finished_at=time.time(),
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
        # Passages kept in the original may be selected: a successful translation replaces the original.
        if any(s.human or s.validated or
               (s.translation and not s.retained_source and s.status not in {"refused", "error", "blocked"})
               for s in selected):
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
    options = body.model_dump(exclude={"operation", "autopilot", "priority"})
    whole_book = not any(
        options.get(key) for key in ("chapter_id", "segment_id", "segment_ids", "refused_only")
    )
    if body.operation in {"analyze", "translate"} and whole_book:
        # Autopilot by default (project setting, else AUTOPILOT_ENABLED); `autopilot: false` opts out.
        if body.autopilot if body.autopilot is not None else autopilot_default(project):
            options.update(AUTOPILOT)
    # Cost budget: the estimate against what remains, a cap already reached refuses (app.engines.budget).
    estimated: tuple[str, ...] = ()
    if whole_book and body.operation in {"analyze", "translate", "review"}:
        estimated = ("analyze", "translate") if options.get("continue_pipeline") else (body.operation,)
    if body.operation != "sync_memory":  # the only job that never calls a model
        refusal, kept = budget_admission(db, project, estimated, provider_id=body.provider_id)
        if refusal:
            raise HTTPException(409, {"code": "budget_exceeded", "message": refusal})
        if kept:
            options["budget"] = kept
    try:
        priority = requested_priority(db, user, body.priority)
        admit(db, project.owner_id)
    except QueueRefused as exc:
        raise HTTPException(exc.status, exc.detail) from None
    try:
        job = enqueue(db, project, body.operation, options, priority=priority)
    except ValueError as exc:  # A job is already held for this book: a state conflict, not bad input.
        raise HTTPException(409, str(exc)) from None
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
    if action in {"resume", "retry"} and job.status not in ("pending", "waiting"):
        try:
            admit(db, project.owner_id)
        except QueueRefused as exc:
            raise HTTPException(exc.status, exc.detail) from None
    control_job(db, project, job, action)
    db.commit()
    return row(job)


def control_job(db, project: Project, job: Job, action: str) -> Job:
    """Pause, resume, retry or cancel a job the caller may write; the caller commits."""
    if action in {"resume", "retry"} and job.status not in {
        "paused",
        "failed",
        "waiting",
        "blocked",
        "cancelled",
    }:
        raise HTTPException(409, "Seul un travail en pause, en attente, bloqué ou échoué peut être repris.")
    if action in {"resume", "retry"} and db.scalar(
        select(Job.id).where(Job.project_id == project.id, Job.id != job.id, Job.status.in_(HELD))
    ):
        raise HTTPException(409, "Un autre travail est déjà actif pour ce livre.")
    if action in {"resume", "retry"} and project.archived_at is not None:
        raise HTTPException(409, "Restaurez ce projet avant de reprendre un travail.")
    if action in {"resume", "retry"} and (refusal := budget_resume_refusal(db, project, job)):
        raise HTTPException(409, {"code": "budget_exceeded", "message": refusal})
    if action == "cancel" and job.status not in (*HELD, "failed"):
        raise HTTPException(409, "Ce travail est déjà terminé.")
    # Pausing a failed job would turn it back into a held job that blocks the book.
    if action == "pause" and job.status not in HELD:
        raise HTTPException(409, "Seul un travail actif ou bloqué peut être mis en pause.")
    job.status = {"pause": "paused", "resume": "pending", "retry": "pending", "cancel": "cancelled"}[action]
    job.finished_at = time.time() if action == "cancel" else None
    if action in {"resume", "retry"} and not job.options.get("provider_id"):
        job.provider_id = project.provider_id
    job.lease_owner, job.lease_until, job.error = "", 0, ""
    if action in {"resume", "retry"}:
        job.checkpoint = {**job.checkpoint, "consecutive_failures": 0}
        job.queued_at = time.time()  # back in the fair queue, behind the jobs already waiting
    job.next_attempt, job.outage_count = 0, 0
    job.stop_reason = (
        "user_pause" if action == "pause" else "user_cancel" if action == "cancel" else "manual_resume"
    )
    project.status = job.status
    project.updated_at = time.time()
    emit(db, project.id, job_id=job.id, status=job.status)
    return job


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
