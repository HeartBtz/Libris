import io
import time
import zipfile

from fastapi.testclient import TestClient
from legacy_views import legacy_list_item
from sqlalchemy import event, select

from app.api.projects import import_book
from app.db import SessionLocal, engine
from app.main import app
from app.models import (
    Chapter,
    Glossary,
    Job,
    Membership,
    Memory,
    Project,
    Provider,
    RequestLog,
    Segment,
    User,
)
from app.security import password_hash

PASSWORD = "test-password-123456789"


def variant(book: bytes, number: int) -> bytes:
    """The same book with another file hash, so one owner can import it several times."""
    output = io.BytesIO(book)
    with zipfile.ZipFile(output, "a") as archive:
        archive.comment = f"variant {number}".encode()
    return output.getvalue()


def request(project_id, provider_id, operation, status="success", **values):
    return RequestLog(
        project_id=project_id,
        provider_id=provider_id,
        operation=operation,
        model="m",
        fingerprint="f",
        status=status,
        parameters={},
        messages=[],
        **values,
    )


def mixed_library(book: bytes, books: int = 6) -> tuple[str, list[str]]:
    """Books in every state the list shows: fresh, analysed, translating, reviewed, shared, archived."""
    with SessionLocal() as db:
        reader = User(username="reader", password_hash=password_hash(PASSWORD))
        other = User(username="other", password_hash=password_hash(PASSWORD))
        db.add_all([reader, other])
        cheap = Provider(name="Cheap", base_url="https://a.test/v1", model="cheap-model", input_cost=0.5, output_cost=2)
        free = Provider(name="Free", base_url="https://b.test/v1", model="free-model")
        db.add_all([cheap, free])
        db.flush()
        ids = []
        now = time.time()
        for number in range(books):
            owner = other if number == 4 else reader
            project = import_book(db, owner.id, variant(book, number))
            project.updated_at = now - number
            project.provider_id = cheap.id if number % 2 else None
            db.flush()
            ids.append(project.id)
            segments = list(db.scalars(select(Segment).where(Segment.project_id == project.id).order_by(Segment.position)))
            chapters = list(db.scalars(select(Chapter).where(Chapter.project_id == project.id)))
            if number == 0:
                continue
            for segment in segments[: number + 1]:
                db.add(Memory(project_id=project.id, segment_id=segment.id, kind="analysis", content={}))
            chapters[0].analyzed = True
            db.add(Glossary(project_id=project.id, source=f"Term {number}", translation="Terme"))
            if number >= 2:
                for index, segment in enumerate(segments):
                    segment.translation = "Traduit"
                    segment.status = ["ok", "check", "error", "refused", "ok", "check"][index % 6]
                    segment.validated = index == 0
                    segment.human = index == 4
                segments[-1].retained_source = number == 3
                db.add(request(project.id, cheap.id, "chapter_analysis", prompt_tokens=1000, completion_tokens=250, duration=4))
                db.add(request(project.id, cheap.id, "translation", prompt_tokens=2000, completion_tokens=500, duration=8))
                db.add(request(project.id, free.id, "final_review", prompt_tokens=10, completion_tokens=5, duration=2))
                db.add(request(project.id, cheap.id, "translation", status="error", prompt_tokens=7))
                db.add(
                    Job(
                        project_id=project.id,
                        provider_id=cheap.id,
                        operation="translate",
                        status="completed",
                        created_at=now - 100,
                        checkpoint={
                            "step": "final_review",
                            "final_review_targets": [s.id for s in segments[:4]],
                            "final_review_done": [s.id for s in segments[:3]],
                            "final_review_outcomes": {segments[1].id: {"outcome": "resolved", "revised": True}},
                        },
                    )
                )
            if number == 3:
                db.add(
                    Job(
                        project_id=project.id,
                        provider_id=free.id,
                        operation="resolve_validations",
                        status="paused",
                        created_at=now - 10,
                        checkpoint={"step": "final_review", "current": 2, "total": 5},
                    )
                )
            if number == 5:
                project.archived_at = now
                db.add(Job(project_id=project.id, operation="analyze", status="failed", created_at=now - 5, checkpoint={}))
        if books > 4:
            db.add(Membership(project_id=ids[4], user_id=reader.id, role="reader"))
        db.commit()
        return reader.id, ids


class QueryCounter:
    def __init__(self):
        self.count = 0

    def __call__(self, *_arguments):
        self.count += 1

    def __enter__(self):
        event.listen(engine, "before_cursor_execute", self)
        return self

    def __exit__(self, *_arguments):
        event.remove(engine, "before_cursor_execute", self)


def listed(client) -> tuple[list[dict], int]:
    with QueryCounter() as counter:
        response = client.get("/api/projects?include_archived=true")
    assert response.status_code == 200, response.text
    return response.json(), counter.count


def test_project_list_matches_the_previous_output_without_the_bible(book_bytes):
    reader_id, ids = mixed_library(book_bytes)
    with SessionLocal() as db:
        db.get(Project, ids[2]).bible = {"summary": "Un livre."}
        db.commit()
        expected = [
            legacy_list_item(db, project)
            for project in db.scalars(
                select(Project).where(Project.id.in_(ids)).order_by(Project.updated_at.desc())
            )
        ]
    with TestClient(app) as client:
        client.post("/api/auth/login", json={"username": "reader", "password": PASSWORD})
        books, _ = listed(client)
        detail = client.get(f"/api/projects/{ids[2]}").json()
    assert books == expected
    assert all("bible" not in book for book in books)
    assert detail["bible"]["summary"] == "Un livre."
    listed_book = next(book for book in books if book["id"] == ids[2])
    assert detail["progress"] == listed_book["progress"] and detail["stats"] == listed_book["stats"]
    varied = {(book["progress"]["active_stage"], book["progress"]["state"]) for book in books}
    assert len(varied) >= 4, varied
    assert any(book["progress"]["review"]["resolved"] for book in books)
    assert any(book["progress"]["estimate"]["spent_cost"] for book in books)


def test_project_list_runs_a_constant_number_of_queries(book_bytes):
    mixed_library(book_bytes, books=2)
    with TestClient(app) as client:
        client.post("/api/auth/login", json={"username": "reader", "password": PASSWORD})
        few, few_queries = listed(client)
    with SessionLocal() as db:
        reader = db.scalar(select(User).where(User.username == "reader"))
        for number in range(10, 16):
            import_book(db, reader.id, variant(book_bytes, number))
        db.commit()
        legacy = QueryCounter()
        with legacy:
            for project in db.scalars(select(Project)):
                legacy_list_item(db, project)
    with TestClient(app) as client:
        client.post("/api/auth/login", json={"username": "reader", "password": PASSWORD})
        many, many_queries = listed(client)
    assert len(many) == len(few) + 6
    assert many_queries == few_queries
    # Measured on this library of 8 books: 99 queries before (12 per book), 10 now for the whole
    # request, session and account lookups included.
    assert legacy.count >= 10 * len(many) and many_queries <= 15, (legacy.count, many_queries)
