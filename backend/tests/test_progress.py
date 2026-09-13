from sqlalchemy import select

from app.api.projects import project_view
from app.db import SessionLocal
from app.jobs.queue import enqueue
from app.models import Chapter, Project, Segment


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
            "total": len(targets),
            "final_review_targets": targets,
            "final_review_done": targets[:2],
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
