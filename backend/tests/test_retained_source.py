"""A passage kept in the original is not a human correction: a later translation may replace it."""

from sqlalchemy import select

from app.db import SessionLocal
from app.engines.translation import pipeline
from app.engines.translation.versions import save_version
from app.jobs.queue import claim, enqueue
from app.jobs.worker import execute
from app.models import Issue, Job, Project, Segment, User
from app.schemas import TranslationResult


def retain(pid: str) -> str:
    """Keeps the first passage in the original, as the autopilot's last resort does."""
    with SessionLocal() as db:
        segment = db.scalar(select(Segment).where(Segment.project_id == pid).order_by(Segment.position))
        units = [{"id": unit["id"], "text": unit["text"]} for unit in segment.units]
        assert save_version(db, segment.id, units, "source_retained", segment.revision, stage="done")
        db.add(
            Issue(
                project_id=pid, segment_id=segment.id, severity="warning", code="source_retained", message="x"
            )
        )
        db.commit()
        return segment.id


def test_a_retained_passage_is_not_counted_as_a_human_correction(seeded):
    pid = seeded[0]
    sid = retain(pid)
    with SessionLocal() as db:
        segment = db.get(Segment, sid)
        assert (segment.human, segment.retained_source, segment.status) == (False, True, "source_retained")
        # Delivery reports count `passages.human` from this flag.
        assert not db.scalars(
            select(Segment.id).where(Segment.project_id == pid, Segment.human.is_(True))
        ).all()


def test_a_machine_translation_replaces_a_retained_passage(seeded):
    pid = seeded[0]
    sid = retain(pid)
    with SessionLocal() as db:
        segment = db.get(Segment, sid)
        units = [{"id": unit["id"], "text": "Traduit."} for unit in segment.units]
        assert save_version(db, sid, units, "translation", segment.revision)
        db.commit()
        segment = db.get(Segment, sid)
        assert (segment.human, segment.retained_source, segment.translation) == (False, False, "Traduit.")
        issue = db.scalar(select(Issue).where(Issue.segment_id == sid, Issue.code == "source_retained"))
        assert issue.resolved is True


def test_a_human_correction_stays_protected_from_machine_output(seeded):
    pid = seeded[0]
    with SessionLocal() as db:
        segment = db.scalar(select(Segment).where(Segment.project_id == pid).order_by(Segment.position))
        units = [{"id": unit["id"], "text": "Humain."} for unit in segment.units]
        author = db.scalar(select(User.id).where(User.username == "tester"))
        assert save_version(db, segment.id, units, "human", segment.revision, author_id=author)
        db.commit()
        segment = db.get(Segment, segment.id)
        machine = [{"id": unit["id"], "text": "Machine."} for unit in segment.units]
        assert not save_version(db, segment.id, machine, "translation", segment.revision)
        source = [{"id": unit["id"], "text": unit["text"]} for unit in segment.units]
        assert not save_version(db, segment.id, source, "source_retained", segment.revision)
        db.commit()
        assert db.get(Segment, segment.id).translation == "Humain."


async def test_only_a_forced_translation_job_retranslates_a_retained_passage(seeded, monkeypatch):
    pid = seeded[0]
    sid = retain(pid)
    calls = []

    async def reply(project, segment, *args, **kwargs):
        calls.append(segment.id)
        return TranslationResult(units=[{"id": unit["id"], "text": "Traduit."} for unit in segment.units])

    monkeypatch.setattr(pipeline, "translation_call", reply)
    with SessionLocal() as db:
        project = db.get(Project, pid)
        project.quality = "fast"
        enqueue(db, project, "translate", {"segment_id": sid})
        db.commit()
    await execute(*claim())
    assert calls == []
    with SessionLocal() as db:
        assert db.get(Segment, sid).retained_source is True
        job = enqueue(db, db.get(Project, pid), "translate", {"segment_id": sid, "force": True})
        db.commit()
        jid = job.id
    await execute(*claim())
    assert calls == [sid]
    with SessionLocal() as db:
        assert db.get(Job, jid).status == "completed"
        segment = db.get(Segment, sid)
        assert (segment.retained_source, segment.human, segment.translation) == (False, False, "Traduit.")
        assert segment.status in {"ok", "check"}
