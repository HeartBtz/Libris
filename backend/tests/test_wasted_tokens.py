import pytest
from fastapi.testclient import TestClient

from app.db import SessionLocal
from app.main import app
from app.models import RequestLog


def test_wasted_input_tokens_per_project_and_per_model(seeded):
    pid, _, provider_id = seeded
    rows = [
        ("success", 6_000, "test-model"),
        ("error", 2_000, "test-model"),
        ("refused", 1_000, "test-model"),
        ("interrupted", 500, "test-model"),
        ("abandoned", 500, "test-model"),
        ("running", 4_000, "test-model"),
        ("success", 1_000, "other-model"),
    ]
    with SessionLocal() as db:
        for number, (status, prompt, model) in enumerate(rows):
            db.add(RequestLog(project_id=pid, provider_id=provider_id, operation="translation", model=model,
                              fingerprint=str(number), status=status, parameters={}, messages=[],
                              prompt_tokens=prompt, completion_tokens=100))
        db.commit()
    with TestClient(app) as client:
        assert client.post(
            "/api/auth/login", json={"username": "tester", "password": "test-password-123456789"}
        ).status_code == 200
        metrics = client.get(f"/api/projects/{pid}/metrics").json()
        models = {m["model"]: m for m in client.get("/api/statistics/models").json()}
    # Existing keys keep their meaning.
    assert metrics["requests"] == 7 and metrics["input_tokens"] == 15_000 and metrics["errors"] == 1
    assert metrics["wasted_input_tokens"] == 4_000
    assert metrics["wasted_share"] == pytest.approx(4_000 / 15_000)
    assert models["test-model"]["wasted_input_tokens"] == 4_000
    assert models["test-model"]["wasted_share"] == pytest.approx(4_000 / 14_000)
    assert models["other-model"]["wasted_input_tokens"] == 0 and models["other-model"]["wasted_share"] == 0


def test_wasted_share_without_any_request(seeded):
    pid = seeded[0]
    with TestClient(app) as client:
        client.post("/api/auth/login", json={"username": "tester", "password": "test-password-123456789"})
        metrics = client.get(f"/api/projects/{pid}/metrics").json()
        models = client.get("/api/statistics/models").json()
    assert metrics["wasted_input_tokens"] == 0 and metrics["wasted_share"] == 0
    assert models == [
        {"model": "test-model", "requests": 0, "input_tokens": 0, "output_tokens": 0, "total_tokens": 0,
         "wasted_input_tokens": 0, "wasted_share": 0}
    ]
