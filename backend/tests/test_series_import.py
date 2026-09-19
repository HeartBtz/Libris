"""Two-phase imports into series: EPUB volumes, TXT chapters, idempotence and explicit replacement."""

import codecs

import pytest
from epubs import epub_bytes
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.db import SessionLocal
from app.engines.ingestion.naming import natural_key, propose_chapters, propose_volumes, series_from_names
from app.engines.ingestion.text import TextRejected, decode, text_chapter
from app.main import app
from app.models import Chapter, Project, Segment, Series, SourceAsset, User
from app.security import password_hash

PASSWORD = "test-password-123456789"


@pytest.fixture
def client():
    with SessionLocal() as db:
        db.add(User(username="reader", password_hash=password_hash(PASSWORD), admin=True))
        db.commit()
    with TestClient(app) as client:
        assert client.post("/api/auth/login", json={"username": "reader", "password": PASSWORD}).status_code == 200
        yield client


def volume(number: int, uid: str | None = None) -> bytes:
    return epub_bytes(f"<h1>Part {number}</h1><p>Alice walked into volume {number} of the saga.</p>", uid=uid or f"v{number}")


def upload(client, fmt: str, files: list[tuple[str, bytes]]) -> dict:
    session = client.post("/api/imports", json={"format": fmt}).json()
    for name, data in files:
        response = client.post(f"/api/imports/{session['id']}/files", files={"file": (name, data)})
        assert response.status_code == 201, response.text
    return client.get(f"/api/imports/{session['id']}").json()


def test_volume_numbers_follow_the_batch_then_the_name_then_the_metadata():
    names = [f"Saga of Stars - Vol. {n}.epub" for n in (1, 2, 10, 11)]
    assert [p.number for p in propose_volumes(names, [{}] * 4)] == [1, 2, 10, 11]
    assert series_from_names(names).name == "Saga of Stars"
    for name, number in {
        "Saga Volume 3.epub": 3, "saga vol 4.epub": 4, "Saga Tome 5.epub": 5, "Saga Book 6.epub": 6,
        "Saga v07.epub": 7, "Saga #08.epub": 8, "Saga [09].epub": 9, "Saga Tome IV.epub": 4,
    }.items():  # fmt: skip
        assert propose_volumes([name], [{}])[0].number == number, name
    assert [p.number for p in propose_volumes(["x_03.epub", "x_01.epub", "x_02.epub"], [{}] * 3)] == [3, 1, 2]
    metadata = propose_volumes(["Untitled.epub"], [{"series_index": 4.0}])[0]
    assert (metadata.number, metadata.confidence) == (4, "medium")
    unknown = propose_volumes(["Untitled.epub"], [{}])[0]
    assert (unknown.number, unknown.confidence) == (None, "low")


def test_chapter_numbers_and_natural_order():
    names = ["Chapter 1.txt", "Chapter 001 - Beginning.txt", "Chapitre 12.txt", "Ch. 12.txt", "C012.txt", "001.txt"]
    assert [guess.value for guess in propose_chapters(names)] == [1, 1, 12, 12, 12, 1]
    assert sorted(["Chapter 10.txt", "Chapter 2.txt", "Chapter 1.txt"], key=natural_key) == [
        "Chapter 1.txt", "Chapter 2.txt", "Chapter 10.txt",
    ]  # fmt: skip


@pytest.mark.parametrize(
    ("data", "encoding"),
    [
        ("Élodie arriva.\n".encode(), "utf-8"),
        (codecs.BOM_UTF8 + "Élodie arriva.\n".encode(), "utf-8-sig"),
        (codecs.BOM_UTF16_LE + "Élodie arriva.\n".encode("utf-16-le"), "utf-16-le"),
        (codecs.BOM_UTF16_BE + "Élodie arriva.\n".encode("utf-16-be"), "utf-16-be"),
        ("Élodie arriva.\n".encode("cp1252"), "windows-1252"),
    ],
)
def test_text_encodings(data, encoding):
    text, found, warnings = decode(data)
    assert (text, found) == ("Élodie arriva.\n", encoding)
    assert bool(warnings) == (encoding == "windows-1252")


def test_text_layout_is_kept_and_long_paragraphs_are_cut_at_sentences():
    long = " ".join(f"Sentence number {n} is here." for n in range(400))
    source = "Chapter 1\r\n\r\n“Hello,” she said.\r\n\u3000\u3000Indented line.\r\n\r\n* * *\r\n\r\n" + long + "\r\n"
    chapter, warnings = text_chapter(source, title="Chapter 1", resource="txt/test", max_chars=500)
    units = [unit for group in chapter.groups for unit in group]
    assert not warnings
    assert units[0]["tag"] == "h1" and units[0]["text"] == "Chapter 1"
    assert {"fixed": "* * *", "gap": 1, "indent": ""} in chapter.meta["layout"]["items"]
    assert any(item.get("indent") == "\u3000\u3000" for item in chapter.meta["layout"]["items"])
    parts = [unit for unit in units if unit.get("original_id") and unit["parts"] > 1]
    assert len(parts) > 1 and all(len(unit["text"]) <= 500 for unit in parts)
    assert "".join(unit["text"] for unit in parts) == long
    again, _ = text_chapter(source, title="Chapter 1", resource="txt/test", max_chars=500)
    assert [u["id"] for g in again.groups for u in g] == [u["id"] for u in units]
    with pytest.raises(TextRejected):
        text_chapter("Bad ⟦t0⟧ marker", title="x", resource="txt/x")


def test_epub_series_import_with_correction_and_idempotent_commit(client):
    books = {n: volume(n) for n in (1, 2, 4)}  # built once: ZIP timestamps would change the hash
    session = upload(client, "epub", [(f"Star Saga - Vol. {n}.epub", books[n]) for n in (1, 2, 4)])
    proposal = session["proposal"]
    assert proposal["series"]["name"] == "Star Saga"
    assert [item["volume_number"] for item in proposal["items"]] == [1, 2, 4]
    assert proposal["missing_numbers"] == [3]
    items = [
        {"index": item["index"], "volume_number": item["volume_number"], "title": ""} for item in proposal["items"]
    ]
    items[2]["volume_number"] = 3  # the person corrects the proposal
    body = {"destination": {"mode": "series", "series_name": "Star Saga"}, "items": items}
    first = client.post(f"/api/imports/{session['id']}/commit", json=body)
    assert first.status_code == 200, first.text
    again = client.post(f"/api/imports/{session['id']}/commit", json=body)
    assert again.json()["projects"] == first.json()["projects"]
    with SessionLocal() as db:
        series = db.scalar(select(Series))
        volumes = db.scalars(select(Project).where(Project.series_id == series.id).order_by(Project.volume_number)).all()
        assert [v.volume_number for v in volumes] == [1, 2, 3]
        assert all(v.series_name == "Star Saga" and v.source_format == "epub" for v in volumes)
        assert db.scalar(select(SourceAsset).where(SourceAsset.project_id == volumes[0].id)).format == "epub"
    listed = client.get("/api/series").json()
    assert [(s["name"], s["volumes"], s["formats"]) for s in listed] == [("Star Saga", 3, ["epub"])]
    # The same file again: reported as already imported, refused if not removed.
    duplicate = upload(client, "epub", [("Star Saga - Vol. 1.epub", books[1])])
    assert duplicate["files"][0]["duplicate"]["kind"] == "library"


def test_epub_needs_a_destination_and_a_confirmed_number(client):
    session = upload(client, "epub", [("Untitled.epub", volume(9))])
    item = {"index": 0, "volume_number": None}
    assert client.post(f"/api/imports/{session['id']}/commit", json={"items": [item]}).status_code == 422
    body = {"destination": {"mode": "series", "series_name": "Saga"}, "items": [item]}
    refused = client.post(f"/api/imports/{session['id']}/commit", json=body)
    assert refused.status_code == 422 and "volume" in refused.text
    standalone = {"destination": {"mode": "standalone"}, "items": [item]}
    created = client.post(f"/api/imports/{session['id']}/commit", json=standalone).json()
    with SessionLocal() as db:
        project = db.get(Project, created["projects"][0]["id"])
        assert project.series_id is None and project.volume_number is None


def test_duplicate_volume_numbers_are_blocking(client):
    session = upload(client, "epub", [("A 1.epub", volume(1)), ("A 2.epub", volume(2))])
    items = [{"index": 0, "volume_number": 1}, {"index": 1, "volume_number": 1}]
    body = {"destination": {"mode": "series", "series_name": "A"}, "items": items}
    response = client.post(f"/api/imports/{session['id']}/commit", json=body)
    assert response.status_code == 422 and "plusieurs fois" in response.text


def chapter(number: int, text: str | None = None) -> tuple[str, bytes]:
    return f"Chapter {number}.txt", (text or f"Chapter {number}\n\nLine one of chapter {number}.\nLine two.\n").encode()


def commit_txt(client, files, items=None, **extra):
    session = upload(client, "txt", files)
    items = items or [
        {"index": item["index"], "chapter_number": item["chapter_number"]} for item in session["proposal"]["items"]
    ]
    body = {"destination": {"mode": "series", "series_name": "Web Novel"}, "items": items, **extra}
    return session, client.post(f"/api/imports/{session['id']}/commit", json=body)


def test_txt_chapters_become_one_continuous_volume_in_natural_order(client):
    session, response = commit_txt(client, [chapter(10), chapter(2), chapter(1)])
    assert response.status_code == 200, response.text
    assert [item["chapter_number"] for item in session["proposal"]["items"]] == [1, 2, 10]
    with SessionLocal() as db:
        projects = db.scalars(select(Project)).all()
        assert [(p.project_kind, p.source_format) for p in projects] == [("serial", "txt")]
        chapters = db.scalars(select(Chapter).order_by(Chapter.position)).all()
        assert [c.chapter_number for c in chapters] == [1, 2, 10]
        positions = [s.position for s in db.scalars(select(Segment).order_by(Segment.position))]
        assert positions == list(range(len(positions)))
        assert db.scalar(select(Series)).kind == "webnovel"
    # TXT chapters always belong to a series.
    session = upload(client, "txt", [chapter(11)])
    body = {"destination": {"mode": "standalone"}, "items": [{"index": 0, "chapter_number": 11}]}
    assert client.post(f"/api/imports/{session['id']}/commit", json=body).status_code == 422


def long_chapter(second: str) -> tuple[str, bytes]:
    first = " ".join(f"Alice counted star {n} above the tower." for n in range(80))
    return "Chapter 1.txt", f"Chapter 1\n\n{first}\n\n{second}\n".encode()


def test_incremental_chapters_identical_content_and_explicit_replacement(client):
    original = " ".join(f"Bob answered question {n} slowly." for n in range(90))
    _, first = commit_txt(client, [long_chapter(original), chapter(2)])
    project_id = first.json()["projects"][0]["id"]
    with SessionLocal() as db:
        segments = db.scalars(select(Segment).where(Segment.project_id == project_id).order_by(Segment.position)).all()
        assert len(segments) == 3
        human = segments[0]
        human.translation = "Traduction humaine"
        human.translated_units = [{"id": u["id"], "text": "Traduit"} for u in human.units]
        human.human = human.validated = True
        kept = human.id
        db.commit()
    # Chapter 3 is added later; identical chapters answer as unchanged, nothing is translated again.
    _, later = commit_txt(client, [long_chapter(original), chapter(3)], target={"mode": "serial"})
    assert later.json()["chapters"]["created"] == 1 and later.json()["chapters"]["unchanged"] == 1
    # A different chapter 1 needs an explicit replacement.
    changed = long_chapter(original.replace("question 5 ", "question five "))
    _, conflict = commit_txt(client, [changed])
    assert conflict.status_code == 409 and "conflicts" in conflict.text
    _, replaced = commit_txt(client, [changed], items=[{"index": 0, "chapter_number": 1, "replace": True}])
    assert replaced.status_code == 200, replaced.text
    with SessionLocal() as db:
        # The passage whose text did not change keeps its human correction.
        kept_segment = db.get(Segment, kept)
        assert kept_segment is not None and kept_segment.human and kept_segment.translation == "Traduction humaine"
        chapters = db.scalars(select(Chapter).where(Chapter.project_id == project_id).order_by(Chapter.position)).all()
        assert [c.chapter_number for c in chapters] == [1, 2, 3]
        assert [c.context_stale for c in chapters] == [False, True, True]
        positions = [
            s.position for s in db.scalars(select(Segment).where(Segment.project_id == project_id).order_by(Segment.position))
        ]
        assert positions == list(range(len(positions)))
    # Changing the corrected passage itself is refused unless its loss is confirmed.
    rewritten = ("Chapter 1.txt", long_chapter(original)[1].replace(b"star 3 ", b"star three "))
    items = [{"index": 0, "chapter_number": 1, "replace": True}]
    _, protected = commit_txt(client, [rewritten], items=items)
    assert protected.status_code == 409 and "protected_segments" in protected.text
    _, confirmed = commit_txt(client, [rewritten], items=items, discard_human=True)
    assert confirmed.status_code == 200, confirmed.text
    with SessionLocal() as db:
        assert db.get(Segment, kept) is None


def test_a_middle_chapter_is_inserted_in_order_and_shifts_later_positions(client):
    _, first = commit_txt(client, [chapter(1), chapter(3)])
    project_id = first.json()["projects"][0]["id"]
    _, second = commit_txt(client, [chapter(2)])
    assert second.status_code == 200, second.text
    with SessionLocal() as db:
        chapters = db.scalars(select(Chapter).where(Chapter.project_id == project_id).order_by(Chapter.position)).all()
        assert [c.chapter_number for c in chapters] == [1, 2, 3]
        assert [c.context_stale for c in chapters] == [False, False, True]
        by_chapter = {}
        for segment in db.scalars(select(Segment).where(Segment.project_id == project_id).order_by(Segment.position)):
            by_chapter.setdefault(segment.chapter_id, []).append(segment.position)
        assert [by_chapter[c.id][0] for c in chapters] == sorted(by_chapter[c.id][0] for c in chapters)


def test_another_owner_cannot_see_or_import_into_a_series(client):
    commit_txt(client, [chapter(1)])
    with SessionLocal() as db:
        series_id = db.scalar(select(Series.id))
        db.add(User(username="intruder", password_hash=password_hash(PASSWORD)))
        db.commit()
    with TestClient(app) as other:
        other.post("/api/auth/login", json={"username": "intruder", "password": PASSWORD})
        assert other.get("/api/series").json() == []
        assert other.get(f"/api/series/{series_id}").status_code == 404
        session = upload(other, "txt", [chapter(2)])
        body = {"destination": {"mode": "series", "series_id": series_id}, "items": [{"index": 0, "chapter_number": 2}]}
        assert other.post(f"/api/imports/{session['id']}/commit", json=body).status_code == 404
        # The same name for another owner is another series.
        body = {"destination": {"mode": "series", "series_name": "Web Novel"}, "items": [{"index": 0, "chapter_number": 2}]}
        assert other.post(f"/api/imports/{session['id']}/commit", json=body).status_code == 200
    with SessionLocal() as db:
        assert db.scalar(select(Series).where(Series.id == series_id)).name == "Web Novel"
        assert len(db.scalars(select(Series)).all()) == 2


async def test_files_uploaded_at_once_are_all_kept():
    import asyncio

    import httpx

    with SessionLocal() as db:
        db.add(User(username="fast", password_hash=password_hash(PASSWORD), admin=True))
        db.commit()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        await client.post("/api/auth/login", json={"username": "fast", "password": PASSWORD})
        session = (await client.post("/api/imports", json={"format": "txt"})).json()
        responses = await asyncio.gather(*(
            client.post(f"/api/imports/{session['id']}/files", files={"file": chapter(n)}) for n in range(1, 9)
        ))
        assert all(r.status_code == 201 for r in responses)
        files = (await client.get(f"/api/imports/{session['id']}")).json()["files"]
    assert sorted(item["index"] for item in files) == list(range(8))


def test_a_text_volume_cannot_leave_its_series(client):
    _, response = commit_txt(client, [chapter(1)], target={"mode": "new_volume", "volume_number": 1})
    project_id = response.json()["projects"][0]["id"]
    project = client.get(f"/api/projects/{project_id}").json()
    settings = {k: project[k] for k in ("title", "author", "volume_number", "source_language", "target_language",
                                        "provider_id", "quality", "context_backend", "instructions")}
    assert client.put(f"/api/projects/{project_id}", json={**settings, "series_name": ""}).status_code == 409
    batch = {"project_ids": [project_id], "mode": "clear"}
    assert client.put("/api/projects/batch/series", json=batch).status_code == 409
