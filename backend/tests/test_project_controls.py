from fastapi.testclient import TestClient
from sqlalchemy import select

from app.api.projects import configure, import_book
from app.db import SessionLocal
from app.jobs.queue import claim, enqueue
from app.main import app
from app.models import Chapter, Job, Memory, Project, Provider, Segment, User
from app.schemas import ProjectConfig
from app.security import password_hash


def test_new_project_configuration_defaults_to_private_memory():
    assert ProjectConfig(title="Private by default").context_backend == "internal"


def test_first_provider_selection_starts_automatic_pipeline(seeded):
    pid, user_id, provider_id = seeded
    with SessionLocal() as db:
        project = db.get(Project, pid)
        project.provider_id = None
        body = ProjectConfig(
            title=project.title,
            author=project.author,
            series_name=project.series_name,
            volume_number=project.volume_number,
            source_language=project.source_language,
            target_language=project.target_language,
            provider_id=provider_id,
            quality=project.quality,
            context_backend=project.context_backend,
            instructions=project.instructions,
        )
        configured = configure(pid, body, db.get(User, user_id), db)
        assert configured["provider_id"] == provider_id

    with SessionLocal() as db:
        job = db.scalar(select(Job).where(Job.project_id == pid))
        assert job.operation == "analyze" and job.status == "pending"
        # Autopilot by default (AUTOPILOT_ENABLED): nothing waits for a person.
        assert job.options == {
            "continue_pipeline": True,
            "automatic_recovery": True,
            "full_review": True,
            "autopilot": True,
        }


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


def test_owner_can_assign_series_archive_and_restore_project(seeded, book_bytes):
    pid = seeded[0]
    with TestClient(app) as client:
        client.post("/api/auth/login", json={"username": "tester", "password": "test-password-123456789"})
        second = client.post(
            "/api/projects",
            files={"file": ("second.epub", book_bytes + b"\nsecond", "application/epub+zip")},
        ).json()
        configured = client.put(
            "/api/projects/batch/series",
            json={
                "project_ids": [pid, second["id"]],
                "series_name": "Mushoku Tensei",
                "first_volume": 3,
                "mode": "sequential",
            },
        )
        assert configured.status_code == 200
        assert configured.json()[0]["series_name"] == "Mushoku Tensei"
        assert [project["volume_number"] for project in configured.json()] == [3, 4]
        renamed = client.put(
            "/api/projects/batch/series",
            json={
                "project_ids": [pid, second["id"]],
                "series_name": "Mushoku Tensei FR",
                "mode": "preserve",
            },
        ).json()
        assert [project["series_name"] for project in renamed] == ["Mushoku Tensei FR"] * 2
        assert [project["volume_number"] for project in renamed] == [3, 4]
        assert client.put(
            "/api/projects/batch/series",
            json={"project_ids": [pid, pid], "series_name": "Duplicate", "first_volume": 1},
        ).status_code == 422
        cleared = client.put(
            "/api/projects/batch/series",
            json={"project_ids": [second["id"]], "mode": "clear"},
        ).json()
        assert cleared[0]["series_name"] == "" and cleared[0]["volume_number"] is None

        archived = client.post(f"/api/projects/{pid}/archive")
        assert archived.status_code == 200 and archived.json()["archived_at"] is not None
        assert all(project["id"] != pid for project in client.get("/api/projects").json())
        archived_list = client.get("/api/projects?include_archived=true").json()
        assert any(project["id"] == pid for project in archived_list)
        assert client.post(f"/api/projects/{pid}/jobs", json={"operation": "analyze"}).status_code == 409

        restored = client.post(f"/api/projects/{pid}/restore")
        assert restored.status_code == 200 and restored.json()["archived_at"] is None
        assert any(project["id"] == pid for project in client.get("/api/projects").json())


def test_duplicate_epub_is_rejected_even_when_archived(seeded, book_bytes):
    pid = seeded[0]
    with TestClient(app) as client:
        client.post("/api/auth/login", json={"username": "tester", "password": "test-password-123456789"})
        duplicate = client.post("/api/projects", files={"file": ("duplicate.epub", book_bytes)})
        assert duplicate.status_code == 409
        assert "déjà importé" in duplicate.json()["detail"]

        assert client.post(f"/api/projects/{pid}/archive").status_code == 200
        archived = client.post("/api/projects", files={"file": ("duplicate.epub", book_bytes)})
        assert archived.status_code == 409
        assert "projet archivé" in archived.json()["detail"]


def test_duplicate_epub_check_is_scoped_to_owner(seeded, book_bytes):
    with SessionLocal() as db:
        owner = User(username="second-owner", password_hash=password_hash("second-password-123456789"))
        db.add(owner)
        db.flush()
        imported = import_book(db, owner.id, book_bytes)
        db.commit()
        assert imported.owner_id == owner.id


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


def test_paused_job_with_explicit_provider_uses_new_project_provider_when_resumed(seeded):
    pid, _, original_provider_id = seeded
    with SessionLocal() as db:
        project = db.get(Project, pid)
        job = enqueue(db, project, "analyze", {"provider_id": original_provider_id})
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
        saved = db.get(Job, jid)
        assert saved.provider_id == alternate_id
        assert saved.options["provider_id"] == alternate_id


def test_archived_projects_cannot_restart_model_work(seeded):
    pid, _, provider_id = seeded
    with TestClient(app) as client:
        client.post("/api/auth/login", json={"username": "tester", "password": "test-password-123456789"})
        job = client.post(f"/api/projects/{pid}/jobs", json={"operation": "analyze"}).json()
        assert client.post(f"/api/projects/{pid}/jobs/{job['id']}/cancel").status_code == 200
        assert client.post(f"/api/projects/{pid}/archive").status_code == 200
        for action in ("resume", "retry"):
            refused = client.post(f"/api/projects/{pid}/jobs/{job['id']}/{action}")
            assert refused.status_code == 409 and "Restaurez" in refused.json()["detail"]
        # Choosing a first provider on an archived book must not enqueue the automatic pipeline.
        with SessionLocal() as db:
            db.get(Project, pid).provider_id = None
            db.commit()
        config = client.get(f"/api/projects/{pid}").json()
        body = {key: config[key] for key in ProjectConfig.model_fields if key in config}
        assert client.put(f"/api/projects/{pid}", json={**body, "provider_id": provider_id}).status_code == 200
        with SessionLocal() as db:
            statuses = [job.status for job in db.scalars(select(Job).where(Job.project_id == pid))]
        assert statuses == ["cancelled"]
        # Once restored, the same job can be resumed.
        assert client.post(f"/api/projects/{pid}/restore").status_code == 200
        assert client.post(f"/api/projects/{pid}/jobs/{job['id']}/resume").json()["status"] == "pending"


def test_failed_jobs_cannot_be_paused_and_job_conflicts_are_409(seeded):
    pid = seeded[0]
    with TestClient(app) as client:
        client.post("/api/auth/login", json={"username": "tester", "password": "test-password-123456789"})
        job = client.post(f"/api/projects/{pid}/jobs", json={"operation": "analyze"}).json()
        conflict = client.post(f"/api/projects/{pid}/jobs", json={"operation": "analyze", "force": True})
        assert conflict.status_code == 409 and "existe déjà" in conflict.json()["detail"]
        with SessionLocal() as db:
            db.get(Job, job["id"]).status = "failed"
            db.commit()
        paused = client.post(f"/api/projects/{pid}/jobs/{job['id']}/pause")
        assert paused.status_code == 409
        with SessionLocal() as db:
            assert db.get(Job, job["id"]).status == "failed"
        # A failed job never blocks the book: a new job can start, and cancelling it stays possible.
        restarted = client.post(f"/api/projects/{pid}/jobs", json={"operation": "analyze", "force": True})
        assert restarted.status_code == 202 and restarted.json()["id"] != job["id"]
        assert client.post(f"/api/projects/{pid}/jobs/{job['id']}/cancel").status_code == 200
