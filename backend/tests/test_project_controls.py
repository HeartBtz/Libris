from fastapi.testclient import TestClient
from sqlalchemy import select

from app.db import SessionLocal
from app.jobs.queue import claim, enqueue
from app.main import app
from app.models import Chapter, Job, Memory, Project, Provider, Segment


def test_explicit_resume_of_cancelled_job_keeps_analysis_checkpoint(seeded):
    pid, _, _ = seeded
    with SessionLocal() as db:
        project = db.get(Project, pid)
        job = enqueue(db, project, "analyze", {})
        job.checkpoint = {"step": "chapter_analysis", "current": 2}
        sid = db.scalar(select(Segment.id).where(Segment.project_id == pid))
        db.add(
            Memory(project_id=pid, segment_id=sid, position=0, kind="analysis", content={"summary": "Saved"})
        )
        db.commit()
        jid = job.id
    with TestClient(app) as client:
        client.post("/api/auth/login", json={"username": "tester", "password": "test-password-123456789"})
        assert client.post(f"/api/projects/{pid}/jobs/{jid}/cancel").status_code == 200
        assert claim() is None
        response = client.post(f"/api/projects/{pid}/jobs/{jid}/resume")
        assert response.status_code == 200 and response.json()["checkpoint"]["current"] == 2
        assert client.get(f"/api/projects/{pid}").json()["stats"]["analyzed_segments"] == 1
    assert claim()[0] == jid


def test_owner_can_confirm_delete_with_active_job(seeded):
    pid = seeded[0]
    with SessionLocal() as db:
        enqueue(db, db.get(Project, pid), "analyze", {})
        db.commit()
    with TestClient(app) as client:
        client.post("/api/auth/login", json={"username": "tester", "password": "test-password-123456789"})
        current = client.get(f"/api/projects/{pid}").json()
        config = {
            key: current[key]
            for key in (
                "title",
                "author",
                "source_language",
                "target_language",
                "provider_id",
                "quality",
                "context_backend",
                "instructions",
            )
        }
        config.update(series_name="Active series", volume_number=1)
        assert client.put(f"/api/projects/{pid}", json=config).status_code == 200
        assert client.post(f"/api/projects/{pid}/archive").status_code == 409
        assert client.delete(f"/api/projects/{pid}").status_code == 409
        assert client.delete(f"/api/projects/{pid}?stop_jobs=true").status_code == 200
        assert client.get(f"/api/projects/{pid}").status_code == 404
    with SessionLocal() as db:
        assert db.scalar(select(Job).where(Job.project_id == pid)) is None


def test_owner_can_assign_series_archive_and_restore_project(seeded):
    pid = seeded[0]
    with TestClient(app) as client:
        client.post("/api/auth/login", json={"username": "tester", "password": "test-password-123456789"})
        configured = client.put(
            "/api/projects/batch/series",
            json={"project_ids": [pid], "series_name": "Mushoku Tensei", "first_volume": 1},
        )
        assert configured.status_code == 200
        assert configured.json()[0]["series_name"] == "Mushoku Tensei"
        assert configured.json()[0]["volume_number"] == 1
        assert client.put(
            "/api/projects/batch/series",
            json={"project_ids": [pid, pid], "series_name": "Duplicate", "first_volume": 1},
        ).status_code == 422

        archived = client.post(f"/api/projects/{pid}/archive")
        assert archived.status_code == 200 and archived.json()["archived_at"] is not None
        assert all(project["id"] != pid for project in client.get("/api/projects").json())
        archived_list = client.get("/api/projects?include_archived=true").json()
        assert any(project["id"] == pid for project in archived_list)
        assert client.post(f"/api/projects/{pid}/jobs", json={"operation": "analyze"}).status_code == 409

        restored = client.post(f"/api/projects/{pid}/restore")
        assert restored.status_code == 200 and restored.json()["archived_at"] is None
        assert any(project["id"] == pid for project in client.get("/api/projects").json())


def test_analyze_already_completed_is_idempotent_and_does_not_queue(seeded):
    pid = seeded[0]
    with SessionLocal() as db:
        for segment in db.scalars(select(Segment).where(Segment.project_id == pid)):
            db.add(
                Memory(
                    project_id=pid,
                    segment_id=segment.id,
                    position=segment.position,
                    kind="analysis",
                    content={"summary": "Saved analysis"},
                )
            )
        for chapter in db.scalars(select(Chapter).where(Chapter.project_id == pid)):
            chapter.analyzed = True
        project = db.get(Project, pid)
        project.bible, project.status = {"summary": "Complete"}, "ready"
        db.commit()
    with TestClient(app) as client:
        client.post("/api/auth/login", json={"username": "tester", "password": "test-password-123456789"})
        first = client.post(f"/api/projects/{pid}/jobs", json={"operation": "analyze"})
        second = client.post(f"/api/projects/{pid}/jobs", json={"operation": "analyze"})
        assert first.status_code == 202 and first.json()["status"] == "completed"
        assert first.json()["id"] == second.json()["id"]
    assert claim() is None


def test_paused_job_uses_new_project_provider_when_resumed(seeded):
    pid = seeded[0]
    with SessionLocal() as db:
        project = db.get(Project, pid)
        job = enqueue(db, project, "analyze", {})
        alternate = Provider(
            name="Alternate",
            base_url="https://alternate.test/v1",
            model="alternate-model",
            capabilities={"supports_json_schema": True},
            context_window=64000,
        )
        db.add(alternate)
        db.commit()
        jid, alternate_id = job.id, alternate.id

    with TestClient(app) as client:
        client.post("/api/auth/login", json={"username": "tester", "password": "test-password-123456789"})
        assert client.post(f"/api/projects/{pid}/jobs/{jid}/pause").status_code == 200
        current = client.get(f"/api/projects/{pid}").json()
        config = {
            key: current[key]
            for key in (
                "title",
                "author",
                "source_language",
                "target_language",
                "quality",
                "context_backend",
                "instructions",
            )
        }
        config["provider_id"] = alternate_id
        assert client.put(f"/api/projects/{pid}", json=config).status_code == 200
        resumed = client.post(f"/api/projects/{pid}/jobs/{jid}/resume")
        assert resumed.status_code == 200 and resumed.json()["provider_id"] == alternate_id

    with SessionLocal() as db:
        assert db.get(Job, jid).provider_id == alternate_id
