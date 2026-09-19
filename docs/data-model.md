# Data model

SQL is the source of truth. Files under `DATA_DIR` (source books and chapters) are referenced by SQL
rows at paths Libris chooses; nothing is ever located by a name taken from an upload.

## Series, volumes and chapters

| User vocabulary | SQL | Notes |
| --- | --- | --- |
| **Series** — the literary project shown first in the library | `series` | One owner; its name is unique per owner once case and spacing are folded (`normalized_name`). `kind` is `books` or `webnovel`. Default languages, provider, quality, context backend and instructions for new volumes; `bible` is the Series Bible snapshot (`bible_validated` when a person edited it). |
| **Volume** — the unit the pipeline processes | `projects` with `project_kind = volume` | `series_id` (null for a **standalone volume**), `volume_number`, `source_format` (`epub`, `txt`, `json`), `external_id` (integrations), `import_meta` (versioned: adapter, original name, segmentation…). |
| **Continuous chapter flow** of a webnovel | `projects` with `project_kind = serial` | At most one per series (partial unique index). TXT chapters imported without a volume go here. |
| **Chapter** | `chapters` | `position` is the order inside the volume; `chapter_number` is the author's number (may be `12.5`); `external_id`; `source_checksum` (SHA-256 of the normalized text); `import_meta.layout` for TXT/JSON (blank lines, indentation, scene breaks, title line); `context_stale` when an earlier chapter's source was replaced; `source_asset_id`. |
| **Passage** | `segments` | Unchanged: groups of units the models translate, with versions, human edits, reviews. |

`projects.series_name` is kept in step with `series_id` for clients of the 0.5 API: setting the name
attaches the volume to the owner's series of that normalized name (created if needed). New code uses
`series_id`.

## Sources

`source_assets` has one row per imported file: `format`, `original_name` (display only), `media_type`,
`storage_path` (relative to `DATA_DIR`; books imported before 0.6 keep their absolute path, with
`books/<project>.epub` as fallback), `size`, `sha256`, `meta` (e.g. detected text encoding), creation
date. EPUB volumes store their book at `books/<project>.epub`; TXT chapters and JSON payloads under
`sources/<project>/<asset>.<ext>`. `projects.original_path/original_hash` are still filled for EPUB
volumes during the compatibility period.

Unit identifiers of TXT and JSON chapters derive from a virtual resource `txt/<hash>` or `json/<hash>`
(the volume and the chapter number or external identifier) and the line index: importing the same
chapter again gives the same identifiers, and a replaced chapter keeps the identifiers of unchanged
lines.

## Imports

`import_sessions` hold the files uploaded for inspection (`DATA_DIR/staging/<session>`) and, once
confirmed, the answer of the commit, so that repeating a commit returns what the first one did.
Sessions expire after `IMPORT_SESSION_HOURS` (24 h); the retention removes their files.

A session has one `format`: `epub` (one file is a volume) or `txt`, `md`, `html`, `docx` (one file is
a chapter of a series; accepted extensions `.txt`, `.md`/`.markdown`, `.html`/`.htm`/`.xhtml`,
`.docx`). Structured chapters are stored as `source_assets` of their format and cut like TXT chapters
(resource `<format>/<hash>`, layout in `import_meta`). Every volume and text chapter records the
passage size it was cut with (`book_info.passage_max_chars`, `import_meta.passage_max_chars`; absent
means 3 500, the size of every import before 0.6).

## Series memory

| Table | Content |
| --- | --- |
| `series_entities` | Canonical identities of the series: characters, and places, organizations, objects named by the volumes' bibles. First appearance (`first_volume_number`, `first_position`), aliases, profile, `merged_into_id` after a person's merge. |
| `series_entity_links` | A volume's character (`entities`) attached to a series identity: `linked`, `proposed` (ambiguous: several identities bear the name — never merged automatically) or `rejected`; `human` when a person decided. |
| `series_relations` | Relations between series identities, with evidence and first appearance. |
| `series_glossary` | Series terms (`origin`: `volume` when aggregated from accepted volume terms, `human` for a person's decision, never rewritten by the aggregation). |
| `glossary.series_override` | A volume deliberately departs from the series term (audited). |
| `audit_entries` | Merges, splits, link decisions, Series Bible edits, series terms, glossary overrides, API token creation and revocation — never a secret. |

Priority when translating: explicit instructions, validated human decisions, locked term of the volume
(or a volume override), locked term of the series (a person's series decision or an earlier volume),
accepted term of the series, automatic proposals. Prompts only receive series knowledge from earlier
volumes: identities under the names those volumes used, their terms and human decisions.

## Automation

`api_tokens` (owner, name, SHA-256 of the secret, displayable prefix, scopes, expiry, revocation, last
use) and `translation_requests` (owner, token, `external_id`, `Idempotency-Key`, payload hash, series,
volume, job, status, options, chapters, error) — see [the API guide](api.md).

## Usage aggregates

`usage_daily` holds one row per UTC day, book, provider (`""` when unknown; not a foreign key, a
deleted provider keeps its history), operation, model, outcome and cache flag: `requests`,
`prompt_tokens`, `completion_tokens`, `duration`, `cost` (at the price recorded with each request).
Rows are added by the worker's hourly rollup of requests older than two hours
(`app/maintenance/usage.py`); `app_settings["usage_rollup"]` is the watermark. Statistics read the
aggregates plus the requests created since the watermark. Rows follow their book (`ON DELETE CASCADE`).

## External memory

`memory_outbox` is the send queue to OpenViking; `uri` records where an entry was last written. Event
documents are derived from `memories` — see [OpenViking](openviking.md).
