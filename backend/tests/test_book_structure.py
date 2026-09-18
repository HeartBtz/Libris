import io
import zipfile

from epubs import epub_files, xhtml
from fastapi.testclient import TestClient
from sqlalchemy import select
from test_segmentation import roundtrip

from app.db import SessionLocal
from app.main import app
from app.models import Chapter, Segment


def login(client):
    client.post("/api/auth/login", json={"username": "tester", "password": "test-password-123456789"})


def test_navigation_documents_are_not_counted_as_chapters(seeded):
    pid = seeded[0]
    with TestClient(app) as client:
        login(client)
        project = client.get(f"/api/projects/{pid}").json()
        chapters = client.get(f"/api/projects/{pid}/chapters").json()
    # nav.xhtml and toc.ncx used to make this two-chapter book count four.
    assert project["stats"]["chapters"] == 2
    assert {c["resource"].split("/")[-1]: c["kind"] for c in chapters} == {
        "chapter1.xhtml": "narrative",
        "chapter2.xhtml": "narrative",
        "nav.xhtml": "navigation",
        "toc.ncx": "navigation",
    }
    with SessionLocal() as db:
        navigation = db.scalar(select(Chapter).where(Chapter.project_id == pid, Chapter.kind == "navigation"))
        # Their passages are still translated and shown.
        assert db.scalar(select(Segment.id).where(Segment.chapter_id == navigation.id))


def test_non_linear_documents_follow_the_story():
    data = epub_files(
        {"notes.xhtml": xhtml("<p>Cover note text</p>"), "c1.xhtml": xhtml("<h1>One</h1><p>Story.</p>")},
        spine=[("notes.xhtml", ' linear="no"'), ("c1.xhtml", "")],
    )
    parsed, _ = roundtrip(data)
    assert [(c["resource"], c["kind"]) for c in parsed["chapters"]] == [
        ("OEBPS/c1.xhtml", "narrative"),
        ("OEBPS/notes.xhtml", "auxiliary"),
        ("OEBPS/nav.xhtml", "navigation"),
    ]


def test_description_and_subject_are_translated():
    data = epub_files(
        {"c1.xhtml": xhtml("<p>Story.</p>")},
        metadata="<dc:description>A long English blurb about the book.</dc:description><dc:subject>Fantasy</dc:subject>",
    )
    parsed, out = roundtrip(data)
    metadata = parsed["chapters"][-1]
    assert metadata["kind"] == "metadata"
    assert [u["text"] for g in metadata["groups"] for u in g] == [
        "A long English blurb about the book.",
        "Fantasy",
    ]
    opf = zipfile.ZipFile(io.BytesIO(out)).read("OEBPS/content.opf").decode()
    assert "<dc:description>A LONG ENGLISH BLURB ABOUT THE BOOK.</dc:description>" in opf
    assert "<dc:subject>FANTASY</dc:subject>" in opf
