import atexit
import io
import os
import shutil
import tempfile
from pathlib import Path

import pytest
from ebooklib import epub

temporary = tempfile.TemporaryDirectory(prefix="libris-tests-")
atexit.register(temporary.cleanup)
os.environ["DATABASE_URL"] = os.environ.get(
    "LIBRIS_TEST_DATABASE_URL", "sqlite:///" + temporary.name + "/tests.db"
)
os.environ["DATA_DIR"] = temporary.name
os.environ["SECRET_KEY"] = "test-only-secret-key-with-more-than-32-characters"
os.environ["BOOTSTRAP_PASSWORD"] = "test-password-123456789"
# Only tests marked `epubcheck` run the real validator (see the `epubcheck_jar` fixture): one JVM per
# export would make the whole suite several times slower.
EPUBCHECK_JAR = os.environ.get("EPUBCHECK_JAR", "")
os.environ["EPUBCHECK_JAR"] = ""

from app import models  # noqa: E402,F401
from app.config import settings  # noqa: E402
from app.db import Base, SessionLocal, engine  # noqa: E402


@pytest.fixture(autouse=True)
def database():
    from app.main import login_attempts

    login_attempts.clear()
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    settings().prepare()
    yield


@pytest.fixture
def epubcheck_jar(monkeypatch):
    available = bool(EPUBCHECK_JAR) and Path(EPUBCHECK_JAR).is_file() and shutil.which("java") is not None
    if not available:
        if os.environ.get("LIBRIS_REQUIRE_EPUBCHECK") == "1":
            pytest.fail("EPUBCHECK_JAR must name an EPUBCheck JAR and java must be installed")
        pytest.skip("EPUBCheck is not installed (set EPUBCHECK_JAR)")
    monkeypatch.setattr(settings(), "epubcheck_jar", EPUBCHECK_JAR)
    return EPUBCHECK_JAR


@pytest.fixture
def book_bytes():
    book = epub.EpubBook()
    book.set_identifier("test-book")
    book.set_title("The Silver Tower")
    book.set_language("en")
    book.add_author("Test Author")
    one = epub.EpubHtml(title="Chapter One", file_name="chapter1.xhtml", lang="en")
    one.content = """<html xmlns="http://www.w3.org/1999/xhtml"><body>
    <h1 id="chapter-one">Chapter One</h1>
    <p id="opening">Alice entered the <em>Silver Tower</em> and stopped.</p>
    <p>“Hello,” Bob said. “This pendant belonged to my mother.”</p>
    <p>She remembered <a href="chapter2.xhtml#revelation">his promise</a>.
    <img src="images/pixel.png" alt="A silver pendant"/></p>
    <p translate="no">Do not translate this inscription.</p>
    <p>Before <span translate="no">UNTOUCHABLE</span> after.</p>
    </body></html>"""
    two = epub.EpubHtml(title="Chapter Two", file_name="chapter2.xhtml", lang="en")
    two.content = """<html xmlns="http://www.w3.org/1999/xhtml"><body>
    <h1 id="revelation">Chapter Two</h1><p>Alice discovered that Bob was her brother.</p>
    <p>“The Silver Tower remembers,” she whispered.</p></body></html>"""
    book.add_item(one)
    book.add_item(two)
    book.add_item(
        epub.EpubItem(
            uid="image", file_name="images/pixel.png", media_type="image/png", content=b"fake-image"
        )
    )
    book.add_item(
        epub.EpubItem(uid="css", file_name="style.css", media_type="text/css", content=b"p { color: black; }")
    )
    book.toc = (one, two)
    book.add_item(epub.EpubNcx())
    book.add_item(epub.EpubNav())
    book.spine = ["nav", one, two]
    output = io.BytesIO()
    epub.write_epub(output, book)
    return output.getvalue()


@pytest.fixture
def seeded(book_bytes):
    from app.api.projects import import_book
    from app.models import Provider, User
    from app.security import password_hash

    with SessionLocal() as db:
        user = User(username="tester", password_hash=password_hash("test-password-123456789"), admin=True)
        db.add(user)
        provider = Provider(
            name="Mock",
            base_url="https://llm.test/v1",
            model="test-model",
            capabilities={"supports_json_schema": True},
            context_window=64000,
        )
        db.add(provider)
        db.flush()
        project = import_book(db, user.id, book_bytes)
        project.provider_id = provider.id
        project.context_backend = "internal"
        project.quality = "fast"
        db.commit()
        return project.id, user.id, provider.id
