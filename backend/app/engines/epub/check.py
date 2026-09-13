import json
import subprocess
import tempfile
from pathlib import Path

from app.config import settings


def epubcheck(data: bytes) -> dict:
    jar = settings().epubcheck_jar
    if not jar:
        return {
            "available": False,
            "valid": None,
            "message": "EPUBCheck non configuré ; contrôles internes seuls.",
        }
    with tempfile.TemporaryDirectory() as directory:
        book, report = Path(directory) / "book.epub", Path(directory) / "report.json"
        book.write_bytes(data)
        result = subprocess.run(
            ["java", "-jar", jar, str(book), "--json", str(report)],
            capture_output=True,
            timeout=90,
            check=False,
        )
        if not report.exists():
            raise ValueError("EPUBCheck n’a pas produit de rapport exploitable.")
        return {"available": True, "valid": result.returncode == 0, "report": json.loads(report.read_text())}
