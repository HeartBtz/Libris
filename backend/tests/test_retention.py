import time

import respx
from sqlalchemy import func, select
from test_pipeline import mock_completion

from app.config import settings
from app.db import SessionLocal
from app.maintenance import retention
from app.models import BibleRevision, Event, Outbox, RequestLog

OLD = time.time() - 90 * 86400


def seed(pid: str, provider_id: str) -> None:
    with SessionLocal() as db:
        for age, status in ((OLD, "success"), (OLD, "error"), (OLD, "running"), (time.time(), "success")):
            db.add(
                RequestLog(
                    project_id=pid, provider_id=provider_id, operation="translation", model="m", fingerprint="f",
                    status=status, parameters={"temperature": 0}, messages=[{"role": "user", "content": "x" * 500}],
                    raw={"choices": [1]}, context={"selected": [1]}, parsed={"units": []}, prompt_tokens=120,
                    completion_tokens=30, created_at=age,
                )
            )
        for index in range(retention.KEPT_EVENTS + 40):
            db.add(Event(project_id=pid, created_at=OLD, payload={"n": index}))
        db.add(Event(project_id=pid, created_at=time.time(), payload={"n": "recent"}))
        for index, (status, age) in enumerate((("sent", OLD), ("sent", time.time()), ("pending", OLD))):
            db.add(Outbox(project_id=pid, event_key=f"k{index}", session_name="s", payload={}, status=status, created_at=age))
        for index in range(25):
            db.add(BibleRevision(project_id=pid, content={"n": index}, human=index == 0, created_at=OLD + index))
        db.commit()


def test_retention_empties_bodies_and_trims_logs_but_keeps_what_counts(seeded):
    pid, _, provider_id = seeded
    seed(pid, provider_id)
    expected = {"request_bodies": 2, "events": 41, "outbox": 1, "bible_revisions": 4}
    assert retention.apply(dry_run=True) == expected
    with SessionLocal() as db:  # a dry run writes nothing
        assert db.scalar(select(func.count()).select_from(Event).where(Event.project_id == pid)) == retention.KEPT_EVENTS + 41
    assert retention.apply() == expected
    assert retention.apply() == dict.fromkeys(expected, 0)  # idempotent
    with SessionLocal() as db:
        logs = db.scalars(select(RequestLog).where(RequestLog.project_id == pid)).all()
        emptied = [log for log in logs if not log.messages]
        assert {log.status for log in emptied} == {"success", "error"} and all(log.created_at == OLD for log in emptied)
        # Costs and the cached answer survive; only the bulk goes.
        assert all(log.prompt_tokens == 120 and log.parsed == {"units": []} and log.raw == {} for log in emptied)
        assert sum(1 for log in logs if log.messages) == 2  # the running one and the recent one
        assert db.scalar(select(func.count()).select_from(Event).where(Event.project_id == pid)) == retention.KEPT_EVENTS
        assert {o.event_key for o in db.scalars(select(Outbox))} == {"k1", "k2"}
        revisions = db.scalars(select(BibleRevision).where(BibleRevision.project_id == pid)).all()
        assert sum(1 for r in revisions if r.human) == 1 and sum(1 for r in revisions if not r.human) == 20
        assert min(r.content["n"] for r in revisions if not r.human) == 5  # the most recent ones are kept


def test_zero_disables_a_rule(seeded, monkeypatch):
    pid, _, provider_id = seeded
    seed(pid, provider_id)
    for name in ("retention_request_bodies_days", "retention_events_days", "retention_outbox_sent_days", "retention_bible_revisions"):
        monkeypatch.setattr(settings(), name, 0)
    assert retention.apply() == {"request_bodies": 0, "events": 0, "outbox": 0, "bible_revisions": 0}


@respx.mock
async def test_a_cache_hit_points_to_the_original_request_instead_of_copying_the_prompt(seeded):
    from app.providers.llm import llm
    from app.schemas import BookOverview

    pid, _, provider_id = seeded
    route = respx.post("https://llm.test/v1/chat/completions").mock(side_effect=mock_completion)
    arguments = dict(project_id=pid, provider_id=provider_id, operation="book_analysis",
                     messages=[{"role": "user", "content": "Describe the book."}], response_model=BookOverview)
    first = await llm.complete(**arguments)
    assert await llm.complete(**arguments) == first and route.call_count == 1
    with SessionLocal() as db:
        original, hit = db.scalars(select(RequestLog).where(RequestLog.project_id == pid).order_by(RequestLog.cached)).all()
        assert original.messages and not hit.messages and hit.context == {"cached_from": original.id}
    retention.request_bodies(30, False, time.time() + 40 * 86400)  # bodies emptied later on…
    assert await llm.complete(**arguments) == first and route.call_count == 1  # …the cache still answers
