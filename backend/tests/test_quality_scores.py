"""Passage quality scores: computed from recorded signals, kept in step with the passage, shown ranked."""

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete, select, update

from app.db import SessionLocal
from app.engines.autopilot.decisions import record
from app.engines.quality.score import band, score_passage
from app.main import app
from app.models import Issue, Project, RequestLog, Segment
from app.models.quality import PassageQuality

SOURCE = "Alice walked slowly along the old river, counting the silver lanterns that lined the quay at dusk."
GOOD = (
    "Alice marchait lentement le long du vieux fleuve, comptant les lanternes d’argent du quai au crépuscule."
)


def passage(**values) -> dict:
    return {
        "source": SOURCE, "translation": GOOD, "status": "ok", "retained_source": False, "validated": False,
        "critique": [], "uncertainties": [], "error": "", **values,
    }  # fmt: skip


def codes(signals: list[dict]) -> dict[str, int]:
    return {item["code"]: item["penalty"] for item in signals}


def test_a_clean_passage_scores_100_and_each_signal_costs_points():
    assert score_passage(passage(), [], [], 0) == (100, [])
    score, signals = score_passage(
        passage(critique=[{"severity": "error"}, {"severity": "warning"}], uncertainties=["a", "b"]),
        [("locked_term", "error"), ("repetition", "warning"), ("length", "warning")],
        [("recovery", "recovered"), ("settle", "kept_translation"), ("arbitration", "rejected")],
        2,
    )
    assert codes(signals) == {
        "locked_term": 20, "critique": 15, "recovery": 10, "open_points_closed": 12, "doubt": 10,
        "alert_warning": 8, "retry": 6, "arbitration": 2,
    }  # fmt: skip
    assert score == 100 - sum(codes(signals).values()) == 17 and band(score) == "poor"
    # Largest penalty first: the reason a passage ranks low comes first.
    assert signals[0]["code"] == "locked_term"


def test_retained_originals_failures_and_lengths_rank_low_and_validation_settles():
    assert score_passage(passage(translation=SOURCE, retained_source=True), [], [], 0)[0] == 40
    assert codes(score_passage(passage(status="error"), [], [], 0)[1]) == {"failed": 40}
    assert codes(score_passage(passage(translation="Alice."), [], [], 0)[1]) == {"length_ratio": 15}
    assert codes(score_passage(passage(translation=GOOD * 3), [], [], 0)[1]) == {"length_ratio": 5}
    # Ideographic sources grow a lot in alphabetic languages: only the hard bounds apply.
    cjk = "爱丽丝沿着古老的河流慢慢地走着，数着黄昏时分码头上排列的银色灯笼。" * 4
    assert score_passage(passage(source=cjk, translation=GOOD * 2), [], [], 0)[0] == 100
    # Penalties are capped per signal, and the score never goes below 0.
    many = [("locked_term", "error")] * 5 + [("x", "error")] * 5 + [("y", "warning")] * 9
    score, signals = score_passage(passage(uncertainties=["?"] * 9), many, [], 20)
    assert codes(signals)["locked_term"] == 40 and codes(signals)["retry"] == 12 and score == 0
    # Someone read and validated it.
    assert score_passage(passage(validated=True), many, [], 3) == (
        100,
        [{"code": "validated", "count": 1, "penalty": 0}],
    )
    assert [band(value) for value in (100, 85, 84, 70, 69, 50, 49)] == [
        "good", "good", "fair", "fair", "weak", "weak", "poor",
    ]  # fmt: skip


def stored(sid: str) -> PassageQuality | None:
    with SessionLocal() as db:
        return db.get(PassageQuality, sid)


def translate(pid: str) -> list[str]:
    with SessionLocal() as db:
        segments = db.scalars(
            select(Segment).where(Segment.project_id == pid).order_by(Segment.position)
        ).all()
        for segment in segments:
            segment.translated_units = [dict(unit) for unit in segment.units]
            segment.translation = "\n\n".join(unit["text"] for unit in segment.units) + " (fr)"
            segment.status = "ok"
        db.commit()
        return [segment.id for segment in segments]


def test_scores_follow_every_change_of_a_passage(seeded):
    pid = seeded[0]
    first, second, *_ = ids = translate(pid)
    assert all(stored(sid) is not None for sid in ids)
    assert stored(first).score == 100 and stored(first).band == "good"
    # An alert, added through the ORM.
    with SessionLocal() as db:
        db.add(Issue(project_id=pid, segment_id=first, severity="error", code="locked_term", message="x"))
        db.commit()
    assert (stored(first).score, stored(first).signals[0]["code"]) == (80, "locked_term")
    # Settled by a bulk UPDATE, as the recovery and the arbitration do.
    with SessionLocal() as db:
        db.execute(update(Issue).where(Issue.segment_id == first).values(resolved=True))
        db.commit()
    assert stored(first).score == 100
    # A bulk DELETE of alerts, as a new automatic check does, is followed too.
    with SessionLocal() as db:
        db.add(Issue(project_id=pid, segment_id=second, severity="warning", code="repetition", message="y"))
        db.commit()
        assert db.get(PassageQuality, second).score == 92
        db.execute(delete(Issue).where(Issue.segment_id == second))
        db.commit()
    assert stored(second).score == 100
    # Failed model calls and the autopilot's recovery of the passage.
    with SessionLocal() as db:
        project = db.get(Project, pid)
        for status in ("error", "success", "refused"):
            db.add(
                RequestLog(project_id=pid, segment_id=first, provider_id=project.provider_id, operation="translation",
                           model="m", fingerprint="f", status=status, parameters={}, messages=[])
            )  # fmt: skip
        record(db, pid, stage="recovery", kind="failed_passage", action="recovered", segment_id=first)
        db.commit()
    assert codes(stored(first).signals) == {"retry": 6, "recovery": 10}
    # Open critiques on the passage itself.
    with SessionLocal() as db:
        db.get(Segment, second).critique = [{"severity": "error", "description": "Contresens"}]
        db.commit()
    assert codes(stored(second).signals) == {"critique": 10}
    # A passage that loses its translation has nothing left to score.
    with SessionLocal() as db:
        segment = db.get(Segment, second)
        segment.translation, segment.translated_units, segment.status = "", [], "pending"
        db.commit()
    assert stored(second) is None
    # A rolled-back change leaves the stored score as it was.
    with SessionLocal() as db:
        db.get(Segment, first).validated = True
        db.flush()
        db.rollback()
    assert stored(first).score == 84


@pytest.fixture
def client(seeded):
    with TestClient(app) as test_client:
        login = {"username": "tester", "password": "test-password-123456789"}
        assert test_client.post("/api/auth/login", json=login).status_code == 200
        yield test_client


def test_the_dashboard_ranks_weakest_first_and_repairs_missing_scores(seeded, client):
    pid = seeded[0]
    ids = translate(pid)
    with SessionLocal() as db:
        segments = {s.id: s for s in db.scalars(select(Segment).where(Segment.id.in_(ids)))}
        weak, weaker, validated = segments[ids[-1]], segments[ids[-2]], segments[ids[0]]
        weak.uncertainties = ["?", "?", "?"]
        weak.critique = [{"severity": "error"}, {"severity": "error"}]
        weaker.translation, weaker.retained_source, weaker.status = weaker.source, True, "source_retained"
        validated.validated = True
        validated.critique = [{"severity": "error"}]
        db.commit()
        weak_chapter = weaker.chapter_id
        # Scores lost or computed on an older revision (books translated before the scores existed).
        db.execute(delete(PassageQuality).where(PassageQuality.segment_id.in_(ids[1:3])))
        db.execute(
            update(PassageQuality).where(PassageQuality.segment_id == ids[3]).values(revision=-1, score=1)
        )
        db.commit()
    data = client.get(f"/api/projects/{pid}/quality").json()
    summary = data["summary"]
    assert summary["scored"] == len(ids) == sum(summary["bands"].values()) == sum(summary["histogram"])
    assert summary["minimum"] == 40 and summary["to_review"] == 2 and summary["review_below"] == 70
    assert [item["segment_id"] for item in data["review_first"]] == [weaker.id, weak.id]
    first = data["review_first"][0]
    assert (
        first["score"] == 40 and first["band"] == "poor" and first["signals"][0]["code"] == "source_retained"
    )
    assert first["chapter_title"] and first["excerpt"] and first["project_id"] == pid
    assert data["chapters_total"] == len(data["chapters"])
    averages = [chapter["average"] for chapter in data["chapters"]]
    assert averages == sorted(averages) and averages[0] < 100 and averages[-1] == 100
    chapter = next(item for item in data["chapters"] if item["chapter_id"] == weak_chapter)
    assert chapter["minimum"] == 40 and sum(item["weak"] for item in data["chapters"]) == 2
    with SessionLocal() as db:
        assert db.get(PassageQuality, ids[3]).score == 100 and db.get(PassageQuality, ids[1]) is not None
    # The editor reads the scores of one chapter.
    scores = client.get(f"/api/projects/{pid}/quality/passages", params={"chapter_id": weak_chapter}).json()
    assert scores[weaker.id]["score"] == 40 and scores[weaker.id]["signals"][0]["code"] == "source_retained"
    # The completion report carries the summary.
    completion = client.get(f"/api/projects/{pid}/completion").json()
    assert completion["quality"]["to_review"] == 2 and completion["quality"]["scored"] == len(ids)


def test_the_series_dashboard_covers_its_readable_volumes(seeded, client):
    from epubs import epub_bytes

    from app.api.projects import import_book

    pid, owner, _ = seeded
    with SessionLocal() as db:
        db.get(Project, pid).series_name = "Saga"
        second = import_book(db, owner, epub_bytes("<p>The second volume opens at night.</p>", uid="v2"))
        second.series_name, second.volume_number = "Saga", 2
        archived = import_book(db, owner, epub_bytes("<p>An archived volume.</p>", uid="v3"))
        archived.series_name, archived.archived_at = "Saga", 1.0
        db.commit()
        series_id = db.get(Project, pid).series_id
        other, hidden = second.id, archived.id
    translate(pid)
    translate(other)
    translate(hidden)
    with SessionLocal() as db:
        segment = db.scalars(
            select(Segment).where(Segment.project_id == other).order_by(Segment.position)
        ).first()
        segment.uncertainties = ["?", "?", "?"]
        db.commit()
    data = client.get(f"/api/series/{series_id}/quality").json()
    assert {volume["project_id"] for volume in data["volumes"]} == {pid, other}
    assert data["chapters"][0]["project_id"] == other and data["chapters"][0]["volume_number"] == 2
    assert data["review_first"] == [] and data["summary"]["minimum"] == 85
    assert client.get("/api/series/unknown/quality").status_code == 404
