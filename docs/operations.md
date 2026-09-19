# Operations

This page is for administrators who keep Libris running: updating it without losing work, sizing its capacity,
monitoring it, keeping its database small, controlling model costs and solving common problems. Installation is
covered in the [Docker guide](docker.md), every setting in the [configuration reference](configuration.md), and
backups in [backup and restore](backup.md).

All commands run from the Libris directory unless stated otherwise.

## Update Libris

The simple way is the one in the [Docker guide](docker.md#update): back up, `git pull`, then run the installer with
the new image. It restarts the worker at once; running books resume from their last checkpoint, so no finished
work is lost, but the passages being translated at that moment are sent again.

`scripts/deploy.sh` gives finer control over when the worker restarts:

```bash
LIBRIS_DEPLOY_SOURCE=pull ./scripts/deploy.sh --worker-when-idle
```

| Mode | What it does |
| --- | --- |
| `--api-only` (default) | Gets the image, runs the database migrations and restarts the web application only. The worker keeps running the previous version until you restart it. |
| `--worker-when-idle` | Same, then waits up to 10 minutes for the job queue to be empty, stops the web application so that no new job starts, checks again and restarts both. If jobs stay active it leaves the worker alone and exits with status 3 (4 if a job started at the last moment). |
| `--force-worker` | Restarts the worker at once. Running jobs resume from their checkpoints. |

| Variable | Meaning |
| --- | --- |
| `LIBRIS_DEPLOY_SOURCE` | `build` (default) builds the image from the source tree; `pull` downloads `LIBRIS_IMAGE`; `loaded` uses an image already present locally (`LIBRIS_IMAGE` required). |
| `LIBRIS_COMPOSE_ENV_FILE` | Configuration file to pass to Compose instead of `.env`. |

If the new version fails to start, the script puts the previous image back (tagged `epub-translator:rollback`)
and restarts it. It cannot undo a migration: for that, see
[roll back a failed update](backup.md#roll-back-a-failed-update).

Use `--api-only` for a quick fix of the web side, then finish with `--worker-when-idle` when books are done: the
worker should not keep running an older version than the database schema for long.

### Production deployment script

`deploy/libris-production-deploy` is the script the project's own CI uses to deploy tagged releases onto a
dedicated host. It is only useful if you run a similar pipeline; the release process is described in
[development](development.md). Install it as `/usr/local/sbin/libris-production-deploy`.

```bash
libris-production-deploy COMMIT VERSION APP_IMAGE_ID CODEX_IMAGE_ID   # deploy images already loaded
libris-production-deploy --rollback                                  # back to the images it replaced
libris-production-deploy --check-compose                             # is the installed Compose file the deployed version's?
libris-production-deploy --prune-images [--dry-run]                  # remove old Libris images
```

A deployment checks the images' version and revision labels, refuses to go back to an older version (use
`--rollback`), dumps the database into `$LIBRIS_PRODUCTION_BASE/backups/` (last five kept), stops the application,
installs the Compose file shipped inside the new image, runs the migrations, starts the API and the Codex bridge,
checks `/health`, and only then starts the worker. If anything fails before the schema changed, it restarts the
previous images; after a schema change it leaves the services stopped and names the dump to restore.

| Variable | Default | Meaning |
| --- | --- | --- |
| `LIBRIS_PRODUCTION_BASE` | `/opt/libris-production` | Working directory: Compose file, recorded version, pre-deployment dumps. |
| `LIBRIS_PRODUCTION_COMPOSE_FILE` | `$LIBRIS_PRODUCTION_BASE/docker-compose.yml` | Installed Compose file. |
| `LIBRIS_PRODUCTION_COMPOSE_OVERRIDE` | empty | Optional extra Compose file for local additions. |
| `LIBRIS_PRODUCTION_SECRET_ENV` | `/opt/epub-translator/.env` | The installation's `.env`. |
| `LIBRIS_PRODUCTION_PROJECT` | `epub-translator` | Compose project name. |
| `LIBRIS_PRODUCTION_HEALTH_URL` | the maintainers' own address | Health URL checked after deployment. Always set it. |
| `LIBRIS_PRODUCTION_REGISTRY_REPOSITORY` | the maintainers' own registry | Registry repository whose old pulls `--prune-images` removes. Always set it. |

The Compose options for manual commands on such a host are:

```bash
docker compose --project-name epub-translator --env-file /opt/epub-translator/.env \
  --file /opt/libris-production/docker-compose.yml --profile codex <command>
```

## Capacity and concurrency

Two limits decide how much work runs at once:

- **Concurrent books**, set on each provider in **Settings › LLM providers**: how many books may use that provider
  at the same time, all operations included (analysis, translation, review). Providers are independent: one set to
  3 and another set to 1 allow four active books.
- **`WORKER_BOOK_PARALLELISM`**: how many passages of one book are processed at once. The default, `0`, uses the
  provider's capacity, shared between the books using it. `1` processes one passage at a time. Passage analysis
  and the Book Bible synthesis always run in order.

A book stays **queued** while its provider has no free slot. To change a book's provider mid-way, pause it, choose
the new provider in its configuration and resume: the rest of the book uses the new one.

One worker is enough for most installations. It renews a 60-second lease on each job; if the worker stops
abruptly, another start picks the job up after the lease expires.

## Monitoring

### Health

`GET /health` answers `{"status":"ok","version":"<version>"}` when the API and its database connection work. It
does not test model providers or OpenViking; test those in **Settings**.

```bash
curl --fail http://127.0.0.1:8088/health
```

### Prometheus metrics

`GET /metrics` exposes the state of the installation in Prometheus text format. It is disabled until you set
`METRICS_TOKEN`:

```bash
sed -i "s|^METRICS_TOKEN=.*|METRICS_TOKEN=$(openssl rand -hex 32)|" .env   # the line exists in a generated .env
grep '^METRICS_TOKEN=' .env                                                  # the value to give Prometheus
docker compose up -d --no-build --wait
```

Each scrape must send the token as `Authorization: Bearer <token>`; a browser session is not enough. Without the
variable the endpoint answers 404, with a wrong token 401.

```yaml
scrape_configs:
  - job_name: libris
    scrape_interval: 60s
    metrics_path: /metrics
    scheme: https
    authorization:
      type: Bearer
      credentials_file: /etc/prometheus/libris-metrics-token
    static_configs:
      - targets: ["books.example.com"]
```

| Metric | Type | Content |
| --- | --- | --- |
| `libris_jobs{operation,status}` | gauge | Jobs by operation and state, finished ones included |
| `libris_jobs_oldest_queued_age_seconds` | gauge | How long the oldest job ready to run has been waiting (a resumed job counts from its resumption) |
| `libris_jobs_expired_leases` | gauge | Running jobs whose 60-second lease expired: their worker stopped or hangs |
| `libris_llm_requests_total{operation,status}` | counter | Finished model requests by operation and outcome (`success`, `error`, `refused`, `interrupted`, `abandoned`); cached answers count as `success` |
| `libris_llm_input_tokens_total{operation}`, `libris_llm_output_tokens_total{operation}` | counter | Tokens reported by the providers |
| `libris_llm_wasted_input_tokens_total{operation}` | counter | Input tokens of requests that failed, were refused or interrupted |
| `libris_llm_cache_hits_total{operation}` | counter | Requests answered from the response cache |
| `libris_llm_cache_hit_ratio` | gauge | Share of all finished requests answered from the cache |
| `libris_llm_requests_in_flight{provider}` | gauge | Requests in progress, by provider name |
| `libris_segments{status}` | gauge | Passages of all books, by state |
| `libris_memory_outbox_pending` | gauge | OpenViking updates not delivered yet |

Counters are computed from the database: they count everything since installation, are the same in every process
and survive restarts. Deleting a book deletes its requests, which Prometheus sees as a counter reset. Use
`rate()` or `increase()` for a time window. No label contains a book title, text, id or provider address. The
output is cached for 10 seconds, so scraping every 30 to 60 seconds is enough.

Useful alerts:

| Condition | Meaning |
| --- | --- |
| `libris_jobs_expired_leases > 0` for 5 minutes | The worker is stopped or stuck. |
| `libris_jobs_oldest_queued_age_seconds > 900` | No worker takes jobs, or a provider is saturated. |
| `sum(rate(libris_llm_wasted_input_tokens_total[1h])) / sum(rate(libris_llm_input_tokens_total[1h])) > 0.2` | More than one input token in five is spent on requests that produced nothing. |

Behind a reverse proxy, expose `/metrics` only to your Prometheus network if you can.

### Statistics in the interface

**Statistics** shows token usage by model, and each book shows what it has spent. Costs use the price recorded
with each request, so a later price change on a provider does not rewrite past costs.

### Logs

```bash
docker compose logs --since=30m api worker
docker compose logs -f worker
```

Each container keeps at most three log files of 10 MB. Review logs before sharing them: remove account names,
addresses and anything that looks like a key.

## Data retention

Every model call leaves a row with its prompt and answer, which the request inspector shows. Without limits, this
history grows quickly. The worker cleans it up once at start-up and then every hour, in small batches, following
the `RETENTION_*` settings ([configuration](configuration.md#data-retention)):

- prompts, raw answers and context traces of requests older than 30 days are emptied (the inspector no longer
  shows them), while tokens, cost, duration, status and the cached answer are kept;
- progress events older than 7 days are deleted, always keeping the last 500 of each book;
- OpenViking updates already delivered are deleted after 7 days;
- only the 20 most recent automatic Book Bible revisions of each book are kept (human revisions are all kept);
- the per-passage resume state of jobs that ended more than 30 days ago is deleted. If such a job is resumed
  later, its finished passages are still not translated again (unless you force a new translation);
- result files of automation requests are deleted after 30 days; asking for the result again rebuilds it;
- files of expired imports are deleted from `DATA_DIR/staging`.

Before deleting anything, the same pass adds requests older than two hours to daily usage totals, which the
statistics and `/metrics` read. With `RETENTION_REQUEST_ROWS_DAYS` set (for example `180`), whole request rows
older than that are then deleted; statistics stay correct, but the response cache and the inspector lose them.

See what a pass would remove, or run one now:

```bash
docker compose exec api python -m app.maintenance.retention --dry-run
docker compose exec api python -m app.maintenance.retention
```

PostgreSQL reuses the freed space, but gives it back to the system only after a full vacuum. It locks the table,
so stop the worker first and make sure the disk has as much free space as the table's useful size:

```bash
docker compose stop worker
docker compose exec database sh -c 'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -c "VACUUM (FULL, ANALYZE) llm_requests;"'
docker compose start worker
```

## Maintenance commands

| Command | What it does |
| --- | --- |
| `docker compose exec api python -m app.maintenance.retention [--dry-run]` | Runs the retention pass described above. |
| `docker compose exec api python -m app.maintenance.usage [--dry-run]` | Adds finished requests to the daily usage totals now. The worker does it every hour. |
| `docker compose exec api python -m app.maintenance.compact_request_logs [--dry-run]` | Rewrites old request rows in the compact form new rows use. Safe to interrupt and run again. |
| `docker compose exec api python -m app.maintenance.compare_providers --project <book id> --providers <id>,<id> [--sample 5] [--output report.json]` | Compares providers on the same passages. See below. |
| `python scripts/measure_prompt_cost.py [options]` | Measures the tokens a configuration sends per passage, without any real model. See below. |
| `docker compose exec api alembic check` | Confirms that the database schema matches the application. |

## Cost control

Each model call (translation, review, revision, polishing, final review) carries the same fixed context
(instructions, glossary, Book Bible, character notes, neighbouring passages), whatever the length of the passage.
Three things reduce what you pay:

- **Longer passages.** `PASSAGE_MAX_CHARS` (3500 characters by default, 500 to 20000) sets the passage size of
  books imported afterwards. A volume can have its own (`passage_max_chars` in its configuration, applied to
  chapters added later), and so can an import. Longer passages share the fixed context between more text. Beyond
  8000 to 10000 characters, the expected answer approaches many providers' maximum output and a truncated answer
  costs more than it saves. Books already imported keep their cut.
- **Fused review.** `REVIEW_MODE=fused`, or a volume's review mode, reviews and corrects a passage in one call at
  high and maximum quality, instead of a review call followed by a revision call.
- **Prompt caching.** Automatic: prompt sections go from the most stable to the most variable, so that providers
  with prefix caching can reuse the common start of successive calls.

Other levers: a lower quality level on books that do not need it, `FINAL_REVIEW_ENABLED=false`, and the per-book
estimate shown before each launch.

### Measure a configuration

`scripts/measure_prompt_cost.py` runs the translation pipeline on a synthetic book against a simulated provider (no
network, nothing billed) and reports calls and tokens per passage, the share a prefix cache could reuse, and for
each operation where successive prompts start to differ. Run it from the repository with the backend's
development environment ([development](development.md)):

```bash
python scripts/measure_prompt_cost.py --quality high --passage-chars 3500 --review-issues 0.5
python scripts/measure_prompt_cost.py --review-mode fused --json
```

The figures compare settings of the same code; they do not predict the bill of a real book.

### Compare providers

`app.maintenance.compare_providers` has several providers translate the same sample of a book's passages (spread
over its narrative chapters), with the prompt and context the pipeline would build, and without the response
cache. Nothing is written to the book.

```bash
docker compose exec api python -m app.maintenance.compare_providers \
  --project <book id> --providers <provider id>,<provider id> --sample 5 --output comparison.json
```

The table lists, per provider, passages translated and failed, seconds per passage, tokens, cost and the findings
of the automatic checks (locked glossary, markers, untranslated text…). The JSON report adds failure reasons,
length ratios and the translations side by side. A relative `--output` path is written under `DATA_DIR/tmp`
(`/data/tmp` in the container, whose other directories are read-only), and the command prints the final path;
copy the file out with `docker compose cp api:/data/tmp/comparison.json .`. These calls are real and billed; they appear in the
statistics under the operation `provider_comparison`.

## Troubleshooting

| Symptom | Cause and fix |
| --- | --- |
| A book stays **queued** | The worker is not running (`docker compose ps worker`), or its provider has no free **Concurrent books** slot. |
| A book is **waiting** | The provider is unreachable, timed out or answered 429/5xx. Libris retries on its own: first after 60 seconds (or the **Automatic recovery** delay), then doubling up to an hour; a provider's `Retry-After` is respected up to 24 hours. Under the autopilot, after `AUTOPILOT_OUTAGE_MAX_RETRIES` waits it switches to the next fallback provider. |
| A book is **blocked** | The provider rejected the credentials. Fix the key or sign in again, then resume. Under the autopilot, the next fallback provider takes over, or the job fails if none is left. |
| A book **failed** with "providers exhausted" | Every provider in the autopilot chain was unavailable. Add a fallback provider in **Settings › Autopilot**, then resume. |
| Passages are refused or kept in the source language | See [autopilot](autopilot.md) for the recovery ladder and how to retranslate them with another provider. |
| `libris_jobs_expired_leases` is above 0 | The worker stopped abruptly or hangs: `docker compose logs worker`, then `docker compose restart worker`. Jobs resume from their checkpoints. |
| The live progress stops updating | Too many tabs open (limit `EVENT_STREAMS_PER_USER`), or a proxy buffering server-sent events. |
| An EPUB export is refused | Read the error message. Missing or refused passages must be resolved first, or use the partial export that keeps the source text. "Too many EPUB validations in progress" means `EPUBCHECK_CONCURRENCY` is reached: try again in a moment. |
| The database grows fast | Check the retention settings and run the retention pass with `--dry-run`. |
| Provider keys "must be entered again" | `SECRET_KEY` changed. Restore the original `.env`, or enter each key again. |

For installation, network and sign-in problems, see the [Docker guide](docker.md#troubleshooting).
