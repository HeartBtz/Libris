"""Daily usage aggregates (audit I-5): the statistics give the same figures before and after a rollup."""

import time

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from app.api.monitoring import render
from app.config import settings
from app.db import SessionLocal
from app.main import app
from app.maintenance import retention, usage
from app.models import AppSetting, Project, RequestLog, UsageDaily
from app.progress import book_facts

DAY = 86400
NOW = time.time()


def seed(pid: str, provider_id: str) -> None:
    rows = [
        # (age in days, operation, status, cached, prompt, completion, input price)
        (40, "translation", "success", False, 1000, 400, 2.0),
        (40, "translation", "error", False, 900, 0, 2.0),
        (40, "translation", "success", False, 1100, 500, 2.0),
        (12, "translation_review", "success", True, 0, 0, 2.0),
        (12, "final_review", "refused", False, 700, 0, None),
        (3, "translation", "success", False, 1200, 600, 4.0),
        (0, "translation", "success", False, 1300, 700, 4.0),  # not rolled up yet: too recent
        (0, "translation", "running", False, 0, 0, 4.0),
    ]
    with SessionLocal() as db:
        for age, operation, status, cached, prompt, completion, price in rows:
            db.add(
                RequestLog(
                    project_id=pid, provider_id=provider_id, operation=operation, model="test-model",
                    fingerprint=f"{age}{operation}{status}", status=status, cached=cached, parameters={},
                    messages=[], prompt_tokens=prompt, completion_tokens=completion, duration=2.5,
                    input_cost=price, output_cost=price and price * 4, created_at=NOW - age * DAY - 60,
                )
            )  # fmt: skip
        db.commit()


def figures(pid: str) -> dict:
    with TestClient(app) as client:
        login = {"username": "tester", "password": "test-password-123456789"}
        assert client.post("/api/auth/login", json=login).status_code == 200
        metrics = client.get(f"/api/projects/{pid}/metrics").json()
        models = client.get("/api/statistics/models").json()
    with SessionLocal() as db:
        facts = book_facts(db, [pid])[pid]
    exposition = [line for line in render().splitlines() if line.startswith("libris_llm_")]
    # Sums of floats may differ in the last digits with the order of addition.
    metrics["cost"], metrics["duration"] = round(metrics["cost"], 9), round(metrics["duration"], 9)
    return {
        "metrics": metrics,
        "models": models,
        "spent": round(facts.spent_cost, 9),
        "stages": {
            stage: tuple(round(value, 9) for value in values)
            for stage, values in facts.stage_requests.items()
        },
        "exposition": exposition,
    }


def test_statistics_are_the_same_before_and_after_the_rollup(seeded):
    pid, _, provider_id = seeded
    seed(pid, provider_id)
    before = figures(pid)
    assert before["metrics"]["requests"] == 8 and before["metrics"]["active"] == 1
    assert before["metrics"]["cache_hits"] == 1 and before["metrics"]["errors"] == 1
    assert before["metrics"]["wasted_input_tokens"] == 1600
    assert usage.rollup(NOW, dry_run=True) == 6
    with SessionLocal() as db:
        assert db.get(AppSetting, usage.KEY) is None  # a dry run writes nothing
    assert usage.rollup(NOW) == 6
    assert usage.rollup(NOW) == 0  # idempotent: the watermark moved
    with SessionLocal() as db:
        rows = list(db.scalars(select(UsageDaily)))
        assert sum(row.requests for row in rows) == 6
        assert len({row.day for row in rows}) == 3  # one day per window
        assert usage.watermark(db) == pytest.approx(NOW - usage.ROLLUP_DELAY)
    after = figures(pid)
    assert after == before


def test_old_request_rows_can_go_once_counted(seeded, monkeypatch):
    pid, _, provider_id = seeded
    seed(pid, provider_id)
    before = figures(pid)
    monkeypatch.setattr(settings(), "retention_request_rows_days", 30)
    result = retention.apply()
    assert result["usage_rollup"] == 6 and result["request_rows"] == 3
    with SessionLocal() as db:
        assert db.scalar(select(func.count()).select_from(RequestLog)) == 5
    assert figures(pid) == before


def test_request_rows_are_kept_without_a_rollup(seeded, monkeypatch):
    pid, _, provider_id = seeded
    seed(pid, provider_id)
    monkeypatch.setattr(settings(), "retention_request_rows_days", 30)
    assert retention.request_rows(30, False, NOW) == 0  # nothing counted in the aggregates yet


def test_aggregates_follow_their_book(seeded):
    pid, _, provider_id = seeded
    seed(pid, provider_id)
    usage.rollup(NOW)
    with SessionLocal() as db:
        db.delete(db.get(Project, pid))
        db.commit()
        assert db.scalar(select(func.count()).select_from(UsageDaily)) == 0


def test_first_rollup_without_requests_starts_the_watermark_now(seeded):
    assert usage.rollup(NOW) == 0
    with SessionLocal() as db:
        assert usage.watermark(db) == pytest.approx(NOW - usage.ROLLUP_DELAY)


def test_requests_restored_from_an_archive_are_counted(seeded):
    pid, _, provider_id = seeded
    seed(pid, provider_id)
    usage.rollup(NOW)
    with TestClient(app) as client:
        login = {"username": "tester", "password": "test-password-123456789"}
        assert client.post("/api/auth/login", json=login).status_code == 200
        original = client.get(f"/api/projects/{pid}/metrics").json()
        exported = client.get(f"/api/projects/{pid}/export/project")
        assert client.delete(f"/api/projects/{pid}").status_code == 200
        restored = client.post("/api/projects/import", files={"file": ("p.zip", exported.content)})
        assert restored.status_code == 201, restored.text
        metrics = client.get(f"/api/projects/{restored.json()['id']}/metrics").json()
    assert (metrics["requests"], metrics["input_tokens"]) == (original["requests"], original["input_tokens"])
    with SessionLocal() as db:
        restored_rows = select(func.sum(UsageDaily.requests)).where(UsageDaily.project_id == restored.json()["id"])
        assert db.scalar(restored_rows) == 6  # the dated ones went straight into the aggregates
