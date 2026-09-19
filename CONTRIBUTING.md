# Contributing to Libris

Thank you for helping. This page explains what a good change looks like and how to get it merged. The technical
details (setup, tests, CI, releases) are in [docs/development.md](docs/development.md).

## Before you start

- Read [docs/architecture.md](docs/architecture.md) to see how the pipeline, the worker and the memory fit
  together.
- For a large refactor, a new dependency or a license question, open an issue and discuss it with the
  maintainer first.
- Usage questions belong in the channels described in [SUPPORT.md](SUPPORT.md). Security problems must be
  reported privately, as described in [SECURITY.md](SECURITY.md).

## Rules the code must keep

- **Human work is never overwritten.** A human correction, validation or locked term always wins over a model
  result, including one that arrives while the model is still running.
- **Jobs are resumable.** Anything a job does must survive a restart, a pause or a crash without redoing
  finished work or applying a stale result.
- **Context never leaks forward.** A volume only uses the memory of earlier volumes, and a book never sees
  another owner's data.
- **The code is the reference.** When you change a setting, an endpoint or a visible label, update the page that
  documents it in the same merge request.

## Checks to run

Use Python 3.13 and Node.js 22. At least:

```bash
(cd backend && ../.venv/bin/ruff check app tests && ../.venv/bin/pytest -q)
npm --prefix frontend run build
python3 scripts/check_version.py
```

Changes to queries, locking, concurrency or migrations must also pass on PostgreSQL; interface changes should
pass the mocked Playwright specs. [docs/development.md](docs/development.md#tests) shows how to run both.

Tests that need a running installation (`@journey`, `@integration`, `scripts/smoke.py` and the other smoke
scripts) create and delete data. Run them only against a disposable installation, never against a real
library.

## Merge requests

Describe the problem, the change, how you verified it and any migration or configuration impact. Add
screenshots for visible changes. Keep each merge request focused on one topic and write commit messages in
English, in the Conventional Commits style (`fix: …`, `feat: …`, `docs: …`).

Never commit build outputs, datasets, keys, `.env` files, logs, cookies or books, even fictional ones that are
not part of the test fixtures.

GitLab is the canonical repository; GitHub is a mirror, so pull requests opened there are applied on GitLab by
the maintainer.

By participating, you agree to follow the [code of conduct](CODE_OF_CONDUCT.md).
