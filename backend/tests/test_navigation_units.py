import pytest
from lxml import etree

from app.engines.epub.text import extract_units, outside_markers
from app.engines.quality.checks import validate_translation
from app.schemas import TranslationResult

NAV = b"""<html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops"><body>
<nav epub:type="toc"><h2>Contents</h2><ol><li>
  <a href="c1.xhtml">Chapter <em>One</em></a>
</li></ol></nav>
<nav epub:type="page-list" hidden="hidden"><h2>Pages</h2><ol><li><a href="c1.xhtml#p1">1</a></li></ol></nav>
<nav epub:type="landmarks"><ol><li><a epub:type="toc" href="#toc">Table of Contents</a></li></ol></nav>
</body></html>"""
NCX = b"""<ncx xmlns="http://www.daisy.org/z3986/2005/ncx/"><navMap><navPoint id="a"><navLabel><text>Chapter One</text>
</navLabel><content src="c1.xhtml"/></navPoint></navMap>
<pageList><pageTarget id="p1"><navLabel><text>1</text></navLabel><content src="c1.xhtml#p1"/></pageTarget></pageList></ncx>"""


def result(unit, text):
    return TranslationResult(units=[{"id": unit["id"], "text": text}])


def test_page_lists_are_not_translated_but_contents_and_landmarks_are():
    texts = [u["text"].strip() for u in extract_units(etree.fromstring(NAV), "EPUB/nav.xhtml")]
    assert "Contents" in texts and any("Table of Contents" in t for t in texts)
    assert not any(t in ("Pages", "1") or t.endswith("⟦t0⟧1⟦/t0⟧") for t in texts)
    ncx = [u["text"] for u in extract_units(etree.fromstring(NCX), "EPUB/toc.ncx")]
    assert ncx == ["Chapter One"]


def test_text_next_to_a_contents_link_is_refused():
    entry = next(u for u in extract_units(etree.fromstring(NAV), "EPUB/nav.xhtml") if u["tag"] == "li")
    assert entry["nav"] is True
    validate_translation([entry], result(entry, entry["text"].replace("Chapter", "Chapitre")))
    with pytest.raises(ValueError, match="sommaire"):
        validate_translation([entry], result(entry, entry["text"].rstrip() + " (relu)"))


def test_books_imported_before_the_flag_are_protected_too():
    legacy = {"id": "u", "text": "\n ⟦t0⟧Chapter 1⟦/t0⟧\n", "tag": "li", "resource": "OEBPS/Text/nav.xhtml"}
    with pytest.raises(ValueError, match="sommaire"):
        validate_translation([legacy], result(legacy, "\n ⟦t0⟧Chapitre 1⟦/t0⟧ bis\n"))
    story = {**legacy, "resource": "OEBPS/Text/chapter1.xhtml"}
    validate_translation([story], result(story, "\n ⟦t0⟧Chapitre 1⟦/t0⟧ bis\n"))


def test_ordinary_paragraphs_may_move_text_around_their_markup():
    assert outside_markers("a ⟦t0⟧b ⟦t1⟧c⟦/t1⟧⟦/t0⟧ d ⟦x2⟧ e") == "a  d  e"
    unit = {"id": "u", "text": "⟦t0⟧Hello⟦/t0⟧", "tag": "p", "resource": "EPUB/nav.xhtml", "nav": True}
    # Only entries whose source keeps everything inside the link are constrained; this one is, too.
    with pytest.raises(ValueError):
        validate_translation([unit], result(unit, "⟦t0⟧Bonjour⟦/t0⟧ !"))
    free = {"id": "u", "text": "Say ⟦t0⟧Hello⟦/t0⟧", "tag": "p", "resource": "EPUB/nav.xhtml", "nav": True}
    validate_translation([free], result(free, "Dis ⟦t0⟧bonjour⟦/t0⟧ !"))
