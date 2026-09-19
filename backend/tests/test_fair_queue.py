"""Fair multi-user queue: order across accounts and tokens, priorities, quotas, queue view, settings."""

import time

import pytest
from fastapi.testclient import TestClient
from test_api_v1 import bearer, new_token, payload

from app.api.projects import import_book
from app.api.tokens import limiter
from app.db import SessionLocal
from app.jobs import fairness
from app.jobs.queue import claim, enqueue, suspend
from app.main import app
from app.models import AppSetting, Job, Project, Provider, TranslationRequest, User
from app.security import password_hash

PASSWORD = "test-password-123456789"


@pytest.fixture
def world(book_bytes):
    """Three accounts (one administrator) and one provider of capacity 1."""
    with SessionLocal() as db:
        users = {}
        for name in ("admin", "alice", "bob"):
            user = User(username=name, password_hash=password_hash(PASSWORD), admin=name == "admin")
            db.add(user)
            db.flush()
            users[name] = user.id
        provider = Provider(
            name="Mock", base_url="https://llm.test/v1", model="test-model",
            capabilities={"supports_json_schema": True}, context_window=64000, max_concurrency=1,
        )  # fmt: skip
        db.add(provider)
        db.commit()
        limiter.clear()
        return {"users": users, "provider": provider.id, "book": book_bytes, "count": 0}


def book(world, owner: str) -> str:
    world["count"] += 1
    with SessionLocal() as db:
        project = import_book(
            db, world["users"][owner], world["book"] + f"\n{owner}{world['count']}".encode()
        )
        project.provider_id = world["provider"]
        project.context_backend = "internal"
        db.commit()
        return project.id


def queued(world, owner: str, *, priority: int = 1, age: float = 0, token_id: str | None = None) -> str:
    project_id = book(world, owner)
    with SessionLocal() as db:
        job = enqueue(db, db.get(Project, project_id), "analyze", {}, priority=priority, token_id=token_id)
        job.queued_at = job.created_at = time.time() - age
        db.commit()
        return job.id


def capacity(world, value: int) -> None:
    with SessionLocal() as db:
        db.get(Provider, world["provider"]).max_concurrency = value
        db.commit()


def settings_row(**value) -> None:
    with SessionLocal() as db:
        db.merge(AppSetting(key="queue", value=value))
        db.commit()


def claimed() -> str | None:
    item = claim()
    return item[0] if item else None


def login(username: str) -> TestClient:
    client = TestClient(app)
    client.__enter__()
    assert (
        client.post("/api/auth/login", json={"username": username, "password": PASSWORD}).status_code == 200
    )
    return client


def test_a_new_account_goes_before_the_backlog_of_a_busy_one(world):
    capacity(world, 3)
    alice = [queued(world, "alice", age=300 - index) for index in range(3)]
    bob = queued(world, "bob", age=10)
    # FIFO would run Alice's three books first; the fair queue alternates between accounts.
    assert claimed() == alice[0]
    assert claimed() == bob
    assert claimed() == alice[1]
    assert claimed() is None  # the provider limit still applies


def test_accounts_of_equal_load_take_turns(world):
    capacity(world, 1)
    first = queued(world, "alice", age=200)
    second = queued(world, "alice", age=190)
    bob = queued(world, "bob", age=100)
    running = claim()
    assert running[0] == first
    with SessionLocal() as db:
        db.get(Job, first).claimed_at = time.time()
        db.commit()
    suspend(first, running[1], "paused", "test")
    # Both accounts have nothing running; Alice was served last: Bob's turn, although queued later.
    assert claimed() == bob
    assert second


def test_priority_comes_first_and_waiting_raises_it(world):
    capacity(world, 1)
    settings_row(aging_minutes=60)
    normal = queued(world, "alice", age=600)
    high = queued(world, "bob", priority=fairness.HIGH, age=5)
    assert claimed() == high
    capacity(world, 2)
    low_old = queued(world, "bob", priority=fairness.LOW, age=3 * 3600)  # two levels up: high
    assert claimed() == low_old
    assert normal


def test_expired_leases_are_resumed_before_new_jobs(world):
    capacity(world, 1)
    first = queued(world, "alice", age=100)
    assert claimed() == first
    queued(world, "bob", priority=fairness.HIGH)
    with SessionLocal() as db:
        db.get(Job, first).lease_until = 1  # its worker died
        db.commit()
    assert claimed() == first


def test_account_and_token_running_quotas_make_jobs_wait(world):
    capacity(world, 5)
    settings_row(max_running_per_account=1)
    alice = [queued(world, "alice", age=100 - index) for index in range(2)]
    bob = queued(world, "bob", age=1)
    assert claimed() == alice[0]
    assert claimed() == bob
    assert claimed() is None  # Alice's second book waits for her first one
    with SessionLocal() as db:
        view = fairness.snapshot(db, time.time())
    reasons = {entry["job_id"]: entry["reason"] for entry in view["waiting"]}
    assert reasons == {alice[1]: "account_limit"}

    settings_row(max_running_per_account=1, accounts={world["users"]["bob"]: {"max_running": 0}})
    from app.models import ApiToken

    with SessionLocal() as db:
        token = ApiToken(owner_id=world["users"]["bob"], name="t", token_hash="x" * 64, prefix="lbr_x",
                         scopes=[], max_running=1)  # fmt: skip
        db.add(token)
        db.commit()
        token_id = token.id
    first = queued(world, "bob", token_id=token_id, age=50)
    second = queued(world, "bob", token_id=token_id, age=40)
    other = queued(world, "bob", age=30)
    picked = {claimed(), claimed(), claimed()}
    assert first in picked and other in picked and second not in picked
    with SessionLocal() as db:
        reasons = {
            entry["job_id"]: entry["reason"] for entry in fairness.snapshot(db, time.time())["waiting"]
        }
    assert reasons[second] == "token_limit"


def test_queue_view_gives_each_waiting_job_its_place_and_reason(world):
    capacity(world, 1)
    running = queued(world, "alice", age=100)
    assert claimed() == running
    first = queued(world, "bob", age=50)
    second = queued(world, "alice", age=60)
    later = queued(world, "bob", age=10)
    with SessionLocal() as db:
        job = db.get(Job, later)
        job.status, job.next_attempt = "waiting", time.time() + 600
        db.commit()
    bob = login("bob")
    try:
        view = bob.get("/api/queue").json()
        waiting = {entry["job_id"]: entry for entry in view["waiting"]}
        # Bob sees his own jobs only, with their global place in the provider's line.
        assert set(waiting) == {first, later}
        assert waiting[first]["reason"] == "provider_busy" and waiting[first]["position"] == 1
        assert waiting[later]["reason"] == "retry_scheduled" and waiting[later]["position"] is None
        assert "owner_id" not in waiting[first] and waiting[first]["mine"]
        assert view["running"] == [] and view["totals"] == {"waiting": 3, "running": 1}
        assert view["account"]["max_priority"] == "normal"
    finally:
        bob.__exit__(None, None, None)
    admin = login("admin")
    try:
        view = admin.get("/api/queue").json()
        order = [entry["job_id"] for entry in view["waiting"]]
        assert order[:2] == [first, second]  # Bob has nothing running, Alice has one
        assert [entry["job_id"] for entry in view["running"]] == [running]
        assert view["running"][0]["owner"] == "alice"
        assert admin.get("/api/queue", params={"project_id": "missing"}).status_code == 404
    finally:
        admin.__exit__(None, None, None)


def test_priorities_are_limited_to_what_the_account_may_ask(world):
    project_id = book(world, "alice")
    alice = login("alice")
    admin = login("admin")
    try:
        refused = alice.post(
            f"/api/projects/{project_id}/jobs", json={"operation": "analyze", "priority": "high"}
        )
        assert refused.status_code == 403
        assert refused.json()["detail"]["code"] == "priority_not_allowed"
        english = alice.post(
            f"/api/projects/{project_id}/jobs",
            json={"operation": "analyze", "priority": "high"},
            headers={"Accept-Language": "en"},
        )
        assert english.json()["detail"]["message"].startswith("Priority “high” refused")
        started = alice.post(
            f"/api/projects/{project_id}/jobs", json={"operation": "analyze", "priority": "low"}
        )
        assert started.status_code == 202 and started.json()["priority"] == fairness.LOW
        job_id = started.json()["id"]
        assert alice.put(f"/api/queue/{job_id}/priority", json={"priority": "high"}).status_code == 403
        # An administrator may raise it, and give Alice the right to ask for high priority.
        assert (
            admin.put(f"/api/queue/{job_id}/priority", json={"priority": "high"}).json()["priority"] == "high"
        )
        saved = admin.put(
            "/api/settings/queue",
            json={"accounts": [{"user_id": world["users"]["alice"], "max_priority": "high"}]},
        )
        assert saved.status_code == 200
        assert saved.json()["accounts"][0]["username"] == "alice"
        assert alice.put(f"/api/queue/{job_id}/priority", json={"priority": "normal"}).status_code == 200
        assert alice.get("/api/queue").json()["account"]["max_priority"] == "high"
        assert alice.get("/api/settings/queue").status_code == 403
    finally:
        alice.__exit__(None, None, None)
        admin.__exit__(None, None, None)


def test_a_full_queue_refuses_new_jobs_with_429(world):
    settings_row(max_queued_per_account=1)
    first, second = book(world, "alice"), book(world, "alice")
    alice = login("alice")
    try:
        assert alice.post(f"/api/projects/{first}/jobs", json={"operation": "analyze"}).status_code == 202
        refused = alice.post(f"/api/projects/{second}/jobs", json={"operation": "analyze"})
        assert refused.status_code == 429
        assert refused.json()["detail"]["code"] == "queue_full"
        assert refused.json()["detail"]["scope"] == "account"
        with SessionLocal() as db:
            assert db.query(Job).filter(Job.project_id == second).count() == 0
        # Once the first one runs, its place is free.
        assert claimed()
        assert alice.post(f"/api/projects/{second}/jobs", json={"operation": "analyze"}).status_code == 202
    finally:
        alice.__exit__(None, None, None)


def test_queue_settings_are_saved_and_reset_by_an_administrator(world):
    admin = login("admin")
    try:
        view = admin.get("/api/settings/queue").json()
        assert view["saved"] is False and view["values"] == view["defaults"]
        saved = admin.put(
            "/api/settings/queue",
            json={"max_running_per_account": 2, "max_queued_per_account": 10, "aging_minutes": 15},
        ).json()
        assert saved["saved"] and saved["values"]["max_running_per_account"] == 2
        unknown = admin.put(
            "/api/settings/queue", json={"accounts": [{"user_id": "nobody", "max_running": 1}]}
        )
        assert unknown.status_code == 404
        assert admin.delete("/api/settings/queue").json()["saved"] is False
    finally:
        admin.__exit__(None, None, None)


def test_api_tokens_carry_a_priority_ceiling_and_quotas(world):
    alice = login("alice")
    admin = login("admin")
    try:
        refused = alice.post(
            "/api/tokens", json={"name": "t", "scopes": ["jobs:read"], "max_priority": "high"}
        )
        assert refused.status_code == 403
        created = alice.post(
            "/api/tokens", json={"name": "t", "scopes": ["jobs:read"], "max_priority": "low", "max_queued": 3}
        ).json()
        assert (
            created["max_priority"] == "low" and created["max_queued"] == 3 and created["max_running"] is None
        )
        changed = alice.put(
            f"/api/tokens/{created['id']}/queue", json={"max_priority": "normal", "max_running": 2}
        )
        assert changed.json()["max_priority"] == "normal" and changed.json()["max_running"] == 2
        assert admin.put(f"/api/tokens/{created['id']}/queue", json={}).status_code == 404
    finally:
        alice.__exit__(None, None, None)
        admin.__exit__(None, None, None)


def test_automation_requests_ask_for_a_priority_and_respect_quotas(world):
    alice = login("alice")
    try:
        secret = new_token(alice, max_priority="normal", max_queued=1)
    finally:
        alice.__exit__(None, None, None)
    with TestClient(app) as api:
        body = payload(world["provider"])
        body["pipeline"]["priority"] = "high"
        refused = api.post("/api/v1/translation-requests", json=body, headers=bearer(secret))
        assert refused.status_code == 403 and refused.json()["detail"]["code"] == "priority_not_allowed"
        body["pipeline"]["priority"] = "low"
        accepted = api.post("/api/v1/translation-requests", json=body, headers=bearer(secret))
        assert accepted.status_code == 202, accepted.text
        request_id = accepted.json()["request_id"]
        with SessionLocal() as db:
            request = db.get(TranslationRequest, request_id)
            job = db.get(Job, request.job_id)
            assert job.priority == fairness.LOW and job.token_id == request.token_id
        status = api.get(f"/api/v1/translation-requests/{request_id}", headers=bearer(secret)).json()
        assert status["priority"] == "low"
        assert status["queue"]["position"] == 1 and status["queue"]["reason"] == "starting"
        # The token keeps one request waiting at most.
        again = payload(world["provider"], external_id="saga-volume-2")
        again["volume"] = {"external_id": "volume-2", "number": 2, "title": "Volume 2"}
        full = api.post("/api/v1/translation-requests", json=again, headers=bearer(secret))
        assert full.status_code == 429
        assert full.json()["detail"] | {"message": ""} == {
            "code": "queue_full", "scope": "token", "limit": 1, "message": "",
        }  # fmt: skip
        # A replay of the accepted request is still answered (idempotence comes first).
        replay = api.post("/api/v1/translation-requests", json=body, headers=bearer(secret))
        assert replay.status_code == 200


def test_migrated_jobs_keep_their_order_by_creation(world):
    """Jobs queued before the fair queue have no queue time: their creation time stands in."""
    capacity(world, 1)
    older = queued(world, "alice", age=100)
    newer = queued(world, "alice", age=50)
    with SessionLocal() as db:
        for job_id in (older, newer):
            db.get(Job, job_id).queued_at = 0
        db.commit()
    assert claimed() == older
    assert newer
