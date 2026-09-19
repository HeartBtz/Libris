"""Analysis mode and threads chosen by a volume, a launch and an automation request."""

import test_api_v1
from analysis_world import build_world, create_world
from sqlalchemy import select
from test_api_v1 import bearer, new_token, payload

from app.db import SessionLocal
from app.models import Job, Project, User

# The automation API's own fixtures: a provider, the owner's session and a cookie-less client.
provider_id, owner, api = test_api_v1.provider_id, test_api_v1.owner, test_api_v1.api


def test_an_api_request_chooses_its_analysis_mode_and_threads(owner, api, provider_id):
    secret = new_token(owner)
    body = payload(provider_id)
    body["pipeline"].update(analysis_mode="strict", threads=3)
    created = api.post("/api/v1/translation-requests", headers=bearer(secret), json=body)
    assert created.status_code == 202, created.text
    with SessionLocal() as db:
        job = db.get(Job, created.json()["job_id"])
        assert job.options["analysis_mode"] == "strict" and job.options["threads"] == 3
    status = api.get(created.json()["status_url"], headers=bearer(secret)).json()
    assert status["options"]["analysis_mode"] == "strict" and status["options"]["threads"] == 3
    assert status["progress"]["analysis"] is None  # the job has not started its analysis yet
    body = payload(provider_id, external_id="saga-volume-2")
    body["pipeline"]["threads"] = 0
    refused = api.post("/api/v1/translation-requests", headers=bearer(secret), json=body)
    assert refused.status_code == 422


def test_a_volume_keeps_its_analysis_mode_and_threads(owner):
    world = build_world(chapters=2, volumes=1)
    ids, _ = create_world(world, capacity=8)
    with SessionLocal() as db:
        project = db.get(Project, ids[0])
        project.owner_id = db.scalar(select(User.id).where(User.username == "owner"))
        db.commit()
    current = owner.get(f"/api/projects/{ids[0]}").json()
    fields = (
        "title",
        "author",
        "source_language",
        "target_language",
        "provider_id",
        "quality",
        "context_backend",
    )
    answer = owner.put(
        f"/api/projects/{ids[0]}",
        json={**{k: current[k] for k in fields}, "analysis_mode": "strict", "threads": 2},
    )
    assert answer.status_code == 200, answer.text
    with SessionLocal() as db:
        config = db.get(Project, ids[0]).config
    assert config["analysis_mode"] == "strict" and config["threads"] == 2
    started = owner.post(f"/api/projects/{ids[0]}/jobs", json={"operation": "analyze", "threads": 5})
    assert started.status_code == 202, started.text
    with SessionLocal() as db:
        assert db.get(Job, started.json()["id"]).options["threads"] == 5
