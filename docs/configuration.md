# Configuration reference

This page is for administrators. It lists every setting Libris reads: the environment variables in `.env`, and
the settings an administrator can change at run time from **Settings** in the web interface. For each one you
will find its default, what it does and when you would change it.

If you are installing Libris for the first time, you do not need any of this yet: `scripts/setup.py` (run by the
installer) writes a working `.env`. Start with the [Docker guide](docker.md) and come back here when you want to
open Libris to your network, tune limits or change a default.

## How configuration works

- **`.env` is the only file you edit.** It sits next to `docker-compose.yml`, is created by
  `python3 scripts/setup.py` from `.env.example`, and is read by the `api`, `worker` and `migrate` containers.
  Keep it private (mode `0600`) and never commit it.
- **Names are case-insensitive**, but this page uses the upper-case form found in `.env.example`. A variable that
  is absent from `.env` takes the default shown here.
- **Apply a change** by recreating the containers:

  ```bash
  docker compose up -d --no-build --wait
  ```

  Every container reads the environment only when it starts. Run the same command with `--profile codex` if you
  use the Codex bridge.
- **Invalid values stop the application.** Each numeric setting has an allowed range (shown below). A value out
  of range, a `SECRET_KEY` shorter than 32 characters, or a `METRICS_TOKEN` / `API_WEBHOOK_SECRET` that is set but
  too short prevents the API and the worker from starting; `docker compose logs api` names the setting.
- **Some settings can be overridden in the interface.** Autopilot, webhooks, automatic recovery, OpenViking
  memory and SearXNG search have a page under **Settings**. There, a saved value wins over the environment. See
  [Settings changed in the interface](#settings-changed-in-the-interface).

## Installation and network

These variables are read by Docker Compose and the web server rather than by the application code.

| Variable | Default | What it does | When to change it |
| --- | --- | --- | --- |
| `LIBRIS_IMAGE` | `heartbtz/libris:<version>` in the generated `.env` | Application image used by `api`, `worker` and `migrate`. The installer pins the exact release. | To upgrade or downgrade deliberately. Avoid `latest` in production. `epub-translator:local` selects a local build. |
| `LIBRIS_CODEX_IMAGE` | `epub-translator-codex:local` | Image of the optional Codex bridge, built from source. | Rarely; only if you build or tag the bridge image yourself. |
| `LIBRIS_ENV_FILE` | `.env` | File passed to the containers as their environment. | To keep the configuration outside the repository, e.g. `LIBRIS_ENV_FILE=/etc/libris/libris.env docker compose --env-file /etc/libris/libris.env up -d`. |
| `POSTGRES_PASSWORD` | generated | Password of the `translator` PostgreSQL role. Required: Compose refuses to start without it. | Never after the first start. Changing it in `.env` does not change the password already stored in the database volume. |
| `BIND_ADDRESS` | `127.0.0.1` | Host interface on which the web port is published. | `0.0.0.0` to reach Libris from other machines on a private network. Keep `127.0.0.1` behind a reverse proxy on the same host. |
| `PORT` | `8088` | Host port of the web interface and API. | When `8088` is already taken. Update `ALLOWED_ORIGINS` to match. |
| `FORWARDED_ALLOW_IPS` | unset (only `127.0.0.1` is trusted) | Addresses of reverse proxies whose `X-Forwarded-For` / `X-Forwarded-Proto` headers are trusted, comma-separated. Read by the web server (Uvicorn). | Behind a reverse proxy: set it to the proxy's address as seen from the container (for example the Docker bridge gateway `172.18.0.1`) so logs and the failed-login throttle see real client addresses. |

Compose also sets `DATABASE_URL`, `DATA_DIR=/data` and `TMPDIR=/data/tmp` for the application containers; do not
put them in `.env` for a Docker installation.

## Security and access

| Variable | Default | What it does | When to change it |
| --- | --- | --- | --- |
| `SECRET_KEY` | generated (required, at least 32 characters) | Encrypts provider API keys, the OpenViking key and webhook secrets stored in the database. | Never. Keep it with your backups. If it changes, stored keys can no longer be read: Libris asks you to enter each provider key again and ignores unreadable saved secrets. |
| `BOOTSTRAP_USERNAME` | `admin` | Name of the first administrator account. | Before the first start, if you want another name. |
| `BOOTSTRAP_PASSWORD` | generated (at least 12 characters) | Password of the first administrator. Used only when the database has no account at all; the API refuses to start on an empty database without it. | Before the first start. Afterwards, change passwords in the interface: editing `.env` does not touch existing accounts. |
| `ALLOWED_ORIGINS` | `http://localhost:8088,http://127.0.0.1:8088` | Browser origins (scheme, host and port, no path) allowed to send changes. Requests with any other `Origin` get 403. Comma-separated; spaces around each origin are ignored. | Whenever users reach Libris through another address: `http://your-server:8088` on a LAN, `https://books.example.com` behind HTTPS. It is an origin check, not a firewall. |
| `COOKIE_SECURE` | `true` | Marks the session cookie `Secure`: browsers send it only over HTTPS, and to `http://localhost` / `http://127.0.0.1` on the Libris machine itself, which current browsers treat as secure. | Keep `true` behind HTTPS and for local use. Set `false` only when users open Libris over plain HTTP on a network address (`http://192.168.1.10:8088`, `http://your-server:8088`): there the browser drops a `Secure` cookie and sign-in seems to do nothing. |
| `SESSION_DURATION_HOURS` | `24` (1–2160) | Lifetime of a sign-in session. | Longer for a private single-user instance, shorter on shared machines. |
| `OPENAPI_ENABLED` | `true` | Serves the API description at `/openapi.json`, to signed-in users only. | `false` if you do not want the API schema exposed at all. |
| `METRICS_TOKEN` | empty (endpoint disabled) | Enables `GET /metrics` for Prometheus. Scrapers must send `Authorization: Bearer <token>`. At least 24 characters when set. | To monitor Libris; generate one with `openssl rand -hex 32`. See [operations](operations.md). |

## Storage and paths

The defaults match the supplied image. Change them only when you run the backend outside Docker.

| Variable | Default | What it does |
| --- | --- | --- |
| `DATABASE_URL` | `sqlite:////data/app.db` (Compose sets PostgreSQL) | SQLAlchemy database address. Docker installations always use the bundled PostgreSQL. |
| `DATA_DIR` | `/data` | Root of the books volume: `books/`, `sources/`, `projects/`, `exports/`, `staging/`, `results/` and `tmp/` are created in it at start-up. |
| `EPUBCHECK_JAR` | set by the image to the bundled EPUBCheck | Path of the EPUBCheck jar. Empty: exports are checked by Libris' internal checks only, and reports say EPUBCheck is not configured. |
| `FRONTEND_DIR` | `/app/frontend/dist` | Built web interface served by the API. |
| `PROMPT_DIR` | `/app/prompts` | Directory of the built-in prompt templates. Edited prompts are stored in the database, not here. |

## Uploads and imports

| Variable | Default | What it does | When to change it |
| --- | --- | --- | --- |
| `MAX_UPLOAD_MB` | `60` | Largest request body from the web interface: one uploaded EPUB, text file or project archive. | For very large books or archives. Your reverse proxy must accept the same size. |
| `MAX_UNPACKED_MB` | `300` | Largest total unpacked size of an EPUB or project archive. | With `MAX_UPLOAD_MB`, for large illustrated books. |
| `MAX_ENTRIES` | `5000` | Most files inside one EPUB or project archive. | Rarely; for books made of thousands of small files. |
| `MAX_COMPRESSION_RATIO` | `100` (10–100000) | An EPUB that unpacks to more than 8 MiB with a higher overall compression ratio is refused as a possible zip bomb. | Only if a legitimate book is refused for this reason. |
| `IMPORT_MAX_FILES` | `500` (1–5000) | Files in one guided import. | To import a very long series of chapter files at once. |
| `IMPORT_MAX_SESSION_MB` | `2048` | Total size of the files of one guided import. | With `IMPORT_MAX_FILES`. |
| `IMPORT_SESSION_HOURS` | `24` (1–720) | How long uploaded files wait for confirmation in `DATA_DIR/staging` before they are deleted. | If people leave imports unconfirmed for longer. |
| `IMPORT_CONFIRM_LOW_CONFIDENCE` | `false` | `false`: when a volume or chapter number is guessed with low confidence, the best guess is kept and the reason recorded. `true`: the import assistant asks a person to confirm it. | `true` if you prefer to check every uncertain numbering by hand. |
| `TEXT_CHAPTER_MAX_CHARS` | `2000000` (1000–50000000) | Longest single text chapter (TXT file or JSON chapter), in characters after decoding. | Rarely; for whole books delivered as a single chapter. |

A project archive that would exceed `MAX_UPLOAD_MB` or `MAX_UNPACKED_MB` is refused at export time, with the
setting to raise, because it could not be imported again.

## Translation pipeline

| Variable | Default | What it does | When to change it |
| --- | --- | --- | --- |
| `PASSAGE_MAX_CHARS` | `3500` (500–20000) | Longest passage, the unit of text sent in one model call. Applies to volumes and chapters imported afterwards; existing books keep their cut. A volume can set its own. | Longer passages spread the fixed context of each call over more text and lower the cost; see [cost control](operations.md#cost-control) before going beyond 8000. |
| `REVIEW_MODE` | `separate` | At high and maximum quality: `separate` reviews a passage, then revises it in a second call when the review finds something; `fused` reviews and corrects in one call. A volume can choose its own. | `fused` to save calls, once you have checked the quality on your books. |
| `FINAL_REVIEW_ENABLED` | `true` | Runs the final AI review of whole-book translations and automation requests. | `false` to skip that stage everywhere (cheaper, less thorough). A single API request can also leave it out. |
| `WORKER_BOOK_PARALLELISM` | `0` (0–16) | Passages of one book translated or reviewed at the same time. `0` follows the provider's **Concurrent books** capacity, shared by the books using it; `1` processes one passage at a time. | `1` for providers that struggle with parallel calls; a small number to leave capacity to other books. |
| `WORKER_HEARTBEAT_SECONDS` | `2` (1–20) | How often a running job renews its 60-second lease and checks for pause or cancel. | Normally never. |
| `PROVIDER_RECOVERY_BASE_SECONDS` | `60` | First wait before retrying a provider that is unavailable (network error, timeout, HTTP 429 or 5xx). Later waits double. | Replaced by the **Automatic recovery** delay while one is saved in the interface. |
| `PROVIDER_RECOVERY_MAX_SECONDS` | `3600` | Longest wait between two retries. A provider's `Retry-After` can lengthen it, up to 24 hours. | Lower it to retry more often during long outages. |

## Autopilot

The autopilot takes a launched book from import to export without a human step; see [autopilot](autopilot.md).
These variables are defaults that an administrator can override in **Settings › Autopilot**.

| Variable | Default | What it does |
| --- | --- | --- |
| `AUTOPILOT_ENABLED` | `true` | Default for new whole-book launches, from the interface and the API. A volume's own autopilot setting, or `"autopilot": false` in a launch, overrides it. |
| `AUTOPILOT_MAX_ROUNDS` | `3` (1–10) | Rounds of recovery, final review and AI arbitration before the remaining open points are settled. |
| `AUTOPILOT_FALLBACK_PROVIDERS` | empty | Providers (names or ids, comma-separated) tried in order when the job's provider is down or fails a passage, after the volume's own fallback providers. |
| `AUTOPILOT_OUTAGE_MAX_RETRIES` | `5` (1–100) | Waits for an unavailable provider before switching to the next fallback. With none left, the job ends `failed`. |
| `AUTOPILOT_OUTAGE_MAX_WAIT_SECONDS` | `3600` (0–604800) | Longest total wait for an unavailable provider before switching. |
| `AUTOPILOT_GLOSSARY_MIN_CONFIDENCE` | `0.75` (0–1) | Confidence a proposed glossary term needs to be accepted automatically; below it the proposal is rejected. |
| `AUTOPILOT_IDENTITY_MIN_CONFIDENCE` | `0.8` (0–1) | Confidence a proposed series identity link (same character across volumes) needs; below it the proposal is rejected. |
| `AUTOPILOT_BIBLE_MIN_COVERAGE` | `0.8` (0–1) | Coverage a Book Bible update needs to replace the current one; below it the Bible is left as it is. |
| `AUTOPILOT_STALE_MIN_COVERAGE` | `0.5` (0–1) | Coverage a refreshed chapter context needs to replace an outdated one; below it the old context is kept. |

Change the thresholds when the autopilot accepts too much (raise them) or leaves too many decisions unmade (lower
them).

## Automation API

Limits of the automation API (`/api/v1`), used by scripts and other applications with API tokens. See the
[API reference](api.md).

| Variable | Default | What it does | When to change it |
| --- | --- | --- | --- |
| `API_MAX_PAYLOAD_MB` | empty (uses `MAX_UPLOAD_MB`) | Largest request body (JSON, EPUB or text files) accepted with a Bearer token. | When integrations send bigger books than people upload. |
| `API_MAX_CHAPTERS` | `2000` (1–100000) | Chapters in one request. | For very long books sent as JSON. |
| `API_RATE_LIMIT_PER_MINUTE` | `120` (0–100000) | Calls per token and per minute, counted in each API process. `0` removes the limit. | To throttle or free a busy integration. |
| `API_RESULT_MAX_WAIT_SECONDS` | `60` (0–600) | Longest `?wait=` a client may ask for when polling a result. | To allow longer long-polling. |
| `API_REQUEST_STALL_MINUTES` | `360` | A request whose job stays paused, blocked or waiting this long fails, with the reason. | Longer if your provider has long planned outages. |
| `API_REQUEST_MAX_HOURS` | `168` (1–8760) | A request still unfinished after this long fails. No request stays running forever. | Longer for very large books on slow providers. |
| `DELIVERY_REPAIR_ATTEMPTS` | `3` (1–10) | Rounds of automatic repair when a delivered EPUB fails EPUBCheck, before the request fails. | Rarely. |

## Webhooks

Webhooks notify an integration when an API request finishes. They are **off** until at least one host is
allowed. These variables are defaults that an administrator can override in **Settings › Automation API**.

| Variable | Default | What it does |
| --- | --- | --- |
| `API_WEBHOOK_HOSTS` | empty (webhooks refused) | Hosts a `callback_url` may name, comma-separated. `*.example.org` allows every subdomain of `example.org` (not `example.org` itself). |
| `API_WEBHOOK_PRIVATE_NETWORKS` | empty | Private networks (CIDR, e.g. `10.0.0.0/8`) that callbacks may reach anyway. By default a callback that resolves to a private, loopback or reserved address is refused. |
| `API_WEBHOOK_SECRET` | empty | Global HMAC signing secret, at least 32 characters. A token's own webhook secret wins over it. |
| `API_WEBHOOK_MAX_ATTEMPTS` | `6` (1–20) | Delivery attempts per request. |
| `API_WEBHOOK_TIMEOUT_SECONDS` | `10` (1–60) | Timeout of each call. |

## Memory and web search

| Variable | Default | What it does | When to change it |
| --- | --- | --- | --- |
| `OPENVIKING_URL` | empty | Address of an external OpenViking memory service. Empty: books use Libris' internal memory. | Only if you run OpenViking; see [OpenViking](openviking.md). Can be set in **Settings › Memory · OpenViking** instead. |
| `OPENVIKING_API_KEY` | empty | Key for OpenViking. | With `OPENVIKING_URL`. |
| `OPENVIKING_ROOT_URI` | `viking://resources/epub-translator` | Root under which Libris publishes its resources in OpenViking. | To share one OpenViking server between several Libris installations. |
| `OPENVIKING_CLEANUP_ON_DELETE` | `false` | `true`: deleting a volume or a series also removes its OpenViking documents; the worker does it after the deletion, with retries (see [cleanup](openviking.md#cleanup-of-deleted-volumes-and-series)). | To reclaim space on the OpenViking server. Can be switched in **Settings › Memory · OpenViking** instead. |
| `MEMORY_CATALOG_INTERVAL_SECONDS` | `60` (10–86400) | How often the worker refreshes the published book catalogue in external memory. | Higher to reduce load on OpenViking. |
| `SEARXNG_URL` | empty (search off) | SearXNG instance used for terminology searches during the final review. Setting it turns search on. | To let the final review look terms up on the web. Search terms are sent to your instance and its upstream engines. |

## Codex bridge

Used only by the optional **Codex · ChatGPT account** provider; see [Codex](codex.md).

| Variable | Default | What it does |
| --- | --- | --- |
| `CODEX_BRIDGE_TOKEN` | generated | Private secret between the API and the bridge container (at least 32 characters, or the bridge refuses to start). `python3 scripts/enable_codex.py` adds it to an older `.env`. |
| `CODEX_BRIDGE_URL` | `http://codex:8092` | Address of the bridge on the Compose network. |

## Server resources

| Variable | Default | What it does | When to change it |
| --- | --- | --- | --- |
| `EVENT_STREAMS_PER_USER` | `4` (1–100) | Live progress connections (one per browser tab open on a book) per account. Beyond it the API answers 429 and asks to close tabs. | If people legitimately keep many books open. |
| `EVENT_STREAMS_TOTAL` | `100` (1–10000) | Live progress connections for the whole API process. | On instances with many simultaneous users. |
| `PREVIEW_CACHE_MB` | `64` (0–4096) | Memory kept for the unpacked books behind recent chapter previews. `0` disables the cache. | Lower on small machines, higher if previews of large books are slow. |
| `EPUBCHECK_CONCURRENCY` | `2` (1–16) | EPUBCheck validations run at once in each process (one Java process each). | Higher on machines with spare cores and memory. |
| `EPUBCHECK_MAX_HEAP_MB` | `1024` (128–16384) | Memory ceiling of each EPUBCheck run. | Higher if validation of very large books fails; lower on small machines. |

## Data retention

The worker cleans up diagnostic data once at start-up and then every hour. `0` disables a rule. What each rule
keeps, and how to measure it before applying, is explained in [operations](operations.md#data-retention).

| Variable | Default | What it removes |
| --- | --- | --- |
| `RETENTION_REQUEST_BODIES_DAYS` | `30` | Prompt, raw response and context trace of finished model requests older than this. The request row, tokens, cost and cached answer stay. |
| `RETENTION_REQUEST_ROWS_DAYS` | `0` (keep) | Whole request rows older than this, once counted in the daily usage totals. Statistics stay right; the response cache and the request inspector lose those rows. `180` is a reasonable value. |
| `RETENTION_EVENTS_DAYS` | `7` | Progress events older than this, always keeping the last 500 of each book. |
| `RETENTION_OUTBOX_SENT_DAYS` | `7` | OpenViking updates already delivered. |
| `RETENTION_BIBLE_REVISIONS` | `20` | Automatic Book Bible revisions beyond the most recent 20 per book. Human revisions are always kept. |
| `RETENTION_JOB_STATE_DAYS` | `30` | Per-passage resume state of jobs finished, failed or cancelled longer ago. Final-review outcomes are kept. |
| `RETENTION_RESULTS_DAYS` | `30` | Result files of automation requests finished longer ago. The request and its report stay; asking for the result again rebuilds it. |

## Settings changed in the interface

Administrators can change the following without restarting anything, from **Settings** in the web interface (or
the matching API route, with an administrator session). The new value applies to the next decision the worker
makes.

| Settings page | API route | Replaces |
| --- | --- | --- |
| **Autopilot** | `GET`, `PUT`, `DELETE /api/settings/autopilot` | All `AUTOPILOT_*` variables |
| **Automation API** (webhooks part) | `GET`, `PUT`, `DELETE /api/settings/webhooks` | All `API_WEBHOOK_*` variables |
| **Automatic recovery** | `GET`, `PUT`, `DELETE /api/settings/recovery` | `PROVIDER_RECOVERY_BASE_SECONDS` (5–3600 seconds). `PROVIDER_RECOVERY_MAX_SECONDS` still applies. |
| **Memory · OpenViking** | `GET`, `PUT /api/settings/memory`, `POST /api/settings/memory/test` | `OPENVIKING_URL`, `OPENVIKING_API_KEY`, `OPENVIKING_ROOT_URI`, plus search options, budgets, minimum score, timeout and authentication mode that have no variable |
| **Memory · OpenViking** (card **OpenViking cleanup**) | `GET`, `PUT`, `DELETE /api/settings/memory/cleanup` | `OPENVIKING_CLEANUP_ON_DELETE` |
| **SearXNG** | `GET`, `PUT /api/settings/searxng`, `POST /api/settings/searxng/test` | `SEARXNG_URL`, with a separate on/off switch |

Other **Settings** pages (LLM providers, Prompts, Users, API tokens) hold data that exists only in the
database; they have no environment equivalent.

### Which value wins

From strongest to weakest:

1. **The launch or request itself**, e.g. `"autopilot": false` or `"final_review": false` in an API request.
2. **The volume's own settings**: its autopilot switch, fallback providers, passage size and review mode.
3. **A value saved in Settings.**
4. **The environment variable** in `.env`.
5. **The built-in default** shown on this page.

Details per page:

- **Autopilot** and **webhooks** show the effective values next to the environment values. **Go back to the
  environment values** (`DELETE`) forgets everything saved on that page. Fallback providers are saved as
  provider ids; an unknown name is refused, and a provider deleted later is skipped.
- The **global webhook secret** entered in the interface is stored encrypted with `SECRET_KEY` and never shown
  again. It wins over `API_WEBHOOK_SECRET`; **Forget the secret saved here** falls back to the variable. If
  `SECRET_KEY` changed and the saved secret cannot be read, the variable is used.
- **Automatic recovery** shows the delay in force, the value of `PROVIDER_RECOVERY_BASE_SECONDS` and whether the
  delay was saved here (badge **Delay saved here** or **Environment delay**). **Go back to the environment delay**
  (`DELETE`) forgets the saved delay; the variable applies again to the next retries.
- **Memory · OpenViking** overrides the environment field by field. A key saved there is encrypted with
  `SECRET_KEY`; if it can no longer be read, OpenViking search is turned off and translation continues with the
  internal memory.
- **OpenViking cleanup** shows the switch in force, the value of `OPENVIKING_CLEANUP_ON_DELETE` and whether the
  switch was saved here. **Go back to the environment value** (`DELETE`) forgets it. The switch applies to the
  next deletions; cleanups already queued still run.
- **SearXNG** replaces the environment completely once saved: the saved URL and switch are used, even if
  `SEARXNG_URL` is set.
