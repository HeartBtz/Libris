"""Markdown, HTML and DOCX chapters: read into the TXT chapter model, imported, exported, archived."""

import io
import zipfile

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.config import settings
from app.db import SessionLocal
from app.engines.ingestion import DocxAdapter, HtmlAdapter, MarkdownAdapter, TextRejected
from app.engines.ingestion.markdown import read_markdown
from app.main import app
from app.models import Chapter, Project, Segment, SourceAsset, User
from app.security import password_hash

PASSWORD = "test-password-123456789"

MARKDOWN = """---
title: "The Glass Road"
author: Jane Doe
---

# Chapter 1

Alice walked *slowly*
into the tower.

- first clue
- second clue

> A quoted line
> that continues.

```
code stays = "as is"
```

***

The End.
"""

HTML = """<!DOCTYPE html><html lang="en"><head><title>Glass Road</title><meta name="author" content="Jane Doe">
<style>p { color: red }</style><script>alert("no")</script></head><body>
<h2>Chapter 2</h2><p>Bob <em>laughed</em>.<br>Then he left.</p>
<div>Loose text<p>Inner paragraph.</p></div><ul><li>One</li><li>Two</li></ul><hr><pre>kept   as is</pre>
<!-- a comment --></body></html>"""


def docx_bytes(
    paragraphs: list[tuple[str, str | None]], title: str = "Word Title", extra: dict | None = None
) -> bytes:
    """A minimal DOCX: (text, style id) paragraphs, one text box paragraph nested in the first."""
    body = []
    for text, style in paragraphs:
        properties = f'<w:pPr><w:pStyle w:val="{style}"/></w:pPr>' if style else ""
        if style == "List":
            properties = '<w:pPr><w:numPr><w:ilvl w:val="0"/><w:numId w:val="1"/></w:numPr></w:pPr>'
        body.append(f'<w:p>{properties}<w:r><w:t xml:space="preserve">{text}</w:t></w:r></w:p>')
    document = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main" '
        'xmlns:mc="http://schemas.openxmlformats.org/markup-compatibility/2006"><w:body>'
        + "".join(body)
        + "<w:p><w:r><w:t>Split</w:t></w:r><w:r><w:tab/><w:t>text</w:t></w:r><w:r><w:delText>gone</w:delText></w:r>"
        "</w:p><w:p><mc:AlternateContent><mc:Fallback><w:p><w:r><w:t>Duplicate</w:t></w:r></w:p></mc:Fallback>"
        "</mc:AlternateContent></w:p></w:body></w:document>"
    )
    styles = (
        '<w:styles xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
        '<w:style w:type="paragraph" w:styleId="Titre1"><w:name w:val="heading 1"/></w:style></w:styles>'
    )
    core = (
        '<cp:coreProperties xmlns:cp="http://schemas.openxmlformats.org/package/2006/metadata/core-properties" '
        f'xmlns:dc="http://purl.org/dc/elements/1.1/"><dc:title>{title}</dc:title><dc:creator>Jane Doe</dc:creator>'
        "<dc:language>en-GB</dc:language></cp:coreProperties>"
    )
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("[Content_Types].xml", "<Types/>")
        archive.writestr("word/document.xml", document)
        archive.writestr("word/styles.xml", styles)
        archive.writestr("docProps/core.xml", core)
        for name, value in (extra or {}).items():
            archive.writestr(name, value)
    return output.getvalue()


def units_of(chapter) -> list[dict]:
    return [unit for group in chapter.groups for unit in group]


def test_markdown_blocks_become_units_and_layout():
    document = read_markdown(MARKDOWN)
    assert (document.title, document.author) == ("The Glass Road", "Jane Doe")
    chapter = MarkdownAdapter().parse("ch1.md", MARKDOWN.encode(), resource="md/1")
    units = units_of(chapter)
    assert [(u["tag"], u["text"]) for u in units] == [
        ("h1", "Chapter 1"),
        ("p", "Alice walked *slowly* into the tower."),
        ("li", "first clue"),
        ("li", "second clue"),
        ("blockquote", "A quoted line that continues."),
        ("p", "The End."),
    ]
    assert chapter.title == "Chapter 1"
    layout = chapter.meta["layout"]["items"]
    assert [item.get("fixed") for item in layout if "fixed" in item] == [
        '```\ncode stays = "as is"\n```',
        "***",
    ]
    assert [item["indent"] for item in layout if "unit" in item] == ["# ", "", "- ", "- ", "> ", ""]
    assert chapter.meta["passage_max_chars"] == 3500


def test_html_reads_blocks_without_scripts_styles_or_comments():
    chapter = HtmlAdapter().parse("page.html", HTML.encode(), resource="html/1")
    texts = [(u["tag"], u["text"]) for u in units_of(chapter)]
    assert texts == [
        ("h2", "Chapter 2"),
        ("p", "Bob laughed. Then he left."),
        ("p", "Loose text"),
        ("p", "Inner paragraph."),
        ("li", "One"),
        ("li", "Two"),
    ]
    joined = " ".join(text for _, text in texts)
    assert "alert" not in joined and "color" not in joined and "comment" not in joined
    fixed = [item["fixed"] for item in chapter.meta["layout"]["items"] if "fixed" in item]
    assert fixed == ["* * *", "kept   as is"]
    inspection = HtmlAdapter().inspect("page.html", HTML.encode())
    assert (inspection.title, inspection.author, inspection.language) == ("Glass Road", "Jane Doe", "en")


def test_html_entity_declarations_are_refused():
    evil = '<!DOCTYPE x [<!ENTITY a "boom">]><html><body><p>&a;</p></body></html>'
    assert HtmlAdapter().inspect("evil.html", evil.encode()).errors


def test_docx_paragraphs_headings_lists_without_deleted_or_fallback_text():
    data = docx_bytes([("Chapter 3", "Titre1"), ("Carol opened the door.", None), ("an item", "List")])
    chapter = DocxAdapter().parse("ch3.docx", data, resource="docx/3")
    assert [(u["tag"], u["text"]) for u in units_of(chapter)] == [
        ("h1", "Chapter 3"),
        ("p", "Carol opened the door."),
        ("li", "an item"),
        ("p", "Split text"),
    ]
    inspection = DocxAdapter().inspect("ch3.docx", data)
    assert (inspection.title, inspection.author, inspection.language) == ("Word Title", "Jane Doe", "en-GB")
    assert not inspection.errors


@pytest.mark.parametrize(
    "data",
    [
        b"not a zip",
        docx_bytes([("x", None)], extra={"word/evil.xml": "x"}).replace(
            b"word/document.xml", b"word/documenX.xml"
        ),
    ],
)
def test_unreadable_docx_is_explained(data):
    found = DocxAdapter().inspect("broken.docx", data)
    assert found.errors and not found.meta
    with pytest.raises(TextRejected):
        DocxAdapter().parse("broken.docx", data, resource="docx/x")


def test_docx_entity_declarations_are_refused():
    data = docx_bytes([("Hello", None)])
    with zipfile.ZipFile(io.BytesIO(data)) as source:
        entries = {name: source.read(name) for name in source.namelist()}
    entries["word/document.xml"] = entries["word/document.xml"].replace(
        b"<w:document", b'<!DOCTYPE w:document [<!ENTITY a "boom">]><w:document', 1
    )
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w") as archive:
        for name, value in entries.items():
            archive.writestr(name, value)
    assert DocxAdapter().inspect("evil.docx", output.getvalue()).errors


def test_libris_markers_and_empty_documents_are_refused():
    assert MarkdownAdapter().inspect("x.md", "Text ⟦t0⟧ here".encode()).errors
    assert MarkdownAdapter().inspect("x.md", b"```\nonly code\n```\n").errors


def test_passage_size_is_an_option_of_the_adapters():
    text = "\n\n".join(f"Paragraph {n} " + "word " * 150 for n in range(20)).encode()
    small = MarkdownAdapter().parse("x.md", text, resource="md/x", max_chars=1000)
    large = MarkdownAdapter().parse("x.md", text, resource="md/x", max_chars=8000)
    assert len(small.groups) > len(large.groups) >= 2
    assert large.meta["passage_max_chars"] == 8000
    # Same units whatever the passage size: only their grouping changes.
    assert [u["id"] for u in units_of(small)] == [u["id"] for u in units_of(large)]


@pytest.fixture
def client():
    with SessionLocal() as db:
        db.add(User(username="reader", password_hash=password_hash(PASSWORD), admin=True))
        db.commit()
    with TestClient(app) as client:
        assert (
            client.post("/api/auth/login", json={"username": "reader", "password": PASSWORD}).status_code
            == 200
        )
        yield client


def commit(client, fmt: str, files: list[tuple[str, bytes]], **settings_values):
    session = client.post("/api/imports", json={"format": fmt}).json()
    for name, data in files:
        response = client.post(f"/api/imports/{session['id']}/files", files={"file": (name, data)})
        assert response.status_code == 201, response.text
    proposal = client.get(f"/api/imports/{session['id']}").json()["proposal"]
    items = [{"index": item["index"], "chapter_number": item["chapter_number"]} for item in proposal["items"]]
    body = {
        "destination": {"mode": "series", "series_name": "Glass"},
        "items": items,
        "settings": settings_values,
    }
    return client.post(f"/api/imports/{session['id']}/commit", json=body)


def test_documents_import_as_chapters_export_as_text_and_round_trip_through_an_archive(client):
    long = "\n\n".join(f"Sentence {n} about the long glass road and its keepers." for n in range(120))
    response = commit(
        client,
        "md",
        [("Chapter 1.md", MARKDOWN.encode()), ("Chapter 2.markdown", long.encode())],
        passage_max_chars=1200,
    )
    assert response.status_code == 200, response.text
    project_id = response.json()["projects"][0]["id"]
    assert commit(client, "html", [("Chapter 3.htm", HTML.encode())]).status_code == 200
    assert commit(
        client, "docx", [("Chapter 4.docx", docx_bytes([("Chapter 4", "Titre1"), ("Dan ran.", None)]))]
    )
    # A file whose extension does not match the import format is refused before inspection.
    session = client.post("/api/imports", json={"format": "md"}).json()
    assert (
        client.post(f"/api/imports/{session['id']}/files", files={"file": ("x.txt", b"x")}).status_code == 422
    )
    with SessionLocal() as db:
        project = db.get(Project, project_id)
        assert project.config["passage_max_chars"] == 1200
        chapters = db.scalars(
            select(Chapter).where(Chapter.project_id == project_id).order_by(Chapter.position)
        ).all()
        assert [c.chapter_number for c in chapters] == [1, 2, 3, 4]
        assert [c.import_meta["adapter"] for c in chapters] == ["md", "md", "html", "docx"]
        # The volume's passage size also applies to the chapters added later.
        assert {c.import_meta["passage_max_chars"] for c in chapters} == {1200}
        segments = db.scalars(select(Segment).where(Segment.chapter_id == chapters[1].id)).all()
        assert len(segments) > 1 and all(len(s.source) <= 1200 + 200 for s in segments)
        assert sorted(a.format for a in db.scalars(select(SourceAsset))) == ["docx", "html", "md", "md"]
    text = client.get(f"/api/projects/{project_id}/export/md?allow_source=true")
    assert text.status_code == 200, text.text
    assert "# Chapter 1" in text.text and "- first clue" in text.text and 'code stays = "as is"' in text.text
    assert "Alice walked *slowly* into the tower." in text.text
    # Archive: the sources are cut again with their own adapter and passage size.
    exported = client.get(f"/api/projects/{project_id}/export/project")
    assert exported.status_code == 200, exported.text
    with SessionLocal() as db:
        project = db.get(Project, project_id)
        project.series_id = None
        db.commit()
    previous = settings().passage_max_chars
    settings().passage_max_chars = 5000  # a later default must not change how an archive is cut
    try:
        restored = client.post("/api/projects/import", files={"file": ("p.zip", exported.content)})
    finally:
        settings().passage_max_chars = previous
    assert restored.status_code == 201, restored.text
    with SessionLocal() as db:

        def sources(pid):
            query = select(Segment.source).where(Segment.project_id == pid).order_by(Segment.position)
            return list(db.scalars(query))

        assert sources(restored.json()["id"]) == sources(project_id)
