import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
import respx
from sqlalchemy import select

from app.config import settings
from app.db import SessionLocal
from app.engines.quality.checks import validate_translation
from app.models import Provider, RequestLog
from app.providers.llm import InvalidResponseExhausted, LLMError, llm
from app.providers.transports import generation_parameters, normalize_response
from app.schemas import BookBible, ProviderInput, TranslationResult


def test_reasoning_level_is_explicit_and_capability_gated():
    provider = SimpleNamespace(
        kind="openai",
        model="qwen",
        temperature=0.2,
        top_p=0.9,
        max_output_tokens=4096,
        capabilities={
            "supports_reasoning": True,
            "reasoning_effort": "none",
            "max_tokens_parameter": "max_tokens",
        },
    )
    assert generation_parameters(provider, None)["reasoning_effort"] == "none"
    provider.capabilities["supports_reasoning"] = False
    assert "reasoning_effort" not in generation_parameters(provider, None)


@respx.mock
async def test_reasoning_without_final_content_has_specific_error(seeded, monkeypatch):
    pid, _, provider_id = seeded
    with SessionLocal() as db:
        provider = db.get(Provider, provider_id)
        provider.capabilities = {
            **provider.capabilities,
            "supports_reasoning": True,
            "reasoning_effort": "low",
        }
        db.commit()
    route = respx.post("https://llm.test/v1/chat/completions").respond(
        200,
        json={
            "choices": [
                {
                    "finish_reason": "stop",
                    "message": {"content": None, "reasoning": "internal reasoning"},
                }
            ],
            "usage": {"prompt_tokens": 10, "completion_tokens": 20},
        },
    )

    async def no_wait(_seconds):
        pass

    monkeypatch.setattr("app.providers.llm.asyncio.sleep", no_wait)
    with pytest.raises(InvalidResponseExhausted, match="raisonnement sans contenu final"):
        await llm.complete(
            project_id=pid,
            provider_id=provider_id,
            operation="book_analysis",
            messages=[{"role": "system", "content": "Analyze"}],
            response_model=BookBible,
        )
    assert route.call_count == 2
    assert json.loads(route.calls[0].request.content)["reasoning_effort"] == "low"
    assert json.loads(route.calls[1].request.content)["reasoning_effort"] == "none"
    with SessionLocal() as db:
        logs = list(
            db.scalars(
                select(RequestLog).where(
                    RequestLog.project_id == pid,
                    RequestLog.operation == "book_analysis",
                )
            )
        )
    assert len(logs) == 2
    assert all("raisonnement sans contenu final" in log.error for log in logs)


@respx.mock
async def test_marker_violation_gets_one_repair_attempt_then_stops(seeded):
    pid, _, provider_id = seeded
    route = respx.post("https://llm.test/v1/chat/completions").respond(
        200,
        json={
            "choices": [
                {
                    "finish_reason": "stop",
                    "message": {
                        "content": json.dumps(
                            {
                                "units": [{"id": "u1", "text": "Le feu brille."}],
                                "new_terms": [],
                                "events": [],
                                "uncertainties": [],
                            }
                        )
                    },
                }
            ],
            "usage": {"prompt_tokens": 10, "completion_tokens": 20},
        },
    )
    source = [{"id": "u1", "text": "The ⟦t0⟧fire⟦/t0⟧ shines."}]
    with pytest.raises(LLMError, match="marqueurs EPUB immuables"):
        await llm.complete(
            project_id=pid,
            provider_id=provider_id,
            operation="translation_revision",
            messages=[{"role": "system", "content": "Revise"}],
            response_model=TranslationResult,
            validator=lambda result: validate_translation(source, result),
        )
    assert route.call_count == 2
    retry_payload = json.loads(route.calls[1].request.content)
    assert "exact marker sequence" in retry_payload["messages"][-1]["content"]
    with SessionLocal() as db:
        logs = list(
            db.scalars(
                select(RequestLog).where(
                    RequestLog.project_id == pid,
                    RequestLog.operation == "translation_revision",
                )
            )
        )
    assert len(logs) == 2
    assert all("marqueurs EPUB immuables" in log.error for log in logs)


@respx.mock
async def test_responses_transport_preserves_schema_usage_and_cache(seeded):
    pid, _, provider_id = seeded
    with SessionLocal() as db:
        provider = db.get(Provider, provider_id)
        provider.kind = "openai_responses"
        provider.capabilities = {
            "supports_json_schema": True,
            "supports_reasoning": True,
            "reasoning_effort": "high",
        }
        db.commit()
    route = respx.post("https://llm.test/v1/responses").respond(
        200,
        json={
            "status": "completed",
            "output": [
                {"type": "reasoning", "summary": [{"text": "Do not output reasoning"}]},
                {
                    "type": "message",
                    "role": "assistant",
                    "content": [{"type": "output_text", "text": '{"summary":"Correct book summary"}'}],
                },
            ],
            "usage": {"input_tokens": 75, "output_tokens": 22},
        },
    )
    args = dict(
        project_id=pid,
        provider_id=provider_id,
        operation="book_analysis",
        messages=[{"role": "system", "content": "Literary analysis"}],
        response_model=BookBible,
    )
    result = await llm.complete(**args)
    assert result.summary == "Correct book summary"
    payload = json.loads(route.calls[0].request.content)
    assert payload["tools"] == [] and payload["tool_choice"] == "none"
    assert payload["reasoning"]["effort"] == "high"
    assert payload["text"]["format"]["type"] == "json_schema"
    assert "temperature" not in payload and "top_p" not in payload
    await llm.complete(**args)
    assert route.call_count == 1
    with SessionLocal() as db:
        log = db.scalar(select(RequestLog).where(RequestLog.cached.is_(False)))
        assert log.prompt_tokens == 75 and log.completion_tokens == 22


@respx.mock
async def test_codex_transport_keeps_bridge_token_out_of_audit(seeded, monkeypatch):
    pid, _, provider_id = seeded
    monkeypatch.setattr(settings(), "codex_bridge_token", "private-test-bridge-token-not-for-output")
    with SessionLocal() as db:
        provider = db.get(Provider, provider_id)
        provider.kind = "codex_chatgpt"
        provider.base_url = ""
        db.commit()
    route = respx.post(f"http://codex:8092/providers/{provider_id}/complete").respond(
        200,
        json={
            "status": "completed",
            "text": '{"summary":"Codex literary result"}',
            "usage": {"input_tokens": 90, "output_tokens": 17},
            "thread_id": "isolated-thread",
        },
    )
    result = await llm.complete(
        project_id=pid,
        provider_id=provider_id,
        operation="book_analysis",
        messages=[{"role": "system", "content": "Translate"}],
        response_model=BookBible,
    )
    assert result.summary == "Codex literary result"
    assert (
        route.calls[0].request.headers["authorization"].endswith("private-test-bridge-token-not-for-output")
    )
    with SessionLocal() as db:
        log = db.scalar(select(RequestLog))
        assert "private-test-bridge-token" not in str(log.parameters) + str(log.context) + str(log.raw)
        assert log.context["upstream_prompt_managed_by_codex"] is True


def test_codex_configuration_requires_no_api_key_or_url():
    value = ProviderInput(name="Codex", kind="codex_chatgpt", model="selected-model")
    assert value.base_url == ""
    with pytest.raises(ValueError):
        ProviderInput(name="Codex", kind="codex_chatgpt", model="model", api_key="do-not-send")
    with pytest.raises(ValueError):
        ProviderInput(name="OpenAI", kind="openai_responses", model="model")


def test_responses_incomplete_and_refusal_not_normalized_to_success():
    incomplete = normalize_response("openai_responses", {"status": "incomplete", "output": []})
    assert incomplete["choices"][0]["finish_reason"] == "length"
    refusal = normalize_response(
        "openai_responses",
        {
            "status": "completed",
            "output": [
                {"type": "message", "role": "assistant", "content": [{"type": "refusal", "refusal": "No"}]}
            ],
        },
    )
    assert refusal["choices"][0]["message"]["refusal"]


@pytest.fixture
def bridge_modules(monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[2]))
    from codex_bridge.rpc import CodexSession

    return CodexSession


async def test_rpc_denies_server_tool_requests(bridge_modules):
    session = bridge_modules(Path("/tmp/unused"))
    stream = asyncio.StreamReader()
    for method in (
        "item/commandExecution/requestApproval",
        "item/fileChange/requestApproval",
        "item/tool/call",
    ):
        stream.feed_data(
            (json.dumps({"id": method, "method": method, "params": {"command": "read auth"}}) + "\n").encode()
        )
    stream.feed_eof()
    session.process = SimpleNamespace(stdout=stream)
    messages = []

    async def capture(message):
        messages.append(message)

    session.send = capture
    await session.read_loop()
    assert len(messages) == 3 and all("error" in m and "result" not in m for m in messages)


async def test_codex_thread_is_fresh_and_only_final_answer_retained(bridge_modules):
    session = bridge_modules(Path("/tmp/unused"))
    calls = []

    async def start():
        pass

    async def call(method, params, timeout=30):
        calls.append((method, params))
        if method == "account/read":
            return {"account": {"type": "chatgpt"}}
        if method == "thread/start":
            return {"thread": {"id": "fresh-thread"}}
        if method == "turn/start":
            queue = session.events["fresh-thread"]
            for item in (
                {"type": "agentMessage", "id": "comment", "phase": "commentary", "text": "Working…"},
                {"type": "reasoning", "id": "reason", "text": "Hidden"},
                {
                    "type": "agentMessage",
                    "id": "final",
                    "phase": "final_answer",
                    "text": '{"summary":"Final"}',
                },
            ):
                queue.put_nowait({"method": "item/completed", "params": {"item": item}})
            queue.put_nowait({"method": "turn/completed", "params": {"turn": {"status": "completed"}}})
            return {"turn": {"id": "turn"}}
        return {}

    session.start, session.call = start, call
    result = await session.complete(
        {
            "model": "model",
            "messages": [
                {"role": "system", "content": "Literary rules"},
                {"role": "user", "content": "Source passage"},
            ],
            "schema": {},
            "timeout": 5,
        }
    )
    assert result["text"] == '{"summary":"Final"}'
    thread = next(p for m, p in calls if m == "thread/start")
    assert (
        thread["ephemeral"] and thread["sandbox"] == "read-only" and thread["approvalPolicy"] == "untrusted"
    )
    assert thread["baseInstructions"] == "Literary rules"
    turn = next(p for m, p in calls if m == "turn/start")
    assert turn["sandboxPolicy"]["networkAccess"] is False
    assert session.events == {}


@respx.mock
async def test_disconnected_codex_stops_without_five_retries(seeded, monkeypatch):
    pid, _, provider_id = seeded
    monkeypatch.setattr(settings(), "codex_bridge_token", "test-token-with-more-than-32-characters")
    with SessionLocal() as db:
        provider = db.get(Provider, provider_id)
        provider.kind, provider.base_url = "codex_chatgpt", ""
        db.commit()
    route = respx.post(f"http://codex:8092/providers/{provider_id}/complete").respond(
        401, json={"detail": "Connect account"}
    )
    with pytest.raises(LLMError, match="Codex déconnecté"):
        await llm.complete(
            project_id=pid,
            provider_id=provider_id,
            operation="book_analysis",
            messages=[{"role": "user", "content": "Analyze"}],
            response_model=BookBible,
        )
    assert route.call_count == 1


def test_private_bridge_authentication_and_rpc_allowlist(bridge_modules, monkeypatch):
    from codex_bridge import app as module
    from fastapi.testclient import TestClient

    token = "bridge-private-test-token-with-more-than-32-chars"
    monkeypatch.setenv("CODEX_BRIDGE_TOKEN", token)
    called = []

    async def call(method, params):
        called.append(method)
        return {"account": {"type": "chatgpt", "planType": "plus", "access_token": "MUST_NOT_LEAK"}}

    async def session_for(_pid):
        return SimpleNamespace(call=call, generation_lock=asyncio.Lock())

    monkeypatch.setattr(module, "session_for", session_for)
    prefix = "/providers/00000000-0000-4000-8000-000000000777"
    with TestClient(module.app) as client:
        assert client.post(prefix + "/status").status_code == 401
        headers = {"Authorization": "Bearer " + token}
        response = client.post(prefix + "/status", headers=headers)
        assert response.status_code == 200 and response.json()["connected"]
        assert "MUST_NOT_LEAK" not in response.text
        assert client.post(prefix + "/command-exec", headers=headers).status_code == 422
        assert (
            client.post(
                prefix + "/status", headers={**headers, "Origin": "https://external.test"}
            ).status_code
            == 401
        )
        assert called == ["account/read"]
