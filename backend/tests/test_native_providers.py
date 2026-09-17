import json

import httpx
import pytest
import respx
from fastapi.testclient import TestClient
from pydantic import BaseModel
from sqlalchemy import select

from app.db import SessionLocal
from app.main import app
from app.models import Provider, RequestLog
from app.providers.llm import LLMError, ProviderContentRefused, llm
from app.security import encrypt


class Answer(BaseModel):
    text: str


MESSAGES = [
    {"role": "system", "content": "You are the literary translator. Keep every EPUB marker."},
    {"role": "user", "content": "TARGET_TEXT: Hello."},
]


def native(kind: str, base_url: str) -> str:
    with SessionLocal() as db:
        provider = Provider(
            kind=kind,
            name=kind,
            base_url=base_url,
            model="claude-opus-5" if kind == "anthropic" else "gpt-test",
            encrypted_key=encrypt("secret-key"),
            context_window=64000,
            max_output_tokens=12000,
            temperature=0.2,
        )
        db.add(provider)
        db.commit()
        return provider.id


def anthropic_reply(text: str, stop_reason: str = "end_turn") -> dict:
    return {
        "id": "msg_1",
        "type": "message",
        "role": "assistant",
        "model": "claude-opus-5",
        "content": [{"type": "text", "text": text}],
        "stop_reason": stop_reason,
        "usage": {"input_tokens": 900, "cache_read_input_tokens": 100, "output_tokens": 500},
    }


def complete(project_id: str, provider_id: str, **options):
    return llm.complete(
        project_id=project_id,
        provider_id=provider_id,
        operation="translation",
        messages=MESSAGES,
        response_model=Answer,
        **options,
    )


def test_native_provider_kinds_can_be_created_and_require_a_key(seeded):
    with TestClient(app) as client:
        client.post("/api/auth/login", json={"username": "tester", "password": "test-password-123456789"})
        for kind, url in (("anthropic", "https://api.anthropic.com"), ("openai_direct", "https://api.openai.com")):
            body = {"kind": kind, "name": kind, "base_url": url, "model": "m", "context_window": 64000}
            assert client.post("/api/providers", json=body).status_code == 422
            created = client.post("/api/providers", json={**body, "api_key": "secret"})
            assert created.status_code == 201, created.text
            assert created.json()["kind"] == kind and created.json()["has_api_key"] is True
            assert "secret" not in created.text


@pytest.mark.parametrize("base_url", ["https://api.anthropic.com", "https://api.anthropic.com/v1/"])
@respx.mock
async def test_anthropic_request_keeps_the_system_prompt_and_logs_usage(seeded, base_url):
    provider_id = native("anthropic", base_url.rstrip("/"))
    route = respx.post("https://api.anthropic.com/v1/messages").respond(
        200, json=anthropic_reply('{"text": "Bonjour."}')
    )
    result = await complete(seeded[0], provider_id, temperature=0.05)
    assert result.text == "Bonjour."
    request = route.calls.last.request
    sent = json.loads(request.content)
    assert request.headers["x-api-key"] == "secret-key"
    assert request.headers["anthropic-version"] == "2023-06-01"
    assert "authorization" not in request.headers
    # Every system message reaches Claude: the translator prompt and the JSON schema instruction.
    assert sent["system"].startswith("You are the literary translator.")
    assert "Return JSON matching this schema" in sent["system"]
    assert sent["messages"] == [{"role": "user", "content": "TARGET_TEXT: Hello."}]
    assert sent["max_tokens"] == 12000 and sent["model"] == "claude-opus-5"
    # Current Claude models reject sampling parameters.
    assert not {"temperature", "top_p", "response_format"} & sent.keys()
    with SessionLocal() as db:
        log = db.scalar(select(RequestLog).where(RequestLog.provider_id == provider_id))
        assert (log.status, log.prompt_tokens, log.completion_tokens) == ("success", 1000, 500)
        assert log.raw["provider_response"]["id"] == "msg_1"


@respx.mock
async def test_anthropic_refusal_and_truncation_are_classified(seeded):
    provider_id = native("anthropic", "https://api.anthropic.com")
    route = respx.post("https://api.anthropic.com/v1/messages").respond(
        200, json=anthropic_reply("I can't help with that.", "refusal")
    )
    with pytest.raises(ProviderContentRefused):
        await complete(seeded[0], provider_id)
    assert route.call_count <= 2

    route.respond(200, json=anthropic_reply('{"text": "Bonj', "max_tokens"))
    with pytest.raises(LLMError, match="tronquée"):
        await complete(seeded[0], provider_id, context={"attempt": "truncation"})
    with SessionLocal() as db:
        statuses = {log.status for log in db.scalars(select(RequestLog).where(RequestLog.provider_id == provider_id))}
        assert statuses == {"refused", "error"}


@respx.mock
async def test_anthropic_models_are_listed_from_the_api(seeded):
    provider_id = native("anthropic", "https://api.anthropic.com")
    route = respx.get("https://api.anthropic.com/v1/models").respond(
        200, json={"data": [{"id": "claude-opus-5"}, {"id": "claude-haiku-4-5"}], "has_more": False}
    )
    with SessionLocal() as db:
        models = await llm.models(db.get(Provider, provider_id))
    assert models == ["claude-opus-5", "claude-haiku-4-5"]
    assert route.calls.last.request.headers["x-api-key"] == "secret-key"


@pytest.mark.parametrize("base_url", ["https://api.openai.com", "https://api.openai.com/v1"])
@respx.mock
async def test_openai_direct_uses_the_common_chat_completions_path(seeded, base_url):
    provider_id = native("openai_direct", base_url)
    route = respx.post("https://api.openai.com/v1/chat/completions").respond(
        200,
        json={
            "choices": [{"finish_reason": "stop", "message": {"content": '{"text": "Bonjour."}'}}],
            "usage": {"prompt_tokens": 1000, "completion_tokens": 500},
        },
    )
    assert (await complete(seeded[0], provider_id, temperature=0.05)).text == "Bonjour."
    request = route.calls.last.request
    sent = json.loads(request.content)
    assert request.headers["authorization"] == "Bearer secret-key"
    assert sent["temperature"] == 0.05 and sent["max_tokens"] == 12000
    assert [m["role"] for m in sent["messages"]] == ["system", "user", "system"]
    with SessionLocal() as db:
        log = db.scalar(select(RequestLog).where(RequestLog.provider_id == provider_id))
        assert (log.prompt_tokens, log.completion_tokens) == (1000, 500)


@respx.mock
async def test_openai_direct_empty_choices_is_a_provider_error_not_a_crash(seeded, monkeypatch):
    async def no_delay(_seconds):
        return None

    monkeypatch.setattr("app.providers.llm.asyncio.sleep", no_delay)
    provider_id = native("openai_direct", "https://api.openai.com")
    respx.post("https://api.openai.com/v1/chat/completions").mock(
        return_value=httpx.Response(200, json={"choices": []})
    )
    with pytest.raises(LLMError):
        await complete(seeded[0], provider_id)
    with SessionLocal() as db:
        logs = list(db.scalars(select(RequestLog).where(RequestLog.provider_id == provider_id)))
        assert logs and all(log.status == "error" for log in logs)
