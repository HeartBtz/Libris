"""Writes the OpenAPI description of the automation API to docs/openapi/libris-v1.json.

    python3 scripts/export_openapi.py           # regenerate the file after changing /api/v1
    python3 scripts/export_openapi.py --check   # CI: fail when the committed file is out of date

Needs the backend's dependencies (the same environment as its tests). Nothing is read from the
database or the environment: the document only depends on the code.
"""

import argparse
import difflib
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TARGET = ROOT / "docs/openapi/libris-v1.json"
SCRATCH = tempfile.TemporaryDirectory(prefix="libris-openapi-")


def render() -> str:
    # Importing the application reads its settings: give it throwaway values, never a real database.
    scratch = SCRATCH.name
    os.environ.update(
        DATABASE_URL=f"sqlite:///{scratch}/unused.db",
        DATA_DIR=scratch,
        SECRET_KEY="openapi-export-only-secret-key-not-used",
    )
    sys.path.insert(0, str(ROOT / "backend"))
    from app.api.v1_openapi import document_text
    from app.main import app

    return document_text(app)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--check", action="store_true", help="compare instead of writing")
    parser.add_argument("--output", type=Path, default=TARGET)
    args = parser.parse_args()
    text = render()
    if args.check:
        current = args.output.read_text(encoding="utf-8") if args.output.exists() else ""
        if current == text:
            print(f"{args.output.relative_to(ROOT)} is up to date.")
            return 0
        diff = difflib.unified_diff(
            current.splitlines(), text.splitlines(), "committed", "generated", lineterm="", n=2
        )
        print("\n".join(list(diff)[:80]))
        print(
            f"\n{args.output.relative_to(ROOT)} does not match the code of /api/v1. "
            "Run `python3 scripts/export_openapi.py` and commit the file.",
            file=sys.stderr,
        )
        return 1
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(text, encoding="utf-8")
    print(f"Wrote {args.output.relative_to(ROOT)}.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
