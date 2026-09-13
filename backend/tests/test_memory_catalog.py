import json

import respx
from sqlalchemy import select

from app.db import SessionLocal
from app.engines.context.providers import OpenVikingContextProvider
from app.engines.memory.catalog import queue_catalog
from app.jobs.worker import sync_outbox
from app.models import AppSetting, Outbox, Project
from app.providers.openviking import project_uri


def catalog_project(pid):
    with SessionLocal() as db:
        project = db.get(Project, pid)
        project.context_backend = "hybrid"
        project.bible = {"summary": "Book overview"}
        entry = queue_catalog(db, project)
        db.commit()
        return project, entry


@respx.mock
async def test_named_catalog_documents_have_links_and_are_not_narrative_events(seeded):
    project, entry = catalog_project(seeded[0])
    with SessionLocal() as db:
        db.add(AppSetting(key="openviking", value={"base_url": "https://ov.test"}))
        db.commit()
    route = respx.post("https://ov.test/api/v1/content/write").respond(
        200, json={"status": "ok", "result": {}}
    )
    await OpenVikingContextProvider().ingest(entry)
    payloads = [json.loads(c.request.content) for c in route.calls]
    assert any(p["uri"].endswith("/book.md") and project.title in p["content"] for p in payloads)
    assert all(p["uri"].startswith(project_uri(project) + "/") for p in payloads)
    assert all(p["processing_mode"] == "vectors_only" for p in payloads)
    assert "position" not in entry.payload
    assert f"{project_uri(project)}/characters.json" in entry.payload["files"]["book.md"]


def test_catalog_updates_are_coalesced_and_internal_mode_is_local_only(seeded):
    project, entry = catalog_project(seeded[0])
    with SessionLocal() as db:
        project = db.get(Project, project.id)
        again = queue_catalog(db, project)
        assert again.id == entry.id
        project.bible = {"summary": "Changed overview"}
        assert queue_catalog(db, project).id == entry.id
        db.commit()
        assert len(list(db.scalars(select(Outbox).where(Outbox.event_key.like("catalog:%"))))) == 1
        project.context_backend = "internal"
        assert queue_catalog(db, project) is None


async def test_old_catalog_ack_does_not_lose_new_snapshot(seeded, monkeypatch):
    project, entry = catalog_project(seeded[0])
    monkeypatch.setattr("app.jobs.worker.memory_config", lambda: {"base_url": "https://ov.test"})

    async def changed_during_write(_self, sent):
        with SessionLocal() as db:
            current = db.get(Outbox, sent.id)
            current.payload = {**current.payload, "fingerprint": "newer"}
            current.status = "pending"
            db.commit()

    monkeypatch.setattr(OpenVikingContextProvider, "ingest", changed_during_write)
    await sync_outbox(project.id)
    with SessionLocal() as db:
        assert db.get(Outbox, entry.id).status == "pending"
