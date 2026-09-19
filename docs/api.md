# Automation API

This page is for developers who want a script or another server to send books to Libris and get the
translation back, with no step in the web interface. You send an EPUB, some TXT chapters or a JSON
document; Libris runs the whole pipeline on its own; you poll the status (or receive a webhook) and
download the result with its completion report.

The automation API lives under `/api/v1` and is separate from the API used by the web interface:

- it only accepts API tokens (`Authorization: Bearer …`). The interface's session cookie does not open
  `/api/v1`, and a token does not open the interface's routes;
- work is asynchronous. A request is saved in the database before the `202 Accepted` answer, the
  pipeline runs in the worker, and no HTTP connection stays open during a translation;
- a request always ends: `completed`, `completed_with_residuals`, `failed` (with the reason) or
  `cancelled`. It never stays `running` forever.

## At a glance

| Method and path | Scope | Purpose |
| --- | --- | --- |
| `POST /api/v1/translation-requests` | `content:write` (+ `pipeline:start` to translate) | Send an EPUB, TXT chapters or a JSON document |
| `GET /api/v1/translation-requests/{id}` | `jobs:read` | Status, progress and report (`?wait=` to long-poll) |
| `POST /api/v1/translation-requests/{id}/pause` | `jobs:control` | Pause the request's job |
| `POST /api/v1/translation-requests/{id}/resume` | `jobs:control` | Resume it |
| `POST /api/v1/translation-requests/{id}/cancel` | `jobs:control` | Cancel it |
| `GET /api/v1/translation-requests/{id}/result` | `results:read` | Download the result (EPUB, JSON, TXT or ZIP), for the request's chapters, only the new ones or the whole volume (`?scope=`) |
| `GET /api/v1/providers` | `content:write` | List the providers a request may use |
| `GET /api/v1/series` | `series:read` | List your series |
| `GET /api/v1/series/{id}` | `series:read` | One series and its volumes |
| `GET /api/v1/glossaries` | `series:read` | List your shared glossaries |
| `POST /api/v1/glossaries` | `content:write` | Create a shared glossary |
| `GET /api/v1/glossaries/{id}` | `series:read` | One shared glossary and its terms |
| `GET /api/v1/glossaries/{id}/export/{format}` | `series:read` | Download it as JSON, CSV or TBX |
| `POST /api/v1/glossaries/{id}/import` | `content:write` | Import a JSON, CSV or TBX file (`?dry_run=true` to preview) |
| `GET /api/v1/series/{id}/shared-glossary` | `series:read` | The shared glossary a series follows |
| `PUT /api/v1/series/{id}/shared-glossary` | `content:write` | Attach a series to a shared glossary, or detach it |

Every example on this page uses these shell variables:

```bash
export LIBRIS_URL=https://libris.example.org
export LIBRIS_TOKEN=lbr_xxxxxxxx_...        # shown once when the token is created
export PROVIDER_ID=...                      # a provider id, see "Choosing a provider" below
```

## Create a token

1. In the interface, open **My account › API tokens** (administrators also find it under
   **Settings › Automation API**).
2. Under **Create a token**, give it a name, tick the permissions it needs and choose an expiration
   (30, 90 or 365 days, or never).
3. Optionally tick **Sign webhooks with a secret of this token** (see [Webhooks](#webhooks)).
4. Copy the secret now: it is displayed **once** and never again.

A token acts on behalf of its owner: it only sees the owner's series and requests, and it stops
working when the owner's account is disabled. The list shows each token's prefix, permissions,
creation and expiry dates, last use (updated at most once a minute) and state (active, expired,
revoked). **Revoke** is immediate and final; clients using the token then get `401`. Creating and
revoking a token are written to the audit log, without the secret. An account can hold at most 50
tokens that are not revoked.

| Scope | Interface label | Allows |
| --- | --- | --- |
| `series:read` | Read series | `GET /api/v1/series`, `GET /api/v1/series/{id}` |
| `content:write` | Send content | `POST /api/v1/translation-requests`, `GET /api/v1/providers` |
| `pipeline:start` | Start the pipeline | Together with `content:write`: requests that start the translation (the default). Without it, only `start=false` (import only) is accepted. |
| `jobs:read` | Follow jobs | `GET /api/v1/translation-requests/{id}` |
| `jobs:control` | Control jobs (pause, resume, cancel) | `POST …/pause`, `…/resume`, `…/cancel` |
| `results:read` | Read results | `GET /api/v1/translation-requests/{id}/result` |

A token has the form `lbr_` + 8 identifying characters + `_` + a random secret (256 bits). Libris
stores only its SHA-256 and compares it in constant time.

### Managing tokens from a script

The interface manages tokens through these routes, which use the **session cookie**, not a token:

| Route | Body and answer |
| --- | --- |
| `GET /api/tokens` | The caller's tokens: `id, name, prefix, scopes, created_at, expires_at, revoked_at, last_used_at, state, webhook_secret` (a boolean: whether the token has its own signing secret), `max_priority`, `max_running`, `max_queued` (see [Queue priority and quotas](#queue-priority-and-quotas)). |
| `POST /api/tokens` | Body `{"name": "…", "scopes": ["…"], "expires_in_days": 90, "webhook_secret": false}`, optionally with `max_priority` (`low`, `normal` (default) or `high`), `max_running` (1–1000) and `max_queued` (1–100000). `name` 1–100 characters, at least one scope, `expires_in_days` 1–3650 or `null` for no expiry. Answers `201` with the token view plus `token` (the secret) and, when asked, `webhook_secret` (the signing secret). This is the only answer that ever contains them. |
| `PUT /api/tokens/{id}/queue` | Body `{"max_priority": "normal", "max_running": null, "max_queued": null}`: changes the token's queue limits without changing its secret; returns its view. |
| `DELETE /api/tokens/{id}` | Revokes the token and returns its view. |

### Queue priority and quotas

Libris shares its providers between accounts with a fair queue: waiting jobs start by priority, then from the
account (and the token) with the fewest jobs running, in turn between accounts (see
[architecture](architecture.md#fair-queue)). A request may ask for a priority, `pipeline.priority` in a JSON
document or the `priority` option of a file upload: `low`, `normal` (the default) or `high`.

- The priority may not exceed the token's `max_priority` (`normal` unless set otherwise) nor the account's
  ceiling (`high` for administrators and for accounts an administrator allowed in **Settings › Queue**,
  `normal` otherwise). Above it, the request is refused with `403 priority_not_allowed` and `max_priority` in
  the error. A token whose `max_priority` is `low` sends its requests at low priority by default.
- `max_running` limits the token's jobs running at once: the next ones wait (`queue.reason` is
  `token_limit`). The account's own limit (`QUEUE_MAX_RUNNING_PER_ACCOUNT` or its row in **Settings ›
  Queue**) applies as well (`account_limit`).
- `max_queued` limits the token's requests and jobs waiting to start. A new request over it, or over the
  account's waiting quota, is refused with `429 queue_full` and `scope` (`token` or `account`) and `limit` in
  the error; nothing is stored. Retry once one of them has started. A replay of an accepted request (same
  `Idempotency-Key` or `external_id`) is still answered. Resuming a paused request counts as a new entry in
  the queue.

The priority does not change the request's content: sending the same request again with another priority is a
replay of the first one.

## Send a translation request

`POST /api/v1/translation-requests` accepts three kinds of input:

| Input | How to send it | Default result |
| --- | --- | --- |
| **An EPUB** | `multipart/form-data` with one `.epub` file in the field `file`, options as form fields; or the raw file as `Content-Type: application/epub+zip`, options in the query string | The translated EPUB |
| **TXT chapters** | `multipart/form-data` with one or more `.txt` files in `file` or `files`, options as form fields | JSON |
| **A JSON document** | `Content-Type: application/json`; or one `.json` file in the multipart field `file` (with no other form field) | JSON (or `output.format`) |

One request carries one kind of file: mixing `.epub`, `.txt` and `.json` files, or sending several
EPUB or JSON files, is refused with `422`. Structured formats (Markdown, HTML, DOCX) are not accepted
here: import them through the interface.

Every accepted request answers `202 Accepted`, with a `Location` header pointing to its status:

```json
{
  "request_id": "5b1c…",
  "external_id": "tbate-volume-12",
  "series_id": "…",
  "project_id": "…",
  "job_id": "…",
  "input": "json",
  "status": "pending",
  "status_url": "/api/v1/translation-requests/5b1c…",
  "result_url": "/api/v1/translation-requests/5b1c…/result"
}
```

`input` is `epub`, `txt` or `json`. `job_id` is `null` while the request waits for its volume
(`status: "queued"`).

### Choosing a provider

A translation needs a model provider. Libris uses, in order, `provider_id` from the request, the
volume's provider, then the series' default provider. If none is set, the request is refused with
`422 provider_required`; an unknown id answers `422 unknown_provider`.

The simplest setup is to choose a default provider for the series once, in the interface (the series'
**Defaults** tab), and leave `provider_id` out. To find provider ids, list the providers with a token
that has the `content:write` scope:

```bash
curl -sS "$LIBRIS_URL/api/v1/providers" -H "Authorization: Bearer $LIBRIS_TOKEN"
```

```json
[{"id": "…", "name": "Local", "kind": "openai", "model": "qwen3-32b", "created_at": 1789000000.0,
  "default_for_series": ["…"]}]
```

The list is sorted by name. `kind` is the connection type (`openai`, `openai_direct`, `openai_responses`,
`anthropic` or `codex_chatgpt`), and `default_for_series` lists the ids of your series that use the
provider by default. The provider's address and API key are never included. Providers are managed by
administrators in the interface.

### Send an EPUB

```bash
# Multipart: the file and its options as form fields
curl -sS -X POST "$LIBRIS_URL/api/v1/translation-requests" \
  -H "Authorization: Bearer $LIBRIS_TOKEN" \
  -H "Idempotency-Key: silver-tower-fr-1" \
  -F "file=@The Silver Tower.epub;type=application/epub+zip" \
  -F series="Silver Saga" -F volume=1 \
  -F source_language=en -F target_language=fr \
  -F provider_id="$PROVIDER_ID" -F quality=high

# Raw body: the options in the query string
curl -sS -X POST "$LIBRIS_URL/api/v1/translation-requests?target_language=fr&provider_id=$PROVIDER_ID&filename=tower.epub" \
  -H "Authorization: Bearer $LIBRIS_TOKEN" \
  -H "Content-Type: application/epub+zip" \
  --data-binary @tower.epub
```

What Libris does with it:

- The EPUB becomes a volume: standalone, or in the series named by `series` (created if missing) or
  `series_id`. Its pipeline starts at once.
- The same file sent again (same bytes) reuses the volume already made from it, and the request
  records that decision. An archived volume answers `409 volume_archived`.
- In a series, without `volume`, Libris takes the volume number from the file name when that number is
  free, otherwise the number after the last volume. The choice and its reason are recorded in the
  report (`decisions.intake`). A volume number already used by another book answers
  `409 volume_conflict`.
- Languages default to the language declared in the EPUB (`en` when none) and to the series' target
  language (`fr` when there is none).
- A file that cannot be read as an EPUB answers `422 invalid_epub`.
- If a job is already running on the volume, its settings are left alone and the request waits for it
  (the decision is recorded).

### Send TXT chapters

```bash
curl -sS -X POST "$LIBRIS_URL/api/v1/translation-requests" \
  -H "Authorization: Bearer $LIBRIS_TOKEN" \
  -F "files=@Chapter 1.txt" -F "files=@Chapter 2.txt" -F "files=@Afterword.txt" \
  -F series="Web Saga" -F volume=1 \
  -F source_language=en -F target_language=fr \
  -F provider_id="$PROVIDER_ID" -F output_format=txt-zip
```

Each file becomes one chapter, and the request behaves exactly like the JSON document it stands for
(same idempotency, volume lookup, conflicts and results). `series` (or `series_id`), `volume`,
`source_language` and `target_language` are required.

- **Encoding.** Files are decoded as UTF-8, UTF-16 with a BOM, or, as a last resort, Windows-1252. The
  last case is recorded in the report.
- **Chapter numbers** come from the file names (`Chapter 12.txt`, `012 - Title.txt`, or the part that
  varies across the batch). A file with no number, or with the same number as another file, gets the
  next free number in upload order. These choices are never questions: each one is recorded with its
  reason in `report.decisions.intake`.
- **Titles** are taken from the file names.

### Send a JSON document

```json
{
  "external_id": "tbate-volume-12",
  "series": {"id": null, "name": "The Synthetic Saga", "create_if_missing": true},
  "volume": {"external_id": "volume-12", "number": 12, "title": "Volume 12"},
  "author": "A. Author",
  "source_language": "en",
  "target_language": "fr",
  "chapters": [
    {"external_id": "chapter-001", "number": 1, "title": "Chapter 1",
     "content": "First paragraph.\n\nSecond paragraph.\n"}
  ],
  "replace_changed_chapters": false,
  "discard_human": false,
  "pipeline": {"start": true, "provider_id": null, "quality": "high",
               "context_backend": "hybrid", "final_review": true, "priority": "normal"},
  "output": {"format": "json"},
  "callback_url": "https://hooks.example.org/libris"
}
```

```bash
curl -sS -X POST "$LIBRIS_URL/api/v1/translation-requests" \
  -H "Authorization: Bearer $LIBRIS_TOKEN" \
  -H "Content-Type: application/json" \
  -H "Idempotency-Key: tbate-volume-12-run-1" \
  --data @request.json

# The same document as an uploaded file
curl -sS -X POST "$LIBRIS_URL/api/v1/translation-requests" \
  -H "Authorization: Bearer $LIBRIS_TOKEN" \
  -H "Idempotency-Key: tbate-volume-12-run-1" \
  -F "file=@request.json;type=application/json"
```

| Field | Required | Rules |
| --- | --- | --- |
| `external_id` | no | Your identifier for the request: starts with a letter or digit, then letters, digits and `._:/-`, up to 200 characters. Unique per owner (see [Sending twice](#sending-the-same-request-twice)). |
| `series` | yes | `id` (a series you own) **or** `name`. An unknown name is created when `create_if_missing` is true (the default), otherwise `404 series_not_found`. An archived series answers `409 series_archived`. |
| `volume.number` | yes, unless `volume.latest` | 1–10000. |
| `volume.latest` | no | Default `false`. `true` instead of a number: the chapters go to the series' last volume (the highest number; the series' continuous chapter feed when it has no numbered volume; volume 1, created, when it has neither). See [Following a series over time](#following-a-series-over-time). |
| `volume.external_id`, `volume.title` | no | The volume is found by `external_id`, then by number within the series; otherwise it is created (title defaults to "Series — number"). A volume that came from an EPUB answers `409 volume_conflict`, and so does a volume with that number but another `external_id`. An archived volume answers `409 volume_archived`. |
| `author` | no | Up to 500 characters. |
| `source_language`, `target_language` | yes | BCP 47 tags such as `en`, `fr-FR`, `zh-Hant`, `es-419`. Applied to the volume. |
| `chapters` | yes | 1 to `API_MAX_CHAPTERS` (2000) chapters. `number` is required (0–100000, decimals such as `12.5` allowed); numbers and `external_id`s must not repeat. `content` must not be blank and holds at most `TEXT_CHAPTER_MAX_CHARS` characters. `title` is optional; without it nothing is added to the text and the chapter is named by its number. Chapters are ordered by number. |
| `replace_changed_chapters` | no | Default `false`: a chapter already in the volume (same `external_id` or same number) with a different text answers `409 chapter_conflict` with the list in `conflicts`. With `true`, it is replaced; passages whose text did not change keep their translation. |
| `discard_human` | no | Default `false`: a replacement that would drop passages a person corrected or validated answers `409 conflict` with `protected_segments`. With `true` (and `replace_changed_chapters`), those edits are discarded. |
| `pipeline.start` | no | Default `true`: run the whole pipeline (needs the `pipeline:start` scope). `false` only imports the chapters. |
| `pipeline.provider_id` | no | See [Choosing a provider](#choosing-a-provider). |
| `pipeline.quality` | no | `fast`, `normal`, `high` or `maximum` (see [the autopilot guide](autopilot.md#what-each-quality-level-does)). |
| `pipeline.context_backend` | no | `internal`, `openviking` or `hybrid` (see [OpenViking](openviking.md)). |
| `pipeline.final_review` | no | Default `true`. `false` skips the final review. It never runs when the server sets `FINAL_REVIEW_ENABLED=false`. |
| `pipeline.priority` | no | `low`, `normal` (default) or `high`, within the token's ceiling (see [Queue priority and quotas](#queue-priority-and-quotas)). |
| `output.format` | no | Default format of the result: `json`, `txt`, `txt-zip` or `epub-bilingual`. |
| `callback_url` | no | A webhook called when the request ends (see [Webhooks](#webhooks)). |
| `callback_events` | no | Extra webhook events, on top of the final one: `["chapters.translated"]` sends a batch each time chapters of the request are translated (see [Batches of translated chapters](#batches-of-translated-chapters)). Needs `callback_url`. |

Unknown fields are refused. Libris never downloads anything from a URL found in the document: text is
taken as it is. Chapters go through the same text importer as TXT files (same passages, layout and
checksums), and the normalized document is stored as a source file of the volume.

A new volume, and a series created by the request, take `provider_id`, `quality` and
`context_backend` from `pipeline` when given, otherwise from the series defaults.

### Options of file uploads

For EPUB and TXT uploads, options are form fields (or query parameters for a raw EPUB body). Empty
values count as "not given"; unknown options are refused.

| Option | Meaning |
| --- | --- |
| `series` or `series_id` | The series by name (created when missing) or by id; not both. Required for TXT, optional for an EPUB (standalone volume otherwise). |
| `volume` | Volume number (1–10000), or `latest` for the series' last volume (TXT only, see `volume.latest` above). Required for TXT. |
| `external_id` | Your identifier of the request. |
| `volume_external_id` | Your identifier of the volume. |
| `title`, `author` | Volume title and author (an EPUB keeps its own otherwise). |
| `source_language`, `target_language` | BCP 47 tags. Both required for TXT. |
| `provider_id`, `quality`, `context_backend`, `final_review`, `priority` | As in `pipeline` above. |
| `start` | `true` (default) runs the whole pipeline; `false` only imports. |
| `output_format` | `epub` (EPUB input only; the default for an EPUB), `json`, `txt`, `txt-zip` or `epub-bilingual`. |
| `callback_url` | See [Webhooks](#webhooks). |
| `callback_events` | Comma-separated extra events, for example `chapters.translated` (see `callback_events` above). |
| `replace_changed_chapters`, `discard_human` | TXT only, as in the JSON document. |
| `filename` | Raw EPUB body only: the file name, used to guess the volume number. |

### Sending the same request twice

Send an `Idempotency-Key` header (1–200 printable characters), an `external_id`, or both. Sending the
same content again with the same key or `external_id` answers `200 OK` with the original request and
the header `Idempotent-Replayed: true`: nothing is created twice. The same key or `external_id` with
different content answers `409 idempotency_conflict`.

"Same content" is compared after validation: key order and whitespace do not matter, and a JSON
document and the multipart upload of the same chapters are equivalent. For an EPUB, the content is the
file plus its options, `callback_url` excepted.

Even without a key, sending chapters that are already in the volume with the same text never
duplicates a series, a volume or a chapter: they are reported as `unchanged`.

### Following a series over time

A webnovel is translated as it is published: send each new batch of chapters as its own request, to
the same series and volume (or with `volume.latest: true`, `volume=latest` for TXT files, to follow
the series' last volume without tracking its number).

```bash
curl -sS -X POST "$LIBRIS_URL/api/v1/translation-requests" \
  -H "Authorization: Bearer $LIBRIS_TOKEN" \
  -F "files=@Chapter 51.txt" -F "files=@Chapter 52.txt" \
  -F series="Web Saga" -F volume=latest \
  -F source_language=en -F target_language=fr \
  -F callback_url=https://hooks.example.org/libris -F callback_events=chapters.translated
```

- **Appended in order.** Chapters are placed by number among the chapters already in the volume and
  matched by `external_id`, then by number: a chapter sent again with the same text is `unchanged`,
  one with another text is refused unless `replace_changed_chapters` is true. Give numbers in the
  file names (`Chapter 51.txt`): a file without a number is numbered within its own request only.
- **Only the new chapters are translated.** Chapters already translated are neither translated nor
  reviewed again: they give their context (glossary, characters, summaries and the previous
  passages) to the new ones. The request's job covers the new or replaced chapters, plus any chapter
  of the volume still missing a translation. The status document lists them in `chapters.new`.
- **One request at a time per volume.** A request sent while the volume is busy waits (`queued`)
  and starts after the running one.
- **Updated results.** Each request's result can cover its own chapters, only the new ones, or the
  whole volume (see `scope` in [Get the result](#get-the-result)).

## Follow a request

```bash
curl -sS "$LIBRIS_URL/api/v1/translation-requests/$REQUEST_ID" \
  -H "Authorization: Bearer $LIBRIS_TOKEN"

# Long poll: answers as soon as the request ends, or after 60 seconds at most
curl -sS "$LIBRIS_URL/api/v1/translation-requests/$REQUEST_ID?wait=60" \
  -H "Authorization: Bearer $LIBRIS_TOKEN"
```

`?wait=<seconds>` holds the answer until the request ends, up to `API_RESULT_MAX_WAIT_SECONDS`
(60 by default, 600 at most); a larger value is cut to that limit, and values above 600 are refused
with `422`. Nothing is held open in the database while waiting.

### How a request moves

1. **Queued.** If another job is working on the volume, the request waits as `queued`. Its chapters
   are imported only once no job is active on the volume. The worker checks queued requests every
   2 seconds and starts each one as soon as its volume is free; requests on the same volume start in
   arrival order. A volume held by a paused or blocked job stays busy until that job is resumed and
   finishes, or is cancelled.
2. **Running.** The pipeline runs under the [autopilot](autopilot.md): refusals, invalid model answers
   and open review points never wait for a person, and a provider outage switches to fallback
   providers after a bounded wait. (If an administrator turned the autopilot off, or the volume opted
   out, these steps behave as in the interface.)
3. **Finalizing.** The job ended; Libris builds and stores the result, then writes the report.
4. **Ended.** One of the final statuses below.

Everything lives in the database: restarting the API or the worker loses nothing.

| Status | Meaning |
| --- | --- |
| `queued` | Waiting for the volume to be free. |
| `imported` | Chapters imported, nothing started (`start` was false). This is an end state. |
| `pending`, `running` | The job is waiting for a worker, or working. |
| `paused` | Paused by you or by a person in the interface. |
| `waiting` | The provider is temporarily unavailable; retried automatically. |
| `blocked` | Needs attention, for example the provider refuses its credentials. |
| `finalizing` | The job ended; the result is being built. |
| `completed` | Every passage is translated; the result is stored. |
| `completed_with_residuals` | The result is stored, but some passages kept their source text (refused, failed, or restored by the EPUB repair). They are listed in `report.residuals` with their reason. |
| `failed` | See `error`: the job failed, the EPUB could not be repaired, the job stayed stalled too long, or the request ran past its maximum duration. |
| `cancelled` | Cancelled by you or by a person. |

**No request runs forever.** If its job stays `paused`, `blocked` or `waiting` for more than
`API_REQUEST_STALL_MINUTES` (360), Libris cancels the job and the request fails with the reason. The
same happens to a request still unfinished `API_REQUEST_MAX_HOURS` (168) after it was created. When
the autopilot reports that it failed, the request fails with the autopilot's reason.

### The status document

```json
{
  "request_id": "5b1c…", "external_id": "tbate-volume-12", "series_id": "…", "project_id": "…",
  "job_id": "…", "input": "json", "status": "running",
  "status_url": "/api/v1/translation-requests/5b1c…",
  "result_url": "/api/v1/translation-requests/5b1c…/result",
  "created_at": 1790000000.0, "updated_at": 1790000100.0, "finished_at": null,
  "stage": "translation", "step": "translation",
  "progress": {"segments": 412, "translated": 180, "percent": 44,
               "stages": [{"key": "translation", "done": 180, "total": 412, "percent": 44}]},
  "estimate": {"…": "…"},
  "error": "", "stop_reason": "", "next_attempt": 0,
  "chapters": {"created": 3, "unchanged": 0, "replaced": 0, "new": ["…"],
               "items": [{"chapter_id": "…", "external_id": "chapter-001", "number": 1, "title": "Chapter 1",
                          "segments": 140, "translated": 60, "validated": 0, "flagged": 0, "complete": false}]},
  "options": {"start": true, "final_review": true, "output_format": "json"},
  "priority": "normal",
  "queue": null,
  "result": null,
  "report": null,
  "webhook": {"state": "pending", "attempts": 0, "error": ""},
  "chapter_events": null
}
```

| Field | Meaning |
| --- | --- |
| `stage` | Current stage of the volume: `import`, `analysis`, `translation`, `review` or `export` (`null` without a job). |
| `step` | Current step of the job (for example `translation`, `final_review`, `autopilot`, `arbitration`). |
| `progress` | `segments`, `translated` and `percent` for the request's chapters (every chapter for an EPUB), and `stages`, the volume's progress per stage. |
| `estimate` | Remaining time and cost, once enough model calls have been observed; otherwise `null`. |
| `error`, `stop_reason`, `next_attempt` | Why the job stopped or is waiting, and when it will retry (Unix time, `0` when not waiting). |
| `priority` | The request's priority (`low`, `normal`, `high`), as changed by a person in the interface if it was. |
| `queue` | While the request waits to start: `position` (its place in the line of its provider, 1 = next), `reason` (`starting`, `provider_busy`, `account_limit`, `token_limit`, `retry_scheduled`, `provider_missing`, or `volume_busy` while another job holds the volume), `effective_priority` (raised by waiting) and `next_attempt`. `null` once it runs or ended. |
| `chapters` | How many chapters were `created`, `unchanged` or `replaced`, `new` (the ids of the created and replaced ones), and per chapter its passages, translated, validated and flagged counts, and whether it is `complete`. |
| `result` | Once stored: `format`, `media_type`, `filename`, `size`, `sha256`, `created_at`. |
| `report` | The [completion report](#completion-report), once the request ended. |
| `webhook` | Only when a `callback_url` was given: `state` (`pending`, `delivered`, `failed`), `attempts`, last `error`. |
| `chapter_events` | Only when `callback_events` was given: `batches` queued so far, how many are `delivered`, `pending` or `failed`, `waiting_chapters` (not translated yet) and the last `error`. |

Times are Unix timestamps in seconds.

### Pause, resume or cancel

```bash
curl -sS -X POST "$LIBRIS_URL/api/v1/translation-requests/$REQUEST_ID/pause"  -H "Authorization: Bearer $LIBRIS_TOKEN"
curl -sS -X POST "$LIBRIS_URL/api/v1/translation-requests/$REQUEST_ID/resume" -H "Authorization: Bearer $LIBRIS_TOKEN"
curl -sS -X POST "$LIBRIS_URL/api/v1/translation-requests/$REQUEST_ID/cancel" -H "Authorization: Bearer $LIBRIS_TOKEN"
```

Each answers with the status document. They follow the same rules as the interface: `409` when the
job's state does not allow the action. A request that has no job yet (`queued`) can only be cancelled;
pausing or resuming it answers `409 not_started`.

## Get the result

```bash
# The default format: the translated EPUB for an EPUB, otherwise the request's output format
curl -sS "$LIBRIS_URL/api/v1/translation-requests/$REQUEST_ID/result" \
  -H "Authorization: Bearer $LIBRIS_TOKEN" -o book.fr.epub

# JSON
curl -sS "$LIBRIS_URL/api/v1/translation-requests/$REQUEST_ID/result?format=json" \
  -H "Authorization: Bearer $LIBRIS_TOKEN" -o result.json

# One UTF-8 text file, chapters under their translated headings
curl -sS "$LIBRIS_URL/api/v1/translation-requests/$REQUEST_ID/result?format=txt" \
  -H "Authorization: Bearer $LIBRIS_TOKEN" -o volume-12.txt

# ZIP: chapters/001 - Title.txt …, manifest.json with the SHA-256 of each file
curl -sS "$LIBRIS_URL/api/v1/translation-requests/$REQUEST_ID/result?format=txt-zip" \
  -H "Authorization: Bearer $LIBRIS_TOKEN" -o volume-12.zip

# Bilingual EPUB for proofreading: each source paragraph with its translation (any input)
curl -sS "$LIBRIS_URL/api/v1/translation-requests/$REQUEST_ID/result?format=epub-bilingual&layout=side-by-side" \
  -H "Authorization: Bearer $LIBRIS_TOKEN" -o volume-12-bilingual.epub

# Whatever is ready so far
curl -sS "$LIBRIS_URL/api/v1/translation-requests/$REQUEST_ID/result?format=json&partial=true" \
  -H "Authorization: Bearer $LIBRIS_TOKEN"
```

**Choosing the format.** `?format=` wins (`epub`, `json`, `txt`, `txt-zip`, `epub-bilingual`);
otherwise the `Accept` header (`application/epub+zip`, `application/json`, `text/plain`,
`application/zip`); otherwise the request's own format. `epub` exists only for a request that sent an
EPUB (`409 format_unavailable` otherwise).

**Bilingual EPUB.** `epub-bilingual` exists for every request, whatever was sent: a new EPUB 3 with
one page per chapter, where each source paragraph is followed by its translation (`layout=interleaved`,
the default) or placed next to it in two columns (`layout=side-by-side`; the columns stack on a narrow
screen). It carries the text only (no image, no original styling) and is meant for proofreading on an
e-reader. In a partial result, a passage without translation shows its source and an empty
translation marked `—`. A stored bilingual result is the interleaved one; `layout=side-by-side` is
rendered on demand. When EPUBCheck is installed and refuses the book, the answer is
`422 delivery_failed`.

**What it covers.** `?scope=` chooses, in reading order:

| `scope` | Chapters |
| --- | --- |
| `request` (default) | The chapters the request sent, `unchanged` ones included; for an EPUB, the whole book. |
| `new` | Only the chapters the request created or replaced: the new chapters of a follow-up. |
| `volume` | Every chapter of the volume, those of earlier requests included: the updated volume. |

The `epub` format is always the whole book. The JSON result says its `scope`. With `new` or `volume`,
`complete` (and `X-Libris-Complete`) is true only when every chapter covered is fully translated.

**Stored and rendered results.** When a request ends successfully, Libris builds its result once, in
the request's default format and the `request` scope, and stores it under `DATA_DIR/results/<request id>/`. That file is
served as is. Other formats are rendered on demand from the database. Stored files are removed after
`RETENTION_RESULTS_DAYS` (30 days); asking again then renders the result from the database.

**Before the end.** While the request is not finished, the answer is `409 result_not_ready` with
`status`, `incomplete_chapters` and a `Retry-After: 5` header. A failed or cancelled request answers
`409 request_failed` or `409 request_cancelled` with the `reason`. Add `?wait=<seconds>` to wait for
the end first, or `?partial=true` to get what is ready: then `complete` is `false`, and
`incomplete_chapters` lists the chapters that are not fully translated.

**Missing passages keep their source text** in every format: residuals of a
`completed_with_residuals` request, and passages not yet translated in a partial result.

Every result carries two headers: `X-Libris-Complete: true|false` and `X-Libris-Status: <status>`.
Files other than JSON also carry a `Content-Disposition` with a file name. If the volume was deleted,
the answer is `404 volume_not_found`.

### The delivered EPUB

The translated EPUB is rebuilt from the original file with every translated passage. Passages without
a usable translation keep their source text and markup: passages not translated, passages kept in the
original by the autopilot, and passages whose translated markup no longer matches the source
(`markup_mismatch`).

The book is then checked with EPUBCheck (when the server has `EPUBCHECK_JAR`). If EPUBCheck rejects it,
Libris repairs it on its own: the files named by the errors go back to their source text (the passages
concerned become residuals with the reason `epubcheck_repair`), and the book is checked again, up to
`DELIVERY_REPAIR_ATTEMPTS` (3) times. Errors the original EPUB already had are not caused by the
translation: they are listed in `report.delivery.inherited_errors` and do not block delivery. Only
when the repairs run out does the request fail, with the errors in `report.delivery.errors`. When the
EPUB is rendered on demand and cannot be built, the answer is `422 delivery_failed`.

### The JSON result

```json
{
  "schema_version": 1,
  "request_id": "…", "external_id": "tbate-volume-12", "status": "completed", "complete": true,
  "series": {"id": "…", "name": "The Synthetic Saga"},
  "volume": {"project_id": "…", "external_id": "volume-12", "number": 12, "title": "Volume 12"},
  "source_language": "en", "target_language": "fr",
  "strategy": {"provider": {"name": "Local", "model": "…"}, "quality": "high",
               "context_backend": "hybrid", "final_review": true},
  "incomplete_chapters": [],
  "chapters": [{
    "chapter_id": "…", "external_id": "chapter-001", "number": 1,
    "title": "Chapter 1", "translated_title": "Chapitre 1",
    "complete": true, "missing_segments": 0,
    "translation": "Premier paragraphe.\n\nDeuxième paragraphe.\n",
    "source_sha256": "…", "sha256": "…",
    "review": {"segments": 2, "validated": 0, "flagged": 0},
    "issues": [], "flagged_passages": []
  }],
  "report": {"…": "the completion report"}
}
```

- `sha256` is the SHA-256 of `translation` (UTF-8); `source_sha256` is that of the normalized source
  text.
- `issues` lists unresolved quality issues (`segment_id`, `severity`, `code`, `message`);
  `flagged_passages` lists passages still flagged (`check`, `error` or `refused`, not validated).
- `strategy` names the provider and model only, never the provider's address or key.
- `report` is the completion report once the request ended, `null` before.

### Completion report

The report appears in the status document (`report`), in the JSON result, as `report.json` inside
the stored ZIP, and in the webhook.

```json
{
  "version": 1, "outcome": "completed_with_residuals", "reason": null,
  "passages": {"total": 412, "translated": 410, "source_retained": 1, "untranslated": 1, "flagged": 3,
               "validated": 0, "human": 0,
               "by_status": {"ok": 407, "check": 3, "source_retained": 1, "error": 1}},
  "residual_total": 2,
  "residuals": [{"segment_id": "…", "chapter_id": "…", "chapter_external_id": null,
                 "chapter_title": "…", "position": 118, "status": "source_retained",
                 "kept": "source", "reason": "…"}],
  "residuals_truncated": false,
  "usage": {"calls": 1290, "prompt_tokens": 2410000, "completion_tokens": 610000,
            "cached_calls": 12, "cost": 3.41},
  "durations": {"total_seconds": 5230.1, "queued_seconds": 0.4, "job_seconds": 5211.8},
  "autopilot": {"outcome": "completed_with_residuals", "rounds": 2, "reason": null},
  "decisions": {"autopilot": 17,
                "intake": [{"file": 2, "name": "Afterword.txt", "chapter_number": 3.0,
                            "confidence": "low", "reason": "…"}]},
  "delivery": {"validation": {"available": true, "valid": true}, "repairs": [], "inherited_errors": []},
  "quality": {"scored": 411, "average": 91.4, "minimum": 40, "to_review": 6, "review_below": 70,
              "bands": {"good": 380, "fair": 25, "weak": 5, "poor": 1},
              "histogram": [0, 0, 0, 0, 1, 2, 3, 10, 35, 360],
              "weakest_chapters": [{"chapter_id": "…", "title": "…", "external_id": null, "number": 12.0,
                                    "project_id": "…", "passages": 38, "scored": 38, "average": 78.2,
                                    "minimum": 40, "weak": 3, "…": "…"}],
              "review_first": [{"segment_id": "…", "chapter_id": "…", "chapter_title": "…",
                                "chapter_external_id": null, "position": 118, "score": 40, "band": "poor",
                                "signals": [{"code": "source_retained", "count": 1, "penalty": 60}],
                                "excerpt": "…", "…": "…"}]}
}
```

| Field | Meaning |
| --- | --- |
| `outcome`, `reason` | `completed`, `completed_with_residuals`, `failed` or `cancelled`, and why when it did not complete. |
| `passages` | Counts over the request's passages (the whole book for an EPUB). |
| `residuals` | Passages delivered in their source text, at most 500 (`residual_total` counts them all, `residuals_truncated` says when the list is cut). `reason` is the autopilot's when it recorded one, else the passage's last error, else `source_retained`, `untranslated`, `markup_mismatch` or `epubcheck_repair`. |
| `usage` | Model calls of the request's job and their tokens. `cost` only counts calls with a known price, and is `null` when none had one. |
| `durations` | Seconds since the request was created, spent waiting for the volume, and spent in the job. |
| `autopilot` | How the autopilot ended (`null` when it did not run). |
| `decisions` | `autopilot`: the number of decisions the autopilot logged for the job; `intake`: the choices made when reading the upload (volume and chapter numbers, text encoding, reused EPUB). |
| `quality` | Quality scores (0–100) of the request's translated passages, computed from the signals Libris records (checks, critiques, doubts, failed calls, recoveries, retained originals; see [the architecture](architecture.md#passage-quality-scores)): count, average, lowest, `bands` (`good` from 85, `fair` from 70, `weak` from 50, `poor` below), a ten-bucket `histogram`, `to_review` (below `review_below` and not validated by a person), the 10 weakest chapters and the 10 passages to review first with the signals that lowered them. `null` when the request has no volume. |
| `delivery` | EPUB only: EPUBCheck `validation`, the `repairs` made (attempt, errors, files, passages restored), `inherited_errors`, and `errors` when the delivery failed. |

The full log of autopilot decisions for a book is shown in the interface (the book's **Autopilot**
tab); see [the autopilot guide](autopilot.md#the-decision-log-and-the-report).

## Webhooks

A request may name a `callback_url`. When it ends (any final status, and also `imported`), the
**worker** sends one `POST` to that URL; with `callback_events`, it also sends one per
[batch of translated chapters](#batches-of-translated-chapters) before. The webhook is a
convenience: the status document stays the reference, and you can always poll it.

### Enabling webhooks (administrators)

Webhooks are off until an administrator allows at least one host. In **Settings › Automation API ›
Webhooks of API requests**, or with environment variables:

| Setting | Environment variable | Default |
| --- | --- | --- |
| Allowed hosts (`hooks.example.org`, `*.partner.example` for its subdomains) | `API_WEBHOOK_HOSTS` (comma-separated) | empty: webhooks refused |
| Allowed private networks, in CIDR notation | `API_WEBHOOK_PRIVATE_NETWORKS` | empty |
| Attempts at most | `API_WEBHOOK_MAX_ATTEMPTS` | 6 |
| Timeout of a call, in seconds | `API_WEBHOOK_TIMEOUT_SECONDS` | 10 |
| Global signing secret (32 characters at least) | `API_WEBHOOK_SECRET` | empty |

Values saved in the interface win over the environment until **Go back to the environment values**.
They apply without a restart. Every webhook must be signed: a token needs its own signing secret
(chosen when the token is created) or a global secret must exist.

### What is sent

```json
{
  "event": "translation_request.finished",
  "request_id": "…", "external_id": "…",
  "status": "completed_with_residuals", "error": null,
  "project_id": "…", "job_id": "…",
  "status_url": "/api/v1/translation-requests/…",
  "result_url": "/api/v1/translation-requests/…/result",
  "artifact": {"format": "epub", "size": 812345, "sha256": "…"},
  "report": {"outcome": "completed_with_residuals", "residual_total": 2, "…": "…"},
  "finished_at": 1790000000.0
}
```

| Header | Value |
| --- | --- |
| `X-Libris-Event` | `translation_request.finished` |
| `X-Libris-Delivery` | `<request id>:<attempt number>` |
| `X-Libris-Timestamp` | Unix time in seconds |
| `X-Libris-Signature` | `sha256=<hex>`: HMAC-SHA256 of `<timestamp>.<body>` |
| `User-Agent` | `Libris-Webhook/1` |

The signature uses the token's own webhook secret when it has one, otherwise the global secret.

### Verifying a webhook

Check the signature over the raw body, and refuse old timestamps to block replays:

```python
import hashlib
import hmac
import time


def verify(secret: str, body: bytes, timestamp: str, signature: str, tolerance: int = 300) -> bool:
    if abs(time.time() - int(timestamp)) > tolerance:
        return False
    expected = hmac.new(secret.encode(), timestamp.encode() + b"." + body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(signature, "sha256=" + expected)
```

### Retries

Any `2xx` answer counts as delivered. Anything else (another status, a timeout, a network error) is
retried with an exponential backoff: 30 seconds, then 60, 120… up to one hour between attempts, for
at most `API_WEBHOOK_MAX_ATTEMPTS` calls. Redirects are not followed. The status document shows the
webhook's `state`, `attempts` and last `error`.

### Protections

A `callback_url` that breaks one of these rules is refused at once with `422 callback_refused`,
before anything is stored:

- it must be an `http` or `https` URL without user name or password, of at most 2000 characters;
- its host must be in the allowed list;
- a signing secret must exist (the token's or the global one);
- its name must resolve only to public addresses. Loopback, private, link-local, reserved and
  multicast addresses are refused unless they fall inside an allowed private network.

The name is resolved again before every call, and the call goes to the address just checked (with the
original name in the `Host` header and the TLS server name), so a DNS answer that changes in between
cannot redirect it. Proxy environment variables are ignored.

### Batches of translated chapters

A request whose `callback_events` contains `chapters.translated` also gets one webhook per batch of
chapters whose passages all have a translation, while the job runs: a client can publish chapters one
batch at a time instead of waiting for the whole request. Chapters already translated when the
request was imported are not announced. The request's final `translation_request.finished` webhook
is sent as usual.

```json
{
  "event": "chapters.translated",
  "request_id": "…", "external_id": "…", "series_id": "…", "project_id": "…",
  "batch": 2,
  "chapters": [{"chapter_id": "…", "external_id": "chapter-052", "number": 52, "title": "Chapter 52"}],
  "announced": 2, "total": 3,
  "status_url": "/api/v1/translation-requests/…",
  "result_url": "/api/v1/translation-requests/…/result?partial=true",
  "created_at": 1790000000.0
}
```

`batch` counts from 1 per request; `announced` is the number of chapters announced so far, `total` the
number of chapters in the request. Batches use the same allowed hosts, signature, headers and retries
as the final webhook, with `X-Libris-Event: chapters.translated` and `X-Libris-Delivery:
<request id>:chapters.translated:<batch>:<attempt>`. They are sent before the final webhook when
both are due, but a batch that is retried can arrive after it: order them by `batch`. A batch is a
draft: until the request ends, the final review may still improve its chapters. Fetch them with
`?partial=true&scope=new` (or `format=json`, which gives each chapter with its `chapter_id`); the
final result stays the reference.

## List your series

```bash
curl -sS "$LIBRIS_URL/api/v1/series" -H "Authorization: Bearer $LIBRIS_TOKEN"
curl -sS "$LIBRIS_URL/api/v1/series/$SERIES_ID" -H "Authorization: Bearer $LIBRIS_TOKEN"
```

The list is sorted by name and gives `id, name, kind, source_language, target_language, archived,
volumes, created_at, updated_at`. The detail adds `volume_list`, sorted by volume number, with
`project_id, title, volume_number, external_id, source_format, project_kind, status, chapters`. Only
series the token's owner owns are visible; volumes shared with them are not.

## Shared glossaries

A shared glossary is the terminology of a universe common to several of your series (places, titles,
spells…). Each series follows at most one; its accepted terms reach every volume of the series, after the
book's and the series' own terms: book > series > shared glossary. A locked shared term is enforced and
checked in every passage, and beats an unlocked term an earlier volume proposed; a person's series decision
or a volume's deliberate override always wins. The same glossaries are managed in the interface
(**Glossaires partagés**).

```bash
# Create one; languages are optional (with them, it only applies to volumes of the same pair).
curl -sS "$LIBRIS_URL/api/v1/glossaries" -H "Authorization: Bearer $LIBRIS_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"name": "Glass Road universe", "source_language": "en", "target_language": "fr"}'

# Preview an import, then apply it.
curl -sS "$LIBRIS_URL/api/v1/glossaries/$GLOSSARY_ID/import?dry_run=true" \
  -H "Authorization: Bearer $LIBRIS_TOKEN" -F file=@universe.csv -F strategy=replace
curl -sS "$LIBRIS_URL/api/v1/glossaries/$GLOSSARY_ID/import" \
  -H "Authorization: Bearer $LIBRIS_TOKEN" -F file=@universe.csv -F strategy=replace

# Make a series follow it (send {"glossary_id": null} to detach).
curl -sS -X PUT "$LIBRIS_URL/api/v1/series/$SERIES_ID/shared-glossary" \
  -H "Authorization: Bearer $LIBRIS_TOKEN" -H "Content-Type: application/json" \
  -d "{\"glossary_id\": \"$GLOSSARY_ID\"}"

# Download it; for spreadsheets: CSV with semicolons and a byte order mark.
curl -sS "$LIBRIS_URL/api/v1/glossaries/$GLOSSARY_ID/export/csv?delimiter=semicolon&bom=true" \
  -H "Authorization: Bearer $LIBRIS_TOKEN" -o universe.csv
```

A glossary gives `id, name, description, source_language, target_language, created_at, updated_at,
term_count, locked_count, series` (the series that follow it, `id` and `name`) and, for one glossary,
`terms` with `id, source, translation, category, description, locked, accepted`. Only accepted terms are
applied.

**Files.** JSON (a list of terms with the fields above), CSV or TBX (v2 and v3). CSV files may carry a byte
order mark or come from Excel on Windows; the separator (`;`, `,` or tab) is detected, and headers are
recognised in French or English (`source`, `terme source`, `traduction`, `target`, `catégorie`, `notes`,
`verrouillé`, `accepté`…); a file without header uses its first two columns. Form fields of an import:

| Field | Values | Default |
| --- | --- | --- |
| `file` | The glossary file (2 MB at most) | required |
| `strategy` | `skip` keeps terms in place; `replace` replaces unlocked terms that differ; `replace_all` replaces locked ones too | `skip` |
| `delimiter` | `semicolon`, `comma` or `tab` | detected |
| `mapping` | JSON object from field to column number (from 0), for example `{"source": 0, "translation": 2}` | from the headers |
| `header` | `true` or `false`: whether the first row holds column names | detected |
| `skip_invalid` | `true` leaves invalid rows out instead of refusing the file | `false` |

The answer is the import report: `format`, `encoding`, `delimiter`, `columns` (the first row), `header`,
`mapping`, `strategy`, `counts` (`terms, new, unchanged, conflicts, replaced, kept, duplicates, errors`), the
lists `new`, `conflicts` (with `existing`, `incoming`, the differing `fields`, `locked` and the `action`
`replace` or `keep`), `duplicates` (a source repeated in the file: the first row counts) and `errors` (`line`,
`message`), each cut to 200 items (`truncated`), and `applied`. An applied import adds `imported`,
`replaced` and `skipped`. Sources are matched case-insensitively. A preview (`dry_run=true`) lists invalid
rows instead of refusing the file.

## Errors

Every error has the same shape:

```json
{"detail": {"code": "chapter_conflict", "message": "…", "conflicts": ["…"]}}
```

`code` is stable; `message` is in French by default and in English with `Accept-Language: en`.
Validation errors never echo the submitted values, so book text is never sent back.

| HTTP | `code` | When |
| --- | --- | --- |
| 401 | `missing_token`, `invalid_token`, `revoked_token`, `expired_token`, `inactive_account` | No token, or a bad one (header `WWW-Authenticate: Bearer`). |
| 401 | `unauthorized` | A request with a body but no `Authorization: Bearer` header. |
| 403 | `insufficient_scope` (with `scope`) | The token lacks a permission. |
| 403 | `forbidden` | A browser request from another site (see below). |
| 403 | `priority_not_allowed` (with `max_priority`) | The priority asked for is above the token's or the account's ceiling. |
| 404 | `request_not_found`, `series_not_found`, `volume_not_found`, `glossary_not_found`, `not_found` | Unknown, or owned by someone else. |
| 409 | `idempotency_conflict` (with `request_id`) | Same key or `external_id`, different content. |
| 409 | `chapter_conflict` (with `conflicts`) | Chapters exist with another text; send `replace_changed_chapters`. |
| 409 | `conflict` (with `protected_segments`) | A replacement would drop human edits; send `discard_human`. |
| 409 | `volume_conflict`, `series_archived`, `volume_archived` | The target volume cannot take this content. |
| 409 | `result_not_ready`, `request_failed`, `request_cancelled`, `format_unavailable` | The result cannot be served (see [Get the result](#get-the-result)). |
| 409 | `not_started`, `conflict` | Pause, resume or cancel not allowed in the current state. |
| 409 | `glossary_exists`, `language_mismatch` | A shared glossary of that name exists; its languages differ from the series'. |
| 413 | `payload_too_large`, `glossary_too_large` | The body is above the size limit. |
| 415 | `unsupported_media_type` | Neither JSON, EPUB nor multipart. |
| 422 | `invalid_payload` (with `errors: [{loc, msg, type}]`) | The document or the upload is invalid. |
| 422 | `invalid_request` (with `errors`) | A bad query parameter (for example `format`, `wait`). |
| 422 | `invalid_idempotency_key`, `unknown_provider`, `provider_required`, `invalid_epub`, `callback_refused`, `delivery_failed` | See the sections above. |
| 422 | `invalid_glossary`, `invalid_strategy`, `invalid_mapping`, `invalid_name` | The glossary file, its import options or the glossary name (see [Shared glossaries](#shared-glossaries)). |
| 429 | `rate_limited` | Too many calls for this token (header `Retry-After`). |
| 429 | `queue_full` (with `scope`, `limit`) | The token or the account already has its quota of requests waiting (see [Queue priority and quotas](#queue-priority-and-quotas)). |
| 500 | `server_error` | Unexpected failure; the message carries a diagnostic reference for the server logs. |

Server-to-server clients do not send an `Origin` header and are accepted. A browser page on another
site is refused like for the interface (`ALLOWED_ORIGINS`, `Sec-Fetch-Site`).

## Limits

| Setting | Default | Effect |
| --- | --- | --- |
| `API_MAX_PAYLOAD_MB` | `MAX_UPLOAD_MB` (60) | Largest request body with a Bearer token: an EPUB, or all TXT files together. Enforced before the body is read. Without a Bearer header the limit is 1 MiB and the answer `401`. |
| `API_MAX_CHAPTERS` | 2000 | Chapters (or TXT files) per request. |
| `TEXT_CHAPTER_MAX_CHARS` | 2,000,000 | Characters per chapter, shared with TXT imports. |
| `API_RATE_LIMIT_PER_MINUTE` | 120 | Calls per token over a sliding minute, **counted in each API process** (with several API replicas the effective limit is multiplied). `0` disables it. |
| `API_RESULT_MAX_WAIT_SECONDS` | 60 | Longest `?wait=` (0–600). |
| `API_REQUEST_STALL_MINUTES` | 360 | A request whose job stays paused, blocked or waiting longer fails, and the job is cancelled. |
| `API_REQUEST_MAX_HOURS` | 168 | A request still unfinished after this fails, and the job is cancelled. |
| `DELIVERY_REPAIR_ATTEMPTS` | 3 | EPUBCheck repair rounds of a delivered EPUB. |
| `RETENTION_RESULTS_DAYS` | 30 | Days a stored result file is kept (it can be rendered again afterwards). |
| `API_WEBHOOK_*` | see [Webhooks](#enabling-webhooks-administrators) | Webhook hosts, networks, secret, attempts and timeout. |
| `QUEUE_*` | see [configuration](configuration.md#fair-queue) | Jobs per account running and waiting, priority aging; a token can have lower limits of its own. |

An EPUB is also bounded by the archive limits of every import (`MAX_UNPACKED_MB`, `MAX_ENTRIES`,
`MAX_COMPRESSION_RATIO`). All settings are described in [the configuration reference](configuration.md).

## A complete example

Upload an EPUB, wait for the end, then download the translated book and read the report:

```bash
#!/usr/bin/env bash
set -euo pipefail

REQUEST_ID=$(curl -sS -X POST "$LIBRIS_URL/api/v1/translation-requests" \
  -H "Authorization: Bearer $LIBRIS_TOKEN" \
  -H "Idempotency-Key: book-fr-1" \
  -F "file=@book.epub" -F target_language=fr -F provider_id="$PROVIDER_ID" \
  | jq -r .request_id)

while :; do   # each call answers when the request ends, or after 60 seconds
  STATUS=$(curl -sS "$LIBRIS_URL/api/v1/translation-requests/$REQUEST_ID?wait=60" \
    -H "Authorization: Bearer $LIBRIS_TOKEN" | jq -r .status)
  case "$STATUS" in completed|completed_with_residuals|failed|cancelled) break ;; esac
done
echo "Request ended: $STATUS"

if [ "$STATUS" = completed ] || [ "$STATUS" = completed_with_residuals ]; then
  curl -sS "$LIBRIS_URL/api/v1/translation-requests/$REQUEST_ID/result" \
    -H "Authorization: Bearer $LIBRIS_TOKEN" -o book.fr.epub
fi
curl -sS "$LIBRIS_URL/api/v1/translation-requests/$REQUEST_ID" \
  -H "Authorization: Bearer $LIBRIS_TOKEN" | jq '.report | {outcome, residual_total, usage}'
```
