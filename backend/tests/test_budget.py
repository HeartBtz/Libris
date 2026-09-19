"""Cost budgets: estimate against the cap before a launch, cheaper provider or pause near it, token caps."""

import time

import pytest
import respx
from fastapi.testclient import TestClient
from sqlalchemy import select
from test_api_v1 import ALL, bearer, payload
from test_pipeline import mock_completion

from app.api.tokens import limiter
from app.config import settings
from app.db import SessionLocal
from app.engines.budget import book_spent, period_start, token_spent
from app.engines.delivery.lifecycle import finalize
from app.jobs.launch import launch
from app.jobs.queue import claim
from app.jobs.worker import execute
from app.main import app
from app.models import ApiToken, AutopilotDecision, Job, Project, Provider, RequestLog, TranslationRequest

PASSWORD = "test-password-123456789"
# Per call of the synthetic model (120 prompt + 80 completion tokens): 0.02 at 100 per million.
PRICE = 100.0
CALL = 0.02


@pytest.fixture(autouse=True)
def quiet(monkeypatch):
    monkeypatch.setattr(settings(), "final_review_enabled", False)
    limiter.clear()


def client() -> TestClient:
    value = TestClient(app)
    value.__enter__()
    assert value.post("/api/auth/login", json={"username": "tester", "password": PASSWORD}).status_code == 200
    return value


def priced(provider_id: str, price: float = PRICE) -> None:
    with SessionLocal() as db:
        provider = db.get(Provider, provider_id)
        provider.input_cost = provider.output_cost = price
        db.commit()


def spend(project_id: str, provider_id: str, amount: float) -> None:
    """Past model calls of the book worth `amount` (at 1 per million tokens)."""
    with SessionLocal() as db:
        db.add(RequestLog(project_id=project_id, provider_id=provider_id, operation="translation", model="m",
                          fingerprint="past", parameters={}, messages=[], status="success",
                          prompt_tokens=int(amount * 1_000_000), completion_tokens=0,
                          input_cost=1.0, output_cost=0.0))  # fmt: skip
        db.commit()


def cap(project_id: str, amount: float | None) -> None:
    with SessionLocal() as db:
        project = db.get(Project, project_id)
        project.config = {**project.config, "budget_amount": amount}
        db.commit()


def backup(project_id: str, price: float = 0.0) -> str:
    with SessionLocal() as db:
        provider = Provider(name="Cheap", base_url="https://cheap.test/v1", model="cheap-model",
                            capabilities={"supports_json_schema": True}, context_window=64000,
                            input_cost=price, output_cost=price)  # fmt: skip
        db.add(provider)
        db.flush()
        project = db.get(Project, project_id)
        project.config = {**project.config, "fallback_provider_ids": [provider.id]}
        db.commit()
        return provider.id


async def run(job_id: str, turns: int = 40) -> Job:
    """Runs the job until it ends or stops for a person (paused)."""
    for _ in range(turns):
        with SessionLocal() as db:
            job = db.get(Job, job_id)
            if job.status in {"completed", "failed", "cancelled", "paused"}:
                return job
            assert job.status in {"pending", "waiting"}, (job.status, job.stop_reason, job.error)
            job.next_attempt = 0
            db.commit()
        item = claim()
        assert item, "the job must be claimable"
        await execute(*item)
    raise AssertionError("the job never ended")


def test_the_estimate_is_compared_with_the_budget_and_a_launch_warns_or_is_refused(seeded, monkeypatch):
    pid, _, provider_id = seeded
    priced(provider_id)
    web = client()
    free = web.get(f"/api/projects/{pid}/estimate", params={"operation": "analyze"}).json()
    assert free["budget"] is None and free["cost"] > 0
    cap(pid, free["cost"] / 2)
    estimate = web.get(f"/api/projects/{pid}/estimate", params={"operation": "analyze"}).json()
    assert estimate["budget"]["exceeds"] is True and estimate["budget"]["on_estimate"] == "warn"
    assert estimate["budget"]["remaining"] == pytest.approx(free["cost"] / 2)

    # Warn (the default): the job starts and keeps the estimate and the warning.
    started = web.post(f"/api/projects/{pid}/jobs", json={"operation": "analyze", "autopilot": False})
    assert started.status_code == 202, started.text
    budget = started.json()["options"]["budget"]
    assert budget["estimate"] == pytest.approx(free["cost"]) and "sera mis en pause" in budget["warning"]
    assert web.post(f"/api/projects/{pid}/jobs/{started.json()['id']}/cancel").status_code == 200

    # Refuse: the launch is refused with a code, in English when asked.
    monkeypatch.setattr(settings(), "budget_on_estimate", "refuse")
    refused = web.post(
        f"/api/projects/{pid}/jobs", json={"operation": "analyze"}, headers={"Accept-Language": "en"}
    )
    assert refused.status_code == 409
    assert refused.json()["detail"]["code"] == "budget_exceeded"
    assert "launch refused" in refused.json()["detail"]["message"]
    web.__exit__(None, None, None)


def test_a_reached_cap_refuses_launches_and_resumes_until_it_is_raised(seeded):
    pid, _, provider_id = seeded
    priced(provider_id)
    spend(pid, provider_id, 3.0)
    web = client()
    assert web.put(f"/api/projects/{pid}/budget", json={"amount": 2.5}).status_code == 200
    view = web.get(f"/api/projects/{pid}/budget").json()
    assert view["state"] == "exceeded" and view["spent"] == pytest.approx(3.0) and view["remaining"] == 0
    refused = web.post(f"/api/projects/{pid}/jobs", json={"operation": "analyze"})
    assert refused.status_code == 409 and "Budget du livre atteint (3.00 sur 2.50)" in refused.text
    # 0 turns the budget off for this book, even with an installation default.
    assert web.put(f"/api/projects/{pid}/budget", json={"amount": 0}).json()["state"] == "none"
    assert web.post(f"/api/projects/{pid}/jobs", json={"operation": "analyze"}).status_code == 202
    web.__exit__(None, None, None)


def test_the_installation_default_applies_to_books_without_their_own(seeded):
    pid, _, provider_id = seeded
    spend(pid, provider_id, 1.0)
    web = client()
    assert web.get("/api/settings/budget").json()["saved"] is False
    saved = web.put(
        "/api/settings/budget", json={"default_book": 0.5, "switch_threshold": 0.8, "on_estimate": "warn"}
    )
    assert saved.status_code == 200 and saved.json()["values"]["default_book"] == 0.5
    view = web.get(f"/api/projects/{pid}/budget").json()
    assert view["amount"] == 0.5 and view["own_amount"] is None and view["state"] == "exceeded"
    assert view["switch_threshold"] == 0.8
    assert web.delete("/api/settings/budget").json()["values"]["default_book"] == 0
    assert web.get(f"/api/projects/{pid}/budget").json()["state"] == "none"
    web.__exit__(None, None, None)


@respx.mock
async def test_near_the_cap_the_job_moves_to_a_cheaper_provider_and_ends(seeded):
    pid, _, provider_id = seeded
    priced(provider_id)
    cheap = backup(pid)
    respx.post("https://llm.test/v1/chat/completions").mock(side_effect=mock_completion)
    respx.post("https://cheap.test/v1/chat/completions").mock(side_effect=mock_completion)
    cap(pid, 3 * CALL)
    with SessionLocal() as db:
        job, reason = launch(db, db.get(Project, pid), "pipeline")
        assert job, reason
        assert job.options["budget"]["estimate"] > 0
        db.commit()
        jid = job.id

    job = await run(jid)

    assert job.status == "completed", (job.stop_reason, job.error)
    assert job.provider_id == cheap
    with SessionLocal() as db:
        # Three priced calls at most: from 90 % of the cap on, every call went to the free provider.
        assert book_spent(db, pid) == pytest.approx(3 * CALL)
        decisions = list(db.scalars(select(AutopilotDecision).where(AutopilotDecision.stage == "budget")))
        assert [(d.kind, d.action) for d in decisions] == [("book", "fallback_provider")]
        assert "le travail continue avec « Cheap »" in decisions[0].reason
    web = client()
    report = web.get(f"/api/projects/{pid}/autopilot").json()["report"]
    assert report["cost"]["actual"] == pytest.approx(3 * CALL) and report["cost"]["estimated"] > 0
    assert report["cost"]["provider_switches"] == 1
    view = web.get(f"/api/projects/{pid}/budget").json()
    assert view["last_job"]["id"] == jid and view["state"] == "exceeded"
    web.__exit__(None, None, None)


@respx.mock
async def test_without_a_cheaper_provider_the_job_pauses_and_resumes_once_the_budget_is_raised(seeded):
    pid, _, provider_id = seeded
    priced(provider_id)
    respx.post("https://llm.test/v1/chat/completions").mock(side_effect=mock_completion)
    cap(pid, 2 * CALL)
    with SessionLocal() as db:
        job, reason = launch(db, db.get(Project, pid), "pipeline")
        assert job, reason
        db.commit()
        jid = job.id

    job = await run(jid)

    assert job.status == "paused" and job.stop_reason == "budget_exceeded"
    assert "Budget du livre atteint à 100 %" in job.error and "reprenez le travail" in job.error
    with SessionLocal() as db:
        assert db.get(Project, pid).status == "paused"
        assert book_spent(db, pid) == pytest.approx(2 * CALL)
    web = client()
    refused = web.post(f"/api/projects/{pid}/jobs/{jid}/resume", headers={"Accept-Language": "en"})
    assert refused.status_code == 409 and refused.json()["detail"]["code"] == "budget_exceeded"
    assert "Book budget reached" in refused.json()["detail"]["message"]
    web.put(f"/api/projects/{pid}/budget", json={"amount": 100})
    assert web.post(f"/api/projects/{pid}/jobs/{jid}/resume").status_code == 200
    web.__exit__(None, None, None)

    job = await run(jid)
    assert job.status == "completed", (job.stop_reason, job.error)


def token_request(db, token_id: str, cost: float | None, finished_at: float, job_id: str | None = None):
    owner = db.get(ApiToken, token_id).owner_id
    db.add(TranslationRequest(owner_id=owner, token_id=token_id, payload_sha256="x", status="completed",
                              finished_at=finished_at, cost=cost, job_id=job_id))  # fmt: skip


def test_a_token_over_its_budget_is_refused_new_work_with_a_clear_error(seeded):
    pid, _, provider_id = seeded
    web = client()
    created = web.post("/api/tokens", json={"name": "Nightly", "scopes": ALL, "budget_amount": 1.0})
    assert created.status_code == 201, created.text
    token = created.json()
    assert token["budget"] == {"amount": 1.0, "period": "month", "spent": 0, "resets_at": pytest.approx(
        token["budget"]["resets_at"])}  # fmt: skip
    now = time.time()
    with SessionLocal() as db:
        # Last month's request does not count against this month's cap; this month's do.
        token_request(db, token["id"], 5.0, period_start("month", now) - 10)
        token_request(db, token["id"], 0.6, now)
        db.commit()
        assert token_spent(db, db.get(ApiToken, token["id"])) == pytest.approx(0.6)
    api = TestClient(app)
    accepted = api.post("/api/v1/translation-requests", json=payload(provider_id, external_id="first"),
                        headers=bearer(token["token"]))  # fmt: skip
    assert accepted.status_code == 202, accepted.text
    with SessionLocal() as db:
        token_request(db, token["id"], 0.5, now)
        db.commit()
    refused = api.post("/api/v1/translation-requests", json=payload(provider_id, external_id="second"),
                       headers=bearer(token["token"], **{"Accept-Language": "en"}))  # fmt: skip
    assert refused.status_code == 402
    detail = refused.json()["detail"]
    assert detail["code"] == "budget_exceeded" and detail["budget"]["period"] == "month"
    assert detail["budget"]["spent"] == pytest.approx(1.1) and detail["budget"]["resets_at"] > now
    assert "API token budget “Nightly” reached (1.10 of 1.00, this month)" in detail["message"]
    # An import alone costs nothing: it is still accepted.
    imported = api.post("/api/v1/translation-requests", headers=bearer(token["token"]), json=payload(
        provider_id, external_id="third", pipeline={"start": False}))  # fmt: skip
    assert imported.status_code == 202, imported.text
    # Over the token's whole life, last month counts too; raising the cap lets work start again.
    changed = web.put(f"/api/tokens/{token['id']}/budget", json={"amount": 10, "period": "total"})
    assert changed.json()["budget"]["spent"] == pytest.approx(6.1)
    listed = web.get("/api/tokens").json()
    assert listed[0]["budget"]["amount"] == 10 and listed[0]["budget"]["resets_at"] is None
    assert web.put(f"/api/tokens/{token['id']}/budget", json={"amount": None}).json()["budget"] is None
    web.__exit__(None, None, None)


def test_an_ended_request_keeps_its_cost_for_the_token_budgets(seeded):
    pid, user_id, provider_id = seeded
    with SessionLocal() as db:
        request = TranslationRequest(owner_id=user_id, payload_sha256="x", status="running")
        db.add(request)
        db.flush()
        finalize(request, "completed", report={"usage": {"cost": 0.25}})
        assert request.cost == 0.25
        finalize(request, "cancelled")
        assert request.cost is None


@respx.mock
async def test_a_token_cap_pauses_its_request_and_the_report_gives_estimated_and_real_cost(seeded):
    from app.jobs.requests import dispatch

    _, _, provider_id = seeded
    priced(provider_id)
    respx.post("https://llm.test/v1/chat/completions").mock(side_effect=mock_completion)
    web = client()
    token = web.post("/api/tokens", json={"name": "Nightly", "scopes": ALL, "budget_amount": 2 * CALL}).json()
    api = TestClient(app)
    body = payload(provider_id)
    body["pipeline"]["final_review"] = False
    created = api.post("/api/v1/translation-requests", json=body, headers=bearer(token["token"])).json()
    job = await run(created["job_id"])
    assert job.status == "paused" and job.stop_reason == "budget_exceeded"
    assert "Budget du jeton d’API « Nightly » atteint à 100 %" in job.error
    status = api.get(
        created["status_url"], headers=bearer(token["token"], **{"Accept-Language": "en"})
    ).json()
    assert status["status"] == "paused" and status["stop_reason"] == "budget_exceeded"
    assert "API token budget “Nightly” at 100%" in status["error"]
    resumed = api.post(created["status_url"] + "/resume", headers=bearer(token["token"]))
    assert resumed.status_code == 409 and resumed.json()["detail"]["code"] == "budget_exceeded"

    web.put(f"/api/tokens/{token['id']}/budget", json={"amount": 100, "period": "month"})
    assert api.post(created["status_url"] + "/resume", headers=bearer(token["token"])).status_code == 200
    job = await run(created["job_id"])
    assert job.status == "completed", job.error
    dispatch()
    report = api.get(created["status_url"], headers=bearer(token["token"])).json()["report"]
    assert report["cost"]["estimated"] > 0 and report["cost"]["actual"] == report["usage"]["cost"] > 2 * CALL
    with SessionLocal() as db:
        request = db.get(TranslationRequest, created["request_id"])
        assert request.cost == pytest.approx(report["usage"]["cost"])
        assert token_spent(db, db.get(ApiToken, token["id"])) == pytest.approx(request.cost)
    web.__exit__(None, None, None)
