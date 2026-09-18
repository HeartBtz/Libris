
import respx
from epubs import epub_files, xhtml
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from test_pipeline import mock_completion, run_job

from app.api.projects import import_book
from app.db import SessionLocal
from app.engines.translation.memory import translation_memory_enabled
from app.main import app
from app.models import Glossary, Project, Provider, RequestLog, Segment, TranslationVersion, User
from app.security import password_hash

INTERLUDE = "<h1>Interlude</h1><p>The bells rang over the silent city, and nobody answered them.</p>"


def chapter(number: int) -> str:
    return xhtml(f"<h1>Chapter {number}</h1><p>Story number {number} begins with a unique sentence.</p>")


def repeated_book(uid: str) -> bytes:
    files = {}
    for number in range(1, 6):
        files[f"i{number}.xhtml"] = xhtml(INTERLUDE)
        files[f"c{number}.xhtml"] = chapter(number)
    return epub_files(files, uid=uid)


def owner_and_provider():
    with SessionLocal() as db:
        user = db.scalar(select(User).where(User.username == "tester"))
        if not user:
            user = User(username="tester", password_hash=password_hash("test-password-123456789"), admin=True)
            db.add(user)
        provider = Provider(
            name="Mock",
            base_url="https://llm.test/v1",
            model="m",
            capabilities={"supports_json_schema": True},
            context_window=64000,
        )
        db.add(provider)
        db.commit()
        return user.id, provider.id


def book(data: bytes, quality: str = "fast", **fields) -> str:
    user_id, provider_id = owner_and_provider()
    with SessionLocal() as db:
        project = import_book(db, user_id, data)
        project.provider_id, project.quality, project.context_backend = provider_id, quality, "internal"
        project.bible = {"summary": "test"}
        for key, value in fields.items():
            setattr(project, key, value)
        db.commit()
        return project.id


def translation_calls(pid: str) -> int:
    with SessionLocal() as db:
        return db.scalar(
            select(func.count())
            .select_from(RequestLog)
            .where(
                RequestLog.project_id == pid,
                RequestLog.operation == "translation",
                RequestLog.cached.is_(False),
            )
        )


def reused(pid: str, text: str = "") -> list[Segment]:
    with SessionLocal() as db:
        return db.scalars(
            select(Segment)
            .join(TranslationVersion, TranslationVersion.segment_id == Segment.id)
            .where(
                Segment.project_id == pid,
                TranslationVersion.origin == "translation_memory",
                Segment.source.contains(text),
            )
        ).all()


@respx.mock
async def test_repeated_passages_are_translated_once_and_the_rate_is_measured():
    respx.post("https://llm.test/v1/chat/completions").mock(side_effect=mock_completion)
    pid = book(repeated_book("tm-1"))
    job = await run_job(pid, "translate")
    assert job.status == "completed", job.error
    with SessionLocal() as db:
        keys = db.scalars(select(Segment.source_key).where(Segment.project_id == pid)).all()
    calls = translation_calls(pid)
    # Five identical interludes: the first is translated, the four others come from the memory, like
    # the <title> repeated in every document.
    assert len(reused(pid, "bells")) == 4
    assert len(reused(pid)) == len(keys) - len(set(keys))
    assert calls == len(set(keys))
    print(f"translation memory: {len(reused(pid))}/{len(keys)} passages reused, {calls} model calls")
    with TestClient(app) as client:
        client.post("/api/auth/login", json={"username": "tester", "password": "test-password-123456789"})
        project = client.get(f"/api/projects/{pid}").json()
    assert project["stats"]["translation_memory_reused"] == len(keys) - len(set(keys))
    assert project["translation_memory"] is True


@respx.mock
async def test_memory_crosses_books_of_the_same_owner_and_prefers_human_choices():
    respx.post("https://llm.test/v1/chat/completions").mock(side_effect=mock_completion)
    first = book(repeated_book("tm-a"))
    await run_job(first, "translate")
    with SessionLocal() as db:
        interludes = db.scalars(
            select(Segment).where(Segment.project_id == first, Segment.source.contains("bells"))
        ).all()
        chosen = interludes[-1]
        chosen.translated_units = [{"id": u["id"], "text": "HUMAN " + u["text"]} for u in chosen.units]
        chosen.translation = "\n\n".join(u["text"] for u in chosen.translated_units)
        chosen.human = chosen.validated = True
        db.commit()
    second = book(
        epub_files({"a.xhtml": xhtml(INTERLUDE), "b.xhtml": chapter(9)}, uid="tm-b"), quality="normal"
    )
    job = await run_job(second, "translate")
    assert job.status == "completed", job.error
    [segment] = reused(second, "bells")
    assert segment.translated_units[0]["text"].startswith("HUMAN ")
    # Reuse replaces the first translation only: review still runs on the reused passage.
    with SessionLocal() as db:
        reviews = db.scalar(
            select(func.count())
            .select_from(RequestLog)
            .where(RequestLog.segment_id == segment.id, RequestLog.operation == "translation_review")
        )
    assert reviews == 1


@respx.mock
async def test_memory_respects_series_order_setting_and_locked_terms():
    respx.post("https://llm.test/v1/chat/completions").mock(side_effect=mock_completion)
    later = book(repeated_book("tm-v2"), series_name="Saga", volume_number=2)
    await run_job(later, "translate")
    earlier = book(
        epub_files({"a.xhtml": xhtml(INTERLUDE)}, uid="tm-v1"), series_name="Saga", volume_number=1
    )
    await run_job(earlier, "translate")
    assert reused(earlier, "bells") == []  # a later volume never feeds an earlier one

    other = book(epub_files({"a.xhtml": xhtml(INTERLUDE)}, uid="tm-off"))
    with TestClient(app) as client:
        client.post("/api/auth/login", json={"username": "tester", "password": "test-password-123456789"})
        body = client.get(f"/api/projects/{other}").json()
        fields = ("title", "author", "source_language", "target_language", "quality", "provider_id")
        config = {key: body[key] for key in fields}
        assert (
            client.put(f"/api/projects/{other}", json={**config, "translation_memory": False}).status_code
            == 200
        )
        # A client that does not know the setting keeps it as stored.
        client.put(f"/api/projects/{other}", json=config)
        assert client.get(f"/api/projects/{other}").json()["translation_memory"] is False
    await run_job(other, "translate")
    assert reused(other) == []  # not even within the book

    locked = book(epub_files({"a.xhtml": xhtml(INTERLUDE)}, uid="tm-locked"))
    with SessionLocal() as db:
        db.add(Glossary(project_id=locked, source="bells", translation="cloches", accepted=True, locked=True))
        db.commit()
    await run_job(locked, "translate")
    assert reused(locked, "bells") == []  # the remembered text does not say "cloches"


async def test_different_formatting_is_not_reused():
    pid = book(
        epub_files(
            {"a.xhtml": xhtml("<p>The <b>bells</b> rang.</p>"), "b.xhtml": xhtml("<p>The bells rang.</p>")}
        )
    )
    with SessionLocal() as db:
        keys = db.scalars(select(Segment.source_key).where(Segment.project_id == pid)).all()
        assert len(set(keys)) == len(keys)
        assert translation_memory_enabled(db.get(Project, pid))
