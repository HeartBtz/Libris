"""Exports of TXT, JSON and EPUB volumes as text, previews of text chapters and version 3 archives."""

import hashlib
import io
import json
import zipfile

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.db import SessionLocal
from app.engines.translation.versions import save_version
from app.main import app
from app.models import Chapter, Segment, User
from app.security import password_hash

PASSWORD = "test-password-123456789"

FIRST = "Chapter 1\n\n“Hello,” said Mira.\nShe waited.\n\n　　Indented line here.\n\n* * *\n\nLast paragraph of chapter one.\n"
SECOND = "Mira crossed the <bridge> & sang.\n\nNobody followed.\n"
TENTH = "Chapter 10\n\nThe tenth morning was quiet.\n"


def login(username: str) -> TestClient:
    client = TestClient(app)
    assert client.post("/api/auth/login", json={"username": username, "password": PASSWORD}).status_code == 200
    return client


@pytest.fixture
def client():
    with SessionLocal() as db:
        db.add(User(username="reader", password_hash=password_hash(PASSWORD), admin=True))
        db.add(User(username="stranger", password_hash=password_hash(PASSWORD)))
        db.commit()
    with login("reader") as client:
        yield client


def import_txt(client, files: list[tuple[str, str]], target: dict | None = None, series: str = "Glass Road") -> str:
    session = client.post("/api/imports", json={"format": "txt"}).json()
    for name, text in files:
        response = client.post(f"/api/imports/{session['id']}/files", files={"file": (name, text.encode())})
        assert response.status_code == 201, response.text
    proposal = client.get(f"/api/imports/{session['id']}").json()["proposal"]
    items = [{"index": item["index"], "chapter_number": item["chapter_number"]} for item in proposal["items"]]
    body = {"destination": {"mode": "series", "series_name": series}, "items": items}
    if target:
        body["target"] = target
    response = client.post(f"/api/imports/{session['id']}/commit", json=body)
    assert response.status_code == 200, response.text
    return response.json()["projects"][0]["id"]


def translate(pid: str, skip: int | None = None, how=lambda text: "FR " + text) -> None:
    """Every passage translated (`FR <source>` by default); passage `skip` left untranslated."""
    with SessionLocal() as db:
        segments = db.scalars(select(Segment).where(Segment.project_id == pid).order_by(Segment.position)).all()
        for index, segment in enumerate(segments):
            if index != skip:
                units = [{"id": u["id"], "text": how(u["text"])} for u in segment.units]
                save_version(db, segment.id, units, "translation", 0, stage="translated")
        db.commit()


def chapters(pid: str) -> list[Chapter]:
    with SessionLocal() as db:
        return list(db.scalars(select(Chapter).where(Chapter.project_id == pid).order_by(Chapter.position)))


def unzip(content: bytes) -> dict[str, bytes]:
    with zipfile.ZipFile(io.BytesIO(content)) as archive:
        return {name: archive.read(name) for name in archive.namelist()}


@pytest.fixture
def novel(client):
    pid = import_txt(
        client,
        [("Chapter 10.txt", TENTH), ("Chapter 2 - The Bridge.txt", SECOND), ("Chapter 1.txt", FIRST)],
        target={"mode": "new_volume", "volume_number": 1, "volume_title": "Glass Road One"},
    )
    return pid


def test_a_zip_holds_one_clean_utf8_file_per_chapter_in_natural_order_with_a_manifest(client, novel):
    translate(novel)
    response = client.get(f"/api/projects/{novel}/export/txt-zip")
    assert response.status_code == 200, response.text
    assert response.headers["content-type"] == "application/zip"
    files = unzip(response.content)
    names = [name for name in files if name.startswith("chapters/")]
    assert names == [
        "chapters/001 - FR Chapter 1.txt",
        "chapters/002 - FR Chapter 2 - The Bridge.txt",
        "chapters/010 - FR Chapter 10.txt",
    ]
    first = files[names[0]].decode("utf-8")
    # Paragraphs, lines, blank lines, indentation and the scene break as in the source file.
    assert first == (
        "FR Chapter 1\n\nFR “Hello,” said Mira.\nFR She waited.\n\n　　FR Indented line here.\n\n"
        "* * *\n\nFR Last paragraph of chapter one.\n"
    )
    # A title taken from the file name is not written in the chapter file.
    assert files[names[1]].decode() == "FR Mira crossed the <bridge> & sang.\n\nFR Nobody followed.\n"
    assert not any("⟦" in content.decode() or "⟧" in content.decode() for content in files.values())
    manifest = json.loads(files["manifest.json"])
    assert manifest["complete"] and manifest["source_format"] == "txt" and manifest["series"] == "Glass Road"
    assert [c["file"] for c in manifest["chapters"]] == names
    assert [c["number"] for c in manifest["chapters"]] == [1, 2, 10]
    for chapter in manifest["chapters"]:
        assert chapter["sha256"] == hashlib.sha256(files[chapter["file"]]).hexdigest()
    assert "Glass Road One.txt" not in files
    both = unzip(client.get(f"/api/projects/{novel}/export/txt-zip?consolidated=true").content)
    assert both["Glass Road One.txt"].decode() == client.get(f"/api/projects/{novel}/export/txt").text


def test_the_consolidated_text_and_markdown_name_every_chapter_once(client, novel):
    translate(novel)
    text = client.get(f"/api/projects/{novel}/export/txt")
    assert text.status_code == 200 and text.headers["content-type"] == "text/plain; charset=utf-8"
    body = text.text
    assert body.startswith("Glass Road One\n\n\nFR Chapter 1\n\nFR “Hello,” said Mira.\n")
    # The heading read from the file name comes before its chapter; a heading in the text is not repeated.
    assert body.count("FR Chapter 1\n") == 1 and "\n\n\nFR Chapter 2 - The Bridge\n\nFR Mira crossed" in body
    assert body.index("FR Chapter 1") < body.index("FR Chapter 2") < body.index("FR Chapter 10")
    markdown = client.get(f"/api/projects/{novel}/export/md")
    assert markdown.headers["content-type"] == "text/markdown; charset=utf-8"
    assert markdown.text.startswith("# Glass Road One\n\n## FR Chapter 1\n\nFR “Hello,” said Mira.\n")
    assert "## FR Chapter 2 - The Bridge\n\nFR Mira crossed" in markdown.text
    assert markdown.text.count("FR Chapter 1\n") == 1 and "## FR Chapter 10\n\nFR The tenth" in markdown.text


def test_unfinished_translations_are_refused_or_exported_with_originals_flagged(client, novel):
    translate(novel, skip=1)
    for fmt in ("txt", "txt-zip", "md"):
        refused = client.get(f"/api/projects/{novel}/export/{fmt}")
        assert refused.status_code == 409 and "pas encore complète" in refused.json()["detail"]
    partial = client.get(f"/api/projects/{novel}/export/txt-zip?allow_source=true")
    assert partial.status_code == 200
    assert "partial-with-originals" in partial.headers["content-disposition"]
    files = unzip(partial.content)
    manifest = json.loads(files["manifest.json"])
    assert not manifest["complete"]
    assert [c["complete"] for c in manifest["chapters"]].count(False) == 1
    incomplete = next(c for c in manifest["chapters"] if not c["complete"])
    assert incomplete["missing_segments"] == 1
    assert client.get(f"/api/projects/{novel}/export/txt?allow_source=true").status_code == 200


def test_an_epub_export_of_a_text_volume_is_refused_in_the_reader_language(client, novel):
    translate(novel)
    refused = client.get(f"/api/projects/{novel}/export/epub")
    assert refused.status_code == 409 and "pas depuis un EPUB" in refused.json()["detail"]
    english = client.get(f"/api/projects/{novel}/export/epub", headers={"Accept-Language": "en"})
    assert "was imported from text files" in english.json()["detail"]
    assert client.get(f"/api/projects/{novel}/export/epub?allow_source=true").status_code == 409


def test_the_text_export_of_an_epub_keeps_its_chapters(seeded):
    pid = seeded[0]
    # Words replaced inside the units: text added next to a table-of-contents link would be refused.
    translate(pid, how=lambda text: text.replace("Chapter", "Chapitre").replace("Alice", "Alicia"))
    client = login("tester")
    text = client.get(f"/api/projects/{pid}/export/txt").text
    assert "⟦" not in text and "Alice " not in text
    # The translated heading names each chapter once; the table of contents is not exported.
    assert text.count("Chapitre One") == 1 and text.count("Chapitre Two") == 1
    one, two = text.index("Chapitre One"), text.index("Chapitre Two")
    assert one < text.index("Silver Tower", one) < two
    assert "\n\n\nChapitre Two\n\nAlicia discovered" in text
    files = unzip(client.get(f"/api/projects/{pid}/export/txt-zip").content)
    assert [name for name in files if name.startswith("chapters/")] == [
        "chapters/001 - Chapitre One.txt",
        "chapters/002 - Chapitre Two.txt",
    ]
    assert files["chapters/002 - Chapitre Two.txt"].decode().startswith("Chapitre Two\n\nAlicia discovered")
    markdown = client.get(f"/api/projects/{pid}/export/md").text
    assert "## Chapitre One\n" in markdown and "## Chapitre Two\n\nAlicia discovered" in markdown


def test_a_text_chapter_preview_is_escaped_html_built_without_any_epub(client, novel):
    translate(novel)
    first, second, _ = chapters(novel)
    preview = client.get(f"/api/projects/{novel}/preview/{first.id}").json()
    assert preview["simplified"] and "default-src 'none'" in preview["html"]
    assert "<h1>FR Chapter 1</h1>" in preview["html"]
    assert '<p class="text">FR “Hello,” said Mira.<br>FR She waited.</p>' in preview["html"]
    assert '<p class="break">* * *</p>' in preview["html"]
    escaped = client.get(f"/api/projects/{novel}/preview/{second.id}").json()["html"]
    assert "<h1>FR Chapter 2 - The Bridge</h1>" in escaped
    assert "FR Mira crossed the &lt;bridge&gt; &amp; sang." in escaped and "<bridge>" not in escaped
    source = client.get(f"/api/projects/{novel}/preview/{second.id}?translated=false").json()["html"]
    assert "<h1>Chapter 2 - The Bridge</h1>" in source and "FR " not in source


def test_a_series_exports_as_one_zip_with_a_folder_per_volume(client):
    one = import_txt(client, [("Chapter 1.txt", FIRST)], target={"mode": "new_volume", "volume_number": 1})
    two = import_txt(client, [("Chapter 1.txt", TENTH)], target={"mode": "new_volume", "volume_number": 2})
    translate(one)
    translate(two, skip=0)
    refused = client.post("/api/exports/text", json={"project_ids": [two, one]})
    assert refused.status_code == 409 and "Glass Road — 2" in refused.json()["detail"]
    response = client.post("/api/exports/text", json={"project_ids": [two, one], "allow_source": True,
                                                      "consolidated": True})
    assert response.status_code == 200
    files = unzip(response.content)
    assert sorted(files) == [
        "01 - Glass Road — 1/Glass Road — 1.txt",
        "01 - Glass Road — 1/chapters/001 - FR Chapter 1.txt",
        "01 - Glass Road — 1/manifest.json",
        "02 - Glass Road — 2/Glass Road — 2.txt",
        "02 - Glass Road — 2/chapters/001 - Chapter 1.txt",
        "02 - Glass Road — 2/manifest.json",
    ]
    assert not json.loads(files["02 - Glass Road — 2/manifest.json"])["complete"]
    assert client.post("/api/exports/text", json={"project_ids": [one, one]}).status_code == 422


def test_other_owners_reach_neither_exports_nor_previews(client, novel):
    translate(novel)
    chapter = chapters(novel)[0]
    with login("stranger") as stranger:
        for url in (
            f"/api/projects/{novel}/export/txt",
            f"/api/projects/{novel}/export/txt-zip",
            f"/api/projects/{novel}/export/md",
            f"/api/projects/{novel}/preview/{chapter.id}",
        ):
            assert stranger.get(url).status_code == 404, url
        assert stranger.post("/api/exports/text", json={"project_ids": [novel]}).status_code == 404
