"""Bilingual EPUB: source and translation paragraph by paragraph, for every kind of volume."""

import io
import zipfile

import pytest
import respx
import test_api_v1
from fastapi.testclient import TestClient
from lxml import etree
from sqlalchemy import select
from test_api_v1 import bearer, new_token, payload, run_pending_job
from test_document_imports import commit
from test_pipeline import mock_completion
from test_text_exports import FIRST, SECOND, TENTH, import_txt, translate

from app.db import SessionLocal
from app.engines.exports.bilingual import build_bilingual_epub, volume_pairs
from app.main import app
from app.models import Project, Segment, User
from app.security import password_hash

PASSWORD = "test-password-123456789"
X = "{http://www.w3.org/1999/xhtml}"
OPF = "{http://www.idpf.org/2007/opf}"
DC = "{http://purl.org/dc/elements/1.1/}"
XML_LANG = "{http://www.w3.org/XML/1998/namespace}lang"
provider_id, owner, api = test_api_v1.provider_id, test_api_v1.owner, test_api_v1.api


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


def unzip(content: bytes) -> dict[str, bytes]:
    with zipfile.ZipFile(io.BytesIO(content)) as archive:
        first = archive.infolist()[0]
        assert first.filename == "mimetype" and first.compress_type == zipfile.ZIP_STORED
        return {name: archive.read(name) for name in archive.namelist()}


def pages(files: dict[str, bytes]) -> list[etree._Element]:
    """The chapter documents in reading order, parsed as XML (every page must be well formed)."""
    return [etree.fromstring(files[name]) for name in sorted(files) if "/chapter-" in name]


def pairs(page: etree._Element) -> list[tuple[str, str]]:
    found = []
    for block in page.iter(f"{X}div"):
        source, translation = block.findall(f"{X}p")
        found.append(("".join(source.itertext()), "".join(translation.itertext())))
    return found


def test_a_txt_volume_pairs_every_paragraph_with_its_translation(client):
    pid = import_txt(
        client, [("Chapter 1.txt", FIRST), ("Chapter 2 - The Bridge.txt", SECOND), ("Chapter 10.txt", TENTH)]
    )
    translate(pid)
    response = client.get(f"/api/projects/{pid}/export/epub-bilingual")
    assert response.status_code == 200, response.text
    assert response.headers["content-type"] == "application/epub+zip"
    files = unzip(response.content)
    assert {"META-INF/container.xml", "OEBPS/content.opf", "OEBPS/nav.xhtml", "OEBPS/style.css"} <= set(files)
    first, second, tenth = pages(files)
    assert first.find(f"{X}body").get("class") == "interleaved"
    assert first.get("lang") == "fr" and first.get(XML_LANG) == "fr"
    assert "".join(first.find(f".//{X}h1").itertext()) == "FR Chapter 1"
    assert pairs(first) == [
        ("“Hello,” said Mira.", "FR “Hello,” said Mira."), ("She waited.", "FR She waited."),
        ("Indented line here.", "FR Indented line here."),
        ("Last paragraph of chapter one.", "FR Last paragraph of chapter one."),
    ]  # fmt: skip
    assert all(translation == "FR " + source for source, translation in pairs(second))
    assert ("Mira crossed the <bridge> & sang.", "FR Mira crossed the <bridge> & sang.") in pairs(second)
    # The scene break is written once, not paired.
    assert [p.text for p in first.iter(f"{X}p") if p.get("class") == "fixed"] == ["* * *"]
    source = next(p for p in second.iter(f"{X}p") if p.get("class") == "source")
    assert source.get("lang") == "en" and source.get(XML_LANG) == "en"
    assert pairs(tenth) == [("The tenth morning was quiet.", "FR The tenth morning was quiet.")]
    nav = etree.fromstring(files["OEBPS/nav.xhtml"])
    assert [a.text for a in nav.iter(f"{X}a")] == [
        "FR Chapter 1",
        "FR Chapter 2 - The Bridge",
        "FR Chapter 10",
    ]
    package = etree.fromstring(files["OEBPS/content.opf"])
    assert [n.text for n in package.iter(f"{DC}language")] == ["fr", "en"]
    assert package.find(f".//{OPF}meta[@property='dcterms:modified']").text.endswith("Z")
    spine = [item.get("idref") for item in package.iter(f"{OPF}itemref")]
    manifest = {item.get("id"): item.get("href") for item in package.iter(f"{OPF}item")}
    assert [manifest[idref] for idref in spine] == [
        "title.xhtml", "chapter-0001.xhtml", "chapter-0002.xhtml", "chapter-0003.xhtml",
    ]  # fmt: skip

    side = client.get(f"/api/projects/{pid}/export/epub-bilingual?layout=side-by-side")
    assert pages(unzip(side.content))[0].find(f"{X}body").get("class") == "side-by-side"
    assert client.get(f"/api/projects/{pid}/export/epub-bilingual?layout=columns").status_code == 422


def test_an_unfinished_translation_is_refused_unless_the_gaps_are_accepted(client):
    pid = import_txt(client, [("Chapter 2.txt", SECOND)])
    translate(pid, skip=0)
    refused = client.get(f"/api/projects/{pid}/export/epub-bilingual")
    assert refused.status_code == 409
    partial = client.get(f"/api/projects/{pid}/export/epub-bilingual?allow_source=true")
    assert partial.status_code == 200
    (page,) = pages(unzip(partial.content))
    missing = [p for p in page.iter(f"{X}p") if "missing" in (p.get("class") or "")]
    assert missing and all(p.text == "—" and p.get("lang") is None for p in missing)
    # The source of the untranslated passage is still there to read.
    assert any("Mira crossed" in source for source, _ in pairs(page))


def test_an_epub_volume_gets_its_chapters_headings_and_paragraphs(client, book_bytes):
    response = client.post("/api/projects", files={"file": ("book.epub", book_bytes, "application/epub+zip")})
    assert response.status_code == 201, response.text
    pid = response.json()["id"]
    translate(
        pid, how=lambda text: text.replace("Chapter", "Chapitre").replace("Silver Tower", "Tour d’argent")
    )
    files = unzip(client.get(f"/api/projects/{pid}/export/epub-bilingual").content)
    one, two = pages(files)
    assert "".join(one.find(f".//{X}h1").itertext()) == "Chapitre One"
    subtitle = one.find(f".//{X}p[@class='heading-source']")
    assert subtitle.text == "Chapter One" and subtitle.get("lang") == "en"
    assert (
        "Alice entered the Silver Tower and stopped.",
        "Alice entered the Tour d’argent and stopped.",
    ) in pairs(one)
    assert (
        "Alice discovered that Bob was her brother.",
        "Alice discovered that Bob was her brother.",
    ) in pairs(two)
    # No markup code of the pipeline, no image, no link in the proofreading copy.
    text = b"".join(files[name] for name in files if name.endswith(".xhtml")).decode()
    assert "⟦" not in text and "<img" not in text and "pixel.png" not in text
    assert not any(name.endswith(".png") for name in files)


def test_documents_keep_subheadings_lists_and_quotes(client):
    markdown = "# The Keeper\n\nFirst line.\n\n## Morning\n\n- Bread\n- Salt\n\n> Old words.\n"
    response = commit(client, "md", [("Chapter 1.md", markdown.encode())])
    assert response.status_code == 200, response.text
    pid = response.json()["projects"][0]["id"]
    translate(pid)
    (page,) = pages(unzip(client.get(f"/api/projects/{pid}/export/epub-bilingual").content))
    assert "".join(page.find(f".//{X}h1").itertext()) == "FR The Keeper"
    blocks = {"".join(div.find(f"{X}p").itertext()): div.get("class") for div in page.iter(f"{X}div")}
    assert blocks == {
        "First line.": "pair", "Morning": "pair subheading", "- Bread": "pair", "- Salt": "pair",
        "Old words.": "pair quote",
    }  # fmt: skip
    assert ("- Bread", "- FR Bread") in pairs(page)


def test_control_characters_right_to_left_and_free_form_languages(client):
    pid = import_txt(client, [("Chapter 1.txt", "The gate opened.\n")])
    translate(pid, how=lambda text: "بوابة " + text)
    with SessionLocal() as db:
        project = db.get(Project, pid)
        project.target_language, project.source_language = "ar", "Old Norse"
        project.title = "Glass\x00 Road"
        db.commit()
        content = build_bilingual_epub(project, volume_pairs(db, project), "side-by-side")
    files = unzip(content)
    (page,) = pages(files)
    assert page.get("dir") == "rtl" and page.get("lang") == "ar"
    translation = next(p for p in page.iter(f"{X}p") if p.get("class") == "translation")
    assert "".join(translation.itertext()).startswith("بوابة") and translation.get("dir") == "rtl"
    source = next(p for p in page.iter(f"{X}p") if p.get("class") == "source")
    assert source.get("lang") is None  # "Old Norse" is not a language tag
    package = etree.fromstring(files["OEBPS/content.opf"])
    assert [n.text for n in package.iter(f"{DC}language")] == ["ar"]
    assert package.find(f"{DC}title") is None and package.find(f".//{DC}title").text == "Glass Road"
    assert package.find(f"{OPF}spine").get("page-progression-direction") == "rtl"
    with pytest.raises(ValueError):
        build_bilingual_epub(project, [], "columns")


@respx.mock
async def test_the_automation_api_delivers_a_bilingual_epub(owner, api, provider_id):
    respx.post("https://llm.test/v1/chat/completions").mock(side_effect=mock_completion)
    secret = new_token(owner)
    body = payload(provider_id, output={"format": "epub-bilingual"})
    body["pipeline"]["final_review"] = False
    created = api.post("/api/v1/translation-requests", headers=bearer(secret), json=body)
    assert created.status_code == 202, created.text
    request = created.json()
    assert (await run_pending_job()).status == "completed"
    status = api.get(request["status_url"], headers=bearer(secret)).json()
    assert status["result"]["format"] == "epub-bilingual"
    result = api.get(request["result_url"] + "?wait=5", headers=bearer(secret))
    assert result.status_code == 200 and result.headers["content-type"] == "application/epub+zip"
    assert "bilingue.epub" in result.headers["content-disposition"]
    files = unzip(result.content)
    first, second = pages(files)
    assert first.find(f"{X}body").get("class") == "interleaved"
    assert any(source == "Élodie opened the gate." for source, _ in pairs(first))
    assert any("Tour d’argent" in translation for _, translation in pairs(first) + pairs(second))
    side = api.get(
        request["result_url"] + "?format=epub-bilingual&layout=side-by-side", headers=bearer(secret)
    )
    assert (
        side.status_code == 200
        and pages(unzip(side.content))[0].find(f"{X}body").get("class") == "side-by-side"
    )
    # A JSON request can also ask for the bilingual EPUB afterwards, only its chapters in it.
    other = api.get(request["result_url"] + "?format=json", headers=bearer(secret))
    assert other.status_code == 200 and len(other.json()["chapters"]) == 2
    wrong = api.get(request["result_url"] + "?format=epub-bilingual&layout=columns", headers=bearer(secret))
    assert wrong.status_code == 422


def test_a_partial_api_result_marks_the_missing_translations(owner, api, provider_id):
    secret = new_token(owner)
    body = payload(provider_id)
    body["pipeline"]["start"] = False
    request = api.post("/api/v1/translation-requests", headers=bearer(secret), json=body).json()
    early = api.get(request["result_url"] + "?format=epub-bilingual", headers=bearer(secret))
    assert early.status_code == 409 and early.json()["detail"]["code"] == "result_not_ready"
    partial = api.get(request["result_url"] + "?format=epub-bilingual&partial=true", headers=bearer(secret))
    assert partial.status_code == 200 and partial.headers["x-libris-complete"] == "false"
    with SessionLocal() as db:
        assert db.scalars(select(Segment).where(Segment.project_id == request["project_id"])).first()
    page = pages(unzip(partial.content))[0]
    assert all(translation == "—" for _, translation in pairs(page))
