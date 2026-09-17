import json

import respx
from sqlalchemy import select
from test_pipeline import mock_completion

from app.db import SessionLocal
from app.jobs.queue import claim, enqueue
from app.jobs.worker import execute
from app.models import Job, Project, RequestLog
from app.providers.tracing import EXCERPT, compact_trace


def test_compact_trace_keeps_the_reasons_and_drops_the_copies():
    trace = compact_trace(
        {
            "prompt": "translation",
            "selected": [{"source": "PREVIOUS_CONTEXT", "content": "x" * 5000, "relevance": 1.0}],
            "discarded": [{"source": "MEMORY", "content": "y" * 5000, "reason": "budget", "tokens_estimate": 1300}],
            "mandatory": {"TARGET_TEXT": "z" * 5000, "GLOSSARY": []},
            "retrieval": {"hits": []},
        }
    )
    assert trace["selected"] == [{"source": "PREVIOUS_CONTEXT", "relevance": 1.0, "chars": 5000}]
    assert trace["discarded"][0]["reason"] == "budget" and trace["discarded"][0]["tokens_estimate"] == 1300
    assert trace["discarded"][0]["excerpt"] == "y" * EXCERPT and "content" not in trace["discarded"][0]
    assert trace["mandatory"] == ["GLOSSARY", "TARGET_TEXT"]
    assert trace["discarded_summary"] == {"total": 1, "by_reason": {"budget": 1}}
    assert trace["prompt"] == "translation" and trace["retrieval"] == {"hits": []}
    assert len(json.dumps(trace)) < 700


def test_compact_trace_bounds_large_discard_lists_and_retrieval_queries():
    dropped = [
        {"source": "MEMORY", "content": "m" * 800, "relevance": number / 200, "reason": "low_relevance"}
        for number in range(150)
    ]
    dropped.append({"source": "MEMORY", "content": "b" * 800, "relevance": 0.99, "reason": "budget"})
    trace = compact_trace(
        {
            "discarded": dropped,
            "retrieval": {"query": "q" * 9000, "deep": False, "openviking": {"query": "o" * 9000, "hits": []}},
        }
    )
    assert len(trace["discarded"]) == 12 and trace["discarded"][0]["reason"] == "budget"
    assert trace["discarded_summary"] == {"total": 151, "by_reason": {"low_relevance": 150, "budget": 1}}
    assert trace["retrieval"]["query"] == "q" * EXCERPT and trace["retrieval"]["query_chars"] == 9000
    assert trace["retrieval"]["openviking"]["query_chars"] == 9000 and trace["retrieval"]["deep"] is False
    assert len(json.dumps(trace)) < 6000
    assert compact_trace(trace) == trace  # idempotent: totals survive a second pass


@respx.mock
async def test_request_logs_store_the_prompt_once(seeded):
    pid = seeded[0]
    with SessionLocal() as db:
        project = db.get(Project, pid)
        project.bible = {"summary": "Known book context"}
        jid = enqueue(db, project, "translate", {}).id
        db.commit()
    respx.post("https://llm.test/v1/chat/completions").mock(side_effect=mock_completion)
    await execute(*claim())
    with SessionLocal() as db:
        assert db.get(Job, jid).status == "completed"
        logs = list(db.scalars(select(RequestLog).where(RequestLog.project_id == pid)))
    assert logs
    for log in logs:
        prompt = json.dumps(log.messages, ensure_ascii=False)
        assert log.messages and "messages" not in log.parameters and "input" not in log.parameters
        assert log.parameters["model"] == "test-model"
        for item in [*log.context.get("selected", []), *log.context.get("discarded", [])]:
            assert "content" not in item and item["chars"] >= 0
        assert isinstance(log.context.get("mandatory", []), list)
        # The trace explains the prompt; it must stay much smaller than the prompt itself.
        assert len(json.dumps(log.context, ensure_ascii=False)) < len(prompt)


def test_existing_request_logs_can_be_compacted_in_place(seeded):
    from app.maintenance.compact_request_logs import compact

    pid, _, provider_id = seeded
    with SessionLocal() as db:
        for number in range(3):
            db.add(
                RequestLog(
                    project_id=pid,
                    provider_id=provider_id,
                    operation="translation",
                    model="test-model",
                    fingerprint=f"f{number}",
                    messages=[{"role": "user", "content": "prompt"}],
                    parameters={"model": "test-model", "messages": [{"role": "user", "content": "p" * 4000}]},
                    context={"discarded": [{"source": "MEMORY", "content": "d" * 9000, "reason": "budget"}]},
                    parsed={"text": "kept"},
                    status="success",
                )
            )
        db.commit()
    measured = compact(batch=2, dry_run=True)
    assert measured["changed"] == 3 and measured["after"] < measured["before"] / 10
    with SessionLocal() as db:
        assert "messages" in db.scalars(select(RequestLog)).first().parameters
    assert compact(batch=2)["changed"] == 3
    assert compact(batch=2)["changed"] == 0  # idempotent
    with SessionLocal() as db:
        for log in db.scalars(select(RequestLog)):
            assert log.parameters == {"model": "test-model"} and log.parsed == {"text": "kept"}
            assert log.messages == [{"role": "user", "content": "prompt"}]
            assert log.context["discarded"][0]["chars"] == 9000
