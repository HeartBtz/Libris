"""Bounded growth of diagnostic data: request bodies, events, outbox, bible revisions, old job state.

    docker compose exec api python -m app.maintenance.retention --dry-run
    docker compose exec api python -m app.maintenance.retention

The worker runs the same pass at start-up and then every hour. Request rows themselves are kept — token
counts, costs, durations, errors and the cached answer (`parsed`) are untouched — only their bulky
prompt, raw response and context trace are emptied, unless RETENTION_REQUEST_ROWS_DAYS is set: rows
older than that are deleted once counted in the daily usage aggregates (`app.maintenance.usage`), which
the statistics read. Each rule is disabled by setting its value to 0.
PostgreSQL reuses the freed space but only returns it to the operating system after
`VACUUM (FULL, ANALYZE) llm_requests;`, which locks the table: stop the worker first.
"""

import argparse
import shutil
import time

from sqlalchemy import Text, cast, delete, func, select, update

from app.config import settings
from app.db import SessionLocal
from app.jobs.segment_state import REVIEWED
from app.models import (
    BibleRevision,
    Event,
    ImportSession,
    Job,
    JobSegmentState,
    Outbox,
    RequestLog,
    TranslationRequest,
)

DAY = 86400
KEPT_EVENTS = 500  # per book, whatever their age: the interface replays recent progress from them
BATCH = 200


def request_bodies(days: int, dry_run: bool, now: float) -> int:
    done = 0
    while days:
        with SessionLocal() as db:
            ids = list(
                db.scalars(
                    select(RequestLog.id)
                    .where(
                        RequestLog.created_at < now - days * DAY,
                        RequestLog.status != "running",
                        func.length(cast(RequestLog.messages, Text)) > 2,
                    )
                    .order_by(RequestLog.id)
                    .offset(done if dry_run else 0)
                    .limit(BATCH)
                )
            )
            if not ids:
                break
            if not dry_run:
                db.execute(
                    update(RequestLog).where(RequestLog.id.in_(ids)).values(messages=[], raw={}, context={})
                )
                db.commit()
        done += len(ids)
    return done


def events(days: int, dry_run: bool, now: float) -> int:
    done = 0
    if not days:
        return done
    cutoff = now - days * DAY
    with SessionLocal() as db:
        projects = list(db.scalars(select(Event.project_id).where(Event.created_at < cutoff).distinct()))
    for project_id in projects:
        with SessionLocal() as db:
            floor = db.scalar(
                select(Event.id)
                .where(Event.project_id == project_id)
                .order_by(Event.id.desc())
                .offset(KEPT_EVENTS - 1)
                .limit(1)
            )
            if floor is None:
                continue
            old = (Event.project_id == project_id, Event.id < floor, Event.created_at < cutoff)
            if dry_run:
                done += db.scalar(select(func.count()).select_from(Event).where(*old))
            else:
                done += db.execute(delete(Event).where(*old)).rowcount
                db.commit()
    return done


def outbox(days: int, dry_run: bool, now: float) -> int:
    if not days:
        return 0
    old = (Outbox.status == "sent", Outbox.created_at < now - days * DAY)
    with SessionLocal() as db:
        if dry_run:
            return db.scalar(select(func.count()).select_from(Outbox).where(*old))
        count = db.execute(delete(Outbox).where(*old)).rowcount
        db.commit()
        return count


def bible_revisions(keep: int, dry_run: bool) -> int:
    done = 0
    if not keep:
        return done
    with SessionLocal() as db:
        crowded = list(
            db.scalars(
                select(BibleRevision.project_id)
                .where(BibleRevision.human.is_(False))
                .group_by(BibleRevision.project_id)
                .having(func.count() > keep)
            )
        )
    for project_id in crowded:
        with SessionLocal() as db:
            ids = list(
                db.scalars(
                    select(BibleRevision.id)
                    .where(BibleRevision.project_id == project_id, BibleRevision.human.is_(False))
                    .order_by(BibleRevision.created_at.desc(), BibleRevision.id.desc())
                    .offset(keep)
                )
            )
            done += len(ids)
            if ids and not dry_run:
                for start in range(0, len(ids), BATCH):
                    db.execute(delete(BibleRevision).where(BibleRevision.id.in_(ids[start : start + BATCH])))
                db.commit()
    return done


def job_state(days: int, dry_run: bool, now: float) -> int:
    """Per-passage state of jobs ended long ago; review outcomes stay, the book's review history shows them."""
    done = 0
    if not days:
        return done
    ended = select(Job.id).where(
        Job.status.in_(("completed", "failed", "cancelled")),
        # Jobs that ended before the column existed count from their creation.
        func.coalesce(Job.finished_at, Job.created_at) < now - days * DAY,
    )
    prunable = (JobSegmentState.job_id.in_(ended), JobSegmentState.step != REVIEWED)
    with SessionLocal() as db:
        jobs = list(db.scalars(select(JobSegmentState.job_id).where(*prunable).distinct()))
    for start in range(0, len(jobs), BATCH):
        batch = (JobSegmentState.job_id.in_(jobs[start : start + BATCH]), JobSegmentState.step != REVIEWED)
        with SessionLocal() as db:
            if dry_run:
                done += db.scalar(select(func.count()).select_from(JobSegmentState).where(*batch))
            else:
                done += db.execute(delete(JobSegmentState).where(*batch)).rowcount
                db.commit()
    return done


def import_sessions(dry_run: bool, now: float) -> int:
    """Expired imports lose their uploaded files; a confirmed one keeps its answer a week longer."""
    from app.engines.ingestion.store import data_path

    with SessionLocal() as db:
        expired = list(db.scalars(select(ImportSession).where(ImportSession.expires_at < now)))
        if dry_run:
            return len(expired)
        for session in expired:
            shutil.rmtree(data_path(f"staging/{session.id}"), ignore_errors=True)
            if session.result is None or session.expires_at < now - 7 * DAY:
                db.delete(session)
        db.commit()
    return len(expired)


def request_rows(days: int, dry_run: bool, now: float) -> int:
    """Whole request rows, once counted in `usage_daily`: the statistics keep them, the inspector does not."""
    from app.maintenance.usage import watermark

    done = 0
    if not days:
        return done
    with SessionLocal() as db:
        through = watermark(db)
    if through is None:
        return done
    old = (
        RequestLog.created_at < min(now - days * DAY, through),
        RequestLog.status != "running",
    )
    while True:
        with SessionLocal() as db:
            if dry_run:
                return db.scalar(select(func.count()).select_from(RequestLog).where(*old))
            ids = list(db.scalars(select(RequestLog.id).where(*old).limit(BATCH)))
            if not ids:
                return done
            done += db.execute(delete(RequestLog).where(RequestLog.id.in_(ids))).rowcount
            db.commit()


def results(days: int, dry_run: bool, now: float) -> int:
    """Delivered result files of old requests: the row keeps the report, and asking for the result again
    rebuilds the file from the database."""
    from app.engines.delivery.results import discard

    if not days:
        return 0
    with SessionLocal() as db:
        old = list(
            db.scalars(
                select(TranslationRequest).where(TranslationRequest.finished_at < now - days * DAY)
            )
        )
        old = [request for request in old if (request.artifact or {}).get("path")]
        if dry_run:
            return len(old)
        for request in old:
            discard(request)
            request.artifact = None
        db.commit()
    return len(old)


def apply(dry_run: bool = False) -> dict:
    from app.maintenance.usage import rollup

    config, now = settings(), time.time()
    return {
        # First: a request is counted in the daily aggregates before any rule may delete it.
        "usage_rollup": rollup(now, dry_run),
        "request_rows": request_rows(config.retention_request_rows_days, dry_run, now),
        "request_bodies": request_bodies(config.retention_request_bodies_days, dry_run, now),
        "events": events(config.retention_events_days, dry_run, now),
        "outbox": outbox(config.retention_outbox_sent_days, dry_run, now),
        "bible_revisions": bible_revisions(config.retention_bible_revisions, dry_run),
        "job_state": job_state(config.retention_job_state_days, dry_run, now),
        "import_sessions": import_sessions(dry_run, now),
        "results": results(config.retention_results_days, dry_run, now),
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--dry-run", action="store_true", help="count without writing")
    arguments = parser.parse_args()
    result = apply(arguments.dry_run)
    verb = "would be" if arguments.dry_run else "were"
    print(
        f"{result['usage_rollup']} requests {verb} rolled up into usage_daily and {result['request_rows']} "
        f"request rows {verb} deleted; {result['request_bodies']} request bodies {verb} emptied; {result['events']} events, "
        f"{result['outbox']} sent outbox rows, {result['bible_revisions']} bible revisions and "
        f"{result['job_state']} job state rows {verb} deleted; {result['import_sessions']} expired imports "
        f"and {result['results']} delivered results {verb} cleaned."
    )
