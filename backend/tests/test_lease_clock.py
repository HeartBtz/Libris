"""Job leases on the database clock (audit R-13): a worker's clock jump neither steals nor loses a job."""

import time
import types

import pytest
from sqlalchemy import select

from app.db import SessionLocal
from app.jobs import clock, queue
from app.jobs.queue import JobStopped, checkpoint, claim, enqueue, fence
from app.models import Job, Project


def skewed(monkeypatch, seconds: float) -> None:
    """This process's wall clock is `seconds` off; the database's is not."""
    fake = types.SimpleNamespace(
        time=lambda: time.time() + seconds, monotonic=time.monotonic, sleep=time.sleep
    )
    monkeypatch.setattr(queue, "time", fake)


def claimed(pid: str) -> tuple[str, str]:
    with SessionLocal() as db:
        enqueue(db, db.get(Project, pid), "translate", {})
        db.commit()
    found = claim()
    assert found
    return found


def test_the_database_clock_is_the_wall_clock_of_its_server():
    with SessionLocal() as db:
        assert clock.now(db) == pytest.approx(time.time(), abs=5)


@pytest.mark.parametrize("seconds", [3600, -3600])
def test_a_clock_jump_in_a_worker_does_not_move_leases(seeded, monkeypatch, seconds):
    pid, _, _ = seeded
    job_id, owner = claimed(pid)
    skewed(monkeypatch, seconds)
    # Another worker whose clock jumped ahead does not take a job whose lease is alive...
    assert claim() is None
    # ...and the owner, whatever its own clock says, keeps renewing and writing.
    renewed = checkpoint(job_id, owner, {"step": "translation"})
    with SessionLocal() as db:
        assert fence(db, job_id, owner).id == job_id
        assert renewed.lease_until == pytest.approx(clock.now(db) + clock.LEASE_SECONDS, abs=5)


def test_an_expired_lease_is_taken_over_by_database_time(seeded):
    pid, _, _ = seeded
    job_id, owner = claimed(pid)
    with SessionLocal() as db:
        db.get(Job, job_id).lease_until = clock.now(db) - 1
        db.commit()
    with SessionLocal() as db, pytest.raises(JobStopped):
        fence(db, job_id, owner)
    taken = claim()
    assert taken and taken[0] == job_id and taken[1] != owner
    with SessionLocal() as db:
        assert db.scalar(select(Job.lease_owner).where(Job.id == job_id)) == taken[1]
