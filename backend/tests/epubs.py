"""Hand-made EPUB archives: exact control over markup that ebooklib would normalise."""

import io
import zipfile

CONTAINER = (
    '<?xml version="1.0"?><container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container">'
    '<rootfiles><rootfile full-path="OEBPS/content.opf" media-type="application/oebps-package+xml"/>'
    "</rootfiles></container>"
)


def xhtml(body: str, head: str = "<title>Doc title</title>", attrs: str = "") -> str:
    return (
        '<?xml version="1.0" encoding="utf-8"?><html xmlns="http://www.w3.org/1999/xhtml" '
        f'xmlns:epub="http://www.idpf.org/2007/ops"{attrs}><head>{head}</head><body>{body}</body></html>'
    )


NAV = xhtml(
    '<nav epub:type="toc"><h1>Contents</h1><ol><li><a href="c1.xhtml">Chapter One</a></li></ol></nav>'
)
NCX = (
    '<?xml version="1.0"?><ncx xmlns="http://www.daisy.org/z3986/2005/ncx/" version="2005-1"><head>'
    '<meta name="dtb:uid" content="urn:{uid}"/></head><docTitle><text>Source Title</text></docTitle>'
    '<navMap><navPoint id="n1" playOrder="1"><navLabel><text>Chapter One</text></navLabel>'
    '<content src="c1.xhtml"/></navPoint></navMap></ncx>'
)


def epub_files(
    files: dict[str, str],
    uid: str = "x",
    language: str = "en",
    spine: list[tuple[str, str]] | None = None,
    metadata: str = "",
    ncx: bool = False,
    version: str = "3.0",
) -> bytes:
    """files: name relative to OEBPS -> XHTML text; spine: (name, itemref attributes)."""
    names = [n for n in files if n != "nav.xhtml"]
    spine = spine if spine is not None else [(n, "") for n in names]
    ident = {n: f"i{i}" for i, n in enumerate(names)}
    items = '<item id="nav" href="nav.xhtml" media-type="application/xhtml+xml" properties="nav"/>'
    items += "".join(f'<item id="{ident[n]}" href="{n}" media-type="application/xhtml+xml"/>' for n in names)
    if ncx:
        items += '<item id="ncx" href="toc.ncx" media-type="application/x-dtbncx+xml"/>'
    refs = "".join(f'<itemref idref="{ident[n]}"{a}/>' for n, a in spine)
    opf = (
        f'<?xml version="1.0" encoding="utf-8"?><package xmlns="http://www.idpf.org/2007/opf" version="{version}" '
        'unique-identifier="uid"><metadata xmlns:dc="http://purl.org/dc/elements/1.1/">'
        f'<dc:identifier id="uid">urn:{uid}</dc:identifier><dc:title>Source Title</dc:title>'
        f"<dc:language>{language}</dc:language><dc:creator>Au Thor</dc:creator>{metadata}"
        '<meta property="dcterms:modified">2020-01-01T00:00:00Z</meta></metadata>'
        f"<manifest>{items}</manifest><spine{' toc="ncx"' if ncx else ''}>{refs}</spine></package>"
    )
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w") as archive:
        archive.writestr("mimetype", "application/epub+zip", compress_type=zipfile.ZIP_STORED)
        archive.writestr("META-INF/container.xml", CONTAINER)
        archive.writestr("OEBPS/content.opf", opf)
        archive.writestr("OEBPS/nav.xhtml", files.get("nav.xhtml", NAV))
        if ncx:
            archive.writestr("OEBPS/toc.ncx", NCX.format(uid=uid))
        for name in names:
            archive.writestr(f"OEBPS/{name}", files[name])
    return out.getvalue()


def epub_bytes(body: str, uid: str = "x", **options) -> bytes:
    return epub_files({"c1.xhtml": xhtml(body)}, uid=uid, **options)
