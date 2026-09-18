import io
import zipfile

import pytest

from app.engines.epub.archive import numeric_entities, xml
from app.engines.epub.book import parse_book, rebuild


def rewrite(data: bytes, change) -> bytes:
    output = io.BytesIO()
    with zipfile.ZipFile(io.BytesIO(data)) as source, zipfile.ZipFile(output, "w") as target:
        for info in source.infolist():
            content = change(info.filename, source.read(info))
            if content is not None:
                kind = zipfile.ZIP_STORED if info.filename == "mimetype" else zipfile.ZIP_DEFLATED
                target.writestr(info.filename, content, kind)
    return output.getvalue()


def test_named_html_entities_become_characters():
    doctype = b'<!DOCTYPE html PUBLIC "-//W3C//DTD XHTML 1.1//EN" "http://www.w3.org/TR/xhtml11/DTD/xhtml11.dtd">'
    root = xml(doctype + b'<html xmlns="http://www.w3.org/1999/xhtml"><body><p>A&nbsp;B &eacute; &amp; &lt;</p></body></html>')
    assert "".join(root.itertext()) == "A B é & <"
    assert numeric_entities(b"&amp; &bogus; &hellip;") == b"&amp; &bogus; &#8230;"


def test_entity_declarations_are_still_refused():
    with pytest.raises(ValueError):
        xml(b'<!DOCTYPE r [<!ENTITY x "boom">]><r>&x;</r>')
    with pytest.raises(Exception):  # noqa: B017, PT011 - an unknown entity stays a parse error
        xml(b"<r>&bogus;</r>")


def test_epub2_with_nbsp_is_imported_and_rebuilt(book_bytes):
    def change(name, content):
        if name.endswith("chapter1.xhtml"):
            return content.replace(b"Alice entered", b"Alice&nbsp;entered")
        return content

    data = rewrite(book_bytes, change)
    parsed = parse_book(data)
    texts = [u["text"] for c in parsed["chapters"] for g in c["groups"] for u in g]
    assert any("Alice entered" in text for text in texts)
    assert zipfile.ZipFile(io.BytesIO(rebuild(data, [], "fr"))).testzip() is None


def test_a_missing_resource_outside_the_spine_does_not_block_the_book(book_bytes):
    data = rewrite(book_bytes, lambda name, content: None if name.endswith("pixel.png") else content)
    parsed = parse_book(data)
    assert parsed["title"] == "The Silver Tower" and parsed["author"] == "Test Author" and parsed["language"] == "en"
    with zipfile.ZipFile(io.BytesIO(rebuild(data, [], "fr"))) as archive:
        opf = archive.read(next(n for n in archive.namelist() if n.endswith(".opf")))
    assert b"pixel.png" not in opf


def test_a_missing_spine_document_is_named(book_bytes):
    data = rewrite(book_bytes, lambda name, content: None if name.endswith("chapter2.xhtml") else content)
    with pytest.raises(ValueError, match="chapter2.xhtml"):
        parse_book(data)


@pytest.mark.parametrize("encoding", ["utf-8", "utf-16", "utf-16-le", "utf-16-be"])
def test_entity_declarations_are_refused_in_any_encoding(encoding):
    declared = "UTF-8" if encoding == "utf-8" else "UTF-16"
    document = (
        f'<?xml version="1.0" encoding="{declared}"?>'
        '<!DOCTYPE r [<!ENTITY a "AAAAAAAAAA"><!ENTITY b "&a;&a;&a;&a;&a;&a;&a;&a;&a;&a;">]>'
        '<r title="&b;&b;&b;">text</r>'
    )
    data = document.encode(encoding)
    if encoding in ("utf-16-le", "utf-16-be"):
        data = ("\ufeff" + document).encode(encoding)
    with pytest.raises(ValueError, match="entités"):
        xml(data)


def test_an_ordinary_doctype_is_still_accepted():
    doctype = b'<!DOCTYPE html PUBLIC "-//W3C//DTD XHTML 1.1//EN" "http://www.w3.org/TR/xhtml11/DTD/xhtml11.dtd">'
    assert xml(doctype + b'<html xmlns="http://www.w3.org/1999/xhtml"><body><p>ok</p></body></html>') is not None
    assert xml(b"<!DOCTYPE html><html><body/></html>") is not None
