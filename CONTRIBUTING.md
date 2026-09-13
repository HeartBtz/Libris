# Contributing

Read the README and architecture guide first. Keep changes focused and preserve the sequential processing and human-edit protections of each book.

## Local checks

Use Python 3.13 and Node.js 22, matching the container and CI toolchains. Install dependencies and run the checks in the README. Backend tests use a disposable temporary SQLite database and mocked providers; production uses PostgreSQL, so concurrency/migration changes also require PostgreSQL verification.

## Browser screenshots

`frontend/e2e/showcase.spec.ts` renders the real interface with synthetic API fixtures. It uses no login credentials, book data or inference. Run against Vite preview on a disposable local port:

```bash
npm --prefix frontend run build
npm --prefix frontend exec playwright install chromium
# Terminal 1
npm --prefix frontend exec vite preview -- --host 127.0.0.1 --port 4173
# Terminal 2
SHOWCASE_URL=http://127.0.0.1:4173 npm --prefix frontend run test:e2e -- showcase.spec.ts
```

The screenshots under `docs/screenshots/` are generated assets. Inspect them before committing. Do not replace them with screenshots of a user's library or credentials.

Other E2E/smoke scripts operate on a running installation and may create/delete test projects. Run them only in an isolated test deployment, following the French user guide. Do not point them at production.

`python3 scripts/check_installation.py` tests a fresh Compose installation on local port 4188, with a generated configuration and random project name. It checks bootstrap login, health and migrations, then removes only that temporary stack and its volumes. It requires Docker and a free port 4188.

## Pull requests

Describe the problem, change, verification and any migration impact. Include UI screenshots when useful. Do not commit generated build outputs, datasets, keys, logs, cookies or personal books. Discuss large refactors and license changes with the maintainer first.
