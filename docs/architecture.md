# Architecture

This page is for developers who want to understand how Libris works before changing it: the
processes, the path a book takes, the rules the code never breaks, the data model, how jobs run, and
the security design. For running Libris, see [Docker](docker.md) and [operations](operations.md); for
contributing, see [development](development.md).

## Components

| Component | Role |
| --- | --- |
| **API** (`backend/app/main.py`, FastAPI) | Serves the React interface and its API (`/api/*`, session cookie), the [automation API](api.md) (`/api/v1`, tokens) and live event streams (SSE). It never runs a translation itself. |
| **Worker** (`python -m app.jobs.worker`) | A persistent process that claims jobs from the database and runs them: analysis, translation, reviews, the [autopilot](autopilot.md). It also starts queued automation requests, sends their webhooks, writes the external memory queue and applies data retention every hour. |
| **Database** | PostgreSQL in production (SQLite for development and tests). The single source of truth. |
| **Migrations** (`alembic upgrade head`) | A one-shot service that runs before the API and the worker. |
| **Data directory** (`DATA_DIR`, `/data`) | Source files, stored results, import staging and large temporary files, all at paths Libris chooses. |
| **Model providers** | OpenAI-compatible endpoints configured by an administrator (`providers/llm.py`), or the optional [Codex bridge](codex.md). |
| **OpenViking** (optional) | A semantic index of the book memory ([OpenViking](openviking.md)). |
| **SearXNG** (optional) | Web search for the final review ([autopilot](autopilot.md#optional-web-search-searxng)). |
| **EPUBCheck** (optional, bundled in the image) | Validates imported and exported EPUB files. |

### Code map

| Module | Responsibility |
| --- | --- |
| `engines/ingestion` | Source adapters: `inspect()` reads a file without creating anything, `parse()` turns it into volumes and chapters. EPUB, TXT, Markdown, HTML, DOCX and JSON. Series, volume and chapter inference (`naming.py`), storage in SQL and under `DATA_DIR` (`store.py`), adding, inserting and replacing chapters. The rest of the pipeline ignores the source format. |
| `engines/epub` | ZIP preflight, EbookLib reading, lxml DOM, units with inline codes, segmentation, rebuilding a translated copy of the archive, EPUBCheck. |
| `engines/series` | Series memory (canonical identities, links, relations, series glossary, Series Bible), rebuilt from the volumes (`refresh_series`), and the audit log. |
| `engines/context` | Context selection for each model call: narrative query, local, external or hybrid memory, budget, inspector; series conventions inherited from earlier volumes (`series.py`); section order (`prefix.py`). |
| `engines/memory` | Human decisions, characters, glossary, OpenViking events and catalogs, the send queue, the opt-in cleanup of deleted items. |
| `engines/translation` | Analysis, translation, review, revision and polishing, global consistency, final review, repair in groups (`repair.py`), translation memory (`memory.py`), versions. |
| `engines/autopilot` | The convergence loop (`loop.py`), recovery ladder (`recovery.py`), AI arbitration (`arbitration.py`), memory decisions (`memory.py`), provider fallback (`providers.py`), skipping optional steps (`degrade.py`) and the decision log (`decisions.py`). |
| `engines/quality` | Deterministic checks: unit ids, markup codes, empty output, length, repetition, unchanged text, terminology. |
| `engines/delivery` | Automation requests: upload intake (`intake.py`), always-terminal lifecycle (`lifecycle.py`), completion report (`report.py`), stored results (`results.py`), EPUB delivery with automatic repair (`epub.py`), signed webhooks (`webhooks.py`). |
| `engines/exports` | Text and Markdown renderings of a volume. |
| `jobs` | Queue, leases and fencing (`queue.py`, `clock.py`), per-passage job state (`segment_state.py`), running work off the event loop (`concurrency.py`), automation request dispatch (`requests.py`), the worker (`worker.py`). |
| `providers` | Model calls (`llm.py`: structured output, validation, retries, cache, budget, traces), OpenViking client, SearXNG, Codex bridge. |
| `api` | Interface routes, [automation API](api.md) (`v1.py`, `tokens.py`), administration settings. |
| `maintenance` | Retention, usage aggregation, request log compaction, provider comparison. |

## The path of a book

1. **Import.** Files are uploaded to an import session (`/api/imports`), inspected, then committed:
   each becomes a volume (EPUB) or a chapter (text formats) of a series. The automation API writes the
   same rows directly.
2. **Analysis.** Passage by passage, in order: chapter summaries, characters, relations and narrative
   state, then the Book Bible. Stored as memories in SQL.
3. **Translation.** Each passage is translated with a context built from the book's memory, then
   reviewed, revised or polished depending on the quality level.
4. **Whole-book steps.** Global consistency, final review, and under the autopilot the convergence
   rounds (recovery ladder, final review, AI arbitration). See [autopilot](autopilot.md).
5. **Output.** Exports from the interface, or the stored result of an automation request.

Every step writes to SQL first. External systems (OpenViking, providers, webhooks) receive copies and
never hold anything that cannot be rebuilt.

## Invariants

These rules hold everywhere in the code; changes must keep them.

- **SQL is the source of truth.** Files under `DATA_DIR` are referenced by SQL rows at paths Libris
  chooses; nothing is located by a name taken from an upload. External memory is a rebuildable copy,
  and an external failure never removes a local result.
- **Human work wins.** A passage corrected or validated by a person is never overwritten by a job, the
  autopilot, or a chapter replacement (unless the person or the API client explicitly discards it). A validated human correction is the top priority in the context of later passages.
- **Writes are fenced.** A job writes only while it holds its lease; every write checks the lease and
  the passage revision. A late answer from a job that lost its lease is refused, even if the provider
  finished the call.
- **Versions, not overwrites.** Every translation of a passage is a new version with its origin; the
  active version changes only when allowed.
- **Nothing waits forever.** Under the autopilot every job ends completed or failed; every automation
  request ends with a final status.
- **Model output is data.** Generated HTML is never accepted as DOM structure; book text in prompts
  cannot close a prompt section; web results are untrusted hints.

### Important transactions

1. **Saving a translation:** check the job's lease, compare the passage revision, insert a version,
   switch the active version only if allowed. Memory events for the send queue are written in the same
   transaction.
2. **Human correction:** access check, expected revision required, unit and code validation, new
   version, memory updated with priority when validated, commit.
3. **Resuming:** the previous lease holder is invalidated; its late writes are refused.
4. **Synchronizing:** SQL stays canonical; a lost write acknowledgement is replayed on the same stable
   URI.

An HTTP answer received just before a crash may be computed again if it was not committed: Libris
guarantees that committed results persist, not that a remote inference runs exactly once.

## From source to passages

A **unit** is one translatable piece of text; a **passage** (a `segments` row) is a group of units and
the unit of every model call.

- **EPUB.** XHTML and NCX documents are working sections; semantic subdivisions are kept in the units
  and in `Segment.section`. The original spine order is stored explicitly. Anchors are deterministic:
  resource, XPath and field type. A leaf block (`p`, `li`, `td`…) forms a unit; inside a parent that
  mixes text and blocks, each run of text and inline elements between two blocks forms a `run` unit.
  Ruby readings (`rt`, `rp`), code, formulas and preformatted text are immutable markers; SVG `<text>`
  and `aria-label` are translated; `pre`, MathML and kept SVG titles are listed in
  `book_info.untranslated`.
- **Chapter kinds.** `Chapter.kind` separates the story (`narrative`), documents outside the linear
  reading (`auxiliary`, `linear="no"`, placed after the story), navigation (`navigation`: nav document,
  NCX) and metadata (`metadata`: `dc:description` and short `dc:subject` of the OPF, translated like
  passages).
- **Text formats.** A TXT, Markdown, HTML or DOCX file is one chapter, read as blocks (headings,
  paragraphs, list items, quotes); code, Markdown tables, `<pre>`, rules and scene separators are kept
  as they are. The layout (blank lines, indentation, separators, block markup such as `## `, `- `,
  `> `) is stored in `Chapter.import_meta["layout"]`, never in the translated text. Markdown inline
  formatting stays in the text; HTML and DOCX inline formatting is flattened. HTML is parsed without
  network access (comments, scripts, styles, `<nav>` and forms ignored, entity declarations refused).
  DOCX goes through the same archive and XML checks as EPUB; headings come from paragraph styles, and
  tables and text boxes are included. JSON chapters from the automation API go through the same text
  path.
- **Identifiers.** Units of text chapters derive from a virtual resource (`txt/<hash>`, `json/<hash>`…,
  from the volume and the chapter number or external id) and the line index: importing the same chapter
  again gives the same identifiers, and a replaced chapter keeps the identifiers of unchanged lines.
- **Long paragraphs** can be split at language boundaries and reassembled before rebuilding. CJK
  paragraphs are cut on 。！？… (closing quotes included), then on clauses, then at the limit.
- **Passage size** is chosen at import time: `PASSAGE_MAX_CHARS` (3500 by default), a volume's
  `config.passage_max_chars` for chapters added later, or an import's own setting. It is recorded with
  the source (`book_info.passage_max_chars` for an EPUB, `import_meta.passage_max_chars` for a text
  chapter), so a project archive is always cut again with the size of its import. Changing the setting
  never re-cuts an existing book.
- **Segmentation version.** `book_info.segmentation` records the EPUB segmentation algorithm (currently
  2). Books imported with version 1 keep their units, and a project archive without the field is
  restored with version 1.
- **Translation memory key.** `Segment.source_key` is the SHA-256 of the units normalized with NFKC,
  collapsed spaces and markers included.

On export, translated documents receive the target language and direction (`dir="rtl"` for Arabic,
Hebrew, Persian, Urdu…, and the spine's `page-progression-direction` in EPUB 3). Elements that declared
the source language switch to the target language; elements in a third language keep theirs.

## Data model

SQL tables, grouped by purpose. Column types for documents are SQLAlchemy `JSON` (see
[JSON rather than JSONB](#json-rather-than-jsonb)).

### Library

| User vocabulary | Table | Notes |
| --- | --- | --- |
| **Series**, the literary project shown first in the library | `series` | One owner; the name is unique per owner once case and spacing are folded (`normalized_name`). `kind` is `books` or `webnovel`. Default languages, provider, quality, context backend and instructions for new volumes; `bible` is the Series Bible (`bible_validated` once a person edited it). |
| **Volume**, the unit the pipeline processes | `projects`, `project_kind = volume` | `series_id` (null for a standalone volume), `volume_number`, `source_format` (`epub`, `txt`, `json`…), `external_id`, `import_meta`, `book_info`, `config` (autopilot, fallback providers, passage size, review mode), `bible`. `series_name` mirrors the series for older clients. |
| **Continuous chapter flow** of a webnovel | `projects`, `project_kind = serial` | At most one per series. Text chapters imported without a volume go here. |
| **Chapter** | `chapters` | `position` (order in the volume), `chapter_number` (the author's, may be `12.5`), `external_id`, `source_checksum` (SHA-256 of the normalized text), `import_meta.layout`, `context_stale` (an earlier chapter's source was replaced), `kind`, `source_asset_id`. |
| **Passage** | `segments` | Units, active translation, status and stage, `human`, `validated`, `retained_source`, `revision`, critique, uncertainties, narrative state, last error. |
| Passage history | `translation_versions` | Every version with its origin (`translation`, `revision`, `human`, `final_review`, `arbitration`, `recovery`, `translation_memory`, `source_retained`…) and base revision. |
| Source files | `source_assets` | One row per imported file: format, original name (display only), media type, `storage_path` relative to `DATA_DIR` (`books/<project>.epub`, `sources/<project>/<asset>.<ext>`), size, SHA-256, metadata such as the detected encoding. |
| Import sessions | `import_sessions` | Files uploaded for inspection (`DATA_DIR/staging/<session>`) and, once committed, the answer of the commit, so that repeating a commit returns what the first one did. They expire after `IMPORT_SESSION_HOURS` (24). |

### Book memory

| Table | Content |
| --- | --- |
| `memories` | Analyses, narrative states and validated human decisions, by passage position. |
| `entities`, `character_relations`, `entity_merges` | Characters of a volume, their relations and merges. |
| `glossary` | Terms of a volume; `locked` terms are enforced; `series_override` marks a deliberate departure from the series term (audited). |
| `bible_revisions` | Previous Book Bible versions (bounded by retention). |
| `memory_outbox` | The OpenViking send queue; `uri` records where an entry was last written. |
| `openviking_cleanups` | Opt-in removals of the OpenViking directories of deleted volumes and series, and of orphans: directories, state, attempts, lease and the log of what was removed. No foreign key: rows outlive the deleted items. |

### Series memory

| Table | Content |
| --- | --- |
| `series_entities` | Canonical identities of the series: characters, and places, organizations and objects named by the volumes' bibles. First appearance, aliases, profile, `merged_into_id` after a person's merge. |
| `series_entity_links` | A volume's character attached to a series identity: `linked`, `proposed` (ambiguous: never merged automatically) or `rejected`; `human` when a person decided. |
| `series_relations` | Relations between series identities, with evidence and first appearance. |
| `series_glossary` | Series terms: `origin` is `volume` when aggregated from accepted volume terms, `human` for a person's decision (never rewritten by the aggregation). |
| `audit_entries` | Merges, splits, link decisions, Series Bible edits, series terms, glossary overrides, API token creation and revocation. Never a secret. |

### Jobs and runs

| Table | Content |
| --- | --- |
| `jobs` | One job per launched operation: provider, options, status, lease (`lease_owner`, `lease_until`), `checkpoint`, `result` (the autopilot report), error and stop reason. |
| `job_segment_state` | What a job settled passage by passage (see [below](#checkpoint-and-per-passage-state)). |
| `events` | Progress events streamed to the interface (bounded by retention). |
| `llm_requests` | Every model call: messages, answer, tokens, cost, status, context inspector. |
| `usage_daily` | One row per UTC day, book, provider, operation, model, outcome and cache flag. Filled by the worker's hourly rollup of requests older than two hours (`app_settings["usage_rollup"]` is the watermark); statistics read the aggregates plus the requests since the watermark. A deleted provider keeps its history; rows follow their book. |
| `quality_issues` | Findings of the checks and reviews, open until resolved. |
| `autopilot_decisions` | The [decision log](autopilot.md#the-decision-log-and-the-report). |

### Accounts, automation and settings

| Table | Content |
| --- | --- |
| `users`, `login_sessions`, `memberships` | Accounts, sessions, and per-book sharing roles. |
| `providers` | Model providers; API keys encrypted with `SECRET_KEY`. |
| `prompts` | Prompt overrides saved from the interface. |
| `api_tokens` | Owner, name, SHA-256 of the secret, displayable prefix, scopes, expiry, revocation, last use, optional webhook signing secret (encrypted). |
| `translation_requests` | Automation requests: owner, token, `external_id`, `Idempotency-Key`, payload hash, series, volume, job, status, options (input kind, intake decisions), chapters, error, report, stored `artifact` (path, format, size, SHA-256) and webhook state. |
| `app_settings` | Settings saved from the interface (autopilot, webhooks, OpenViking, SearXNG, provider recovery), watermarks and markers. |

### JSON rather than JSONB

Document columns (`projects.bible`, `jobs.checkpoint`, `llm_requests.messages`, `segments.units`…) use
SQLAlchemy `JSON`, which is `json` in PostgreSQL. This is deliberate:

- no query filters or indexes inside a document; everything is read by row and used in Python;
- `jsonb` reorders object keys. Objects read back from the database are serialized into prompts, so a
  new key order would change prompt bytes, invalidate the response cache and the providers' prefix
  cache, and make every book in progress pay its calls again;
- converting would rewrite every table under an exclusive lock for no benefit.

For a hand-written query that needs a JSON operator, cast: `column::jsonb ? 'key'`. If a feature one day
needs to query inside a document, convert that one column with
`JSON().with_variant(JSONB(), "postgresql")`, a dedicated migration and a GIN index.

## Context and series rules

**Priority.** Instructions > validated human decisions > locked glossary > locked series terms >
validated structured data > external retrieval > automatic summaries > neighbouring passages >
inferences.

**Budget.** The provider's window minus the reserved output, the operation's response schema and a
safety margin checked by `llm.complete`. Sizes are measured on the serialized sections (escapes and
tags included); `input_estimate` is exactly what `llm.complete` compares with the window. If the passage
and its mandatory rules do not fit, the error gives the numbers (window, output, schema, system prompt,
passage, rules) and the window that would suffice. A first translation is then cut at sentence
boundaries into parts sized for the window (half of what remains after the rules; the other half goes
to the neighbourhood), translated with their context and reassembled; a revision only cuts between
whole units.

**Neighbourhood.** Neighbouring passages are served first so that a passage is never isolated, but
they get at most 60% of the optional budget (two thirds of it for what precedes) as soon as other
material is a candidate (character sheets, glossary, chapter state, memory). A neighbour that is too
long is cut to an excerpt (end of the previous passage, start of the next one) rather than dropped.
Instructions and the mandatory glossary are never dropped to hide an overflow. Dropped elements and
the reason are recorded in the context inspector.

**Narrative state versus editorial knowledge.** Character sheets and the Book Bible built from a whole
reading are marked editorial: they must not lead the translation to reveal an ambiguity the text keeps.

**Series.** A volume inherits from **earlier volumes only**: same series, same owner, lower volume
number, and the same language pair compared on the primary subtag (`en-US` ≈ `en`). A continuous
webnovel flow or an unnumbered volume has no earlier volume: it relies on its own chapters, read in
order. `SERIES_CONVENTIONS` carries the terms, the human decisions, and `known_identities` (characters
met in earlier volumes, under the names those volumes used). Term priority: explicit instruction >
validated human decision > locked volume term (or an audited `series_override`) > locked series term > accepted
series term > automatic proposal; for one term, a locked choice beats any unlocked one, then the most
recent volume wins. Locked series terms are checked in the output like the book's locked glossary,
unless the book locks the same term differently. A validated human correction of a machine
translation records its short replacements and the names in the passage; a later volume that mentions
those names receives them in `SERIES_CONVENTIONS.human_decisions`.

**Translation memory.** Before calling the model for a first translation, Libris reuses a finished
passage with the same `source_key`, same owner, same source language (primary subtag) and same target
language; a version validated by a person comes first. In a series, only the same book and earlier
volumes qualify. Units are copied only if the marker structure is valid and the locked glossary
(series included) is respected. The version's origin is `translation_memory`; review, revision and
final review then apply normally. A forced rerun always calls the model. With several passages in
flight, passages with the same `source_key` in one job run one after the other, so the second reuses
the first instead of racing it.

**Prompts.** Section contents escape `<` and `>`, so book text cannot close a section. `load_prompt`
adds to every prompt, overrides included, an "untrusted data" clause and, for operations that write or
review, a register rule (formal or informal address) and the target language's typography; languages
are named ("French (fr)"). The prompt version is `file-v3` or `db-vN`, suffixed with `+rules-v1`. The
JSON schema is sent once: in `response_format` in structured mode, otherwise in a system message.

**Section order** (`context/prefix.py`). Providers with a prefix cache (OpenAI and compatible servers,
vLLM, llama.cpp, DeepSeek) only recompute or bill what follows the first byte that differs from an
earlier request. The user message is therefore written from the most stable to the most variable:
book context (`EDITORIAL_BOOK_CONTEXT`, character register), then what holds for the chapter or job
(`USER_RULES`, chapter context), then what the names in the passage select (glossaries, identities,
sheets, relations, series conventions), then retrieved memory and chapter state, the neighbours, the
operation's material (`CURRENT_TRANSLATION`, `REVIEW`…) and finally `TARGET_TEXT`, always last.
`scripts/measure_prompt_cost.py` measures the effect on a given configuration.

## Jobs, leases and the worker

### Claiming and leases

A job is claimed in a transaction: `pending` jobs, `waiting` jobs whose retry time has come, and
running jobs whose lease expired. A provider's `max_concurrency` bounds how many jobs use it at once.
The lease lasts 60 seconds and is renewed by a heartbeat every `WORKER_HEARTBEAT_SECONDS` (2) on a
dedicated thread pool. Leases are written and compared with the **database clock**, read in the same
statement (`jobs/clock.py`: `clock_timestamp()` in PostgreSQL, the host clock with SQLite), so workers
with drifting or jumping clocks never steal a live job or believe they lost theirs. Durations inside
one process (heartbeat grace, call timeouts) use `time.monotonic()`.

A job with no provider (deleted, or never chosen) is marked `blocked` with the reason instead of
waiting forever.

### Checkpoint and per-passage state

`jobs.checkpoint` holds only a cursor and counters: `step`, `current`, `total`, `segment_id`,
`consecutive_failures`, recovery flags, `review_targets`, the autopilot round and phase. Its size does
not depend on the book (always under 4 KB); it is rewritten at each passage and returned by
`GET /api/projects/{id}/jobs`.

What a job settled passage by passage lives in `job_segment_state` (primary key `job_id, step,
segment_id, key`; idempotent writes):

| `step` | Meaning |
| --- | --- |
| `finished` | Nothing left to do for this passage in this job (including a human correction or a kept original during the job). |
| `started` | A forced rerun already applied its new version. |
| `review_target`, `reviewed` | The frozen scope of the final review, and its outcome (`resolved`, `needs_human`, `protected`, `failed`, with `data.revised`). |
| `recovery_target` | Passages retried by the automatic recovery pass. |
| `repair` | One validated four-unit group of a passage being repaired (`key` = revision:operation:start). |
| `bible`, `consistency` | Book Bible batches and consistency samples already processed (empty `segment_id`). |
| `analysis_skipped` | Autopilot: analysis given up for this passage. |
| `autopilot_ladder`, `autopilot_arbitrated` | Autopilot: passage taken through the recovery ladder, or its open points arbitrated, during round `key` (`r1`, `r2`…), with the outcome. |

Progress (`project.progress`, `stats`) reads these rows. Once a job has been finished for
`RETENTION_JOB_STATE_DAYS`, only its `reviewed` rows are kept. Project archives carry these rows with
their jobs.

### The event loop and threads

All jobs of a worker share one asyncio loop, so a synchronous query or a long CPU loop there would
delay the other jobs' heartbeats and cost them their lease. SQL sessions and work proportional to the
book (context preparation, memory scoring, consistency sampling, model call admission and logging,
result writes) run in threads, one session per call: eight threads for this work and two reserved for
lease renewals, below the connection pool size. A per-job lock serializes the checkpoint's
read-modify-write inside a worker (PostgreSQL already does it with `FOR UPDATE`; SQLite reads before
taking its write lock).

### Several passages of one book at once

Translation, final review and consistency checks process several passages of a book at once, in a
sliding window: passages start in book order, and at most N are in flight. N is the provider's
`max_concurrency`, shared equally (rounded up) among the books using it at that moment and read again
before each start; `WORKER_BOOK_PARALLELISM` caps it (`1` makes processing strictly sequential). In
each process, calls to a provider go through a queue sized to its capacity, served in arrival order,
before the database-level admission that bounds all processes together.

The trade-off: a passage's context only contains what is already saved. A previous neighbour still in
flight shows its source only, and its narrative state is missing from `CHAPTER_STATE`; with N passages
in flight, at most the N − 1 previous ones are affected. With `WORKER_BOOK_PARALLELISM=1`, each passage
sees the translation of all the passages before it.

Analysis stays sequential: each analysis reads the chapter summary, characters and relations left by
the previous passages and rewrites the summary "up to this point"; in parallel, the chronological
memory would be built out of order. Book Bible synthesis stays sequential for the same reason.

Resumption and safety:

- A passage is marked `finished` only once all its steps are saved. Every write checks the lease and
  the passage revision; a version already applied is not applied again.
- A pause, a cancellation, a lost lease or a worker shutdown cancels every call in flight (its request
  becomes `interrupted`); the first error of one passage (provider outage, authentication) stops the
  others too. Interrupted passages are not marked, so a resume restarts them from what they saved,
  without redoing finished ones or emitting events for them.
- The ten-consecutive-failures counter follows the order in which passages finish. It never stops an
  autopilot job or a pipeline with automatic recovery.
- **Lock order.** The worker locks its job row (`fence`) before any passage row. API actions that touch
  a passage and the book's live jobs (human correction, kept original, queuing an accepted proposal)
  first lock those jobs (`lock_live_jobs`, by increasing id), then the passage, so a passage/job
  deadlock with the worker cannot happen in PostgreSQL.

### Outages and failures

A provider outage suspends the job as `waiting` with an exponential delay (the saved **Automatic
recovery** delay, else `PROVIDER_RECOVERY_BASE_SECONDS`, capped by `PROVIDER_RECOVERY_MAX_SECONDS`);
refused credentials make it `blocked`. Under the autopilot, the fallback chain takes over after a
bounded wait ([autopilot](autopilot.md#fallback-providers-and-outages)). A database outage suspends the
job as `waiting`; a worker shutdown puts it back to `pending`.

### Automation requests

A request of the [automation API](api.md) is saved (row, payload file, chapters when the volume is
free) before the API answers. The worker's request dispatcher checks live requests every 2 seconds:
it imports the chapters of queued requests once no job is active on the volume, starts their pipeline
once no job holds it, and settles running requests from their job's state (building and storing the
result, writing the report, failing stalled or overdue requests). The API also settles a request
before answering, so a client never waits for the next pass. Webhooks are sent by a separate worker
loop, never by the API.

## Exports and project archives

`GET /api/projects/{id}/export/{format}`:

| Format | Sources | Content |
| --- | --- | --- |
| `epub` | EPUB only (`409` for other sources) | The EPUB rebuilt from the original, validated by EPUBCheck. |
| `txt` | All | One file: the volume title, then each chapter under its title, chapters separated by two blank lines. |
| `txt-zip` | All | `chapters/NNN - Title.txt` (UTF-8, reading order, zero-padded, cleaned unique names) and `manifest.json` (SHA-256 of each file, incomplete chapters); `consolidated=true` adds the single file. |
| `md` | All | `# Volume`, then `## Chapter` above each chapter. |
| `bible` | All | The Book Bible as JSON. |
| `project` | All | The project archive, below. |

`allow_source=true` exports an unfinished translation with the originals in place of missing passages;
without it, an incomplete export answers `409`. `POST /api/exports/text` exports several volumes (a
series) as one ZIP with a `NN - Title/` folder per volume, and `POST /api/exports/epub` several
translated EPUB files. Text rendering follows the stored layout for text sources and gives one
paragraph per unit for an EPUB; no internal `⟦…⟧` marker is ever written.

### Project archive (schema version 3)

`translation-project.zip` contains `project.json` and the source files under names Libris sets:
`sources/<n>.epub|txt|json` (every source file of the volume) and, for JSON chapters, `texts/<n>.txt`.
`project.json` holds the volume, its chapters, passages, versions, memory, glossary, jobs and job state,
plus the series (`name`, `kind`, `authors`), the source files (`file`, `format`, `original_name`,
`media_type`, `sha256`, `meta`), and for each text chapter the file to cut again and the options that
give back the same units.

The archive keeps everything that makes up the work on the book: settings (title, series and volume,
languages, quality, memory backend, instructions), chapter and passage instructions, translations with
their status, stage, critiques and uncertainties, the full version history, glossary, Book Bible and
its revisions, characters, merges and links, memories, quality issues, jobs with their checkpoints and
per-passage state, and the figures of the model calls (operation, model, tokens, duration, cost,
status). It serves as a backup of one volume or to move it to another instance. Export refuses an
archive that the import could not read back (`MAX_UPLOAD_MB`, `MAX_UNPACKED_MB`), and the message
names the setting to raise on both servers.

Restoring (`POST /api/projects/import`) validates everything before writing anything: only
`project.json`, `original.epub` (archives of schema 1 and 2) and the `sources/…` and `texts/…` names are
accepted, none is used as a path; entry count, declared sizes, compression ratio, reads bounded by the
declared size, SHA-256 checksums and the consistency between sources and chapters are all checked. An
EPUB is imported again from the archive; a text volume is recreated with its recorded resources, so the
unit identifiers match, and every passage must have the same source text as in the archive. It all
happens in one transaction, and files are removed on failure. The restoring user becomes the owner;
the series is found or created by normalized name among theirs; a second continuous flow or an already
used volume `external_id` in the series is refused (`409`). Owners, members, permissions and providers
are never restored, nor are the full prompts and answers of model calls, progress events and the
OpenViking send queue. Interrupted jobs come back paused, without provider, so nothing restarts or is
billed without an action; an archived book comes back active. Archives of schema versions 1 and 2
remain readable. `NOT_ARCHIVED` lists the
columns deliberately left out; a test fails if a new column is neither archived nor listed.

## Security design

**Untrusted files**

- ZIP archives (EPUB, DOCX, project archives) are never extracted to paths the file chooses. Paths,
  duplicate entries, symbolic links, sizes, entry count and the whole-archive compression ratio are
  checked before anything is unpacked (`MAX_UNPACKED_MB`, `MAX_ENTRIES`, `MAX_COMPRESSION_RATIO`).
- XML parsers refuse entity declarations and never load DTDs or network resources; glossary TBX files
  use the same reader.
- Request bodies are bounded before they are buffered: 1 MiB without a session or Bearer token,
  `MAX_UPLOAD_MB` or `API_MAX_PAYLOAD_MB` otherwise. EPUBCheck runs are bounded in number and memory.

**Error messages**

- Messages are written in French where they are raised; when a request prefers English
  (`Accept-Language: en`, sent by the interface in English), the exception handlers translate the
  `detail` from the catalog in `backend/app/i18n.py`. Translation only rewrites the message text and
  never adds internal information. A test fails if a message raised in the code has no English entry.
- Unexpected errors answer with a diagnostic reference that points to the server log, never with a
  stack trace. Validation errors of the automation API never echo submitted values.

**Model output and previews**

- Model-generated HTML is never accepted as DOM structure; translations are text reinjected into the
  original markup.
- Chapter previews are sanitized and shown in a sandboxed iframe under a restrictive Content Security
  Policy. The interface sends a strict CSP, `X-Frame-Options`, `nosniff` and a same-origin referrer
  policy.

**Secrets**

- Provider keys, the OpenViking key, token webhook secrets and the saved webhook secret are encrypted
  at rest with `SECRET_KEY` (at least 32 characters). They never appear in API answers, traces or
  exports; the automation API names a provider and model only.
- API tokens are stored as SHA-256 hashes and compared in constant time.

**Access**

- Libraries are private, with per-book access control, including logs, event streams, versions and
  exports. Non-administrators see providers without their address. Changing the address or type of a
  provider that holds a key requires entering the key again, so a stored key is never sent to a new
  host.
- A shared editor cannot attach a book to a series of the owner that contains books the editor cannot
  read.
- Session cookies are HTTP-only, `SameSite=Strict`, and `Secure` with `COOKIE_SECURE=true`. State-
  changing requests are refused from origins outside `ALLOWED_ORIGINS` and from cross-site browser
  contexts (`Sec-Fetch-Site`).
- Failed sign-ins are throttled per client and account (20 failures in five minutes) and per client
  (200). The throttle and the automation API rate limit live in each process's memory: they are not a
  complete internet-facing abuse control.
- `/openapi.json` requires a session and can be disabled with `OPENAPI_ENABLED=false`. `/metrics`
  exists only when `METRICS_TOKEN` is set, and requires it.
- Live event streams are bounded per account and per process.

**Outbound connections**

- Only services an administrator configured are called. Outbound clients ignore proxy environment
  variables, do not follow redirects, and bound the size of answers (SearXNG: 2 MiB).
- Webhooks go only to allowed hosts, to public addresses (unless an allowed private network), with the
  address checked just before each call ([details](api.md#protections)).
- Web search queries chosen by the model remain a possible exfiltration channel under prompt
  injection: enable SearXNG only towards an instance you control.

**Containers and supply chain**

- Every container runs with `no-new-privileges` and `cap_drop: [ALL]`; the database keeps only the
  five capabilities its entry point needs to own its data and switch to the `postgres` user. Root
  file systems are read-only, `/tmp` is in memory, and large temporary files go to the data volume
  (`TMPDIR=/data/tmp`). Application processes run as non-root users.
- PostgreSQL and the Codex bridge are not published on host ports by the supplied Compose file.
- Python dependencies are installed with `--require-hashes` from hashed lock files
  (`backend/requirements.lock`, `codex_bridge/requirements.lock`, generated by
  `scripts/hash_lock.py`): a modified or substituted package is refused at image build time.

The operator's side (HTTPS, firewalling, backups, provider privacy terms, retention of model traces)
is covered in [operations](operations.md) and in the repository's [security policy](../SECURITY.md).

## Extending

The `ContextProvider` boundary lets other memory engines be added without touching the translation
engine. Generation models are configurable, and no assumption about a specific model family or context
window is encoded in the prompts.
