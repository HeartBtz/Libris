import asyncio
import io
import zipfile

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.api import exports, observability
from app.config import settings
from app.db import SessionLocal
from app.engines.epub import inspect_archive
from app.engines.translation.versions import save_version
from app.main import app
from app.models import Chapter, Event, Segment

PASSWORD = "test-password-123456789"


def session_cookie(client) -> str:
    client.post("/api/auth/login", json={"username": "tester", "password": PASSWORD})
    return client.cookies.get("epub_session")


async def open_stream(path: str, cookie: str, until_disconnect: asyncio.Event) -> tuple[asyncio.Task, int, bytes]:
    """Drive the ASGI app like a browser that reads the first chunk of a live stream, then leaves."""
    received = asyncio.Event()
    status, body = 0, b""
    requested = False

    async def receive():
        nonlocal requested
        if not requested:
            requested = True
            return {"type": "http.request", "body": b"", "more_body": False}
        await until_disconnect.wait()
        return {"type": "http.disconnect"}

    async def send(message):
        nonlocal status, body
        if message["type"] == "http.response.start":
            status = message["status"]
        elif message["type"] == "http.response.body":
            body += message.get("body", b"")
            received.set()

    scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": "GET",
        "scheme": "http",
        "path": path,
        "raw_path": path.encode(),
        "query_string": b"after=0",
        "root_path": "",
        "headers": [(b"host", b"testserver"), (b"cookie", f"epub_session={cookie}".encode())],
        "client": ("127.0.0.1", 1234),
        "server": ("testserver", 80),
    }
    task = asyncio.create_task(app(scope, receive, send))
    await asyncio.wait_for(received.wait(), 10)
    return task, status, body


def test_event_streams_are_bounded_per_account_and_released(seeded, monkeypatch):
    pid = seeded[0]
    with SessionLocal() as db:
        db.add(Event(project_id=pid, created_at=0, payload={"status": "imported"}))
        db.commit()
    polls = []
    to_thread = asyncio.to_thread

    async def spy(function, *arguments):
        polls.append(function.__name__)
        return await to_thread(function, *arguments)

    monkeypatch.setattr(observability.asyncio, "to_thread", spy)
    monkeypatch.setattr(settings(), "event_streams_per_user", 2)
    with TestClient(app) as client:
        cookie = session_cookie(client)

    async def scenario():
        leave = asyncio.Event()
        opened = [await open_stream(f"/api/projects/{pid}/events", cookie, leave) for _ in range(2)]
        assert [status for _, status, _ in opened] == [200, 200]
        assert b"imported" in opened[0][2]
        assert observability.streams.by_user.total() == 2
        _, refused, body = await open_stream(f"/api/projects/{pid}/events", cookie, asyncio.Event())
        assert refused == 429 and "2 au maximum" in body.decode()
        leave.set()
        await asyncio.wait_for(asyncio.gather(*(task for task, _, _ in opened)), 10)
        assert observability.streams.by_user.total() == 0

    asyncio.run(scenario())
    # The database is polled from a worker thread, never inside the event loop.
    assert polls and set(polls) == {"new_events"}


def test_event_streams_are_bounded_for_the_whole_process(seeded, monkeypatch):
    monkeypatch.setattr(settings(), "event_streams_total", 1)
    release = observability.streams.acquire("someone-else")
    try:
        with TestClient(app) as client:
            session_cookie(client)
            response = client.get(f"/api/projects/{seeded[0]}/events")
        assert response.status_code == 429 and "trop de livres" in response.json()["detail"]
    finally:
        release()
    assert observability.streams.by_user.total() == 0


def bomb(entries: int, size: int) -> bytes:
    """An EPUB-shaped archive whose entries stay below 1 MiB each but add up to a large book."""
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("mimetype", "application/epub+zip", zipfile.ZIP_STORED)
        archive.writestr("META-INF/container.xml", "<container/>")
        for number in range(entries):
            archive.writestr(f"OEBPS/filler{number}.xhtml", b" " * size)
    return output.getvalue()


def test_the_compression_ratio_is_checked_for_the_whole_archive():
    data = bomb(40, 1024**2 - 1)
    assert len(data) < 200 * 1024
    with pytest.raises(ValueError, match="global excessif"):
        inspect_archive(data)
    assert len(inspect_archive(bomb(4, 1024**2 - 1))) == 6  # a small repetitive book is fine


def test_the_declared_total_size_is_checked_before_unpacking(monkeypatch):
    monkeypatch.setattr(settings(), "max_unpacked_mb", 1)
    with pytest.raises(ValueError, match="Limite de décompression"):
        inspect_archive(bomb(3, 512 * 1024))


def test_previews_reuse_the_unpacked_book_until_a_passage_changes(seeded, monkeypatch):
    pid = seeded[0]
    rebuilds = []
    rebuild = exports.rebuild

    def counting(*arguments, **options):
        rebuilds.append(1)
        return rebuild(*arguments, **options)

    monkeypatch.setattr(exports, "rebuild", counting)
    with SessionLocal() as db:
        chapter = db.scalar(
            select(Chapter).where(Chapter.project_id == pid, Chapter.resource.endswith("chapter2.xhtml"))
        )
        segment = db.scalar(
            select(Segment).where(Segment.chapter_id == chapter.id, Segment.source.contains("Alice discovered"))
        )
        chapter_id, segment_id = chapter.id, segment.id
        units = [{"id": u["id"], "text": u["text"].replace("Alice", "Alicia")} for u in segment.units]
    with TestClient(app) as client:
        session_cookie(client)
        path = f"/api/projects/{pid}/preview/{chapter_id}"
        first, second = client.get(path), client.get(path)
        assert first.status_code == second.status_code == 200 and first.json() == second.json()
        assert len(rebuilds) == 1
        with SessionLocal() as db:
            save_version(db, segment_id, units, "human", 0)
            db.commit()
        edited = client.get(path)
    assert len(rebuilds) == 2 and "Alicia" in edited.json()["html"]


def test_project_restore_runs_outside_the_event_loop(seeded, monkeypatch):
    called = []

    async def spy(function, *arguments):
        called.append(function.__name__)
        return function(*arguments)

    monkeypatch.setattr(exports, "run_in_threadpool", spy)
    with TestClient(app) as client:
        session_cookie(client)
        content = client.get(f"/api/projects/{seeded[0]}/export/project").content
        client.delete(f"/api/projects/{seeded[0]}")
        assert client.post("/api/projects/import", files={"file": ("p.zip", content)}).status_code == 201
    assert called == ["restore_from_archive"]
