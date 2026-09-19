import math

import pytest
from fastapi.testclient import TestClient

from app.api.estimates import PROMPT_OVERHEAD, RETRY_FACTOR
from app.config import settings
from app.db import SessionLocal
from app.main import app
from app.models import Chapter, Membership, Memory, Project, Provider, RequestLog, Segment, User
from app.security import password_hash

PASSWORD = "test-password-123456789"


@pytest.fixture(autouse=True)
def without_final_review(monkeypatch):
    # Each test opts in: the final review adds a conditional step that would blur the arithmetic.
    monkeypatch.setattr(settings(), "final_review_enabled", False)


def book(db, owner_id, provider_id, passages, quality="fast", chars=400):
    project = Project(owner_id=owner_id, title="Another Book", original_hash="h", original_path="/x",
                      provider_id=provider_id, quality=quality)
    db.add(project)
    db.flush()
    chapter = Chapter(project_id=project.id, position=0, title="One", resource="one.xhtml")
    db.add(chapter)
    db.flush()
    segments = [
        Segment(project_id=project.id, chapter_id=chapter.id, position=n, source="x" * chars, units=[])
        for n in range(passages)
    ]
    db.add_all(segments)
    db.flush()
    return project, segments


def calls(db, project, segment, operation, provider_id, count=1, failed=0, prompt=2000, completion=500):
    for number in range(count + failed):
        db.add(RequestLog(project_id=project.id, segment_id=segment.id if segment else None,
                          provider_id=provider_id, operation=operation, model="test-model",
                          fingerprint=f"{operation}{number}", parameters={}, messages=[],
                          status="error" if number >= count else "success",
                          prompt_tokens=prompt, completion_tokens=completion))


def estimate(pid, operation, username="tester"):
    with TestClient(app) as client:
        login = client.post("/api/auth/login", json={"username": username, "password": PASSWORD})
        assert login.status_code == 200
        return client.get(f"/api/projects/{pid}/estimate", params={"operation": operation})


def test_default_estimate_without_history(seeded, monkeypatch):
    monkeypatch.setattr(settings(), "analysis_mode", "strict")  # the parallel plan: test_parallel_analysis.py
    pid, _, provider_id = seeded
    with SessionLocal() as db:
        db.get(Provider, provider_id).input_cost, db.get(Provider, provider_id).output_cost = 2.0, 8.0
        segments = list(db.query(Segment).filter(Segment.project_id == pid))
        chapters = db.query(Chapter).filter(Chapter.project_id == pid).count()
        chars = sum(len(s.source) for s in segments)
        db.commit()
    with SessionLocal() as db:
        before = db.query(RequestLog).count()
    response = estimate(pid, "analyze")
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["basis_kind"] == "default" and result["history_books"] == 0
    assert "Estimation par défaut" in result["basis"]
    assert result["passages"] == len(segments)
    # One analysis per passage and one synthesis per chapter (fewer than four passages each).
    assert result["requests"] == round(len(segments) * RETRY_FACTOR) + round(chapters * RETRY_FACTOR)
    passage = chars / len(segments) / 4
    analysis = len(segments) * RETRY_FACTOR * (PROMPT_OVERHEAD + passage)
    synthesis = chapters * RETRY_FACTOR * (PROMPT_OVERHEAD + 3200)
    assert result["input_tokens"] == pytest.approx(analysis + synthesis, abs=2)
    expected = (result["input_tokens"] * 2 + result["output_tokens"] * 8) / 1_000_000
    assert result["cost"] == pytest.approx(expected, abs=1e-4)
    assert "Mock" in result["currency_note"]
    assert {step["operation"] for step in result["breakdown"]} == {"chapter_analysis", "book_analysis"}
    with SessionLocal() as db:
        assert db.query(RequestLog).count() == before  # never asks the model


def test_done_and_validated_passages_are_excluded(seeded, monkeypatch):
    monkeypatch.setattr(settings(), "analysis_mode", "strict")
    pid, user_id, provider_id = seeded
    with SessionLocal() as db:
        project, segments = book(db, user_id, provider_id, 6, quality="normal")
        segments[0].stage, segments[0].translation = "done", "fait"
        segments[1].human, segments[1].translation = True, "humain"
        segments[2].stage, segments[2].translation = "translated", "brouillon"
        segments[3].validated, segments[3].stage, segments[3].translation = True, "done", "validé"
        db.add(Memory(project_id=project.id, segment_id=segments[4].id, kind="analysis", content={}))
        project.bible_validated = True
        db.commit()
        other = project.id
    translate = estimate(other, "translate").json()
    # Left to translate: 2 (translated, needs its review), 4 and 5 (translation + review).
    assert translate["passages"] == 3
    steps = {step["operation"]: step["requests"] for step in translate["breakdown"]}
    assert steps == {"translation": round(2 * RETRY_FACTOR), "translation_review": round(3 * RETRY_FACTOR)}
    review = estimate(other, "review").json()
    assert review["passages"] == 5  # everything but the validated passage
    analyze = estimate(other, "analyze").json()
    assert analyze["passages"] == 5 and [s["operation"] for s in analyze["breakdown"]] == ["chapter_analysis"]


def test_nothing_left_to_do(seeded):
    pid = seeded[0]
    with SessionLocal() as db:
        for segment in db.query(Segment).filter(Segment.project_id == pid):
            segment.stage, segment.translation = "done", "fait"
        db.commit()
    result = estimate(pid, "translate").json()
    assert result["passages"] == 0 and result["requests"] == 0 and result["input_tokens"] == 0
    assert result["basis_kind"] == "none" and result["cost"] == 0


def test_history_of_the_owner_books_on_the_same_provider(seeded, monkeypatch):
    pid, user_id, provider_id = seeded
    monkeypatch.setattr(settings(), "final_review_enabled", True)
    with SessionLocal() as db:
        db.get(Project, pid).quality = "normal"
        past, segments = book(db, user_id, provider_id, 10, quality="normal")
        for number, segment in enumerate(segments):
            # Ten passages: translation retried twice in total, one review each, a final review on half.
            calls(db, past, segment, "translation", provider_id, failed=1 if number < 2 else 0,
                  prompt=3000, completion=900)
            calls(db, past, segment, "translation_review", provider_id, prompt=4000, completion=200)
            if number % 2:
                calls(db, past, segment, "final_review", provider_id, prompt=5000, completion=1000)
        # Neither another provider nor another owner's book counts.
        stranger = User(username="stranger", password_hash=password_hash(PASSWORD))
        elsewhere = Provider(name="Elsewhere", base_url="https://elsewhere.test/v1", model="other")
        db.add_all([stranger, elsewhere])
        db.flush()
        foreign, foreign_segments = book(db, stranger.id, provider_id, 5)
        for segment in foreign_segments:
            calls(db, foreign, segment, "translation", provider_id, count=9, prompt=99_999)
        calls(db, past, segments[0], "translation", elsewhere.id, count=9, prompt=99_999)
        db.commit()
        remaining = db.query(Segment).filter(Segment.project_id == pid).count()
    result = estimate(pid, "translate").json()
    assert result["basis_kind"] == "history" and result["history_books"] == 1
    assert result["basis"].startswith("Historique de 1 livre avec ce fournisseur (10 passages traités)")
    steps = {step["operation"]: step for step in result["breakdown"]}
    assert all(step["source"] == "history" for step in steps.values())
    assert steps["translation"]["requests"] == round(remaining * 1.2)
    assert steps["translation"]["input_tokens"] == round(remaining * 1.2 * 3000)
    assert steps["translation_review"]["requests"] == remaining
    assert steps["final_review"]["requests"] == round(remaining * 0.5)
    assert result["input_tokens"] == sum(step["input_tokens"] for step in steps.values())


def test_missing_steps_fall_back_to_defaults(seeded):
    pid, user_id, provider_id = seeded
    with SessionLocal() as db:
        db.get(Project, pid).quality = "normal"
        past, segments = book(db, user_id, provider_id, 6, quality="fast")
        for segment in segments:
            calls(db, past, segment, "translation", provider_id)
        db.commit()
    result = estimate(pid, "translate").json()
    assert result["basis_kind"] == "mixed"
    assert result["basis"].endswith("estimation par défaut pour : translation_review.")
    sources = {step["operation"]: step["source"] for step in result["breakdown"]}
    assert sources == {"translation": "history", "translation_review": "default"}


def test_too_little_history_is_ignored(seeded):
    pid, user_id, provider_id = seeded
    with SessionLocal() as db:
        past, segments = book(db, user_id, provider_id, 2)
        for segment in segments:
            calls(db, past, segment, "chapter_analysis", provider_id, prompt=50_000)
        db.commit()
    assert estimate(pid, "analyze").json()["basis_kind"] == "default"


def test_estimate_requires_read_access(seeded):
    pid, _, _ = seeded
    with SessionLocal() as db:
        reader = User(username="reader", password_hash=password_hash(PASSWORD))
        outsider = User(username="outsider", password_hash=password_hash(PASSWORD))
        db.add_all([reader, outsider])
        db.flush()
        db.add(Membership(project_id=pid, user_id=reader.id, role="reader"))
        db.commit()
    assert estimate(pid, "translate", "reader").status_code == 200
    assert estimate(pid, "translate", "outsider").status_code == 404
    assert estimate(pid, "export").status_code == 422
    with TestClient(app) as client:
        assert client.get(f"/api/projects/{pid}/estimate?operation=analyze").status_code == 401


def test_book_without_provider(seeded):
    pid = seeded[0]
    with SessionLocal() as db:
        db.get(Project, pid).provider_id = None
        db.commit()
    result = estimate(pid, "analyze").json()
    assert result["cost"] == 0 and result["basis_kind"] == "default"
    assert "Aucun fournisseur" in result["currency_note"]
    assert result["input_tokens"] > 0 and math.isfinite(result["input_tokens"])
