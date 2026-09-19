# Development and releases

This page is for contributors and maintainers. It explains how to set up a development environment, run every
kind of test, how the CI/CD pipeline works, how a release is cut and deployed, and how translation quality is
evaluated. To run Libris rather than work on it, read the [Docker guide](docker.md).

## Repository layout

| Directory | Contents |
| --- | --- |
| `backend/` | FastAPI application (`app/`), persistent worker (`app/jobs/worker.py`), Alembic migrations, pytest suite |
| `frontend/` | React + TypeScript interface (`src/`), Playwright specs (`e2e/`) |
| `prompts/` | Versioned model instructions, copied into the image |
| `codex_bridge/` | Optional isolated Codex transport and its own image |
| `scripts/` | Installation, deployment, test and release utilities |
| `deploy/` | Production deployment, backup, restore and CI-runner maintenance procedures |
| `docs/` | Documentation; `docs/screenshots/` and `docs/openapi/` are generated (see [Screenshots](#screenshots) and [The OpenAPI description](#the-openapi-description)) |
| `examples/` | Example client of the automation API (Python, standard library only) |

How the pieces fit together is described in [architecture.md](architecture.md); the interface conventions are in
[design-system.md](design-system.md).

## Set up

Use **Python 3.13** and **Node.js 22**, the versions of the images and of CI.

```bash
python3 -m venv .venv
.venv/bin/pip install -r backend/requirements.lock
.venv/bin/pip install -e './backend[test]'
npm --prefix frontend ci
```

`backend/requirements.lock` pins every Python dependency with its hashes. After changing a pin, regenerate the
hashes with `python3 scripts/hash_lock.py backend/requirements.lock` (network needed); CI runs
`scripts/hash_lock.py --check` and refuses an unhashed pin. Add a dependency only when it is needed, say why in
the merge request, and update the matching lockfile.

## Run the application locally

The simplest loop runs the real stack with Docker Compose and the interface with Vite's development server.

1. Create a local configuration once: `python3 scripts/setup.py` writes a `.env` with generated secrets and never
   overwrites an existing one.
2. Add the Vite origin to `ALLOWED_ORIGINS`, for example
   `ALLOWED_ORIGINS=http://127.0.0.1:8088,http://127.0.0.1:5173`. `COOKIE_SECURE=true` works on `localhost` and
   `127.0.0.1`; set it to `false` only if you open the development server through a network address.
3. Build and start the stack from your working tree, under a local image name so that the published image is
   never overwritten:

   ```bash
   LIBRIS_IMAGE=libris:dev docker compose up -d --build
   ```

4. Start the interface with hot reload. Vite serves it on `http://127.0.0.1:5173` and forwards `/api` and
   `/health` to the API on port 8088:

   ```bash
   npm --prefix frontend run dev
   ```

Sign in with `BOOTSTRAP_USERNAME` and `BOOTSTRAP_PASSWORD` from `.env`. Every setting is described in
[configuration.md](configuration.md).

## Tests

### Backend

```bash
cd backend
../.venv/bin/ruff check app tests
../.venv/bin/pytest -q
```

The suite needs no service: it uses a temporary SQLite database and data directory, and mocked providers
(`tests/mock_server.py`, `respx`). It also checks the migrations: `tests/test_migrations.py` upgrades, downgrades
and upgrades the schema again.

Production runs PostgreSQL, so changes to queries, locking, concurrency or migrations must also pass on
PostgreSQL 17. Point the suite at a throwaway database with `LIBRIS_TEST_DATABASE_URL`:

```bash
docker run -d --name libris-test-db -p 127.0.0.1:5432:5432 --tmpfs /var/lib/postgresql/data \
  -e POSTGRES_DB=libris_test -e POSTGRES_USER=libris -e POSTGRES_PASSWORD=libris_test_password postgres:17-bookworm
cd backend
export DATABASE_URL=postgresql+psycopg://libris:libris_test_password@127.0.0.1:5432/libris_test
export SECRET_KEY=local-test-secret-key-with-more-than-32-characters
export BOOTSTRAP_PASSWORD=local-test-password-123456
../.venv/bin/alembic upgrade head && ../.venv/bin/alembic downgrade base \
  && ../.venv/bin/alembic upgrade head && ../.venv/bin/alembic check
LIBRIS_TEST_DATABASE_URL="$DATABASE_URL" ../.venv/bin/pytest -q
docker rm -f libris-test-db
```

Every migration must stay reversible down to an empty schema, and `alembic check` must find no difference
between the models and the migrations.

### The OpenAPI description

`docs/openapi/libris-v1.json` describes the automation API (`/api/v1`) and is generated from the code by
`backend/app/api/v1_openapi.py`. After adding or changing a `/api/v1` route, regenerate it and commit it:

```bash
.venv/bin/python scripts/export_openapi.py          # rewrite the file
.venv/bin/python scripts/export_openapi.py --check  # only compare (prints a diff)
```

`tests/test_openapi_v1.py` fails while the committed file differs from the code, checks that every `/api/v1`
operation is described with its scope and error responses, and compares the documented answers with real ones.
A new route needs nothing else to appear: its tag comes from its path, its summary from the first line of its
docstring, its scope from its `require(...)` dependency, and it gets the shared error responses. Describe what
FastAPI cannot see (a body read by hand, the fields of a dict answer, specific error codes) in
`v1_openapi.py`: `OPERATIONS` and `SCHEMAS`. `tests/test_example_client.py` runs `examples/libris_client.py`
against the API.

Tests marked `epubcheck` validate exports with the real EPUBCheck. They are skipped unless `EPUBCHECK_JAR` points
to an EPUBCheck JAR and `java` is on the `PATH`; `LIBRIS_REQUIRE_EPUBCHECK=1` turns a missing validator into a
failure. CI runs them inside the built image, which ships EPUBCheck 5.3.0.

### Frontend

```bash
npm --prefix frontend run build
```

The build runs the strict TypeScript check before Vite. Visible text goes through the translation helpers
described in [design-system.md](design-system.md#language); a new string needs its English entry.

### Browser tests (Playwright)

Specs are selected by a tag in their title:

| Tag | Needs | Where it runs |
| --- | --- | --- |
| none | nothing: the spec mocks the API with `page.route` | CI `frontend` job, locally against `vite preview` |
| `@journey` | a disposable stack with the synthetic model of `docker-compose.test.yml` | CI `e2e` job |
| `@integration` | a disposable stack holding the data created by `scripts/smoke.py` | by hand only |

Without `LIBRIS_E2E_URL`, `playwright.config.ts` leaves out `@integration` and `@journey`, so a plain run is
always safe on a workstation:

```bash
npm --prefix frontend run build
npm --prefix frontend exec playwright install chromium
npm --prefix frontend exec vite preview -- --host 127.0.0.1 --port 4173   # terminal 1
npm --prefix frontend run test:e2e                                         # terminal 2
```

The mocked specs open `http://127.0.0.1:4173` unless `SHOWCASE_URL` says otherwise.

To run `@journey` or `@integration` specs, point them at a **disposable** installation on a loopback address.
They never read the repository `.env`:

| Variable | Meaning |
| --- | --- |
| `LIBRIS_E2E_URL` | Base URL of the disposable installation, on `127.0.0.1`, `localhost` or `::1` |
| `LIBRIS_E2E_USERNAME`, `LIBRIS_E2E_PASSWORD` | Its administrator account |
| `LIBRIS_E2E_CONFIRM_DISPOSABLE` | Must be `1`: confirms that the target holds no real data |
| `LIBRIS_E2E_MOCK_LLM_URL` | `@journey` only: the synthetic model as the backend sees it (default `http://mock-llm:8091`) |
| `LIBRIS_E2E_PROJECT_ID` or `LIBRIS_E2E_STATE` | Workspace specs: the smoke project, or the state file written by `scripts/smoke.py` (default `/tmp/libris/epub-smoke.json`) |

### Full-stack smoke test

`scripts/smoke.py` drives a real Compose stack with the synthetic model: it imports a generated EPUB, analyses
and translates it over HTTP, pauses and resumes, kills the worker with SIGKILL and checks recovery, then exports
an EPUB and validates it with EPUBCheck. It reads the local account without printing secrets and refuses to run
without `--confirm-disposable`.

```bash
docker compose -p libris-smoke -f docker-compose.yml -f docker-compose.test.yml --profile test up -d --build
.venv/bin/python scripts/smoke.py --compose-project libris-smoke --confirm-disposable \
  --compose-file docker-compose.yml --compose-file docker-compose.test.yml
# ... run @integration specs against it if needed, then remove the test project:
.venv/bin/python scripts/smoke.py --compose-project libris-smoke --confirm-disposable --cleanup \
  --compose-file docker-compose.yml --compose-file docker-compose.test.yml
docker compose -p libris-smoke -f docker-compose.yml -f docker-compose.test.yml --profile test down --volumes
```

Related scripts, all for disposable installations only:

| Script | What it checks |
| --- | --- |
| `scripts/check_installation.py` | A fresh Compose installation on local port 4188 with a generated configuration and a random project name: login, health, migrations; then removes that stack and its volumes |
| `scripts/resilience_smoke.py` | Provider outage, manual pause, SIGTERM and retry on a synthetic project |
| `scripts/make_browser_fixtures.py` | Writes three small EPUBs under `/tmp/libris/` for browser tests |
| `scripts/cleanup_browser_fixtures.py` | Removes the known browser-test projects left by an interrupted run |
| `scripts/check_epubcheck.py` | Runs the bundled EPUBCheck on valid and invalid synthetic EPUBs (inside the image) |

Never point any of these at a production library.

### Screenshots

The images in `docs/screenshots/` come from `frontend/e2e/showcase.spec.ts`, which renders the real interface
against mocked API answers and fictional books. It uses no credentials and no model, checks the colour contrast
of both themes, and writes to `/tmp/libris/showcase` unless told otherwise. To refresh the documentation images:

```bash
cd frontend
npx vite build
CI=1 SHOWCASE_SCREENSHOT_DIR=../docs/screenshots npx playwright test e2e/showcase.spec.ts
```

`e2e/documentation.spec.ts` captures the same views from a disposable API-backed installation instead; it only
runs with `LIBRIS_DOCS_CAPTURE=1` and `LIBRIS_DOCS_CAPTURE_CONFIRM=disposable`. Look at every image before
committing it.

## CI/CD pipeline

GitLab is the canonical repository and runs the release pipeline. GitHub is a read-only push mirror that repeats
the checks and publishes GHCR images. Never push to the mirror directly: the next mirror update would overwrite
diverging refs.

### What runs when

| Pipeline | Jobs |
| --- | --- |
| Merge request, branch | `backend` (Ruff + pytest on SQLite), `backend-postgres` (migration round trip + pytest on PostgreSQL 17), `frontend` (build, `npm audit`, mocked Playwright specs), `e2e` (`@journey` specs against the Compose stack), `audit` (version consistency, hashed lockfiles, `pip-audit`, Gitleaks) |
| Default branch | the same, then `container-build`, `container-runtime`, `container-epubcheck`, `container-scan` and, once everything passed, `verified-image` |
| Release tag `vX.Y.Z` | `release-policy`, `release-images`, `container-runtime`, `container-scan`, `publish-gitlab`, `publish-dockerhub`, `release-gitlab`, `deploy-production`, and the manual `rollback-production` |

A merge request that changes nothing under `backend/`, `frontend/`, `codex_bridge/`, `prompts/`, `scripts/`,
`deploy/`, `examples/`, `docs/openapi/`, the Dockerfile, the Compose files or `.gitlab-ci.yml` only runs `audit`, which still checks the
version pins in the documentation.

### Images and verification

Only the protected default branch builds and pushes images, addressed by commit: `sha-<commit>` for the
application and `codex-sha-<commit>` for the Codex bridge. Every later job uses those exact digests:

- `container-runtime` starts the image and checks it;
- `container-epubcheck` runs the `epubcheck` tests inside the image;
- `container-scan` runs Trivy once per image: HIGH and CRITICAL findings with a fix fail the job, and the report
  is kept as a CycloneDX SBOM artifact;
- `verified-image` adds `verified-sha-<commit>` when the whole default-branch pipeline passed.

A tag pipeline does not test again. `release-policy` checks that the tag is on the default branch, that it
matches the version (`scripts/check_version.py`) and that `CHANGELOG.md` has its section. `release-images` waits
up to 20 minutes for the `verified-sha-<commit>` marker, since a tag is often pushed while the branch pipeline
is still running, then promotes those digests. If the branch pipeline failed, make it pass and retry
`release-images`.

A tag `vX.Y.Z` publishes:

| Destination | Tags | Published by |
| --- | --- | --- |
| GitLab Container Registry | `X.Y.Z`, `X.Y`, `latest` (and `codex-` variants) | `publish-gitlab` |
| Docker Hub `heartbtz/libris` | `X.Y.Z`, `X.Y`, `latest` | `publish-dockerhub`, which also updates the Docker Hub overview from [docker-hub.md](docker-hub.md) |
| GHCR `ghcr.io/heartbtz/libris` | version and SHA tags, with provenance and SBOM | the mirror's `.github/workflows/release.yml` |

`release-gitlab` creates the GitLab release; its notes are exactly the `CHANGELOG.md` section of that version
(`python3 scripts/release_notes.py vX.Y.Z` prints them). A retried job updates the existing release. The GitHub
release uses the same notes.

### CI runner hygiene

All jobs run on a shell runner and start their tools with `docker run`. Every container is named
`libris-ci-<role>-$CI_JOB_ID`, carries the label `libris-ci-job=$CI_JOB_ID` and runs under `--init`; every
command that can hang is wrapped in `timeout`, and every job has its own `timeout:`. Cancelling a job only kills
the Docker client, so each job's `after_script` (which also runs on cancellation) removes the containers with its
label, and `e2e` takes its Compose project `libris-e2e-$CI_JOB_ID` down with its volumes. As a last resort,
`audit` removes any CI container or `libris-e2e-*` stack older than two hours. To look by hand:
`docker ps --all --filter label=libris-ci-job`.

Volumes can still be left behind by a killed job. `deploy/libris-runner-prune` removes, on the runner host, unused
anonymous volumes and the volumes and networks of `libris-e2e-*` projects older than
`LIBRIS_PRUNE_MIN_AGE_HOURS` (default 6); it never touches named volumes of other projects, images or the build
cache. Install it as root on the runner host:

```bash
install -m 0755 deploy/libris-runner-prune /usr/local/sbin/libris-runner-prune
install -m 0644 deploy/libris-runner-prune.service deploy/libris-runner-prune.timer /etc/systemd/system/
libris-runner-prune --dry-run          # see what it would remove
systemctl daemon-reload && systemctl enable --now libris-runner-prune.timer
```

The timer runs hourly; `journalctl -u libris-runner-prune` shows what was removed.

### Required GitLab settings

- Protect `main` and tags matching `v*`.
- Enable the Container Registry and protect the `sha-*`, `codex-sha-*`, `verified-sha-*` and exact-version tags
  from being overwritten.
- Enable **Prevent outdated deployment jobs**.
- Keep the push mirror from GitLab to GitHub.
- For Docker Hub, add protected, masked CI/CD variables `DOCKERHUB_USERNAME` and `DOCKERHUB_TOKEN` (a token with
  read/write access to the repository). Without them `publish-dockerhub` is skipped; the GitLab registry and
  release still proceed. The predefined `CI_REGISTRY*` variables authenticate the GitLab registry.

Never commit a password, token, `.env`, registry credential or user book.

Dependabot version updates are disabled on the GitHub mirror, because merging there would diverge from GitLab.
Prepare dependency upgrades on GitLab; GitHub security alerts remain useful as advice.

## Releasing

Libris follows Semantic Versioning. While in `0.x`, any operationally breaking change is called out in
`CHANGELOG.md` and therefore in the release notes. **Pushing a tag deploys to production**: agree on the release
with the maintainer before tagging.

### Prepare

1. Set the new version everywhere `scripts/check_version.py` looks: `backend/pyproject.toml`,
   `backend/app/__init__.py`, `frontend/package.json` and its lockfile, `codex_bridge/package.json` and its
   lockfile, `codex_bridge/rpc.py`, the `LIBRIS_VERSION` argument of both Dockerfiles, `LIBRIS_IMAGE` in
   `.env.example`, `scripts/install-docker.sh`, and the `heartbtz/libris:X.Y.Z` pins in `README.md`,
   `docs/docker.md` and `docs/docker-hub.md`.
2. In `CHANGELOG.md`, rename `## [Unreleased]` to `## [X.Y.Z] - YYYY-MM-DD`. Check the notes with
   `python3 scripts/release_notes.py vX.Y.Z`.
3. Run `python3 scripts/check_version.py`, then the backend, PostgreSQL, frontend and installation checks above.
4. Merge to `main` and wait until the default-branch pipeline is green.

### Tag

```bash
VERSION=X.Y.Z
python3 scripts/check_version.py "v${VERSION}"
git tag -s "v${VERSION}" -m "Libris ${VERSION}"
git push origin "v${VERSION}"
```

Use an annotated unsigned tag (`git tag -a`) only when signing is not configured. Never move or reuse a release
tag.

### Verify

A green pipeline is not proof of a release. Check each destination on its own: GitLab registry and release,
Docker Hub tags and overview, the GitHub mirror, GHCR and the GitHub release. Pull a published digest, inspect
its OCI `version` and `revision` labels, and query `/health` from a disposable stack before announcing it.

### Production deployment

`deploy-production` runs automatically on a protected release tag once `release-images`, `container-runtime`,
`container-scan` and `publish-gitlab` have succeeded; a failed gate leaves production untouched. Deployments and
rollbacks are serialised by a `resource_group`. The job pulls the digest-pinned images on the production host
through a read-only registry identity and hands them to `deploy/libris-production-deploy`, installed on that host
as `/usr/local/sbin/libris-production-deploy`. The procedure:

1. refuses an older version, or a version number already used by another commit;
2. takes a consistent PostgreSQL dump while the application still runs (the five most recent are kept);
3. stops the API, worker and Codex bridge, installs the Compose file shipped in the image
   (`/app/deploy/docker-compose.yml`, keeping the replaced file as `docker-compose.yml.before-<version>`), runs
   the migrations and starts the API and bridge;
4. checks the exact version through `/health`, and only then restarts the worker, which resumes from its
   checkpoints;
5. keeps the replaced images as `previous-*` for a rollback and removes older Libris images.

If anything fails and the schema did not change, the previous images and Compose file are restored and verified.
After a schema change it leaves the services stopped with the pre-deployment dump rather than attempt an unsafe
downgrade. A healthy redeploy of the same commit is a no-op, unless the installed Compose file drifted.

The procedure is configured with environment variables whose defaults match the maintainer's host; set them for
another host:

| Variable | Meaning |
| --- | --- |
| `LIBRIS_PRODUCTION_BASE` | Working directory: Compose file, `current-*` markers, `backups/` (default `/opt/libris-production`) |
| `LIBRIS_PRODUCTION_COMPOSE_FILE` | Compose file it drives (default `$LIBRIS_PRODUCTION_BASE/docker-compose.yml`) |
| `LIBRIS_PRODUCTION_COMPOSE_OVERRIDE` | Optional override file for host-specific settings |
| `LIBRIS_PRODUCTION_SECRET_ENV` | The installation's `.env` |
| `LIBRIS_PRODUCTION_PROJECT` | Compose project name (default `epub-translator`) |
| `LIBRIS_PRODUCTION_HEALTH_URL` | URL of `/health` checked after the switch |
| `LIBRIS_PRODUCTION_REGISTRY_REPOSITORY` | Registry repository whose untagged pulls are pruned |

Other modes:

| Command | Effect |
| --- | --- |
| `libris-production-deploy --check-compose` | Compares the installed Compose file with the deployed version's; exits 1 and prints the diff on drift. Changes nothing |
| `libris-production-deploy --prune-images [--dry-run]` | Removes Libris images that are neither deployed, retained as `previous-*`, nor used by a container |
| `libris-production-deploy --rollback` | See below |

Change `docker-compose.yml` in the repository, never on the host. Host-specific values belong in the `.env`
(`BIND_ADDRESS`, `PORT`…) or in the override file. When disk space is short, delete old files inside `backups/`,
never the directory: without its Compose file the next deployment stops with `Production configuration is not
provisioned` (exit 65) before touching anything. Reinstall `/usr/local/sbin/libris-production-deploy` whenever
`deploy/libris-production-deploy` changes.

Updating an ordinary self-hosted installation does not use this procedure: see [operations.md](operations.md).

### Rollback

When a release deployed fine but misbehaves, run the manual `rollback-production` job of its tag pipeline. It
calls `libris-production-deploy --rollback`, which redeploys the retained `previous-*` images through the same
guarded procedure (dump, stop, migrate as a no-op, start, `/health`, worker restart). It only goes backwards, so
running it twice does not redeploy the faulty release.

It refuses, before touching anything, when the release added a migration: reverting an image does not revert a
schema. In that case:

1. Stop the application services:
   `docker compose --project-name <project> --env-file <.env> --file <compose file> --profile codex stop api worker codex`.
2. Restore the newest `backups/pre-<timestamp>-<commit>.dump` with `pg_restore --clean --if-exists --no-owner`
   inside the `database` container, as in [backup.md](backup.md).
3. Run `libris-production-deploy --rollback` again: the schema now matches.

A deployment never changes the books in `/data` or the `.env`; restore them from the scheduled backup only if they
were damaged. The next release deploys normally from its tag.

## Before the first public release of a fork

1. Review the whole Git history, not only the working tree, for secrets, private hostnames, books and test
   artifacts. Rotate any secret that was ever committed.
2. Confirm the rights to the logo and every included asset.
3. Push only the intended branch to the new repository. Never transfer a deployment `.env`, volumes or backups.
4. Enable private vulnerability reporting and branch protection, and run the CI on the new repository.
5. Do not announce images or releases before they exist.
6. If you run a modified Libris for users over a network, the AGPL requires you to offer them its corresponding
   source, for example through a visible link.

## Evaluating translation quality

Passing tests, EPUBCheck and automatic scores show that the output is well formed, not that the translation is
faithful. Before claiming a quality level for a language pair or a kind of book, run a documented evaluation.

### Corpus

Use a text you are allowed to use, several dozen chapters long, with a reference reviewed by a bilingual reader.
It must contain distant callbacks and variations in the source; repeating the same paragraph does not test
narrative memory. Include at least:

1. An invented name with typographic variants: no unjustified change of translation.
2. A character identified late: ambiguous pronouns stay ambiguous before the reveal.
3. An object given in chapter 1, reinterpreted in chapter 15, given again in chapter 30.
4. Formal and informal address, with a motivated and an unmotivated change of register.
5. An unreliable narrator: the characters' beliefs stay distinct from the facts.
6. A recurring idiom or joke: the effect stays consistent without artificial repetition.
7. A human correction in chapter 20 that must influence chapter 35 and remain restorable.
8. A sentence with emphasis, links and a note reference: meaning and markup preserved.

### Controlled comparison

Duplicate the same project before translation and keep the model, parameters and prompts identical. Compare the
`internal`, `openviking` and `hybrid` context engines. Keep the prompts actually sent, the retrieval choices and
the prompt versions.

Score separately, blind if possible: fidelity (omissions, additions), stability of names, voices, pronouns and
relations, handling of ambiguities and reveals, naturalness and literary effect, respect of human decisions, EPUB
structure and formatting. Measure human corrections per 1,000 words, terminology violations, wrongly resolved
references, useful and useless deep-retrieval calls, latency and token use. A model grading its own translation
does not replace this review.

### What the test suite already guards

Regression tests reproduce known failure cases on synthetic books: book text containing prompt delimiters,
series terminology (a term locked in volume 1 kept in volume 3 despite a different unlocked term in volume 2,
nothing leaking from later volumes or another owner), long CJK paragraphs split at sentence ends with ruby kept,
small context windows, right-to-left output and translation-memory reuse. See `tests/test_prompt_hardening.py`,
`test_series_conventions.py`, `test_segmentation.py`, `test_book_structure.py`, `test_small_windows.py`,
`test_rtl.py` and `test_translation_memory.py`. On a real book, `stats.translation_memory_reused` in the project
data gives the reuse rate.

To measure the prompt cost of a configuration without a real model, use `scripts/measure_prompt_cost.py`,
described in [operations.md](operations.md).

### Comparing the analysis modes

`scripts/evaluate_analysis_modes.py` analyses the same synthetic serial in the `strict` mode and in the
`parallel` mode (every passage reconciled, only the ambiguous ones, none) and scores the memory each leaves
against its ground truth: who each passage involves (pronoun referents included), late aliases resolved,
identities kept together in the registry, relations, glossary proposals, and identity links shown to a passage
before the text reveals them. `scripts/benchmark_analysis.py` measures the wall time, calls and tokens of the
analysis of a long serial for several thread counts. Both use `backend/tests/analysis_world.py` (the serial, its
ground truth and a simulated analyst that only knows what its prompt holds), which
`tests/test_parallel_analysis.py` also uses: the parallel mode must score at least as well as the strict one, show
no later fact to any passage, give the same memory for any number of threads and resume at every stage without
asking the model twice. The results are in [architecture](architecture.md#evaluation). They measure what each
mode delivers to each call, not a real model: before changing the default mode, compare both on a real book with
the protocol above (duplicate the project, same model and prompts, then `strict` against `parallel`).

```bash
python scripts/evaluate_analysis_modes.py --chapters 60 --volumes 2 --seeds 1,2,3
python scripts/benchmark_analysis.py --chapters 400 --latency 0.2 --threads 1,4,8,16
```
