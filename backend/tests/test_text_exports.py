"""Exports of TXT, JSON and EPUB volumes as text, previews of text chapters and version 3 archives."""

import hashlib
import io
import json
import zipfile

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from test_project_archive import snapshot

from app.config import settings
from app.db import SessionLocal
from app.engines.ingestion.base import ImportedAsset, ImportedVolume
from app.engines.ingestion.store import Files, create_volume, get_or_create_series
from app.engines.ingestion.text import text_chapter
from app.engines.translation.versions import save_version
from app.main import app
from app.models import Chapter, Glossary, Job, Project, Segment, Series, SourceAsset, User
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


def test_other_owners_reach_neither_exports_nor_previews_nor_archives(client, novel):
    translate(novel)
    chapter = chapters(novel)[0]
    with login("stranger") as stranger:
        for url in (
            f"/api/projects/{novel}/export/txt",
            f"/api/projects/{novel}/export/txt-zip",
            f"/api/projects/{novel}/export/md",
            f"/api/projects/{novel}/export/project",
            f"/api/projects/{novel}/preview/{chapter.id}",
        ):
            assert stranger.get(url).status_code == 404, url
        assert stranger.post("/api/exports/text", json={"project_ids": [novel]}).status_code == 404
        # A restored archive belongs to the person who restores it, in a series of their own.
        archive = client.get(f"/api/projects/{novel}/export/project").content
        restored = stranger.post("/api/projects/import", files={"file": ("p.zip", archive)})
        assert restored.status_code == 201, restored.text
    with SessionLocal() as db:
        stranger_id = db.scalar(select(User.id).where(User.username == "stranger"))
        project = db.get(Project, restored.json()["id"])
        assert project.owner_id == stranger_id and project.provider_id is None
        assert db.get(Series, project.series_id).owner_id == stranger_id
        assert db.scalar(select(func.count()).select_from(Series).where(Series.name == "Glass Road")) == 2


def enrich(pid: str) -> None:
    """Work the archive must carry: versions, validations, glossary override, a job, stale context."""
    with SessionLocal() as db:
        segments = db.scalars(select(Segment).where(Segment.project_id == pid).order_by(Segment.position)).all()
        user = db.scalar(select(User.id).where(User.username == "reader"))
        units = [{"id": u["id"], "text": "Relu " + u["text"]} for u in segments[0].units]
        save_version(db, segments[0].id, units, "human", segments[0].revision, author_id=user, validated=True,
                     stage="done")
        db.add(Glossary(project_id=pid, source="Mira", translation="Myra", locked=True, series_override=True))
        db.add(Job(project_id=pid, operation="translate", status="completed", checkpoint={"step": "done"}))
        chapter = db.scalars(select(Chapter).where(Chapter.project_id == pid).order_by(Chapter.position)).all()[-1]
        chapter.context_stale = True
        chapter.external_id = "chapter-ten"
        db.get(Project, pid).external_id = "glass-road-1"
        db.commit()


def round_trip(client, pid: str) -> tuple[str, dict[str, bytes]]:
    exported = client.get(f"/api/projects/{pid}/export/project")
    assert exported.status_code == 200, exported.text
    assert client.delete(f"/api/projects/{pid}").status_code == 200
    restored = client.post("/api/projects/import", files={"file": ("p.zip", exported.content)})
    assert restored.status_code == 201, restored.text
    return restored.json()["id"], unzip(exported.content)


def test_a_txt_volume_archive_restores_the_same_work(client, novel):
    translate(novel)
    enrich(novel)
    before = snapshot(novel)
    with SessionLocal() as db:
        sources = sorted(a.sha256 for a in db.scalars(select(SourceAsset).where(SourceAsset.project_id == novel)))
    new_id, files = round_trip(client, novel)
    assert sorted(files) == ["project.json", "sources/1.txt", "sources/2.txt", "sources/3.txt"]
    payload = json.loads(files["project.json"])
    assert payload["schema_version"] == 3 and payload["series"] == {"name": "Glass Road", "kind": "webnovel", "authors": []}
    assert all(chapter["text_source"]["file"].startswith("sources/") for chapter in payload["chapters"])
    after = snapshot(new_id)
    for part in before:
        assert after[part] == before[part], part
    with SessionLocal() as db:
        project = db.get(Project, new_id)
        assert (project.source_format, project.project_kind, project.series_name) == ("txt", "volume", "Glass Road")
        assets = db.scalars(select(SourceAsset).where(SourceAsset.project_id == new_id)).all()
        assert sorted(a.sha256 for a in assets) == sources
        assert all((settings().data_dir / a.storage_path).is_file() for a in assets)
        rows = db.scalars(select(Chapter).where(Chapter.project_id == new_id)).all()
        assert {c.source_asset_id for c in rows} == {a.id for a in assets}
        assert db.scalar(select(Glossary.series_override).where(Glossary.project_id == new_id))
    # The restored volume exports the same text.
    exported = unzip(client.get(f"/api/projects/{new_id}/export/txt-zip").content)
    assert exported["chapters/001 - Relu Chapter 1.txt"].startswith(b"Relu Chapter 1\n")


def test_a_serial_archive_restores_into_the_series_found_by_name(client):
    pid = import_txt(client, [("Chapter 1.txt", FIRST), ("Chapter 2.txt", SECOND)])
    translate(pid)
    before = snapshot(pid)
    new_id, _ = round_trip(client, pid)
    assert snapshot(new_id) == before
    with SessionLocal() as db:
        assert db.get(Project, new_id).project_kind == "serial"
        assert db.scalar(select(func.count()).select_from(Series)) == 1
    # A second copy cannot become another serial container of the same series.
    archive = client.get(f"/api/projects/{new_id}/export/project").content
    refused = client.post("/api/projects/import", files={"file": ("p.zip", archive)})
    assert refused.status_code == 409 and "feuilleton" in refused.json()["detail"]


WRAPPED = (
    "The river ran under the old\nstone bridge where the lamps\nburned all night long.\n\n"
    "Mira counted the lamps and\nfound one more than the day\nbefore, which troubled her.\n"
)


def json_volume(owner: str) -> str:
    """A JSON volume as the automation API creates it: text chapters from a payload stored as a source."""
    payload = json.dumps({"chapters": [{"id": "c1", "text": WRAPPED}, {"id": "c2", "text": TENTH}]}).encode()
    asset = ImportedAsset("payload.json", "json", "application/json", payload)
    items = []
    for index, (key, text, title) in enumerate((("c1", WRAPPED, "River Lamps"), ("c2", TENTH, "Tenth"))):
        chapter, _ = text_chapter(text, title=title, resource=f"json/{key}")
        chapter.number, chapter.external_id, chapter.asset = index + 1, key, asset
        items.append(chapter)
    with SessionLocal() as db:
        owner_id = db.scalar(select(User.id).where(User.username == owner))
        series = get_or_create_series(db, owner_id, "Lamp Saga", "webnovel")
        volume = ImportedVolume("Lamp Saga 1", "Someone", "en", items, asset=asset)
        project = create_volume(db, owner_id, volume, Files(), series=series, source_format="json", number=1,
                                external_id="lamp-1")
        db.commit()
        assert items[0].meta["layout"]["mode"] == "wrapped"
        return project.id


def test_a_json_volume_archive_rebuilds_its_chapters_from_their_text(client):
    pid = json_volume("reader")
    translate(pid)
    enrich(pid)
    before = snapshot(pid)
    new_id, files = round_trip(client, pid)
    assert sorted(files) == ["project.json", "sources/1.json", "texts/1.txt", "texts/2.txt"]
    assert files["texts/1.txt"].decode().startswith("The river ran under the old stone bridge where the lamps burned")
    assert snapshot(new_id) == before
    with SessionLocal() as db:
        project = db.get(Project, new_id)
        assert (project.source_format, project.external_id, project.volume_number) == ("json", "glass-road-1", 1)
        asset = db.scalar(select(SourceAsset).where(SourceAsset.project_id == new_id))
        assert asset.format == "json" and (settings().data_dir / asset.storage_path).read_bytes() == files["sources/1.json"]


def test_an_epub_archive_joins_the_existing_series_of_that_name(seeded):
    pid, user_id, _ = seeded
    with SessionLocal() as db:
        series = get_or_create_series(db, user_id, "  silver   TOWERS ", "books")
        db.get(Project, pid).series_id = series.id
        db.commit()
        series_id = series.id
    client = login("tester")
    new_id, files = round_trip(client, pid)
    assert sorted(files) == ["project.json", "sources/1.epub"]
    with SessionLocal() as db:
        project = db.get(Project, new_id)
        assert project.series_id == series_id and project.source_format == "epub"
        assert db.scalar(select(SourceAsset.format).where(SourceAsset.project_id == new_id)) == "epub"


def archive_of(client, pid: str) -> dict[str, bytes]:
    return unzip(client.get(f"/api/projects/{pid}/export/project").content)


def zipped(files: dict[str, bytes], compression=zipfile.ZIP_DEFLATED) -> bytes:
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", compression) as archive:
        for name, content in files.items():
            archive.writestr(name, content)
    return output.getvalue()


def refused(client, data: bytes, expected: str) -> None:
    with SessionLocal() as db:
        projects = db.scalar(select(func.count()).select_from(Project))
    stored = sorted(path for path in settings().data_dir.rglob("*") if path.is_file() and "sources" in path.parts)
    response = client.post("/api/projects/import", files={"file": ("p.zip", data)})
    assert response.status_code in {409, 413, 422}, response.text
    assert expected in response.json()["detail"], response.json()["detail"]
    with SessionLocal() as db:
        assert db.scalar(select(func.count()).select_from(Project)) == projects
    after = sorted(path for path in settings().data_dir.rglob("*") if path.is_file() and "sources" in path.parts)
    assert after == stored


def test_malicious_or_altered_archives_are_refused_without_leaving_anything(client, novel, monkeypatch):
    translate(novel)
    files = archive_of(client, novel)
    payload = json.loads(files["project.json"])
    for name in ("../escape.txt", "/etc/passwd", "sources/1.exe", "sources/0.txt", "texts/../1.txt", "notes.txt"):
        refused(client, zipped({**files, name: b"x"}), "fichier inattendu")
    refused(client, zipped({**files, "original.epub": b"x"}), "Archive projet invalide")
    tampered = dict(files, **{"sources/2.txt": files["sources/2.txt"] + b"extra"})
    refused(client, zipped(tampered), "empreinte")
    missing = {name: content for name, content in files.items() if name != "sources/3.txt"}
    refused(client, zipped(missing), "manquant")
    # A source changed together with its checksum: the passages no longer match it.
    tenth = next(chapter["asset"] for chapter in payload["chapters"] if chapter["chapter_number"] == 10)
    changed = files[tenth].replace(b"quiet", b"loud")
    for source in payload["sources"]:
        if source["file"] == tenth:
            source["sha256"] = hashlib.sha256(changed).hexdigest()
    forged = dict(files, **{tenth: changed, "project.json": json.dumps(payload).encode()})
    refused(client, zipped(forged), "ne correspond pas à ses fichiers sources")
    monkeypatch.setattr(settings(), "max_entries", 3)
    refused(client, zipped(files), "Trop de fichiers")
    monkeypatch.setattr(settings(), "max_entries", 5000)
    bomb = dict(files, **{"texts/9.txt": b"\0" * (9 * 1024**2)})
    refused(client, zipped(bomb), "Ratio de compression")
    monkeypatch.setattr(settings(), "max_unpacked_mb", 0)
    refused(client, zipped(files), "trop volumineuse")


def test_a_json_archive_whose_rebuilt_text_was_edited_is_refused(client):
    pid = json_volume("reader")
    files = archive_of(client, pid)
    client.delete(f"/api/projects/{pid}")
    edited = dict(files, **{"texts/2.txt": files["texts/2.txt"].replace(b"quiet", b"loud")})
    refused(client, zipped(edited), "ne correspond pas à ses fichiers sources")
    assert client.post("/api/projects/import", files={"file": ("p.zip", zipped(files))}).status_code == 201


def test_an_archive_needs_its_source_files_on_the_server(client, novel):
    translate(novel)
    with SessionLocal() as db:
        asset = db.scalar(select(SourceAsset).where(SourceAsset.project_id == novel))
        (settings().data_dir / asset.storage_path).unlink()
    response = client.get(f"/api/projects/{novel}/export/project")
    assert response.status_code == 409 and "DATA_DIR/sources" in response.json()["detail"]
    assert client.get(f"/api/projects/{novel}/export/txt-zip").status_code == 200
