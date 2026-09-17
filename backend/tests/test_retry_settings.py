import time

import pytest
from fastapi import HTTPException

from app.api.recovery import RecoverySettings, set_recovery
from app.db import SessionLocal
from app.jobs.queue import claim, enqueue, suspend
from app.models import Job, Project, User
from app.security import admin_user


def test_configured_retry_delay_and_capacity(seeded):
    with SessionLocal() as db:
        user = db.get(User, seeded[1])
        set_recovery(RecoverySettings(retry_seconds=15), user, db)
        job = enqueue(db, db.get(Project, seeded[0]), "translate", {})
        jid = job.id
        db.commit()
    _, owner = claim()
    suspend(jid, owner, "waiting", "provider_unavailable", "temporary")
    with SessionLocal() as db:
        job = db.get(Job, jid)
        delay = job.next_attempt - time.time()
        assert 14 <= delay <= 16, f"Expected 15s delay, got {delay:.1f}s"
        assert job.status == "waiting"
    assert claim() is None
    with SessionLocal() as db:
        db.get(Job, jid).next_attempt = 0
        db.commit()
    _, owner = claim()
    suspend(jid, owner, "waiting", "provider_unavailable", "rate limited", 120)
    with SessionLocal() as db:
        assert db.get(Job, jid).next_attempt > time.time() + 119


def test_retry_settings_require_admin_and_bounds(seeded):
    with pytest.raises(ValueError):
        RecoverySettings(retry_seconds=0)
    with pytest.raises(HTTPException):
        admin_user(User(username="reader", admin=False))
