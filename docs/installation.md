# Installing Libris

For a first installation using the published Docker Hub image, start with the [beginner Docker guide](docker.md) or run `./scripts/install-docker.sh`. This page documents the configuration and operational details behind that installer.

## 1. Local configuration

Run `python3 scripts/setup.py` in the repository root. It generates independent application, database, bootstrap and Codex bridge secrets. Do not commit `.env` or replace it during upgrades. For an existing installation the script intentionally fails instead of replacing keys.

| Setting                                    | Meaning                                                                  |
| ------------------------------------------ | ------------------------------------------------------------------------ |
| `BIND_ADDRESS`                             | Interface on the Docker host; defaults to `127.0.0.1`                    |
| `PORT`                                     | Published HTTP port; defaults to `8088`                                  |
| `ALLOWED_ORIGINS`                          | Comma-separated browser origins including scheme and port, without paths |
| `COOKIE_SECURE`                            | Use `true` behind HTTPS, `false` for local HTTP                          |
| `SESSION_DURATION_HOURS`                   | Lifetime of a login session; defaults to `24`                            |
| `FORWARDED_ALLOW_IPS`                      | Address of your reverse proxy, so that real client addresses are used (login throttling, logs) |
| `SECRET_KEY`                               | Persistent application encryption key; retain with backups. If it changes, stored provider keys can no longer be read: Libris asks you to enter them again |
| `BOOTSTRAP_USERNAME`, `BOOTSTRAP_PASSWORD` | First administrator, created only on an empty database                   |
| `POSTGRES_PASSWORD`                        | PostgreSQL initialization and connection password                        |
| `CODEX_BRIDGE_TOKEN`                       | Private API-to-bridge authentication secret                              |
| `OPENVIKING_URL`, `OPENVIKING_API_KEY`     | Optional external memory service                                         |
| `MAX_UPLOAD_MB`, `MAX_UNPACKED_MB`, `MAX_ENTRIES` | Largest EPUB or project archive accepted (`60`), its unpacked size (`300`) and file count (`5000`). A project archive that would exceed them is refused at export, with the setting to raise |
| `IMPORT_MAX_FILES`, `IMPORT_MAX_SESSION_MB`, `IMPORT_SESSION_HOURS` | Guided import: files per import (`500`), total size of one import (`2048`), and how long uploaded files wait for confirmation under `DATA_DIR/staging` (`24`) |
| `TEXT_CHAPTER_MAX_CHARS` | Longest TXT or JSON chapter accepted, in characters after decoding; `2000000` |
| `PASSAGE_MAX_CHARS` | Longest passage (the text of one model call) for what is imported from now on; `3500` (500–20000). A volume can choose its own; existing books keep their cut — see the operations guide, "Coût par passage" |
| `REVIEW_MODE` | `separate` (default): at high and maximum quality a review call, then a revision call when it finds something; `fused`: one call does both. A volume can choose its own |
| `API_MAX_PAYLOAD_MB`, `API_MAX_CHAPTERS`, `API_RATE_LIMIT_PER_MINUTE` | Automation API (`/api/v1`, see [the API reference](api.md)): largest request (JSON, EPUB or TXT files) accepted with a Bearer token (defaults to `MAX_UPLOAD_MB`), chapters per request (`2000`) and calls per token and per minute in each API process (`120`, `0` disables the limit) |
| `API_RESULT_MAX_WAIT_SECONDS`, `API_REQUEST_STALL_MINUTES`, `API_REQUEST_MAX_HOURS`, `DELIVERY_REPAIR_ATTEMPTS` | Automation API delivery: longest `?wait=` long poll (`60`), how long a request's job may stay paused/blocked/waiting before the request fails (`360`), longest life of a request (`168`), EPUBCheck repair rounds of a delivered EPUB (`3`) |
| `API_WEBHOOK_HOSTS`, `API_WEBHOOK_PRIVATE_NETWORKS`, `API_WEBHOOK_SECRET`, `API_WEBHOOK_MAX_ATTEMPTS`, `API_WEBHOOK_TIMEOUT_SECONDS` | Webhooks of the automation API: allowed hosts (empty: webhooks refused), private networks allowed anyway (CIDRs), global HMAC secret (32 characters at least; a token's own secret wins), calls per request (`6`) and timeout of each (`10`) |
| `IMPORT_CONFIRM_LOW_CONFIDENCE` | Guided import: `true` asks a person to confirm volume or chapter numbers guessed with low confidence; default `false` accepts the best guess and records the reason |
| `MAX_COMPRESSION_RATIO`                    | Whole-archive compression ratio above which an EPUB of more than 8 MiB unpacked is refused as a possible zip bomb; `100` |
| `EVENT_STREAMS_PER_USER`, `EVENT_STREAMS_TOTAL` | Live progress connections held open at once per account (`4`) and per API process (`100`); beyond them the API answers 429 |
| `PREVIEW_CACHE_MB`                         | Memory kept for the unpacked books of recent chapter previews; `64`, `0` disables the cache |
| `OPENAPI_ENABLED`                          | `/openapi.json`, served to signed-in users only; `false` removes it |
| `EPUBCHECK_CONCURRENCY`, `EPUBCHECK_MAX_HEAP_MB` | Simultaneous EPUBCheck validations per process (`2`) and memory ceiling of each one (`1024`) |
| `WORKER_HEARTBEAT_SECONDS`                 | Interval at which a running job renews its 60 s lease; `2` (1–20)        |
| `WORKER_BOOK_PARALLELISM`                  | Passages of one book translated or reviewed at once; `0` (default) follows the provider's `max_concurrency`, shared between the books using it, `1` processes one passage at a time (0–16) |
| `MEMORY_CATALOG_INTERVAL_SECONDS`          | Interval of the external-memory catalogue refresh; `60` (10–86400)       |
| `PROVIDER_RECOVERY_BASE_SECONDS`, `PROVIDER_RECOVERY_MAX_SECONDS` | First and longest wait before retrying an unavailable provider; `60` and `3600` |
| `AUTOPILOT_ENABLED`                        | Whole-book launches run without any human step ([autopilot](autopilot.md)); `true`. A project's `autopilot` setting or a launch's `"autopilot": false` overrides it |
| `AUTOPILOT_MAX_ROUNDS`                     | Rounds of recovery → final review → AI arbitration before what stays open is settled; `3` (1–10) |
| `AUTOPILOT_FALLBACK_PROVIDERS`             | Providers (names or ids, comma-separated) tried after the job's and the project's own fallbacks; empty |
| `AUTOPILOT_OUTAGE_MAX_RETRIES`, `AUTOPILOT_OUTAGE_MAX_WAIT_SECONDS` | Waits for an unavailable provider before switching to the next one; `5` and `3600`. With no provider left the job ends `failed` |
| `AUTOPILOT_GLOSSARY_MIN_CONFIDENCE`, `AUTOPILOT_IDENTITY_MIN_CONFIDENCE`, `AUTOPILOT_BIBLE_MIN_COVERAGE`, `AUTOPILOT_STALE_MIN_COVERAGE` | Thresholds (0–1) of the automatic glossary, series identity, Book Bible and outdated-context decisions; `0.75`, `0.8`, `0.8`, `0.5` |
| `RETENTION_REQUEST_BODIES_DAYS`, `RETENTION_EVENTS_DAYS`, `RETENTION_OUTBOX_SENT_DAYS`, `RETENTION_BIBLE_REVISIONS`, `RETENTION_JOB_STATE_DAYS`, `RETENTION_REQUEST_ROWS_DAYS`, `RETENTION_RESULTS_DAYS` | Automatic clean-up of diagnostic data and delivered API result files (`30`, `7`, `7`, `20`, `30`, `0`, `30`; `0` disables a rule) — see the operations guide |
| `METRICS_TOKEN`                            | Empty by default: `GET /metrics` answers 404. Set a random value of at least 24 characters (`openssl rand -hex 32`) to let Prometheus scrape it with `Authorization: Bearer <token>` — see the operations guide |

Changing bootstrap credentials does not reset an existing account. Changing the database password in `.env` does not change an initialized PostgreSQL role's password.

## 2. Start and verify

Published image:

```bash
docker compose pull database migrate api worker
docker compose up -d --no-build --wait database migrate api worker
docker compose ps
curl --fail http://127.0.0.1:8088/health
```

Source build for development:

```bash
LIBRIS_IMAGE=epub-translator:local docker compose up -d --build --wait
```

The migration service exits successfully after upgrading the database. This is expected. API health does not prove that a model provider or OpenViking is available: test those separately in Settings.

## 3. LAN access

Set these example values to your own server address:

```dotenv
BIND_ADDRESS=0.0.0.0
PORT=8088
ALLOWED_ORIGINS=http://your-server:8088
COOKIE_SECURE=false
```

Apply with `docker compose up -d --wait`. Restrict network access with your host firewall. `ALLOWED_ORIGINS` is an origin check, not a firewall.

## 4. HTTPS reverse proxy

Use your own DNS name and a reverse proxy that supports long-lived HTTP/SSE connections. For a proxy on the same host, keep the loopback bind and configure:

```dotenv
ALLOWED_ORIGINS=https://books.example.com
COOKIE_SECURE=true
```

Forward `books.example.com` to `http://127.0.0.1:8088`. Set `FORWARDED_ALLOW_IPS` in `.env` to the address of your proxy (for example `FORWARDED_ALLOW_IPS=172.18.0.1`) so that Libris sees each visitor's real address through `X-Forwarded-For`; the failed-login throttle is keyed on that address and on the account name. Preserve the browser Origin header, allow your chosen upload size (default application limit: 60 MiB), disable buffering for `/api/projects/*/events`, and allow long request timeouts. Set TLS certificates through your proxy's normal mechanism. A containerized proxy must use an accessible host address or a shared Docker network; its own `localhost` is not the Libris host.

## 5. Connect a model

OpenAI-compatible providers accept an endpoint such as `https://provider.example/v1`. A local inference server can be reached through a LAN address accessible from the containers. `localhost` in a container refers to that container, not the host. For Linux host inference, an optional Compose override can add `extra_hosts: ["host.docker.internal:host-gateway"]` to **both api and worker**; use that hostname in the provider URL.

Declare only the JSON/reasoning capabilities your provider supports, and set a context window and output budget within its actual limits. A successful model-list test alone does not verify structured translation responses. Try a small EPUB first.

No external memory service is required. Choose `internal` memory in the book configuration for a standalone installation. Configure OpenViking only if you have your own instance.

### Optional Codex / ChatGPT

New setup files include a private bridge token. Existing installations can use `python3 scripts/enable_codex.py` as described in [Codex connection](codex.md).

```bash
docker compose --profile codex build codex
docker compose --profile codex up -d --no-build --wait
```

Complete the official login flow in the provider settings. Keep the bridge port private and include its state volume in backups if you want to preserve authentication. Account eligibility and service limits are determined by the provider.

## Updates

Back up first. Pause jobs in the UI, then:

```bash
git pull --ff-only
./scripts/install-docker.sh
```

Run `./scripts/install-docker.sh --profile codex` instead if you use Codex. Check health and resume jobs. Database migrations run before the API starts. Do not use `down -v`. To roll back a schema-changing update, restore the matching database/files/configuration backup as well as the old application revision.

The Compose project name remains `epub-translator` for compatibility with existing installations. Changing it or using a different `-p` name selects different volumes; do not rename a deployed project casually.

## Backups and recovery

For daily, verified backups to another host with rotation and a restore test, install the scheduled backup described in [backup and restore](backup.md). For a one-off manual backup, pause jobs and stop `api` and `worker` during capture. Store backups outside the repository with restricted permissions.

```bash
mkdir -p backups
chmod 700 backups
docker compose stop api worker
docker compose exec -T database pg_dump -U translator -d translator -Fc > backups/database.dump
docker compose run --rm --no-deps -T api tar -C /data -czf - . > backups/books.tar.gz
cp .env backups/config.env
chmod 600 backups/*
docker compose up -d --wait
```

If using Codex, also back up the `codex-state` named volume with the bridge stopped. External OpenViking requires its own backup procedure.

Restore into an isolated installation with the **same application revision and saved `.env`**. Start the database, restore the dump using `pg_restore -U translator -d translator --clean --if-exists`, extract the books archive into the `/data` volume, then start the application. Test login, book preview, provider decryption and EPUB export before using the recovered instance. A backup file's presence alone is not a restore test.

Example restoration in an empty Compose project:

```bash
cp /secure/backup/config.env .env
docker compose up -d --wait database
docker compose exec -T database pg_restore -U translator -d translator --clean --if-exists < /secure/backup/database.dump
docker compose run --rm --no-deps -T api tar -C /data -xzf - < /secure/backup/books.tar.gz
docker compose up -d --no-build --wait
docker compose exec -T api alembic check
```

Do not run `pg_restore --clean` against an installation you intend to preserve.

## Removal

Stop Libris while retaining every named volume:

```bash
docker compose down
```

After a verified external backup, permanent removal of application data is explicit and destructive:

```bash
docker compose down --volumes --remove-orphans
docker image rm epub-translator:local epub-translator-codex:local  # optional
```

Review `docker volume ls` before and after removal. A different Compose project name uses different volumes and must be removed separately.

## Troubleshooting

- **403 on login:** browser origin must exactly match an `ALLOWED_ORIGINS` entry. Check scheme, hostname and port.
- **Login does not persist:** `COOKIE_SECURE=true` requires HTTPS.
- **502/provider error:** check inference availability from both API and worker; a proxy's `/models` endpoint may work while inference fails.
- **Job queued:** check that provider's “Livres simultanés” limit and active jobs.
- **Switch provider mid-book:** pause, save the new provider, then resume.
- **Export rejected:** inspect missing/refused passages and EPUBCheck output; partial source-retaining export is explicit.

Use bounded logs for diagnosis: `docker compose logs --since=5m api worker`. Review logs before sharing; never publish `.env`, cookies, authentication headers or complete model requests containing private books.
