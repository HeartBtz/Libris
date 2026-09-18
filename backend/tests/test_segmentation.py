import io
import re
import zipfile

from epubs import epub_bytes, xhtml
from lxml import etree

from app.engines.epub.book import parse_book, rebuild
from app.engines.epub.text import MARKER, extract_units, split_unit


def upper(text: str) -> str:
    return "".join(p if MARKER.fullmatch(p) else p.upper() for p in re.split(r"(⟦/?[tx]\d+⟧)", text))


def roundtrip(data: bytes, max_chars: int = 3500, language: str = "fr"):
    parsed = parse_book(data, max_chars)
    segments = [
        {"units": group, "translated_units": [{"id": u["id"], "text": upper(u["text"])} for u in group]}
        for chapter in parsed["chapters"]
        for group in chapter["groups"]
    ]
    return parsed, rebuild(data, segments, language)


def chapter(out: bytes) -> str:
    return zipfile.ZipFile(io.BytesIO(out)).read("OEBPS/c1.xhtml").decode()


def units_of(body: str) -> list[dict]:
    return extract_units(etree.fromstring(xhtml(body).encode()), "c1.xhtml")


def test_long_cjk_paragraph_is_split_on_sentence_punctuation():
    paragraph = "これは長い文章です。" * 900 + "「本当に？」と彼女は言った。"
    parsed, out = roundtrip(epub_bytes(f"<p>{paragraph}</p>"))
    parts = [u for c in parsed["chapters"] for g in c["groups"] for u in g if u["tag"] == "p"]
    assert len(parts) > 2 and max(len(u["text"]) for u in parts) <= 3500
    # Cuts fall right after a sentence mark, closing quotes stay with their sentence.
    assert all(u["text"].endswith(("。", "」と彼女は言った。")) for u in parts)
    assert "".join(u["text"] for u in parts) == paragraph
    assert paragraph.upper() in chapter(out)


def test_cjk_text_without_punctuation_is_still_bounded():
    pieces = split_unit({"id": "u", "text": "漢" * 9000}, 3500)
    assert [len(p["text"]) for p in pieces] == [3500, 3500, 2000]


def test_ruby_readings_are_kept_out_of_the_translation():
    text = units_of(
        "<p><ruby>漢字<rt>かんじ</rt></ruby>と<ruby>東京<rp>(</rp><rt>とうきょう</rt><rp>)</rp></ruby></p>"
    )[0]
    assert "かんじ" not in text["text"] and "とうきょう" not in text["text"] and "(" not in text["text"]
    _, out = roundtrip(epub_bytes("<p><ruby>Kanji<rt>kan</rt></ruby> text.</p>"))
    assert "<ruby>KANJI<rt>kan</rt></ruby> TEXT." in chapter(out)


def test_mixed_content_is_one_unit_per_run_of_text():
    body = '<div class="letter">Dear <b>Alice</b>, I write in haste<br/>and with sorrow. <div class="sig">Bob</div></div>'
    units = units_of(body)
    assert [u["text"] for u in units if u["kind"] != "text"] == [
        "Dear ⟦t0⟧Alice⟦/t0⟧, I write in haste⟦x1⟧and with sorrow. ",
        "Bob",
    ]
    _, out = roundtrip(epub_bytes(body))
    assert (
        '<div class="letter">DEAR <b>ALICE</b>, I WRITE IN HASTE<br/>AND WITH SORROW. <div class="sig">BOB</div></div>'
        in chapter(out)
    )


def test_text_after_a_comment_or_between_blocks_is_translated_in_order():
    body = "<div><!-- c -->After a comment.<p>para</p> tail after para</div><details><summary>S</summary>loose</details>"
    texts = [u["text"] for u in units_of(body) if u["kind"] != "text"]
    assert texts == ["⟦x0⟧After a comment.", "para", " tail after para", "S", "loose"]
    _, out = roundtrip(epub_bytes(body))
    assert "<div><!-- c -->AFTER A COMMENT.<p>PARA</p> TAIL AFTER PARA</div>" in chapter(out)


def test_svg_text_is_translated_and_preformatted_text_is_reported():
    body = (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 10 10"><title>Map</title>'
        '<text x="1" y="5">Cover <tspan>title</tspan></text></svg>'
        "<pre>keep   this</pre><p>Math <math xmlns='http://www.w3.org/1998/Math/MathML'><mi>x</mi></math>.</p>"
    )
    parsed, out = roundtrip(epub_bytes(body))
    assert '<text x="1" y="5">COVER <tspan>TITLE</tspan></text>' in chapter(out)
    assert "<pre>keep   this</pre>" in chapter(out) and "<title>Map</title>" in chapter(out)
    untranslated = parsed["info"]["untranslated"]
    assert {k: v["count"] for k, v in untranslated.items()} == {"svg": 1, "pre": 1, "math": 1}
    assert untranslated["pre"]["resources"] == ["OEBPS/c1.xhtml"]


def test_aria_label_is_translated():
    _, out = roundtrip(epub_bytes('<p aria-label="Aria text">Para.</p>'))
    assert 'aria-label="ARIA TEXT"' in chapter(out)
