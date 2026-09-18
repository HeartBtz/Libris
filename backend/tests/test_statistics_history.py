import pytest
import respx
from fastapi.testclient import TestClient
from test_pipeline import mock_completion

from app.db import SessionLocal
from app.main import app, login_attempts
from app.models import Provider, RequestLog
from app.providers.llm import llm
from app.schemas import BookOverview


@pytest.fixture(autouse=True)
def isolate_rate_limit():
    login_attempts.clear()
    yield
    login_attempts.clear()


def usage(request):
    response = mock_completion(request)
    body = response.json()
    body["usage"] = {"prompt_tokens": 1_000_000, "completion_tokens": 500_000}
    return response.__class__(200, json=body)


@respx.mock
async def test_later_price_and_model_changes_do_not_rewrite_history(seeded):
    pid, _, provider_id = seeded
    respx.post("https://llm.test/v1/chat/completions").mock(side_effect=usage)
    with SessionLocal() as db:
        provider = db.get(Provider, provider_id)
        provider.input_cost, provider.output_cost = 2.0, 8.0
        db.commit()
    await llm.complete(
        project_id=pid, provider_id=provider_id, operation="book_analysis",
        messages=[{"role": "user", "content": "Describe the book."}], response_model=BookOverview,
    )
    with SessionLocal() as db:
        # A request that predates the price columns, then the provider is edited.
        db.add(RequestLog(project_id=pid, provider_id=provider_id, operation="translation", model="test-model",
                          fingerprint="old", status="success", parameters={}, messages=[], prompt_tokens=1_000_000))
        provider = db.get(Provider, provider_id)
        provider.input_cost, provider.output_cost, provider.model = 10.0, 40.0, "renamed-model"
        db.commit()
    with TestClient(app) as client:
        assert client.post("/api/auth/login", json={"username": "tester", "password": "test-password-123456789"}).status_code == 200
        metrics = client.get(f"/api/projects/{pid}/metrics").json()
        models = {m["model"]: m for m in client.get("/api/statistics/models").json()}
    # 1 M in + 0.5 M out at the recorded 2/8 = 6.0; the older row falls back to the current 10 = 10.0.
    assert metrics["cost"] == pytest.approx(16.0) and metrics["requests"] == 2
    assert models["test-model"]["requests"] == 2 and models["test-model"]["input_tokens"] == 2_000_000
    assert models["renamed-model"]["requests"] == 0  # configured, not used yet


def test_requests_of_a_deleted_provider_still_count(seeded):
    pid = seeded[0]
    with SessionLocal() as db:
        db.add(RequestLog(project_id=pid, provider_id=None, operation="translation", model="gone", fingerprint="x",
                          status="success", parameters={}, messages=[], prompt_tokens=2_000_000, input_cost=3.0, output_cost=9.0))
        db.commit()
    with TestClient(app) as client:
        assert client.post("/api/auth/login", json={"username": "tester", "password": "test-password-123456789"}).status_code == 200
        metrics = client.get(f"/api/projects/{pid}/metrics").json()
    assert metrics["requests"] == 1 and metrics["cost"] == pytest.approx(6.0)
