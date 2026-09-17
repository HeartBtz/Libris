import time

from sqlalchemy import select

from app.api.projects import project_view
from app.db import SessionLocal
from app.jobs.queue import checkpoint, enqueue
from app.models import Chapter, Job, Project, Provider, Segment


def test_canonical_progress_tracks_active_job_and_review_outcomes(seeded):
    pid = seeded[0]
    with SessionLocal() as db:
        project = db.get(Project, pid)
        initial = project_view(db, project)["progress"]
        assert initial["active_stage"] == "analysis"
        for chapter in db.scalars(select(Chapter).where(Chapter.project_id == pid)):
            chapter.analyzed = True
        segments = list(db.scalars(select(Segment).where(Segment.project_id == pid)))
        for segment in segments:
            segment.translation = "Traduit"
            segment.translated_units = [{"id": unit["id"], "text": "Traduit"} for unit in segment.units]
            segment.status = "check"
        project.status = "reviewing"
        job = enqueue(db, project, "resolve_validations", {})
        targets = [segment.id for segment in segments]
        job.status = "reviewing"
        job.checkpoint = {
            "step": "final_review",
            "current": 2,
            "total": len(targets),
            "final_review_targets": targets,
            "final_review_done": [*targets[:2], "obsolete-segment"],
            "final_review_outcomes": {
                targets[0]: {"outcome": "resolved", "revised": True},
                targets[1]: {"outcome": "failed", "revised": False},
            },
        }
        segments[0].status = "ok"
        db.flush()
        progress = project_view(db, project)["progress"]
        assert progress["active_stage"] == "review"
        assert progress["current"]["done"] == 2
        assert progress["review"]["resolved"] == 1
        assert progress["review"]["revised"] == 1
        assert progress["review"]["failed"] == 1

        job.status = "completed"
        later = enqueue(db, project, "resolve_validations", {})
        later.status = "reviewing"
        later.checkpoint = {
            "step": "final_review",
            "total": 1,
            "final_review_targets": [targets[2]],
            "final_review_done": [targets[2]],
            "final_review_outcomes": {
                targets[2]: {"outcome": "protected", "revised": False},
            },
        }
        db.flush()
        cumulative = project_view(db, project)["progress"]["review"]
        assert cumulative["examined"] == 3
        assert cumulative["resolved"] == 1
        assert cumulative["failed"] == 1
        assert cumulative["protected"] == 1


def test_active_review_bar_uses_the_current_job_checkpoint(seeded):
    pid = seeded[0]
    with SessionLocal() as db:
        project = db.get(Project, pid)
        project.status = "reviewing"
        job = enqueue(db, project, "resolve_validations", {})
        job.status = "reviewing"
        job.checkpoint = {"step": "final_review", "current": 15, "total": 38}
        db.flush()

        progress = project_view(db, project)["progress"]
        assert progress["current"]["done"] == 15
        assert progress["current"]["total"] == 38
        assert progress["current"]["percent"] == 39

def test_progress_does_not_regress_to_analysis_after_translation_started(seeded):
    pid = seeded[0]
    with SessionLocal() as db:
        project = db.get(Project, pid)
        segment = db.scalar(select(Segment).where(Segment.project_id == pid))
        segment.status = "error"
        segment.error = "Invalid response"
        project.status = "ready"
        db.flush()
        progress = project_view(db, project)["progress"]
        assert progress["active_stage"] == "translation"


def test_automatic_analysis_job_uses_its_current_pipeline_stage(seeded):
    pid = seeded[0]
    with SessionLocal() as db:
        project = db.get(Project, pid)
        job = enqueue(db, project, "analyze", {"continue_pipeline": True})
        job.status = "translating"
        job.checkpoint = {"step": "translation", "current": 2, "total": 4}
        db.flush()

        assert project_view(db, project)["progress"]["active_stage"] == "translation"

        segments = list(db.scalars(select(Segment).where(Segment.project_id == pid)))
        targets = [segment.id for segment in segments]
        job.status = "reviewing"
        job.checkpoint = {
            "step": "final_review",
            "current": 1,
            "total": len(targets),
            "final_review_targets": targets,
            "final_review_done": targets[:1],
        }
        db.flush()

        progress = project_view(db, project)["progress"]
        assert progress["active_stage"] == "review"
        assert progress["current"]["done"] == 1
        assert progress["current"]["total"] == len(targets)


def test_checkpoint_syncs_translation_job_status_to_final_review(seeded):
    pid = seeded[0]
    owner = "test-worker"
    with SessionLocal() as db:
        project = db.get(Project, pid)
        job = enqueue(db, project, "translate", {})
        job.status = "translating"
        job.lease_owner = owner
        job.lease_until = time.time() + 60
        job_id = job.id
        db.commit()

    checkpoint(job_id, owner, {"step": "final_review", "current": 1, "total": 2})

    with SessionLocal() as db:
        assert db.get(Job, job_id).status == "reviewing"
        assert db.get(Project, pid).status == "reviewing"


def test_progress_exposes_the_active_job_model(seeded):
    pid = seeded[0]
    with SessionLocal() as db:
        project = db.get(Project, pid)
        primary = Provider(name="Primary", model="book-model", base_url="https://example.test")
        recovery = Provider(name="Recovery", model="recovery-model", base_url="https://example.test")
        db.add_all([primary, recovery])
        db.flush()
        project.provider_id = primary.id
        job = enqueue(db, project, "translate", {"provider_id": recovery.id})
        job.provider_id = recovery.id
        job.status = "translating"
        db.flush()

        assert project_view(db, project)["progress"]["model"] == "recovery-model"


def test_event_stream_starts_at_the_present_for_a_fresh_client(seeded):
    import pytest
    from fastapi import HTTPException

    from app.api.observability import stream_cursor
    from app.jobs.queue import emit
    from app.models import Event

    pid = seeded[0]
    with SessionLocal() as db:
        for number in range(250):
            emit(db, pid, status="translating", current=number)
        db.commit()
        latest = db.scalar(select(Event.id).where(Event.project_id == pid).order_by(Event.id.desc()))
        # A book opened after the fact must not replay its whole history.
        assert stream_cursor(db, pid, None, None) == latest
        # Reconnection and explicit replay keep their cursor.
        assert stream_cursor(db, pid, None, str(latest - 10)) == latest - 10
        assert stream_cursor(db, pid, 0, None) == 0
        assert stream_cursor(db, pid, 5, "40") == 40
        assert stream_cursor(db, "unknown-project", None, None) == 0
        with pytest.raises(HTTPException):
            stream_cursor(db, pid, None, "not-a-number")
