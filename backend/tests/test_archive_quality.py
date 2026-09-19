import io
import json
import zipfile

from epubs import epub_files, xhtml
from fastapi.testclient import TestClient
from sqlalchemy import select
from test_project_archive import login, zipped
from test_segmentation import upper

from app.api.projects import import_book
from app.db import SessionLocal
from app.engines.translation.memory import memory_key
from app.main import app
from app.models import Chapter, Project, Segment, User
from app.security import password_hash

BOOK = epub_files(
    {
        "notes.xhtml": xhtml("<p>Cover note.</p>"),
        "c1.xhtml": xhtml(
            '<h1>One</h1><div class="letter">Dear <b>Alice</b>, in haste<div class="sig">Bob</div></div>'
            "<p><ruby>漢字<rt>kanji</rt></ruby> text.</p>"
        ),
    },
    spine=[("notes.xhtml", ' linear="no"'), ("c1.xhtml", "")],
    metadata="<dc:description>A blurb.</dc:description>",
    uid="archive-quality",
)


def book(segmentation: int = 2) -> str:
    with SessionLocal() as db:
        user = db.scalar(select(User).where(User.username == "tester"))
        if not user:
            user = User(username="tester", password_hash=password_hash("test-password-123456789"), admin=True)
            db.add(user)
            db.flush()
        project = import_book(db, user.id, BOOK, segmentation)
        for segment in db.scalars(select(Segment).where(Segment.project_id == project.id)):
            segment.translated_units = [{"id": u["id"], "text": upper(u["text"])} for u in segment.units]
            segment.translation = "\n\n".join(u["text"] for u in segment.translated_units)
            segment.status, segment.stage = "ok", "done"
        project.config = {"translation_memory": False}
        db.commit()
        return project.id


def state(pid: str) -> tuple[list, list, dict]:
    with SessionLocal() as db:
        chapters = [
            (c.resource, c.kind)
            for c in db.scalars(select(Chapter).where(Chapter.project_id == pid).order_by(Chapter.position))
        ]
        segments = [
            (s.source, s.translation, s.source_key == memory_key(s.units))
            for s in db.scalars(select(Segment).where(Segment.project_id == pid).order_by(Segment.position))
        ]
        return chapters, segments, db.get(Project, pid).config


def round_trip(pid: str, edit=None) -> str:
    with TestClient(app) as client:
        login(client)
        exported = client.get(f"/api/projects/{pid}/export/project")
        assert exported.status_code == 200, exported.text
        content = exported.content
        if edit:
            # An older archive: version 2 layout (`original.epub` + `project.json`), edited to match.
            with zipfile.ZipFile(io.BytesIO(exported.content)) as archive:
                payload = json.loads(archive.read("project.json"))
                original = archive.read("sources/1.epub")
            payload["schema_version"] = 2
            edit(payload)
            content = zipped(original, payload)
        assert client.delete(f"/api/projects/{pid}").status_code == 200
        imported = client.post("/api/projects/import", files={"file": ("p.zip", content)})
        assert imported.status_code == 201, imported.text
        new_id = imported.json()["id"]
        assert client.get(f"/api/projects/{new_id}").json()["translation_memory"] is False
        assert client.get(f"/api/projects/{new_id}/export/epub").status_code == 200
        return new_id


def test_chapter_kinds_memory_setting_and_source_keys_survive_an_archive():
    pid = book()
    before = state(pid)
    assert ("OEBPS/content.opf", "metadata") in before[0] and ("OEBPS/nav.xhtml", "navigation") in before[0]
    after = state(round_trip(pid))
    assert after == before
    assert all(recomputed for *_, recomputed in after[1])


def test_archives_made_before_segmentation_2_are_restored_as_they_were_cut():
    pid = book(segmentation=1)
    before = state(pid)
    # Up to v0.4: no metadata section, notes kept in spine order, ruby readings inside the text.
    assert [resource for resource, _ in before[0]][:2] == ["OEBPS/notes.xhtml", "OEBPS/c1.xhtml"]
    assert any("kanji" in source for source, *_ in before[1])

    def as_v04(payload):
        payload["project"]["book_info"].pop("segmentation")
        payload["project"]["book_info"].pop("untranslated")
        for chapter in payload["chapters"]:
            chapter.pop("kind")

    after = state(round_trip(pid, as_v04))
    assert after[1] == before[1]
    assert after[0] == before[0]
