"""Exports checked by the real EPUBCheck; skipped unless EPUBCHECK_JAR is installed (the application image)."""

import io
import zipfile

import pytest
from fastapi.testclient import TestClient
from lxml import etree

from app.engines.epub.check import epubcheck
from app.main import app

pytestmark = [pytest.mark.epubcheck, pytest.mark.usefixtures("epubcheck_jar")]

# A real 1×1 PNG: EPUBCheck rejects the placeholder bytes of the shared fixture as a corrupted image.
PIXEL_PNG = bytes.fromhex(
    "89504e470d0a1a0a0000000d4948445200000001000000010806000000"
    "1f15c4890000000d49444154789c6360000002000105fe02fea70000000049454e44ae426082"
)

EPUB2_CONTAINER = b"""<?xml version="1.0" encoding="UTF-8"?>
<container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container">
  <rootfiles><rootfile full-path="OEBPS/content.opf" media-type="application/oebps-package+xml"/></rootfiles>
</container>"""

EPUB2_PACKAGE = b"""<?xml version="1.0" encoding="UTF-8"?>
<package xmlns="http://www.idpf.org/2007/opf" version="2.0" unique-identifier="book-id">
  <metadata xmlns:dc="http://purl.org/dc/elements/1.1/" xmlns:opf="http://www.idpf.org/2007/opf">
    <dc:identifier id="book-id">urn:uuid:5b0f3c1e-3d6a-4f7e-9a51-2f6c1d0e8b42</dc:identifier>
    <dc:title>The Quiet Harbour</dc:title>
    <dc:language>en</dc:language>
    <dc:creator opf:role="aut">Test Author</dc:creator>
  </metadata>
  <manifest>
    <item id="ncx" href="toc.ncx" media-type="application/x-dtbncx+xml"/>
    <item id="chapter1" href="chapter1.xhtml" media-type="application/xhtml+xml"/>
    <item id="chapter2" href="chapter2.xhtml" media-type="application/xhtml+xml"/>
  </manifest>
  <spine toc="ncx"><itemref idref="chapter1"/><itemref idref="chapter2"/></spine>
</package>"""

EPUB2_NCX = b"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE ncx PUBLIC "-//NISO//DTD ncx 2005-1//EN" "http://www.daisy.org/z3986/2005/ncx-2005-1.dtd">
<ncx xmlns="http://www.daisy.org/z3986/2005/ncx/" version="2005-1">
  <head><meta name="dtb:uid" content="urn:uuid:5b0f3c1e-3d6a-4f7e-9a51-2f6c1d0e8b42"/>
    <meta name="dtb:depth" content="1"/><meta name="dtb:totalPageCount" content="0"/>
    <meta name="dtb:maxPageNumber" content="0"/></head>
  <docTitle><text>The Quiet Harbour</text></docTitle>
  <navMap>
    <navPoint id="np1" playOrder="1"><navLabel><text>The Pier</text></navLabel>
      <content src="chapter1.xhtml#pier"/></navPoint>
    <navPoint id="np2" playOrder="2"><navLabel><text>The Lighthouse</text></navLabel>
      <content src="chapter2.xhtml"/></navPoint>
  </navMap>
</ncx>"""

XHTML11 = b"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE html PUBLIC "-//W3C//DTD XHTML 1.1//EN" "http://www.w3.org/TR/xhtml11/DTD/xhtml11.dtd">
<html xmlns="http://www.w3.org/1999/xhtml" xml:lang="en"><head><title>%s</title></head><body>%s</body></html>"""


def epub2_with_nbsp() -> bytes:
    """EPUB 2 in the Calibre style: XHTML 1.1 DTD and named `&nbsp;` entities in the text."""
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w") as archive:
        archive.writestr("mimetype", "application/epub+zip", compress_type=zipfile.ZIP_STORED)
        archive.writestr("META-INF/container.xml", EPUB2_CONTAINER, compress_type=zipfile.ZIP_DEFLATED)
        archive.writestr("OEBPS/content.opf", EPUB2_PACKAGE, compress_type=zipfile.ZIP_DEFLATED)
        archive.writestr("OEBPS/toc.ncx", EPUB2_NCX, compress_type=zipfile.ZIP_DEFLATED)
        chapters = {
            "chapter1.xhtml": (b"The Pier", b'<h1 id="pier">The Pier</h1><p>Mr.&nbsp;Hale waited&nbsp;: '
                               b"the tide was late.</p><p>&laquo;&nbsp;Tomorrow,&nbsp;&raquo; he said.</p>"),
            "chapter2.xhtml": (b"The Lighthouse", b"<h1>The Lighthouse</h1><p>The lamp turned "
                               b"slowly&nbsp;&mdash; once, twice.</p>"),
        }
        for name, (title, body) in chapters.items():
            archive.writestr(f"OEBPS/{name}", XHTML11 % (title, body), compress_type=zipfile.ZIP_DEFLATED)
    return output.getvalue()


def with_real_image(data: bytes) -> bytes:
    output = io.BytesIO()
    with zipfile.ZipFile(io.BytesIO(data)) as source, zipfile.ZipFile(output, "w") as target:
        for info in source.infolist():
            content = PIXEL_PNG if info.filename.endswith("pixel.png") else source.read(info)
            kind = zipfile.ZIP_STORED if info.filename == "mimetype" else zipfile.ZIP_DEFLATED
            target.writestr(info.filename, content, kind)
    return output.getvalue()


def login(client):
    response = client.post("/api/auth/login", json={"username": "tester", "password": "test-password-123456789"})
    assert response.status_code == 200


def create_user():
    from app.db import SessionLocal
    from app.models import User
    from app.security import password_hash

    with SessionLocal() as db:
        db.add(User(username="tester", password_hash=password_hash("test-password-123456789"), admin=True))
        db.commit()


def import_project(client, data: bytes) -> str:
    response = client.post("/api/projects", files={"file": ("book.epub", data, "application/epub+zip")})
    assert response.status_code == 201, response.text
    return response.json()["id"]


def translate_everything(client, project_id, translate):
    for segment in client.get(f"/api/projects/{project_id}/segments").json():
        response = client.put(
            f"/api/segments/{segment['id']}",
            json={
                "revision": 0,
                "units": [{"id": unit["id"], "text": translate(unit["text"])} for unit in segment["units"]],
                "validated": True,
            },
        )
        assert response.status_code == 200, response.text


def exported(client, project_id) -> bytes:
    # The endpoint itself refuses an export that EPUBCheck rejects (422 with the first errors).
    response = client.get(f"/api/projects/{project_id}/export/epub")
    assert response.status_code == 200, response.text
    return response.content


def assert_valid(data: bytes):
    validation = epubcheck(data)
    problems = [
        f"{m.get('ID')} {m.get('message')}"
        for m in validation["report"].get("messages", [])
        if m.get("severity") in {"ERROR", "FATAL"}
    ]
    assert validation["available"] and validation["valid"], problems


def entries(data: bytes) -> dict[str, bytes]:
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        return {name: archive.read(name) for name in archive.namelist()}


def test_the_reference_book_exports_a_valid_epub3(book_bytes):
    create_user()
    with TestClient(app) as client:
        login(client)
        project_id = import_project(client, with_real_image(book_bytes))
        translate_everything(client, project_id, lambda text: text)
        assert_valid(exported(client, project_id))


def test_an_epub2_with_named_entities_stays_valid_after_translation():
    source = epub2_with_nbsp()
    assert_valid(source)
    create_user()
    with TestClient(app) as client:
        login(client)
        project_id = import_project(client, source)
        translate_everything(client, project_id, lambda text: text.replace("Tomorrow", "Demain"))
        output = exported(client, project_id)
    assert_valid(output)
    chapter = entries(output)["OEBPS/chapter1.xhtml"]
    assert b"&nbsp;" not in chapter and "Demain".encode() in chapter
    assert "\u00a0".encode() in chapter or b"&#160;" in chapter


def test_translated_table_of_contents_still_points_to_the_chapters(book_bytes):
    create_user()
    with TestClient(app) as client:
        login(client)
        project_id = import_project(client, with_real_image(book_bytes))
        translate_everything(client, project_id, lambda text: text.replace("Chapter", "Chapitre"))
        output = exported(client, project_id)
    assert_valid(output)
    files = entries(output)
    nav = etree.fromstring(next(content for name, content in files.items() if name.endswith("nav.xhtml")))
    ncx = etree.fromstring(next(content for name, content in files.items() if name.endswith(".ncx")))
    nav_labels = [" ".join(a.itertext()).strip() for a in nav.iter("{http://www.w3.org/1999/xhtml}a")]
    ncx_labels = [t.text for t in ncx.iter("{http://www.daisy.org/z3986/2005/ncx/}text")]
    assert {"Chapitre One", "Chapitre Two"} <= set(nav_labels) & set(ncx_labels)


def test_bilingual_epubs_are_valid_in_both_layouts_for_epub_and_text_volumes(book_bytes):
    create_user()
    with TestClient(app) as client:
        login(client)
        project_id = import_project(client, with_real_image(book_bytes))
        translate_everything(client, project_id, lambda text: text.replace("Chapter", "Chapitre"))
        session = client.post("/api/imports", json={"format": "txt"}).json()
        text = "Chapter 1\n\n“Hello,” said Mira.\nShe waited.\n\n* * *\n\nThe end & <more>.\n"
        client.post(f"/api/imports/{session['id']}/files", files={"file": ("Chapter 1.txt", text.encode())})
        proposal = client.get(f"/api/imports/{session['id']}").json()["proposal"]
        items = [
            {"index": item["index"], "chapter_number": item["chapter_number"]} for item in proposal["items"]
        ]
        committed = client.post(
            f"/api/imports/{session['id']}/commit",
            json={"destination": {"mode": "series", "series_name": "Glass Road"}, "items": items},
        )
        text_id = committed.json()["projects"][0]["id"]
        for layout in ("interleaved", "side-by-side"):
            # The endpoint itself refuses a bilingual EPUB that EPUBCheck rejects.
            response = client.get(f"/api/projects/{project_id}/export/epub-bilingual?layout={layout}")
            assert response.status_code == 200, response.text
            assert_valid(response.content)
            partial = client.get(
                f"/api/projects/{text_id}/export/epub-bilingual?layout={layout}&allow_source=true"
            )
            assert partial.status_code == 200, partial.text
            assert_valid(partial.content)
