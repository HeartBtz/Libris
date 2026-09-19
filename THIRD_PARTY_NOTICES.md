# Third-party notices

Libris's own source code is licensed under AGPL-3.0-only. That license does not replace the licenses of the
components Libris uses: keep their notices when you redistribute images, binaries or dependency bundles.

## Dependencies

- **Interface:** React, React Flow (`@xyflow/react`), Vite, TypeScript and the other packages listed in
  `frontend/package-lock.json`. The Inter typeface (`@fontsource-variable/inter`) is distributed under the SIL
  Open Font License 1.1.
- **Server:** FastAPI, Uvicorn, Pydantic, SQLAlchemy, Alembic, psycopg, HTTPX, cryptography, EbookLib, lxml and
  the other packages listed in `backend/requirements.lock`. EbookLib is licensed under the AGPL.
- **Database:** the official PostgreSQL image.
- **EPUBCheck:** downloaded from the official W3C release during the image build, with pinned security updates
  applied by `scripts/harden_epubcheck.py`. It keeps its own license and notices.
- **Codex bridge (optional):** installs the upstream `@openai/codex` package. Its license, and the terms of the
  OpenAI or ChatGPT account you connect, are separate from Libris.

The installed packages' own license files are authoritative.

## Project assets

The Libris logos in `frontend/public/assets/` were supplied by the project owner. The name and logo do not allow
a fork to imply official endorsement. Screenshot text and test fixtures were written for this project; they
contain no commercial book.

## Not part of this repository

Translations, imported books, model weights, provider credentials and data stored in an external OpenViking
instance are not distributed with Libris.
