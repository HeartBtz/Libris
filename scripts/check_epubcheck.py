"""Exercise the patched EPUBCheck runtime on valid and invalid synthetic EPUBs."""

import subprocess
import tempfile
from pathlib import Path

from ebooklib import epub

with tempfile.TemporaryDirectory() as directory:
    for broken in (False, True):
        book = epub.EpubBook()
        book.set_identifier("libris-security-smoke")
        book.set_title("Synthetic EPUBCheck test")
        book.set_language("en")
        chapter = epub.EpubHtml(title="Chapter", file_name="chapter.xhtml", lang="en")
        chapter.content = (
            "<html><body><h1>Chapter</h1><p>Hello.</p>"
            + ('<a href="missing.xhtml">Missing</a>' if broken else "")
            + "</body></html>"
        )
        book.add_item(chapter)
        book.add_item(epub.EpubNav())
        book.add_item(epub.EpubNcx())
        book.toc = (chapter,)
        book.spine = ["nav", chapter]
        path = Path(directory) / "fixture.epub"
        epub.write_epub(str(path), book)
        result = subprocess.run(
            ["java", "-jar", "/opt/epubcheck-5.3.0/epubcheck.jar", str(path)],
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )
        output = result.stdout + result.stderr
        if (not broken and result.returncode != 0) or (
            broken and "RSC-007" not in output
        ):
            raise RuntimeError(output)
        if "Exception" in output or "NoSuchMethodError" in output:
            raise RuntimeError(output)
        print("Invalid EPUB rejected" if broken else "Valid EPUB accepted")
