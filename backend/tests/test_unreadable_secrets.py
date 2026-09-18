import pytest
import respx
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient

from app.db import SessionLocal
from app.main import app, login_attempts
from app.models import AppSetting, Provider
from app.providers.llm import ProviderAuthenticationRequired, llm
from app.schemas import BookOverview

FOREIGN = Fernet(Fernet.generate_key()).encrypt(b"sk-from-another-installation").decode()


@pytest.fixture(autouse=True)
def isolate_rate_limit():
    login_attempts.clear()
    yield
    login_attempts.clear()


@respx.mock
async def test_an_unreadable_provider_key_asks_for_it_again_without_calling_the_provider(seeded):
    pid, _, provider_id = seeded
    route = respx.post("https://llm.test/v1/chat/completions").respond(200, json={})
    with SessionLocal() as db:
        db.get(Provider, provider_id).encrypted_key = FOREIGN
        db.commit()
    with pytest.raises(ProviderAuthenticationRequired, match="SECRET_KEY"):
        await llm.complete(
            project_id=pid, provider_id=provider_id, operation="book_analysis",
            messages=[{"role": "user", "content": "x"}], response_model=BookOverview,
        )
    assert route.call_count == 0
    with TestClient(app) as client:
        assert client.post("/api/auth/login", json={"username": "tester", "password": "test-password-123456789"}).status_code == 200
        answer = client.post(f"/api/providers/{provider_id}/test").json()
    assert answer["ok"] is False and "Ressaisissez la clé" in answer["message"]


def test_an_unreadable_memory_key_disables_external_memory_only(seeded):
    from app.engines.context.config import memory_config

    with SessionLocal() as db:
        db.add(AppSetting(key="openviking", value={"base_url": "https://memory.test", "encrypted_key": FOREIGN}))
        db.commit()
    config = memory_config()
    assert config["api_key"] == "" and config["enable_search"] is False and config["base_url"] == "https://memory.test"
