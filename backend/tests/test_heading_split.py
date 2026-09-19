"""One TXT, Markdown or DOCX file holding many chapters, split at its chapter headings."""

import pytest
import test_api_v1
from fastapi.testclient import TestClient
from sqlalchemy import select
from test_api_v1 import bearer, new_token
from test_document_imports import docx_bytes

from app.config import settings
from app.db import SessionLocal
from app.engines.ingestion import DocxAdapter
from app.engines.ingestion.naming import chapter_heading, cjk_value
from app.engines.ingestion.split import Part, detect, docx_file, fill_numbers, proposal, read_source
from app.main import app
from app.models import Chapter, Project, Segment, SourceAsset, TranslationRequest, User
from app.security import password_hash

PASSWORD = "test-password-123456789"
provider_id, owner, api = test_api_v1.provider_id, test_api_v1.owner, test_api_v1.api
REQUESTS = "/api/v1/translation-requests"

NOVEL = """The Glass Road
A synthetic serial.

Contents
Chapter 1: The Start
Chapter 2: The Storm
Chapter 3

Prologue

Long ago the glass road was built by people nobody remembers.

Chapter 1: The Start

Alice woke up early. She read chapter 3 of her book again.
Chapter 3 was her favourite, she thought.

Chapter 2: The Storm

Rain fell on the glass road for three days.

Chapter 3
The Keeper

The keeper opened the gate.

Epilogue

Years later, the road was quiet.
"""


def split_of(text: str, fmt: str = "txt"):
    return detect(read_source(fmt, f"novel.{fmt}", text.encode()))


def titles(found) -> list[str]:
    return [part.title for part in found.parts]


def test_heading_lines_are_read_in_english_french_and_chinese():
    assert chapter_heading("Chapter 12").number == 12
    assert chapter_heading("Chapitre 12 : Le retour").subtitle == "Le retour"
    assert chapter_heading("CHAPTER XII").number == 12
    assert chapter_heading("第12章 归来").number == 12
    assert chapter_heading("第一百零五章").number == 105
    assert cjk_value("二十") == 20 and cjk_value("十二") == 12
    assert chapter_heading("Épilogue").number is None and chapter_heading("Prologue").kind == "special"
    assert chapter_heading("12. The Return").kind == "numbered"
    # Sentences that mention a chapter are text, not headings.
    for line in (
        "He read chapter 3.",
        "Chapter 3 was long and dull.",
        "Chapter Ivy",
        "prologue of the thing",
    ):
        assert chapter_heading(line) is None, line


def test_a_novel_is_split_with_its_table_of_contents_front_matter_prologue_and_epilogue():
    found = split_of(NOVEL)
    assert found.confidence == "high" and not found.warnings
    assert titles(found) == [
        "",
        "Prologue",
        "Chapter 1: The Start",
        "Chapter 2: The Storm",
        "Chapter 3 — The Keeper",
        "Epilogue",
    ]
    # Front matter (with the table of contents) first; unnumbered headings numbered between neighbours.
    assert [part.number for part in found.parts] == [0, 0.5, 1, 2, 3, 4]
    assert found.parts[0].start == 0 and not found.parts[0].heading
    # The mid-paragraph mention of "chapter 3" did not start a chapter.
    assert "chapter 3 of her book" in found.parts[2].excerpt
    assert found.parts[4].excerpt == "The keeper opened the gate."


def test_french_roman_and_chinese_headings_and_gaps():
    french = "CHAPITRE I\n\nIl pleut.\n\nCHAPITRE II : La route\n\nIl neige.\n\nCHAPITRE IV\n\nIl vente.\n"
    found = split_of(french)
    assert [part.number for part in found.parts] == [1, 2, 4]
    assert found.confidence == "medium" and found.warnings == ["Chapitres absents du fichier : 3."]
    chinese = "第一章 开始\n天亮了。\n第二章\n下雨了。\n第3章 结束\n结束了。\n"
    found = split_of(chinese)
    assert [part.number for part in found.parts] == [1, 2, 3]
    assert titles(found) == ["第一章 开始", "第二章", "第3章 结束"]


def test_out_of_order_headings_stay_in_the_previous_chapter_with_a_warning():
    text = "Chapter 1\n\nOne.\n\nChapter 2\n\nTwo.\n\nChapter 1. Again The Same Thing\n\nStill two.\n\nChapter 3\n\nThree.\n"
    found = split_of(text)
    assert [part.number for part in found.parts] == [1, 2, 3]
    assert "ne suit pas l’ordre des chapitres" in found.warnings[0]


def test_numbered_lines_split_only_when_they_follow_each_other():
    good = "1. The Start\n\nAlice.\n\n2. The Storm\n\nRain.\n\n3. The End\n\nDone.\n"
    found = split_of(good)
    assert [part.number for part in found.parts] == [1, 2, 3] and found.confidence == "medium"
    assert split_of("1. Buy bread\n\nText.\n\n5. Sell it\n\nMore text.\n\n9. Rest\n\nEnd.\n") is None
    # A single chapter, or none at all, is not split.
    assert split_of("Chapter 4\n\nOnly one chapter here.\n") is None
    assert split_of("Just a text with no heading.\n") is None


def test_markdown_headings_below_the_book_title():
    text = "# The Glass Road\n\n## Chapter 1\n\nAlice.\n\n```\n# not a heading\n```\n\n## Chapter 2\n\nBob.\n"
    found = split_of(text, "md")
    assert titles(found) == ["", "Chapter 1", "Chapter 2"]
    assert [part.start for part in found.parts] == [0, 2, 10]


def test_docx_heading_styles_come_first():
    data = docx_bytes(
        [
            ("Foreword by nobody", None),
            ("The Arrival", "Titre1"),
            ("Carol opened the door.", None),
            ("Chapter 7 is mentioned here", None),
            ("The Departure", "Titre1"),
            ("Carol closed the door.", None),
        ]
    )
    found = detect(read_source("docx", "book.docx", data))
    assert titles(found) == ["", "The Arrival", "The Departure"]
    # Headings that do not name chapters are proposed, not applied by default.
    assert found.confidence == "medium"
    assert [part.number for part in found.parts] == [1, 2, 3]


def test_a_docx_part_reads_back_as_the_same_paragraphs():
    source = read_source("docx", "book.docx", docx_bytes([("Chapter 1", "Titre1"), ("Text", None)]))
    source.blocks[1].text = "A\ttab & <b> kept"
    again = read_source("docx", "part.docx", docx_file(source.blocks))
    assert [(b.text, b.tag) for b in again.blocks] == [(b.text, b.tag) for b in source.blocks]
    chapter = DocxAdapter().parse("part.docx", docx_file(source.blocks), resource="docx/1")
    assert chapter.title == "Chapter 1"


def test_numbers_are_filled_between_neighbours():
    parts = [
        Part(0, None, "", False),
        Part(1, None, "", True),
        Part(2, 1.0, "", True),
        Part(3, None, "", True),
    ]
    fill_numbers(parts)
    assert [part.number for part in parts] == [0, 0.5, 1, 2]
    parts = [Part(0, 5.0, "", True), Part(1, None, "", True), Part(2, 6.0, "", True), Part(3, None, "", True)]
    fill_numbers(parts)
    assert [part.number for part in parts] == [5, 5.5, 6, 7]
    parts = [Part(0, None, "", True), Part(1, 12.0, "", True)]
    fill_numbers(parts)
    assert parts[0].number == 11


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


def upload(client, fmt: str, name: str, data: bytes, **headers) -> tuple[str, dict]:
    session = client.post("/api/imports", json={"format": fmt}).json()
    response = client.post(
        f"/api/imports/{session['id']}/files", files={"file": (name, data)}, headers=headers
    )
    assert response.status_code == 201, response.text
    return session["id"], response.json()


def commit(client, session_id: str, split: list[dict] | None, series: str = "Glass", **extra):
    item = {"index": 0, "chapter_number": 1, **({"split": split} if split is not None else {})}
    body = {
        "destination": {"mode": "series", "series_name": series},
        "items": [item],
        "start": "none",
        **extra,
    }
    return client.post(f"/api/imports/{session_id}/commit", json=body)


def chapters_of(project_id: str) -> list[Chapter]:
    with SessionLocal() as db:
        query = select(Chapter).where(Chapter.project_id == project_id).order_by(Chapter.position)
        return list(db.scalars(query))


def as_choice(parts: list[dict]) -> list[dict]:
    return [{"start": part["start"], "number": part["number"], "title": part["title"]} for part in parts]


def test_the_session_import_creates_one_chapter_per_part_and_dedupes_a_second_upload(client):
    session_id, entry = upload(client, "txt", "Glass Road.txt", NOVEL.encode())
    split = entry["meta"]["split"]
    assert split["default"] is True and split["unit"] == "line" and len(split["parts"]) == 6
    assert split["parts"][2]["characters"] > 0 and split["parts"][2]["first_line"] == "Chapter 1: The Start"
    parts = as_choice(split["parts"])
    parts[0]["title"] = "Front matter"
    response = commit(client, session_id, parts)
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["chapters"]["created"] == 6
    assert [item["part"] for item in result["chapters"]["items"]] == [0, 1, 2, 3, 4, 5]
    project_id = result["projects"][0]["id"]
    chapters = chapters_of(project_id)
    assert [c.chapter_number for c in chapters] == [0, 0.5, 1, 2, 3, 4]
    assert [c.title for c in chapters] == [
        "Front matter", "Prologue", "Chapter 1: The Start", "Chapter 2: The Storm", "Chapter 3 — The Keeper",
        "Epilogue",
    ]  # fmt: skip
    assert {c.import_meta["split_from"] for c in chapters} == {"Glass Road.txt"}
    with SessionLocal() as db:
        # Each part is stored as its own file, the way a separate upload would be.
        assets = list(db.scalars(select(SourceAsset).where(SourceAsset.project_id == project_id)))
        assert len(assets) == 6 and all(asset.format == "txt" for asset in assets)
        sources = list(db.scalars(select(Segment.source).where(Segment.chapter_id == chapters[2].id)))
        assert sources[0].startswith("Chapter 1: The Start")
    # The same file again: nothing is created twice, the proposal says the chapters are identical.
    again, entry = upload(client, "txt", "Glass Road.txt", NOVEL.encode())
    view = client.get(f"/api/imports/{again}?project_id={project_id}").json()
    assert all(item["same_content"] for item in view["proposal"]["items"][0]["split_existing"])
    body = {
        "destination": {"mode": "series", "series_name": "Glass"},
        "items": [{"index": 0, "split": parts}],
        "start": "none",
    }
    second = client.post(f"/api/imports/{again}/commit", json=body)
    assert second.status_code == 200, second.text
    assert second.json()["chapters"]["unchanged"] == 6 and second.json()["chapters"]["created"] == 0
    assert len(chapters_of(project_id)) == 6
    # A split volume round-trips through a project archive: each part is cut again from its own file.
    exported = client.get(f"/api/projects/{project_id}/export/project")
    assert exported.status_code == 200, exported.text
    with SessionLocal() as db:
        db.get(Project, project_id).series_id = None
        db.commit()
    restored = client.post("/api/projects/import", files={"file": ("p.zip", exported.content)})
    assert restored.status_code == 201, restored.text
    assert [c.title for c in chapters_of(restored.json()["id"])] == [c.title for c in chapters]


def test_edited_boundaries_numbers_and_titles_are_honoured(client):
    session_id, entry = upload(client, "txt", "novel.txt", NOVEL.encode())
    parts = as_choice(entry["meta"]["split"]["parts"])
    # Merge the prologue into the front matter and chapter 2 into chapter 1; renumber and rename.
    kept = [parts[0], parts[2], parts[4], parts[5]]
    kept[1] = {**kept[1], "number": 10, "title": "The Start and the Storm"}
    kept[2] = {**kept[2], "number": 11}
    kept[3] = {**kept[3], "number": 12}
    response = commit(client, session_id, kept)
    assert response.status_code == 200, response.text
    chapters = chapters_of(response.json()["projects"][0]["id"])
    assert [c.chapter_number for c in chapters] == [0, 10, 11, 12]
    assert [c.title for c in chapters] == [
        "Avant-propos",
        "The Start and the Storm",
        "Chapter 3 — The Keeper",
        "Epilogue",
    ]
    with SessionLocal() as db:
        first = " ".join(db.scalars(select(Segment.source).where(Segment.chapter_id == chapters[0].id)))
        merged = " ".join(db.scalars(select(Segment.source).where(Segment.chapter_id == chapters[1].id)))
    assert "Long ago the glass road" in first and "Rain fell" in merged


def test_invalid_boundaries_are_refused(client):
    session_id, entry = upload(client, "txt", "novel.txt", NOVEL.encode())
    parts = as_choice(entry["meta"]["split"]["parts"])
    for wrong, word in (
        ([{**parts[0], "start": 3}, *parts[1:]], "début du fichier"),
        ([parts[0], parts[3], parts[2]], "croissantes"),
        ([parts[0], {**parts[1], "start": 7}], "Position 7 invalide"),  # a blank line
        ([parts[0], {**parts[1], "start": 99999}], "invalide"),
    ):
        response = commit(client, session_id, wrong)
        assert response.status_code == 422, response.text
        detail = response.json()["detail"]
        assert detail["code"] == "invalid_split" and word in detail["message"], detail
    english = client.post(
        f"/api/imports/{session_id}/commit",
        json={"destination": {"mode": "series", "series_name": "Glass"},
              "items": [{"index": 0, "split": [{**parts[0], "start": 3}]}]},
        headers={"Accept-Language": "en"},
    )  # fmt: skip
    assert "must start at the beginning of the file" in english.json()["detail"]["message"]
    duplicate = commit(client, session_id, [parts[0], {**parts[1], "number": 0}])
    assert duplicate.status_code == 422 and "plusieurs fois" in str(duplicate.json()["detail"]["errors"])
    with SessionLocal() as db:
        assert not db.scalar(select(Chapter.id).limit(1))
    # Nothing was imported: the proposed split still commits.
    assert commit(client, session_id, parts).status_code == 200


def test_split_is_optional_and_warnings_are_localized(client):
    session_id, entry = upload(client, "txt", "Chapter 1.txt", NOVEL.encode())
    response = commit(client, session_id, None)
    assert response.status_code == 200 and response.json()["chapters"]["created"] == 1
    text = "CHAPITRE I\n\nIl pleut.\n\nCHAPITRE III\n\nIl neige.\n"
    _, entry = upload(client, "txt", "b.txt", text.encode(), **{"Accept-Language": "en"})
    split = entry["meta"]["split"]
    assert split["warnings"] == ["Chapters missing from the file: 2."] and split["default"] is False
    assert split["reason"].startswith("chapter heading lines")


def test_markdown_and_docx_files_are_split_through_the_import(client):
    text = "# The Glass Road\n\nBy nobody.\n\n## Chapter 1\n\nAlice *ran*.\n\n## Chapter 2\n\nBob.\n"
    session_id, entry = upload(client, "md", "road.md", text.encode())
    response = commit(client, session_id, as_choice(entry["meta"]["split"]["parts"]))
    assert response.status_code == 200, response.text
    chapters = chapters_of(response.json()["projects"][0]["id"])
    assert [(c.chapter_number, c.title, c.import_meta["adapter"]) for c in chapters] == [
        (0, "The Glass Road", "md"), (1, "Chapter 1", "md"), (2, "Chapter 2", "md"),
    ]  # fmt: skip
    data = docx_bytes(
        [("Chapter 1", "Titre1"), ("Carol ran.", None), ("Chapter 2", "Titre1"), ("Dan hid.", None)]
    )
    session_id, entry = upload(client, "docx", "road.docx", data)
    split = entry["meta"]["split"]
    assert split["unit"] == "block" and split["default"] is True
    response = commit(client, session_id, as_choice(split["parts"]), series="Word")
    assert response.status_code == 200, response.text
    project_id = response.json()["projects"][0]["id"]
    chapters = [c for c in chapters_of(project_id) if c.import_meta["adapter"] == "docx"]
    assert [c.title for c in chapters] == ["Chapter 1", "Chapter 2"]
    exported = client.get(f"/api/projects/{project_id}/export/project")
    with SessionLocal() as db:
        db.get(Project, project_id).series_id = None
        db.commit()
    assert client.post("/api/projects/import", files={"file": ("p.zip", exported.content)}).status_code == 201


def test_a_file_too_long_for_one_chapter_is_imported_split(client, monkeypatch):
    monkeypatch.setattr(settings(), "text_chapter_max_chars", 1000)
    body = "Some words of this long chapter. " * 20
    text = "".join(f"Chapter {n}\n\n{body}\n\n" for n in range(1, 4))
    session_id, entry = upload(client, "txt", "long.txt", text.encode())
    assert not entry["errors"] and entry["meta"]["split"]["required"] is True
    assert commit(client, session_id, as_choice(entry["meta"]["split"]["parts"])).status_code == 200


def fields(**extra) -> dict:
    return {"series": "Web Saga", "volume": "1", "source_language": "en", "target_language": "fr",
            "start": "false", **extra}  # fmt: skip


def test_the_api_splits_a_txt_file_by_headings(owner, api):
    secret = new_token(owner)
    files = {"file": ("Glass Road.txt", NOVEL.encode(), "text/plain")}
    created = api.post(REQUESTS, headers=bearer(secret), files=files, data=fields(split="headings"))
    assert created.status_code == 202, created.text
    body = created.json()
    status = api.get(body["status_url"], headers=bearer(secret)).json()
    items = status["chapters"]["items"]
    assert [item["number"] for item in items] == [0, 0.5, 1, 2, 3, 4]
    assert [item["title"] for item in items][1:3] == ["Prologue", "Chapter 1: The Start"]
    with SessionLocal() as db:
        decisions = db.get(TranslationRequest, body["request_id"]).options["decisions"]
        chapter = db.scalar(select(Chapter).where(Chapter.title == "Chapter 2: The Storm"))
        text = " ".join(db.scalars(select(Segment.source).where(Segment.chapter_id == chapter.id)))
    assert decisions[0]["split"] == 6 and "découpé en 6 chapitres" in decisions[0]["reason"]
    # The heading is the chapter's title; the text keeps only the chapter's body.
    assert "Rain fell" in text
    # Sent again: the same chapters are found, nothing is duplicated.
    again = api.post(REQUESTS, headers=bearer(secret), files=files, data=fields(split="headings"))
    assert again.status_code == 202, again.text
    assert api.get(again.json()["status_url"], headers=bearer(secret)).json()["chapters"]["unchanged"] == 6
    with SessionLocal() as db:
        assert len(list(db.scalars(select(Chapter.id)))) == 6
    # Without the option, the file stays one chapter (the behaviour of existing clients).
    whole = api.post(REQUESTS, headers=bearer(secret), files={"file": ("Chapter 9.txt", NOVEL.encode(), "text/plain")},
                     data=fields())  # fmt: skip
    assert whole.status_code == 202, whole.text
    assert len(api.get(whole.json()["status_url"], headers=bearer(secret)).json()["chapters"]["items"]) == 1


def test_the_api_splits_a_docx_file_and_refuses_what_it_cannot_split(owner, api):
    secret = new_token(owner)
    data = docx_bytes(
        [("Chapter 1", "Titre1"), ("Carol ran.", None), ("Chapter 2", "Titre1"), ("Dan hid.", None)]
    )
    docx = ("Road.docx", data, "application/vnd.openxmlformats-officedocument.wordprocessingml.document")
    created = api.post(REQUESTS, headers=bearer(secret), files={"file": docx}, data=fields(split="headings"))
    assert created.status_code == 202, created.text
    assert created.json()["input"] == "docx"
    items = api.get(created.json()["status_url"], headers=bearer(secret)).json()["chapters"]["items"]
    assert [(item["number"], item["title"]) for item in items] == [(1, "Chapter 1"), (2, "Chapter 2")]
    two = [
        ("files", ("a.txt", b"Chapter 1\n\nA.\n", "text/plain")),
        ("files", ("b.txt", b"B.\n", "text/plain")),
    ]
    refused = api.post(REQUESTS, headers=bearer(secret), files=two, data=fields(split="headings"))
    assert refused.status_code == 422 and refused.json()["detail"]["errors"][0]["loc"] == ["split"]
    wrong = api.post(REQUESTS, headers=bearer(secret), files={"file": docx}, data=fields(split="chapters"))
    assert wrong.status_code == 422 and wrong.json()["detail"]["code"] == "invalid_payload"
    epub = api.post(REQUESTS, headers=bearer(secret), files={"file": ("b.epub", b"x", "application/epub+zip")},
                    data=fields(split="headings"))  # fmt: skip
    assert epub.status_code == 422 and "EPUB" in epub.json()["detail"]["message"]


def test_the_api_keeps_a_file_without_headings_whole(owner, api):
    secret = new_token(owner)
    files = {"file": ("Chapter 5.txt", b"Only one chapter of text.\n", "text/plain")}
    created = api.post(REQUESTS, headers=bearer(secret), files=files, data=fields(split="headings"))
    assert created.status_code == 202, created.text
    with SessionLocal() as db:
        decisions = db.get(TranslationRequest, created.json()["request_id"]).options["decisions"]
    assert decisions[-1]["split"] == 1 and "seul chapitre" in decisions[-1]["reason"]
    items = api.get(created.json()["status_url"], headers=bearer(secret)).json()["chapters"]["items"]
    assert [item["number"] for item in items] == [5]


def test_the_proposal_is_empty_for_formats_that_are_not_split():
    assert proposal("html", "a.html", b"<h1>Chapter 1</h1><p>x</p><h1>Chapter 2</h1><p>y</p>") is None
    assert proposal("txt", "a.txt", b"\xff\xfe\x00\x00") is None
