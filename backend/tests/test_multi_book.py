import asyncio
from collections import Counter
from pathlib import Path

import respx
from sqlalchemy import select
from test_pipeline import mock_completion
from test_resilience import prepare

from app.api.projects import import_book
from app.db import SessionLocal
from app.jobs.execution import execution
from app.jobs.queue import claim, enqueue, suspend
from app.jobs.worker import worker_slot
from app.models import Job, Project, Provider


@respx.mock
async def test_two_books_are_processed_concurrently_without_mixing_jobs(seeded, book_bytes):
    pid, user_id, provider_id = seeded
    with SessionLocal() as db:
        second = import_book(db, user_id, book_bytes)
        second.provider_id = provider_id
        second.context_backend = "internal"
        second.quality = "fast"
        db.get(Provider, provider_id).max_concurrency = 2
        db.commit()
        second_id = second.id
    jobs = {prepare(pid), prepare(second_id)}
    entered, release, stopped = asyncio.Event(), asyncio.Event(), asyncio.Event()
    seen = set()

    async def answer(request):
        seen.add(execution.get()[0])
        if seen == jobs:
            entered.set()
        await release.wait()
        return mock_completion(request)

    respx.post("https://llm.test/v1/chat/completions").mock(side_effect=answer)
    workers = [asyncio.create_task(worker_slot(stopped)) for _ in range(2)]
    try:
        await asyncio.wait_for(entered.wait(), 5)
        assert seen == jobs
        release.set()
        for _ in range(100):
            with SessionLocal() as db:
                if all(j.status == "completed" for j in db.scalars(select(Job).where(Job.id.in_(jobs)))):
                    break
            await asyncio.sleep(0.05)
        with SessionLocal() as db:
            assert all(j.status == "completed" for j in db.scalars(select(Job).where(Job.id.in_(jobs))))
    finally:
        release.set()
        stopped.set()
        await asyncio.gather(*workers)


def test_provider_limits_are_independent_and_shared_by_all_operations(seeded, book_bytes):
    first_id, user_id, codex_id = seeded
    with SessionLocal() as db:
        codex = db.get(Provider, codex_id)
        codex.name, codex.max_concurrency = "Codex", 3
        qwen = Provider(
            name="Qwen",
            base_url="https://qwen.test/v1",
            model="qwen-uncensored",
            capabilities={"supports_json_schema": True},
            context_window=64000,
            max_concurrency=1,
        )
        db.add(qwen)
        db.flush()
        qwen_id = qwen.id
        projects = [db.get(Project, first_id)]
        for provider_id in [codex_id] * 4 + [qwen_id] * 5:
            project = import_book(db, user_id, book_bytes)
            project.provider_id = provider_id
            projects.append(project)
        for index, project in enumerate(projects):
            enqueue(db, project, "translate" if index % 2 else "analyze", {})
        db.commit()

    claimed = [claim() for _ in range(4)]
    assert all(claimed)
    with SessionLocal() as db:
        providers = Counter(db.get(Job, item[0]).provider_id for item in claimed)
        codex_claim = next(item for item in claimed if db.get(Job, item[0]).provider_id == codex_id)
    assert providers == Counter({codex_id: 3, qwen_id: 1})
    assert claim() is None

    suspend(codex_claim[0], codex_claim[1], "paused", "test")
    replacement = claim()
    with SessionLocal() as db:
        assert replacement and db.get(Job, replacement[0]).provider_id == codex_id


async def test_codex_supports_parallel_isolated_threads(monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[2]))
    from codex_bridge.rpc import CodexSession

    session = CodexSession(Path("/tmp/unused"))
    both = asyncio.Event()
    texts = {}
    next_id = 0
    started = set()

    async def start():
        pass

    async def call(method, params, timeout=30):
        nonlocal next_id
        if method == "account/read":
            return {"account": {"type": "chatgpt"}}
        if method == "thread/start":
            next_id += 1
            return {"thread": {"id": f"thread-{next_id}"}}
        if method == "turn/start":
            tid = params["threadId"]
            texts[tid] = params["input"][0]["text"]
            started.add(tid)
            if len(started) == 2:
                both.set()
            await asyncio.wait_for(both.wait(), 5)
            queue = session.events[tid]
            queue.put_nowait(
                {
                    "method": "item/completed",
                    "params": {
                        "item": {
                            "id": tid,
                            "type": "agentMessage",
                            "phase": "final_answer",
                            "text": texts[tid],
                        }
                    },
                }
            )
            queue.put_nowait({"method": "turn/completed", "params": {"turn": {"status": "completed"}}})
            return {"turn": {"id": f"turn-{tid}"}}
        return {}

    session.start, session.call = start, call
    base = {"model": "model", "schema": {}, "timeout": 10}
    result = await asyncio.gather(
        *(
            session.complete({**base, "messages": [{"role": "user", "content": text}]})
            for text in ("Book A", "Book B")
        )
    )
    assert [r["text"] for r in result] == ["Book A", "Book B"]
    assert len({r["thread_id"] for r in result}) == 2 and session.active_generations == 0
