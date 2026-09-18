import io
import zipfile

from epubs import epub_bytes
from test_segmentation import roundtrip

BODY = (
    '<p dir="ltr">Hello there.</p><p lang="en" xml:lang="en">English again.</p>'
    '<p lang="la" xml:lang="la" dir="ltr">Alea iacta est.</p>'
    '<p>She said <span lang="de" xml:lang="de">Guten Tag</span>.</p>'
)


def files(out: bytes) -> tuple[str, str]:
    archive = zipfile.ZipFile(io.BytesIO(out))
    return archive.read("OEBPS/c1.xhtml").decode(), archive.read("OEBPS/content.opf").decode()


def test_arabic_target_turns_documents_and_spine_right_to_left():
    _, out = roundtrip(epub_bytes(BODY), language="ar")
    chapter, opf = files(out)
    assert 'lang="ar" xml:lang="ar" dir="rtl"' in chapter
    assert '<p dir="rtl">HELLO THERE.</p>' in chapter
    # Text that was in the source language is now Arabic; quotations in other languages stay as they are.
    assert '<p lang="ar" xml:lang="ar">ENGLISH AGAIN.</p>' in chapter
    assert '<p lang="la" xml:lang="la" dir="ltr">ALEA IACTA EST.</p>' in chapter
    assert '<span lang="de" xml:lang="de">GUTEN TAG</span>' in chapter
    assert 'page-progression-direction="rtl"' in opf


def test_hebrew_source_translated_into_french_turns_left_to_right():
    body = '<p dir="rtl" lang="he">שלום</p>'
    _, out = roundtrip(epub_bytes(body, language="he"), language="fr")
    chapter, opf = files(out)
    assert '<p dir="ltr" lang="fr">' in chapter
    assert 'dir="rtl"' not in chapter and "page-progression-direction" not in opf


def test_epub2_never_receives_a_page_progression_direction():
    _, out = roundtrip(epub_bytes(BODY, version="2.0"), language="ar")
    assert "page-progression-direction" not in files(out)[1]
