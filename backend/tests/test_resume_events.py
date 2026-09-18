import respx
from sqlalchemy import select
from test_pipeline import mock_completion

from app.db import SessionLocal
from app.jobs.queue import claim, enqueue
from app.jobs.worker import execute
from app.models import Event, Job, Project, Segment


def step_events(pid: str, jid: str, step: str) -> int:
    with SessionLocal() as db:
        events = db.scalars(select(Event).where(Event.project_id == pid)).all()
    return sum(1 for e in events if e.payload.get("job_id") == jid and e.payload.get("step") == step)


def launch(pid: str, operation: str, options: dict) -> str:
    with SessionLocal() as db:
        job = enqueue(db, db.get(Project, pid), operation, options)
        db.commit()
        return job.id


@respx.mock
async def test_a_second_run_writes_nothing_for_passages_already_done(seeded):
    pid = seeded[0]
    route = respx.post("https://llm.test/v1/chat/completions").mock(side_effect=mock_completion)
    first = launch(pid, "analyze", {"continue_pipeline": True})
    await execute(*claim())
    with SessionLocal() as db:
        assert db.get(Job, first).status == "completed"
        total = len(db.scalars(select(Segment.id).where(Segment.project_id == pid)).all())
    assert step_events(pid, first, "chapter_analysis") == total
    assert step_events(pid, first, "translation") == total
    calls = route.call_count

    again = launch(pid, "analyze", {"continue_pipeline": True})
    await execute(*claim())
    with SessionLocal() as db:
        assert db.get(Job, again).status == "completed"
    # Everything was settled: the rerun used to replay one checkpoint write and one event per passage.
    assert step_events(pid, again, "chapter_analysis") == 0
    assert step_events(pid, again, "translation") == 0
    assert route.call_count - calls <= 2  # at most the book-level synthesis, never a passage


@respx.mock
async def test_only_the_remaining_passages_are_announced(seeded):
    pid = seeded[0]
    respx.post("https://llm.test/v1/chat/completions").mock(side_effect=mock_completion)
    with SessionLocal() as db:
        project = db.get(Project, pid)
        project.bible = {"summary": "Known book context"}
        segments = db.scalars(select(Segment).where(Segment.project_id == pid).order_by(Segment.position)).all()
        segments[0].stage, segments[0].human = "done", True
        db.commit()
        total = len(segments)
    jid = launch(pid, "translate", {})
    await execute(*claim())
    assert step_events(pid, jid, "translation") == total - 1
    with SessionLocal() as db:
        last = [e.payload for e in db.scalars(select(Event).where(Event.project_id == pid)) if e.payload.get("step") == "translation"][-1]
    assert last["total"] == total and last["current"] == total  # numbering still spans the whole book
