import io
import zipfile

import pytest

from app.engines.epub import inspect_archive, parse_book, rebuild
from app.engines.epub.archive import relative_resource, xml
from app.engines.epub.text import (
    apply_unit,
    extract_units,
    group_units,
    linearize,
    plain,
    restore_missing_codes,
    validate_codes,
)


def identity_segments(parsed):
    return [
        {"units": group, "translated_units": [{"id": u["id"], "text": u["text"]} for u in group]}
        for chapter in parsed["chapters"]
        for group in chapter["groups"]
    ]


def test_roundtrip_preserves_resources_spine_links_and_structure(book_bytes):
    parsed = parse_book(book_bytes)
    assert parsed["title"] == "The Silver Tower"
    assert parsed["info"]["images"] == 1
    before = inspect_archive(book_bytes)
    output = rebuild(book_bytes, identity_segments(parsed), "fr", "La Tour d’argent")
    after = inspect_archive(output)
    assert before.keys() == after.keys()
    for name in before:
        if name.endswith((".css", ".png")):
            assert before[name] == after[name]
        if name.endswith((".xhtml", ".ncx")):
            a, b = xml(before[name]), xml(after[name])
            assert [n.tag for n in a.iter()] == [n.tag for n in b.iter()]
            for attr in ("id", "href", "src"):
                assert a.xpath(f"//@{attr}") == b.xpath(f"//@{attr}")
            assert list(a.itertext()) == list(b.itertext())
    a, b = xml(before[parsed["info"]["opf"]]), xml(after[parsed["info"]["opf"]])
    assert a.xpath("//*[local-name()='itemref']/@idref") == b.xpath("//*[local-name()='itemref']/@idref")
    with zipfile.ZipFile(io.BytesIO(output)) as archive:
        assert archive.infolist()[0].filename == "mimetype"
        assert archive.infolist()[0].compress_type == zipfile.ZIP_STORED


def test_inline_phrase_translated_as_whole():
    root = xml(b'<p>Hello <em>beautiful</em> world <a href="#a"><strong>again</strong></a>.</p>')
    value, _ = linearize(root)
    translated = (
        value.replace("Hello ", "Bonjour ")
        .replace("beautiful", "joli")
        .replace(" world ", " monde ")
        .replace("again", "encore")
    )
    unit = extract_units(root, "book.xhtml")[0]
    apply_unit(root, unit, translated)
    assert root.find("em").text == "joli"
    assert root.find("a").get("href") == "#a"
    assert "".join(root.itertext()) == "Bonjour joli monde encore."


@pytest.mark.parametrize("name", ["../evil", "/etc/passwd", "a/../../evil", "C:/evil", "a\\evil"])
def test_zip_traversal(name, book_bytes):
    out = io.BytesIO(book_bytes)
    with zipfile.ZipFile(out, "a") as archive:
        archive.writestr(name, "bad")
    with pytest.raises(ValueError):
        inspect_archive(out.getvalue())


def test_duplicate_zip_names(book_bytes):
    out = io.BytesIO(book_bytes)
    with zipfile.ZipFile(out, "a") as archive:
        archive.writestr("mimetype", "application/epub+zip")
    with pytest.raises(ValueError, match="dupliqués"):
        inspect_archive(out.getvalue())


def test_entity_expansion_refused():
    with pytest.raises(ValueError, match="entités"):
        xml(b'<!DOCTYPE a [<!ENTITY x SYSTEM "file:///etc/passwd">]><a>&x;</a>')


def test_ignore_nontranslatable(book_bytes):
    parsed = parse_book(book_bytes)
    text = " ".join(u["text"] for c in parsed["chapters"] for g in c["groups"] for u in g)
    assert "UNTOUCHABLE" not in text
    assert "Do not translate this inscription" not in text
    assert "A silver pendant" in text


@pytest.mark.parametrize("target", ["Bonjour world", "Bonjour ⟦t0⟧monde⟦/t0⟧⟦x1⟧", "bad\x00"])
def test_bad_codes_rejected(target):
    with pytest.raises(ValueError):
        validate_codes("Hello ⟦t0⟧world⟦/t0⟧", target)


def test_restore_missing_codes_uses_unchanged_text_boundaries():
    assert restore_missing_codes(
        "Ils ne se seraient ⟦t0⟧pas⟦/t0⟧ précipités.",
        "Ils n’auraient pas dû se précipiter.",
    ) == "Ils n’auraient ⟦t0⟧pas⟦/t0⟧ dû se précipiter."
    assert restore_missing_codes(
        "Ces citoyens ⟦t0⟧⟦/t0⟧ordinaires peuvent voir.",
        "Ces citoyens ordinaires et moi pouvons voir.",
    ) == "Ces citoyens ⟦t0⟧⟦/t0⟧ordinaires et moi pouvons voir."


def test_restore_missing_codes_preserves_formatting_around_replacement():
    assert restore_missing_codes(
        "La ⟦t0⟧lumière⟦/t0⟧ brille.",
        "Le feu brille.",
    ) == "Le ⟦t0⟧feu⟦/t0⟧ brille."


def test_restore_missing_codes_rebuilds_a_partial_marker_sequence():
    current = "⟦t0⟧Ils comprennent⟦/t0⟧ ⟦t1⟧pas⟦/t1⟧⟦t2⟧ encore.⟦/t2⟧"
    candidate = "⟦t0⟧Ils comprennent⟦/t0⟧ vraiment pas encore."
    repaired = restore_missing_codes(current, candidate)
    assert repaired is not None
    validate_codes(current, repaired)
    assert plain(repaired) == "Ils comprennent vraiment pas encore."


def test_long_inline_paragraph_split_reversible():
    root = xml(
        (
            "<p>“"
            + "A long sentence with dialogue. " * 30
            + "<em>remember</em> "
            + "Next sentence. " * 40
            + "”</p>"
        ).encode()
    )
    units = extract_units(root, "c.xhtml")
    groups = group_units(units, 200)
    parts = [u for g in groups for u in g]
    assert len(groups) > 5
    assert "".join(u["text"] for u in parts) == units[0]["text"]
    for u in parts:
        assert len(u["text"]) <= 200


def test_relative_links_bounded():
    assert relative_resource("EPUB/text/ch1.xhtml", "../images/a.png") == "EPUB/images/a.png"
    with pytest.raises(ValueError):
        relative_resource("ch1.xhtml", "../../etc/passwd")
    with pytest.raises(ValueError):
        relative_resource("ch1.xhtml", "https://evil.test/a.png")
