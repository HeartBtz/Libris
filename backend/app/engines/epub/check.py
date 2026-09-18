import json
import subprocess
import tempfile
import threading
from pathlib import Path

from app.config import settings

# One JVM per validation: unbounded, a handful of simultaneous exports takes the host's memory and
# cores away from the API and the worker.
_slots: threading.BoundedSemaphore | None = None
_slots_size = 0
_guard = threading.Lock()
QUEUE_TIMEOUT = 120


def slots() -> threading.BoundedSemaphore:
    global _slots, _slots_size
    size = settings().epubcheck_concurrency
    with _guard:
        if _slots is None or size != _slots_size:
            _slots, _slots_size = threading.BoundedSemaphore(size), size
        return _slots


def epubcheck(data: bytes) -> dict:
    jar = settings().epubcheck_jar
    if not jar:
        return {
            "available": False,
            "valid": None,
            "message": "EPUBCheck non configuré ; contrôles internes seuls.",
        }
    slot = slots()
    if not slot.acquire(timeout=QUEUE_TIMEOUT):
        raise ValueError("Trop de validations EPUB en cours sur le serveur ; réessayez dans un instant.")
    try:
        with tempfile.TemporaryDirectory() as directory:
            book, report = Path(directory) / "book.epub", Path(directory) / "report.json"
            book.write_bytes(data)
            try:
                result = subprocess.run(
                    ["java", f"-Xmx{settings().epubcheck_max_heap_mb}m", "-jar", jar, str(book), "--json", str(report)],
                    capture_output=True,
                    timeout=90,
                    check=False,
                )
            except subprocess.TimeoutExpired:
                raise ValueError("EPUBCheck n’a pas terminé en 90 secondes ; réessayez plus tard.") from None
            if not report.exists():
                raise ValueError("EPUBCheck n’a pas produit de rapport exploitable.")
            return {
                "available": True,
                "valid": result.returncode == 0,
                "report": json.loads(report.read_text()),
            }
    finally:
        slot.release()
