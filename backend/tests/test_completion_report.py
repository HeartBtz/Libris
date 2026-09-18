import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.api import coverage
from app.db import SessionLocal
from app.main import app, login_attempts
from app.models import Issue, Segment


@pytest.fixture(autouse=True)
def isolate_rate_limit():
    login_attempts.clear()
    yield
    login_attempts.clear()


def report(pid: str) -> dict:
    with TestClient(app) as client:
        assert client.post("/api/auth/login", json={"username": "tester", "password": "test-password-123456789"}).status_code == 200
        return client.get(f"/api/projects/{pid}/completion").json()


def test_counts_match_a_mixed_book(seeded):
    pid = seeded[0]
    with SessionLocal() as db:
        segments = db.scalars(select(Segment).where(Segment.project_id == pid).order_by(Segment.position)).all()
        assert len(segments) >= 4
        first, second, third, *rest = segments
        first.translation, first.status, first.human = "Traduit", "ok", True
        second.translation, second.status = "À vérifier", "check"
        third.translation, third.retained_source, third.status = third.source, True, "source_retained"
        rest[0].status, rest[0].error = "refused", "Refus du fournisseur."
        db.add(Issue(project_id=pid, segment_id=second.id, severity="warning", code="length", message="x"))
        db.add(Issue(project_id=pid, segment_id=second.id, severity="warning", code="length", message="y", resolved=True))
        db.commit()
        total, stuck = len(segments), len(rest)
    data = report(pid)
    assert (data["total"], data["translated"], data["retained"], data["missing"]) == (total, 2, 1, total - 3)
    assert (data["flagged"], data["issues"], data["protected"]) == (1, 1, 1)
    assert data["coverage_complete"] is False and data["processing"] is False and data["last_job_status"] == "none"
    assert data["recovery_total"] == stuck == len(data["recovery"])
    refused = next(item for item in data["recovery"] if item["status"] == "refused")
    assert refused["error"] == "Refus du fournisseur." and refused["eligible"] and len(refused["excerpt"]) <= 260
    assert [item["position"] for item in data["recovery"]] == sorted(item["position"] for item in data["recovery"])


def test_the_recovery_list_is_bounded_but_the_total_is_not(seeded, monkeypatch):
    monkeypatch.setattr(coverage, "RECOVERY_LIMIT", 2)
    data = report(seeded[0])
    assert len(data["recovery"]) == 2 and data["recovery_total"] == data["total"] > 2
