import json

import pytest
import respx
from sqlalchemy import select

from app.db import SessionLocal
from app.engines.context.builder import build_context
from app.engines.context.providers import InternalContextProvider
from app.engines.quality.checks import checks, validate_translation
from app.engines.translation.versions import save_version
from app.models import Glossary, Memory, Project, Provider, Segment
from app.models.common import uid
from app.providers.llm import LLMError, llm
from app.schemas import BookBible, TranslationResult


async def test_relevant_glossary_and_future_memory_filter(seeded):
    pid, _, _ = seeded
    with SessionLocal() as db:
        segment = db.scalar(
            select(Segment).where(Segment.project_id == pid, Segment.source.contains("Alice entered"))
        )
        db.add(
            Glossary(
                project_id=pid, source="Silver Tower", translation="Tour d’argent", accepted=True, locked=True
            )
        )
        db.add(
            Glossary(
                project_id=pid,
                source="Unrelated Volcano",
                translation="Volcan sans rapport",
                accepted=True,
                locked=True,
            )
        )
        db.add(
            Memory(
                project_id=pid,
                position=999,
                kind="narrative",
                content={"text": "Alice FUTURE_SECRET_REVELATION"},
            )
        )
        db.commit()
        sid = segment.id
    built = await build_context(pid, sid)
    text = json.dumps(built.messages, ensure_ascii=False)
    assert "Silver Tower" in text and "Tour d’argent" in text
    assert "Unrelated Volcano" not in text
    assert "FUTURE_SECRET_REVELATION" not in text
    assert "PREVIOUS_CONTEXT" in text and "NEXT_CONTEXT" in text


async def test_series_context_uses_only_prior_volume_conventions(seeded):
    pid, owner_id, _ = seeded
    with SessionLocal() as db:
        current = db.get(Project, pid)
        current.series_name, current.volume_number = "Silver Tower", 2
        prior = Project(
            id=uid(),
            owner_id=owner_id,
            title="Silver Tower Vol. 1",
            author=current.author,
            series_name=current.series_name,
            volume_number=1,
            source_language=current.source_language,
            target_language=current.target_language,
            provider_id=current.provider_id,
            original_hash="prior",
            original_path=current.original_path,
        )
        future = Project(
            id=uid(),
            owner_id=owner_id,
            title="Silver Tower Vol. 3",
            author=current.author,
            series_name=current.series_name,
            volume_number=3,
            source_language=current.source_language,
            target_language=current.target_language,
            provider_id=current.provider_id,
            original_hash="future",
            original_path=current.original_path,
        )
        db.add_all([prior, future])
        db.flush()
        db.add_all(
            [
                Glossary(
                    project_id=prior.id,
                    source="Silver Tower",
                    translation="Tour d’argent",
                    accepted=True,
                    locked=True,
                ),
                Memory(
                    project_id=prior.id,
                    position=0,
                    kind="human_decision",
                    validated=True,
                    content={"source": "Alice", "translation": "Alice"},
                ),
                Memory(
                    project_id=prior.id,
                    position=0,
                    kind="narrative",
                    content={"text": "PRIOR_NARRATIVE_SECRET"},
                ),
                Glossary(
                    project_id=future.id,
                    source="Silver Tower",
                    translation="FUTURE_TRANSLATION_SECRET",
                    accepted=True,
                    locked=True,
                ),
            ]
        )
        target = db.scalar(
            select(Segment).where(Segment.project_id == pid, Segment.source.contains("Alice entered"))
        )
        db.commit()
    built = await build_context(pid, target.id)
    text = json.dumps(built.messages, ensure_ascii=False)
    assert "SERIES_CONVENTIONS" in text
    assert "Tour d’argent" in text
    assert any('"source_volume": 1' in message["content"] for message in built.messages)
    assert "PRIOR_NARRATIVE_SECRET" not in text
    assert "FUTURE_TRANSLATION_SECRET" not in text


async def test_superseded_human_memory_not_retrieved(seeded):
    pid, user, _ = seeded
    with SessionLocal() as db:
        segment = db.scalar(select(Segment).where(Segment.project_id == pid))
        original = [{"id": u["id"], "text": u["text"] + " OLD_HUMAN_DECISION"} for u in segment.units]
        replacement = [{"id": u["id"], "text": u["text"] + " NEW_HUMAN_DECISION"} for u in segment.units]
        save_version(db, segment.id, original, "human", 0, author_id=user, validated=True)
        db.commit()
        save_version(db, segment.id, replacement, "human", 1, author_id=user, validated=True)
        db.commit()
        project = db.get(Project, pid)
    results = await InternalContextProvider().retrieve(project, "Silver Tower HUMAN_DECISION", 100)
    text = str([r.content for r in results])
    assert "OLD_HUMAN_DECISION" not in text
    assert "NEW_HUMAN_DECISION" in text


def test_missing_paragraph_rejected():
    with pytest.raises(ValueError, match="Paragraphes"):
        validate_translation(
            [{"id": "one", "text": "Hello"}, {"id": "two", "text": "World"}],
            TranslationResult(units=[{"id": "one", "text": "Bonjour"}]),
        )


def test_reasoning_inside_translation_unit_rejected():
    with pytest.raises(ValueError, match="raisonnement"):
        validate_translation(
            [{"id": "one", "text": "Hello world"}],
            TranslationResult(units=[{"id": "one", "text": "<think>analysis</think>Bonjour"}]),
        )


def test_locked_glossary_detects_wrong_term():
    term = Glossary(source="Silver Tower", translation="Tour d’argent", locked=True)
    issues = checks(
        [{"id": "one", "text": "The Silver Tower"}],
        [{"id": "one", "text": "La tour argentée"}],
        [term],
        "en",
        "fr",
    )
    assert any(i["code"] == "locked_term" and i["severity"] == "error" for i in issues)


@respx.mock
async def test_budget_rejected_before_network_call(seeded):
    pid, _, provider_id = seeded
    with SessionLocal() as db:
        provider = db.get(Provider, provider_id)
        provider.context_window = 2048
        provider.max_output_tokens = 1024
        db.commit()
    with pytest.raises(LLMError, match="Budget"):
        await llm.complete(
            project_id=pid,
            provider_id=provider_id,
            operation="analysis",
            messages=[{"role": "user", "content": "many words " * 10000}],
            response_model=BookBible,
        )
    assert not respx.calls


@respx.mock
async def test_truncated_response_never_accepted(seeded, monkeypatch):
    pid, _, provider_id = seeded

    async def no_delay(_):
        pass

    monkeypatch.setattr("app.providers.llm.asyncio.sleep", no_delay)
    route = respx.post("https://llm.test/v1/chat/completions").respond(
        200, json={"choices": [{"finish_reason": "length", "message": {"content": '{"summary":"Partial"}'}}]}
    )
    with pytest.raises(LLMError, match="tronquée"):
        await llm.complete(
            project_id=pid,
            provider_id=provider_id,
            operation="analysis",
            messages=[{"role": "user", "content": "Analyze"}],
            response_model=BookBible,
        )
    assert route.call_count == 5


@respx.mock
async def test_reasoning_outside_json_rejected(seeded, monkeypatch):
    pid, _, provider_id = seeded

    async def no_delay(_):
        pass

    monkeypatch.setattr("app.providers.llm.asyncio.sleep", no_delay)
    respx.post("https://llm.test/v1/chat/completions").respond(
        200,
        json={
            "choices": [
                {
                    "finish_reason": "stop",
                    "message": {"content": '<think>reasoning</think>{"summary":"text"}'},
                }
            ]
        },
    )
    with pytest.raises(LLMError, match="invalide"):
        await llm.complete(
            project_id=pid,
            provider_id=provider_id,
            operation="analysis",
            messages=[{"role": "user", "content": "Analyze"}],
            response_model=BookBible,
        )
