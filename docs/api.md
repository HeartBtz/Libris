# Automation API (`/api/v1`)

The automation API lets a script or another server send chapters to Libris, follow the translation and
fetch the result. It is separate from the API used by the web interface:

- every path starts with `/api/v1` and its contract is versioned;
- it only accepts API tokens (`Authorization: Bearer …`). The interface's session cookie does not open
  `/api/v1`, and a token does not open the interface's `/api/*` routes;
- work is asynchronous: a request is stored in SQL before the `202 Accepted` answer, the pipeline runs in
  the worker, and the client polls. No HTTP request stays open during a translation.

All examples use two shell variables:

```bash
export LIBRIS_URL=https://libris.example.org
export LIBRIS_TOKEN=lbr_xxxxxxxx_...   # shown once when the token is created
```

## Tokens and scopes

Create tokens in the interface: **My account › API tokens** (administrators also find them under
**Settings › Automation API**). Give a name, choose the scopes and an optional expiry (30, 90, 365 days or
never). The secret (`lbr_` + 8 identifying characters + `_` + 256 random bits) is displayed **once**; Libris
only stores its SHA-256 and compares it in constant time. The list shows the prefix, scopes, creation,
expiry, last use (updated at most once a minute) and state. Revocation is immediate and final. Creation and
revocation are recorded in the audit log, without the secret. A token acts on behalf of its owner and only
sees the owner's series and requests; a disabled account makes its tokens unusable.

| Scope | Allows |
| --- | --- |
| `series:read` | `GET /api/v1/series`, `GET /api/v1/series/{id}` |
| `content:write` | `POST /api/v1/translation-requests` (import only) |
| `pipeline:start` | together with `content:write`: requests with `pipeline.start: true` |
| `jobs:read` | `GET /api/v1/translation-requests/{id}` |
| `jobs:control` | `POST /api/v1/translation-requests/{id}/pause|resume|cancel` |
| `results:read` | `GET /api/v1/translation-requests/{id}/result` |

The interface manages tokens through `GET /api/tokens`, `POST /api/tokens`
(`{"name", "scopes", "expires_in_days"}`, the only answer that contains `token`) and
`DELETE /api/tokens/{id}` (revocation), with the session cookie.

## Sending a translation request

`POST /api/v1/translation-requests` accepts `application/json`, or `multipart/form-data` with a single
`.json` file in the field `file` holding exactly the same document.

```json
{
  "external_id": "tbate-volume-12",
  "series": {"id": null, "name": "The Synthetic Saga", "create_if_missing": true},
  "volume": {"external_id": "volume-12", "number": 12, "title": "Volume 12"},
  "author": "A. Author",
  "source_language": "en",
  "target_language": "fr",
  "chapters": [
    {"external_id": "chapter-001", "number": 1, "title": "Chapter 1", "content": "First paragraph.\n\nSecond paragraph.\n"}
  ],
  "replace_changed_chapters": false,
  "pipeline": {"start": true, "provider_id": null, "quality": "high", "context_backend": "hybrid", "final_review": true},
  "output": {"format": "json"}
}
```

| Field | Rules |
| --- | --- |
| `external_id` | Optional, your identifier of the request (letters, digits, `._:/-`, up to 200). Unique per owner. |
| `series` | `id` (a series you own) **or** `name`. An unknown name is created when `create_if_missing` is true (default), otherwise 404. Archived series are refused (409). |
| `volume` | `number` is required (1–10000). The volume is found by `external_id`, then by number within the series, otherwise created as a JSON volume. A volume imported from an EPUB is refused (409). |
| `source_language`, `target_language` | Required BCP 47 tags: `en`, `fr-FR`, `zh-Hant`, `es-419`… They are applied to the volume. |
| `chapters` | At least one; at most `API_MAX_CHAPTERS` (2000). `number` is required, numbers and `external_id`s must not repeat, `content` must not be blank and at most `TEXT_CHAPTER_MAX_CHARS` characters. `title` is optional: without it nothing is added to the text. Chapters are ordered by number. |
| `replace_changed_chapters` | A chapter already in the volume (same `external_id`, or same number) with another text is refused (409 `chapter_conflict`) unless this is true. Passages whose text did not change keep their translation; passages corrected or validated by a person are never discarded through the API (409). |
| `pipeline` | `start` (default true; needs `pipeline:start`), `provider_id` (an existing provider; otherwise the volume's or series' provider — one is required to start), `quality` (`fast`, `normal`, `high`, `maximum`), `context_backend` (`internal`, `openviking`, `hybrid`), `final_review` (false skips the final review; it never runs when the server sets `FINAL_REVIEW_ENABLED=false`). |
| `output.format` | Default format of the result: `json`, `txt` or `txt-zip`. |

Any unknown field is refused. Nothing is ever downloaded from a URL found in the payload: text is taken
as is. Chapters go through the same text adapter as TXT imports (same passages, layout and checksums).
The normalized payload is stored as a JSON source file of the volume, referenced by the chapters it
created or replaced.

```bash
curl -sS -X POST "$LIBRIS_URL/api/v1/translation-requests" \
  -H "Authorization: Bearer $LIBRIS_TOKEN" \
  -H "Content-Type: application/json" \
  -H "Idempotency-Key: tbate-volume-12-run-1" \
  --data @request.json

# The same document as a file:
curl -sS -X POST "$LIBRIS_URL/api/v1/translation-requests" \
  -H "Authorization: Bearer $LIBRIS_TOKEN" \
  -H "Idempotency-Key: tbate-volume-12-run-1" \
  -F "file=@request.json;type=application/json"
```

Answer `202 Accepted` (header `Location` = `status_url`):

```json
{
  "request_id": "5b1c…", "external_id": "tbate-volume-12",
  "series_id": "…", "project_id": "…", "job_id": "…", "status": "pending",
  "status_url": "/api/v1/translation-requests/5b1c…",
  "result_url": "/api/v1/translation-requests/5b1c…/result"
}
```

### Idempotency

Send an `Idempotency-Key` header (1–200 printable characters) and/or an `external_id`. Sending again the
same content with the same key or the same `external_id` answers `200` with the same resource and the
header `Idempotent-Replayed: true`; nothing is created twice. The same key or `external_id` with another
content answers `409 idempotency_conflict`. Content is compared after validation (key order and
whitespace do not matter; JSON and multipart are equivalent). Even without a key, sending chapters that
are already in the volume with the same text never duplicates series, volumes or chapters.

## Asynchronous processing

The request, its chapters and its job are committed before the answer. If another job already works on the
volume, the request stays `queued` (its chapters are imported only when no job is active on the volume);
the worker's dispatcher checks queued requests every 2 seconds and starts each one once the volume is free.
Requests on the same volume start in arrival order. A volume held by a paused or blocked job stays busy
until that job is resumed and finishes, or is cancelled. Everything lives in SQL: restarting the API or the
worker loses nothing.

Status values: `queued` (waiting for the volume), `imported` (chapters imported, `pipeline.start` false),
then the state of the job: `pending`, `running`, `paused`, `waiting` (provider temporarily unavailable,
retried automatically), `blocked` (needs a person, e.g. provider authentication or refused content),
`completed`, `failed`, `cancelled`.

Started requests run under the [autopilot](autopilot.md) (unless the server sets `AUTOPILOT_ENABLED=false`
or the volume opts out): refused content, invalid answers and open review proposals never wait for a
person, and a provider outage switches to the fallback providers after a bounded wait, so a job always ends
`completed` or `failed`. Its report (`outcome`, `rounds`, `residuals` — passages kept in the original, with
their reason — and `reason`) is stored on the job (`result.autopilot`); the interface reads it, with every
decision, from `GET /api/projects/{id}/autopilot`.

```bash
curl -sS "$LIBRIS_URL/api/v1/translation-requests/$REQUEST_ID" -H "Authorization: Bearer $LIBRIS_TOKEN"
```

The status gives `status`, `stage` (`analysis`, `translation`, `review`, `export`), `step` (current step of
the job), `progress` (`segments`, `translated`, `percent` for the request's chapters, and the volume's
`stages`), `estimate` (remaining seconds and cost when enough calls have been observed), `error`,
`stop_reason`, `next_attempt`, and `chapters` (`created`, `unchanged`, `replaced` and per chapter:
identifiers, passages, translated, validated, flagged, complete).

```bash
curl -sS -X POST "$LIBRIS_URL/api/v1/translation-requests/$REQUEST_ID/pause"  -H "Authorization: Bearer $LIBRIS_TOKEN"
curl -sS -X POST "$LIBRIS_URL/api/v1/translation-requests/$REQUEST_ID/resume" -H "Authorization: Bearer $LIBRIS_TOKEN"
curl -sS -X POST "$LIBRIS_URL/api/v1/translation-requests/$REQUEST_ID/cancel" -H "Authorization: Bearer $LIBRIS_TOKEN"
```

Pause, resume and cancel follow the rules of the interface (409 when the job's state does not allow it).
A `queued` request without a job can only be cancelled.

There is **no webhook**, on purpose: Libris never opens a connection to an address chosen by an API client
(no outbound requests to private networks, no redirects to follow, no delivery retries to secure). Poll the
status URL instead, for instance every 30 seconds.

## Results

```bash
# JSON (default, or output.format of the request)
curl -sS "$LIBRIS_URL/api/v1/translation-requests/$REQUEST_ID/result?format=json" \
  -H "Authorization: Bearer $LIBRIS_TOKEN" -o result.json
# One UTF-8 text, chapters under their translated headings
curl -sS "$LIBRIS_URL/api/v1/translation-requests/$REQUEST_ID/result?format=txt" \
  -H "Authorization: Bearer $LIBRIS_TOKEN" -o volume-12.txt
# ZIP: chapters/001 - Title.txt … and manifest.json (SHA-256 of each file)
curl -sS "$LIBRIS_URL/api/v1/translation-requests/$REQUEST_ID/result?format=txt-zip" \
  -H "Authorization: Bearer $LIBRIS_TOKEN" -o volume-12.zip
# What is ready so far
curl -sS "$LIBRIS_URL/api/v1/translation-requests/$REQUEST_ID/result?partial=true" \
  -H "Authorization: Bearer $LIBRIS_TOKEN"
```

The result covers **the chapters of the request only** (not the rest of the volume), in reading order.
While the job is not completed, or a chapter still has untranslated passages, the answer is
`409 result_not_ready` with `status` and `incomplete_chapters`. With `partial=true` the result is returned
anyway: `complete` is false, `incomplete_chapters` lists the chapters concerned, each chapter carries
`complete` and `missing_segments`, and untranslated passages appear in the source language. Every format
sends the header `X-Libris-Complete: true|false`.

JSON result (`schema_version` 1):

```json
{
  "schema_version": 1, "request_id": "…", "external_id": "tbate-volume-12", "status": "completed",
  "complete": true,
  "series": {"id": "…", "name": "The Synthetic Saga"},
  "volume": {"project_id": "…", "external_id": "volume-12", "number": 12, "title": "Volume 12"},
  "source_language": "en", "target_language": "fr",
  "strategy": {"provider": {"name": "Local", "model": "…"}, "quality": "high", "context_backend": "hybrid", "final_review": true},
  "incomplete_chapters": [],
  "chapters": [{
    "chapter_id": "…", "external_id": "chapter-001", "number": 1,
    "title": "Chapter 1", "translated_title": "Chapitre 1",
    "complete": true, "missing_segments": 0,
    "translation": "Premier paragraphe.\n\nDeuxième paragraphe.\n",
    "source_sha256": "…", "sha256": "…",
    "review": {"segments": 2, "validated": 0, "flagged": 0},
    "issues": [], "flagged_passages": []
  }]
}
```

`sha256` is the SHA-256 of `translation` (UTF-8); `source_sha256` the one of the normalized source text.
`issues` are unresolved quality issues, `flagged_passages` passages still flagged for a person (`check`,
`error`, `refused`, not validated). The strategy names the provider and model only, never its address or
key.

## Series

```bash
curl -sS "$LIBRIS_URL/api/v1/series" -H "Authorization: Bearer $LIBRIS_TOKEN"
curl -sS "$LIBRIS_URL/api/v1/series/$SERIES_ID" -H "Authorization: Bearer $LIBRIS_TOKEN"
```

The list gives `id, name, kind, source_language, target_language, archived, volumes, created_at,
updated_at`; the detail adds `volume_list` (`project_id, title, volume_number, external_id, source_format,
project_kind, status, chapters`). Only the owner's series are visible (shared volumes are not).

## Errors

Every error has the shape `{"detail": {"code": "…", "message": "…", …}}`. Messages are in French by
default and in English with `Accept-Language: en`. Book text is never echoed in errors or written to the
service logs.

| HTTP | `code` | When |
| --- | --- | --- |
| 401 | `missing_token`, `invalid_token`, `revoked_token`, `expired_token`, `inactive_account`, `unauthorized` | No or bad token (header `WWW-Authenticate: Bearer`) |
| 403 | `insufficient_scope` (with `scope`), `forbidden` | Missing scope; cross-site browser request |
| 404 | `request_not_found`, `series_not_found`, `volume_not_found`, `not_found` | Unknown, or owned by someone else |
| 409 | `idempotency_conflict`, `chapter_conflict` (with `conflicts`), `volume_conflict`, `series_archived`, `volume_archived`, `result_not_ready`, `not_started`, `conflict` | State conflicts |
| 413 | `payload_too_large` | Body above the limit |
| 415 | `unsupported_media_type` | Neither JSON nor multipart |
| 422 | `invalid_payload` (with `errors: [{loc, msg, type}]`), `invalid_request`, `invalid_idempotency_key`, `unknown_provider`, `provider_required` | Validation |
| 429 | `rate_limited` | Too many calls for this token (header `Retry-After`) |

## Limits

| Setting | Default | Effect |
| --- | --- | --- |
| `API_MAX_PAYLOAD_MB` | `MAX_UPLOAD_MB` | Largest body of a request sent with a Bearer token, enforced before the body is read. Without a token the limit is 1 MiB and the answer 401. |
| `API_MAX_CHAPTERS` | 2000 | Chapters per request. |
| `TEXT_CHAPTER_MAX_CHARS` | 2,000,000 | Characters per chapter (shared with TXT imports). |
| `API_RATE_LIMIT_PER_MINUTE` | 120 | Calls per token over a sliding minute, **counted in each API process** (with several API replicas the effective limit is multiplied). `0` disables it. |

Server-to-server clients do not send an `Origin` header and are accepted; a browser page on another site is
refused like for the interface (`ALLOWED_ORIGINS`, `Sec-Fetch-Site`).
