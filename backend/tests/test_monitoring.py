import re
import time

import pytest
from fastapi.testclient import TestClient

from app.api import monitoring
from app.config import settings
from app.db import SessionLocal
from app.main import app
from app.models import Event, Job, Outbox, Project, Provider, RequestLog, Segment

TOKEN = "metrics-test-token-0123456789abcdef"
LABEL = r'[a-zA-Z_][a-zA-Z0-9_]*="(?:[^"\\\n]|\\.)*"'
SAMPLE = re.compile(rf"^([a-zA-Z_:][a-zA-Z0-9_:]*)(\{{{LABEL}(?:,{LABEL})*\}})? (\S+)$")


@pytest.fixture(autouse=True)
def fresh_cache():
    monitoring.cache.clear()
    yield
    monitoring.cache.clear()


@pytest.fixture
def enabled(monkeypatch):
    monkeypatch.setattr(settings(), "metrics_token", TOKEN)


def scrape(client, token=TOKEN):
    return client.get("/metrics", headers={"Authorization": f"Bearer {token}"} if token else {})


def parse(text: str) -> dict[str, float]:
    """A strict reading of the 0.0.4 text format: every sample belongs to a declared family, once."""
    assert text.endswith("\n")
    declared, samples = {}, {}
    for line in text.splitlines():
        if line.startswith("# HELP "):
            continue
        if line.startswith("# TYPE "):
            _, _, name, kind = line.split(" ")
            assert kind in {"counter", "gauge"} and name not in declared
            declared[name] = kind
            continue
        match = SAMPLE.match(line)
        assert match, line
        name, labels, value = match[1], match[2] or "", match[3]
        assert name in declared, line
        assert name + labels not in samples, line
        samples[name + labels] = float(value)
    return samples


def test_metrics_are_disabled_by_default(seeded):
    with TestClient(app) as client:
        response = scrape(client)
    assert response.status_code == 404
    assert "libris_" not in response.text


def test_metrics_require_the_token(seeded, enabled):
    with TestClient(app) as client:
        missing = scrape(client, token=None)
        wrong = scrape(client, token=TOKEN[:-1] + "x")
        basic = client.get("/metrics", headers={"Authorization": f"Basic {TOKEN}"})
        # A logged-in browser session is not a metrics credential.
        assert client.post(
            "/api/auth/login", json={"username": "tester", "password": "test-password-123456789"}
        ).status_code == 200
        session_only = scrape(client, token=None)
    for response in (missing, wrong, basic, session_only):
        assert response.status_code == 401
        assert response.headers["www-authenticate"] == "Bearer"
        assert "libris_" not in response.text


def test_short_token_is_refused_at_startup(monkeypatch):
    monkeypatch.setattr(settings(), "metrics_token", "short")
    with pytest.raises(RuntimeError, match="METRICS_TOKEN"):
        settings().prepare()


def test_exposition_counts_queue_calls_and_passages_without_leaking(seeded, enabled):
    pid, user_id, provider_id = seeded
    now = time.time()
    with SessionLocal() as db:
        other = Project(owner_id=user_id, title="Hidden Crown", original_hash="h", original_path="/x")
        db.add(other)
        db.flush()
        provider = db.get(Provider, provider_id)
        provider.name = 'Local "fast"\\gpu'
        db.add(Provider(name="Second", base_url="https://secret-host.test/v1", model="m", timeout=60))
        db.add(Job(project_id=pid, operation="translate", status="pending", created_at=now - 600))
        db.add(Job(project_id=pid, operation="analyze", status="completed"))
        db.add(Job(project_id=other.id, operation="analyze", status="analyzing", lease_until=now - 5))
        # Resumed a minute ago: the queue age starts at that event, not at the job's creation.
        db.add(Event(project_id=pid, created_at=now - 60, payload={"status": "pending"}))
        common = dict(project_id=pid, provider_id=provider_id, model="test-model", parameters={}, messages=[])
        db.add_all(
            [
                RequestLog(operation="translation", status="success", fingerprint="a",
                           prompt_tokens=1000, completion_tokens=400, **common),
                RequestLog(operation="translation", status="error", fingerprint="b",
                           prompt_tokens=900, completion_tokens=0, **common),
                RequestLog(operation="translation", status="success", fingerprint="a", cached=True, **common),
                RequestLog(operation="chapter_analysis", status="interrupted", fingerprint="c",
                           prompt_tokens=300, **common),
                RequestLog(operation="translation", status="running", fingerprint="d", created_at=now,
                           **common),
            ]
        )
        db.add(Outbox(project_id=pid, event_key="k1", session_name="s", payload={}, status="pending"))
        db.add(Outbox(project_id=pid, event_key="k2", session_name="s", payload={}, status="sent"))
        segment = db.query(Segment).filter(Segment.project_id == pid).first()
        segment.status = "check"
        segment_total = db.query(Segment).count()
        db.commit()
    with TestClient(app) as client:
        response = scrape(client)
    assert response.status_code == 200
    assert response.headers["content-type"] == "text/plain; version=0.0.4; charset=utf-8"
    for secret in ("Silver Tower", "Hidden Crown", "Alice", "llm.test", "secret-host", "https://", TOKEN):
        assert secret not in response.text
    metrics = parse(response.text)
    assert metrics['libris_jobs{operation="translate",status="pending"}'] == 1
    assert metrics['libris_jobs{operation="analyze",status="completed"}'] == 1
    assert metrics["libris_jobs_expired_leases"] == 1
    assert 55 <= metrics["libris_jobs_oldest_queued_age_seconds"] <= 120
    assert metrics['libris_llm_requests_total{operation="translation",status="success"}'] == 2
    assert metrics['libris_llm_requests_total{operation="translation",status="error"}'] == 1
    assert not any("running" in key for key in metrics if key.startswith("libris_llm_requests_total"))
    assert metrics['libris_llm_input_tokens_total{operation="translation"}'] == 1900
    assert metrics['libris_llm_output_tokens_total{operation="translation"}'] == 400
    assert metrics['libris_llm_wasted_input_tokens_total{operation="translation"}'] == 900
    assert metrics['libris_llm_wasted_input_tokens_total{operation="chapter_analysis"}'] == 300
    assert metrics['libris_llm_cache_hits_total{operation="translation"}'] == 1
    assert metrics["libris_llm_cache_hit_ratio"] == pytest.approx(1 / 4)
    assert metrics['libris_llm_requests_in_flight{provider="Local \\"fast\\"\\\\gpu"}'] == 1
    assert metrics['libris_llm_requests_in_flight{provider="Second"}'] == 0
    assert metrics['libris_segments{status="check"}'] == 1
    assert metrics['libris_segments{status="pending"}'] == segment_total - 1
    assert metrics["libris_memory_outbox_pending"] == 1


def test_scrapes_are_cached_for_a_few_seconds(seeded, enabled):
    pid = seeded[0]
    with TestClient(app) as client:
        first = parse(scrape(client).text)
        with SessionLocal() as db:
            db.add(Job(project_id=pid, operation="translate", status="failed"))
            db.commit()
        assert parse(scrape(client).text) == first
        monitoring.cache.clear()
        assert parse(scrape(client).text)['libris_jobs{operation="translate",status="failed"}'] == 1


def test_empty_installation_is_valid(enabled):
    with TestClient(app) as client:
        metrics = parse(scrape(client).text)
    assert metrics["libris_jobs_oldest_queued_age_seconds"] == 0
    assert metrics["libris_llm_cache_hit_ratio"] == 0
