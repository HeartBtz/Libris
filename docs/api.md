# Automation API (`/api/v1`)

The automation API lets a script or another server send chapters or a whole EPUB to Libris and get the
translation back, with no step in the interface: the file goes in, the pipeline runs to its end on its
own, and the result (a translated EPUB for an EPUB, JSON, text or a ZIP of chapters for chapters) comes
out with a completion report. It is separate from the API used by the web interface:

- every path starts with `/api/v1` and its contract is versioned;
- it only accepts API tokens (`Authorization: Bearer …`). The interface's session cookie does not open
  `/api/v1`, and a token does not open the interface's `/api/*` routes;
- work is asynchronous: a request is stored in SQL before the `202 Accepted` answer, the pipeline runs in
  the worker, and the client polls (or long-polls with `?wait=`, or receives a webhook). No HTTP request
  stays open during a translation;
- a request always ends: `completed`, `completed_with_residuals`, `failed` (with the reason) or
  `cancelled`. It never stays `running` forever.

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
| `pipeline:start` | together with `content:write`: requests that start the pipeline (`pipeline.start` / `start`, true by default) |
| `jobs:read` | `GET /api/v1/translation-requests/{id}` |
| `jobs:control` | `POST /api/v1/translation-requests/{id}/pause|resume|cancel` |
| `results:read` | `GET /api/v1/translation-requests/{id}/result` |

The interface manages tokens through `GET /api/tokens`, `POST /api/tokens`
(`{"name", "scopes", "expires_in_days", "webhook_secret"}`, the only answer that contains `token`) and
`DELETE /api/tokens/{id}` (revocation), with the session cookie. With `"webhook_secret": true` the token
also gets its own secret to sign webhooks, returned once as `webhook_secret` (stored encrypted with
`SECRET_KEY`; the list only says whether one exists).

## Sending a translation request

`POST /api/v1/translation-requests` accepts three kinds of input:

- a JSON document (`application/json`, or a single `.json` file in the multipart field `file`), below;
- an **EPUB**: `multipart/form-data` with one `.epub` file in `file` and the options as form fields, or
  the raw file as `application/epub+zip` with the options in the query string
  ([Sending an EPUB](#sending-an-epub));
- **TXT chapters**: `multipart/form-data` with one or several `.txt` files in `file` or `files` and the
  options as form fields ([Sending TXT chapters](#sending-txt-chapters)).

The JSON document:

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
  "discard_human": false,
  "pipeline": {"start": true, "provider_id": null, "quality": "high", "context_backend": "hybrid", "final_review": true},
  "output": {"format": "json"},
  "callback_url": "https://hooks.example.org/libris"
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
| `discard_human` | With `replace_changed_chapters`, also replaces chapters whose passages a person corrected or validated (their edits are lost). Default false: such chapters are refused (409). |
| `output.format` | Default format of the result: `json`, `txt` or `txt-zip`. |
| `callback_url` | Optional webhook called when the request ends ([Webhooks](#webhooks)). |

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
  "series_id": "…", "project_id": "…", "job_id": "…", "input": "json", "status": "pending",
  "status_url": "/api/v1/translation-requests/5b1c…",
  "result_url": "/api/v1/translation-requests/5b1c…/result"
}
```

### Sending an EPUB

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

The EPUB becomes a volume (standalone, or in the series named by `series` / `series_id`) and its pipeline
starts at once. The same file sent again (same content) reuses the volume already made from it, and the
request records that decision. In a series without `volume`, the number found in the file name is used
when it is free, otherwise the volume after the last one; the choice and its reason are recorded. A volume
number already taken by another book answers `409 volume_conflict`; an unreadable file `422 invalid_epub`.
The result is a translated EPUB by default ([Results](#results)).

Options of file uploads (form fields, or query parameters for a raw body; unknown ones are refused):

| Option | Meaning |
| --- | --- |
| `series` or `series_id` | Series by name (created when missing) or identifier. Required for TXT; optional for an EPUB (standalone volume). |
| `volume` | Volume number (1–10000). Required for TXT. |
| `external_id`, `volume_external_id` | Your identifiers of the request and of the volume. |
| `title`, `author` | Volume title and author (an EPUB keeps its own otherwise). |
| `source_language`, `target_language` | BCP 47 tags. Both required for TXT; an EPUB defaults to its declared language and to the series' (or `fr`) target. |
| `provider_id`, `quality`, `context_backend`, `final_review` | As in `pipeline` above. |
| `start` | `true` (default) runs the whole pipeline; `false` only imports. |
| `output_format` | `epub` (EPUB only, default for an EPUB), `json`, `txt`, `txt-zip`. |
| `callback_url` | Webhook ([Webhooks](#webhooks)). |
| `replace_changed_chapters`, `discard_human` | TXT only, as in the JSON document. |
| `filename` | Raw EPUB body only: the file name (used for the volume number guess). |

### Sending TXT chapters

```bash
curl -sS -X POST "$LIBRIS_URL/api/v1/translation-requests" \
  -H "Authorization: Bearer $LIBRIS_TOKEN" \
  -F "files=@Chapter 1.txt" -F "files=@Chapter 2.txt" -F "files=@Afterword.txt" \
  -F series="Web Saga" -F volume=1 -F source_language=en -F target_language=fr \
  -F provider_id="$PROVIDER_ID" -F output_format=txt-zip
```

Each file becomes a chapter; together they are exactly the JSON request they stand for (same
idempotency, volume lookup, conflicts and results). Files are decoded like TXT imports (UTF-8, UTF-16 with
BOM, Windows-1252 as a recorded last resort). Chapter numbers come from the file names (`Chapter 12.txt`,
`012 - Title.txt`, or the part that varies across the batch); a file without a number, or with the number
of another file, gets the next free number in upload order. These choices are never questions: they are
recorded with their reason in the request (`report.decisions.intake`). Mixed kinds of files in one request
are refused (422).

### Idempotency

Send an `Idempotency-Key` header (1–200 printable characters) and/or an `external_id`. Sending again the
same content with the same key or the same `external_id` answers `200` with the same resource and the
header `Idempotent-Replayed: true`; nothing is created twice. The same key or `external_id` with another
content answers `409 idempotency_conflict`. Content is compared after validation (key order and
whitespace do not matter; JSON and multipart are equivalent). For an EPUB the content is the file and its
options (the `callback_url` aside). Even without a key, sending chapters that
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
retried automatically), `blocked` (e.g. provider authentication), `finalizing` (the job ended, the result
is being built), and finally one of:

| Final status | Meaning |
| --- | --- |
| `completed` | Every passage translated; the result is stored. |
| `completed_with_residuals` | The result is stored, but some passages kept their source text (refused, failed, or restored by the EPUB repair); they are listed in `report.residuals` with their reason. |
| `failed` | With the reason in `error`: the job failed, the EPUB could not be repaired, the job stayed paused/blocked/waiting too long, or the request exceeded its maximum duration. |
| `cancelled` | Cancelled by the client (or a person). |

No request stays `running` forever: a job paused, blocked or waiting for more than
`API_REQUEST_STALL_MINUTES` (360) is cancelled and the request fails with the reason, and so does a request
still unfinished after `API_REQUEST_MAX_HOURS` (168). When the autopilot reports an outcome on the job
(`result.autopilot`), a `failed` outcome fails the request with its reason.

Started requests run under the [autopilot](autopilot.md) (unless the server sets `AUTOPILOT_ENABLED=false`
or the volume opts out): refused content, invalid answers and open review proposals never wait for a
person, and a provider outage switches to the fallback providers after a bounded wait, so a job always ends
`completed` or `failed`. Its report (`outcome`, `rounds`, `residuals` — passages kept in the original, with
their reason — and `reason`) is stored on the job (`result.autopilot`); the interface reads it, with every
decision, from `GET /api/projects/{id}/autopilot`.

```bash
curl -sS "$LIBRIS_URL/api/v1/translation-requests/$REQUEST_ID" -H "Authorization: Bearer $LIBRIS_TOKEN"
# Long poll: answers as soon as the request ends, or after 60 s at most (API_RESULT_MAX_WAIT_SECONDS)
curl -sS "$LIBRIS_URL/api/v1/translation-requests/$REQUEST_ID?wait=60" -H "Authorization: Bearer $LIBRIS_TOKEN"
```

The status gives `status`, `stage` (`analysis`, `translation`, `review`, `export`), `step` (current step of
the job), `progress` (`segments`, `translated`, `percent` for the request's chapters, and the volume's
`stages`), `estimate` (remaining seconds and cost when enough calls have been observed), `error`,
`stop_reason`, `next_attempt`, `chapters` (`created`, `unchanged`, `replaced` and per chapter:
identifiers, passages, translated, validated, flagged, complete), `finished_at`, `result` (format, size,
SHA-256 of the stored result), `report` (the completion report, once the request ended) and `webhook`
(`state`: `pending`, `delivered` or `failed`, `attempts`, last `error`) when a callback was given.

```bash
curl -sS -X POST "$LIBRIS_URL/api/v1/translation-requests/$REQUEST_ID/pause"  -H "Authorization: Bearer $LIBRIS_TOKEN"
curl -sS -X POST "$LIBRIS_URL/api/v1/translation-requests/$REQUEST_ID/resume" -H "Authorization: Bearer $LIBRIS_TOKEN"
curl -sS -X POST "$LIBRIS_URL/api/v1/translation-requests/$REQUEST_ID/cancel" -H "Authorization: Bearer $LIBRIS_TOKEN"
```

Pause, resume and cancel follow the rules of the interface (409 when the job's state does not allow it).
A `queued` request without a job can only be cancelled.

### Webhooks

A request may name a `callback_url`. When it ends (any final status, or `imported`), the **worker** — never
the API — sends one `POST` with a JSON body:

```json
{
  "event": "translation_request.finished", "request_id": "…", "external_id": "…",
  "status": "completed_with_residuals", "error": null, "project_id": "…", "job_id": "…",
  "status_url": "/api/v1/translation-requests/…", "result_url": "/api/v1/translation-requests/…/result",
  "artifact": {"format": "epub", "size": 812345, "sha256": "…"},
  "report": {"outcome": "completed_with_residuals", "residual_total": 2, "…": "…"},
  "finished_at": 1790000000.0
}
```

Headers: `X-Libris-Event`, `X-Libris-Delivery` (`<request id>:<attempt>`), `X-Libris-Timestamp` (Unix
seconds) and `X-Libris-Signature: sha256=<hex>`, the HMAC-SHA256 of `<timestamp>.<body>` with the token's
webhook secret, or `API_WEBHOOK_SECRET` when the token has none. Verify it, and refuse old timestamps:

```python
import hashlib, hmac
expected = hmac.new(secret.encode(), timestamp.encode() + b"." + body, hashlib.sha256).hexdigest()
assert hmac.compare_digest(signature, "sha256=" + expected)
```

Any `2xx` answer is a delivery. Otherwise the call is retried with an exponential backoff (30 s, 60 s, …, at
most one hour) up to `API_WEBHOOK_MAX_ATTEMPTS` (6) times; each call has a `API_WEBHOOK_TIMEOUT_SECONDS`
(10) timeout and redirects are not followed. The webhook is a convenience: the status stays the reference.

Protections (a `callback_url` that breaks one of them is refused at once with `422 callback_refused`):

- webhooks are off until an administrator lists the allowed hosts in `API_WEBHOOK_HOSTS`
  (`hooks.example.org,*.partner.example`); any other host is refused;
- an HTTP(S) URL without credentials; a signing secret must exist (token or global);
- the host is resolved when the request is accepted and again before every call; loopback, private,
  link-local, reserved and multicast addresses are refused unless they fall in
  `API_WEBHOOK_PRIVATE_NETWORKS` (CIDRs, e.g. `10.20.0.0/16` for a receiver on your own network). The call
  goes to the address that was checked (a DNS answer that changes in between cannot redirect it), with the
  original name in `Host` and TLS SNI.

## Results

When a request ends successfully (`completed` or `completed_with_residuals`) Libris builds its result once
and stores it under `DATA_DIR/results/<request id>/` (a name Libris chooses): the translated EPUB for an
EPUB, otherwise the request's `output.format`. `GET …/result` returns that stored file; other formats are
rendered on demand from the database. The format is chosen by `?format=` (`epub`, `json`, `txt`,
`txt-zip`), else by the `Accept` header (`application/epub+zip`, `application/json`, `text/plain`,
`application/zip`), else the request's own format (EPUB for an EPUB). `epub` is only available for an EPUB
(409 `format_unavailable` otherwise). `?wait=<seconds>` waits for the end of the request first (bounded by
`API_RESULT_MAX_WAIT_SECONDS`).

```bash
# Upload, wait, download: the whole journey, with no human step
REQUEST_ID=$(curl -sS -X POST "$LIBRIS_URL/api/v1/translation-requests" \
  -H "Authorization: Bearer $LIBRIS_TOKEN" -F "file=@book.epub" -F target_language=fr \
  -F provider_id="$PROVIDER_ID" | jq -r .request_id)
while :; do  # each call answers when the request ends, or after 60 s
  STATUS=$(curl -sS "$LIBRIS_URL/api/v1/translation-requests/$REQUEST_ID?wait=60" \
    -H "Authorization: Bearer $LIBRIS_TOKEN" | jq -r .status)
  case "$STATUS" in completed|completed_with_residuals|failed|cancelled) break ;; esac
done
echo "$STATUS"

# The translated EPUB (default for an EPUB); its report is in the status
curl -sS "$LIBRIS_URL/api/v1/translation-requests/$REQUEST_ID/result" \
  -H "Authorization: Bearer $LIBRIS_TOKEN" -o book.fr.epub
# JSON (default for chapters, or output.format of the request)
curl -sS "$LIBRIS_URL/api/v1/translation-requests/$REQUEST_ID/result?format=json" \
  -H "Authorization: Bearer $LIBRIS_TOKEN" -o result.json
# One UTF-8 text, chapters under their translated headings
curl -sS "$LIBRIS_URL/api/v1/translation-requests/$REQUEST_ID/result?format=txt" \
  -H "Authorization: Bearer $LIBRIS_TOKEN" -o volume-12.txt
# ZIP: chapters/001 - Title.txt … and manifest.json (SHA-256 of each file); report.json when it is the
# stored result
curl -sS "$LIBRIS_URL/api/v1/translation-requests/$REQUEST_ID/result?format=txt-zip" \
  -H "Authorization: Bearer $LIBRIS_TOKEN" -o volume-12.zip
# What is ready so far
curl -sS "$LIBRIS_URL/api/v1/translation-requests/$REQUEST_ID/result?partial=true" \
  -H "Authorization: Bearer $LIBRIS_TOKEN"
```

The result covers **the chapters of the request only** (not the rest of the volume; the whole book for an
EPUB), in reading order. While the request has not ended, the answer is `409 result_not_ready` (header
`Retry-After`) with `status` and `incomplete_chapters`; a failed or cancelled request answers
`409 request_failed` / `request_cancelled` with the `reason`. With `partial=true` the result is returned
anyway: `complete` is false, `incomplete_chapters` lists the chapters concerned, each chapter carries
`complete` and `missing_segments`. In every format, passages without a translation — residuals of a
`completed_with_residuals` request, or not yet translated in a partial result — **keep their source text**.
Every format sends `X-Libris-Complete: true|false` and `X-Libris-Status`.

#### The delivered EPUB

The translated EPUB is rebuilt from the original file with every translated passage; residual passages
keep their source markup and text. It is then checked by EPUBCheck (when `EPUBCHECK_JAR` is set). When
EPUBCheck rejects it, Libris repairs it automatically: the files named by the errors fall back to their
source text (the passages concerned become residuals with the reason `epubcheck_repair`), and the book is
checked again, at most `DELIVERY_REPAIR_ATTEMPTS` (3) times. Errors the original EPUB already had are
inherited, not caused by the translation: they are listed in `report.delivery.inherited_errors` and do not
block the delivery. Only when the repairs are exhausted does the request fail (`EPUBCheck refuse l’EPUB
traduit, même après réparation automatique.`, with the errors in `report.delivery.errors`). A passage whose
translated markup codes no longer match its source is also written in its source (`markup_mismatch`).

#### Completion report

In the status (`report`), the JSON result (`report`), the stored ZIP (`report.json`) and the webhook:

```json
{
  "version": 1, "outcome": "completed_with_residuals", "reason": null,
  "passages": {"total": 412, "translated": 410, "source_retained": 1, "untranslated": 1, "flagged": 3,
               "validated": 0, "human": 0, "by_status": {"ok": 407, "check": 3, "source_retained": 1, "error": 1}},
  "residual_total": 2,
  "residuals": [{"segment_id": "…", "chapter_id": "…", "chapter_external_id": null, "chapter_title": "…",
                 "position": 118, "status": "source_retained", "kept": "source", "reason": "…"}],
  "residuals_truncated": false,
  "usage": {"calls": 1290, "prompt_tokens": 2410000, "completion_tokens": 610000, "cached_calls": 12, "cost": 3.41},
  "durations": {"total_seconds": 5230.1, "queued_seconds": 0.4, "job_seconds": 5211.8},
  "autopilot": {"outcome": "completed_with_residuals", "rounds": 2, "reason": null},
  "decisions": {"autopilot": 17, "intake": [{"file": 2, "name": "Afterword.txt", "chapter_number": 3.0,
                                             "confidence": "low", "reason": "…"}]},
  "delivery": {"validation": {"available": true, "valid": true}, "repairs": [], "inherited_errors": []}
}
```

`residuals` lists at most 500 passages (`residual_total` counts them all). Their `reason` is the
autopilot's when it recorded one, else the passage's last error, else `source_retained` / `untranslated`.
`usage` sums the model calls of the request's job (`cost` only counts calls made with a known price; null
when none had one). `autopilot` and `decisions.autopilot` (the automatic decisions logged for the job) are
null when this build has no autopilot data.

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
| 409 | `idempotency_conflict`, `chapter_conflict` (with `conflicts`), `volume_conflict`, `series_archived`, `volume_archived`, `result_not_ready`, `request_failed`, `request_cancelled`, `format_unavailable`, `not_started`, `conflict` | State conflicts |
| 413 | `payload_too_large` | Body above the limit |
| 415 | `unsupported_media_type` | Neither JSON, EPUB nor multipart |
| 422 | `invalid_payload` (with `errors: [{loc, msg, type}]`), `invalid_request`, `invalid_idempotency_key`, `unknown_provider`, `provider_required`, `invalid_epub`, `callback_refused`, `delivery_failed` | Validation |
| 429 | `rate_limited` | Too many calls for this token (header `Retry-After`) |

## Limits

| Setting | Default | Effect |
| --- | --- | --- |
| `API_MAX_PAYLOAD_MB` | `MAX_UPLOAD_MB` | Largest body of a request sent with a Bearer token, enforced before the body is read. Without a token the limit is 1 MiB and the answer 401. |
| `API_MAX_CHAPTERS` | 2000 | Chapters per request. |
| `TEXT_CHAPTER_MAX_CHARS` | 2,000,000 | Characters per chapter (shared with TXT imports). |
| `API_RATE_LIMIT_PER_MINUTE` | 120 | Calls per token over a sliding minute, **counted in each API process** (with several API replicas the effective limit is multiplied). `0` disables it. |
| `API_RESULT_MAX_WAIT_SECONDS` | 60 | Longest `?wait=` long poll (0–600). |
| `API_REQUEST_STALL_MINUTES` | 360 | A request whose job stays paused, blocked or waiting longer fails (the job is cancelled). |
| `API_REQUEST_MAX_HOURS` | 168 | A request still unfinished after this fails (the job is cancelled). |
| `DELIVERY_REPAIR_ATTEMPTS` | 3 | EPUBCheck repair rounds of a delivered EPUB before the request fails. |
| `API_WEBHOOK_HOSTS` | empty | Hosts a `callback_url` may name (`host`, `*.domain`); empty: webhooks refused. |
| `API_WEBHOOK_PRIVATE_NETWORKS` | empty | CIDRs allowed although private (otherwise refused). |
| `API_WEBHOOK_SECRET` | empty | Global HMAC secret (32 characters at least) for tokens without their own. |
| `API_WEBHOOK_MAX_ATTEMPTS`, `API_WEBHOOK_TIMEOUT_SECONDS` | 6, 10 | Webhook calls per request, and the timeout of each. |

The size limit applies to the whole body (an EPUB, or all TXT files together) and is enforced before the
body is read; an EPUB is also bounded by the archive limits of imports (`MAX_UPLOAD_MB`,
`MAX_UNPACKED_MB`, `MAX_ENTRIES`, `MAX_COMPRESSION_RATIO`). Stored results stay under `DATA_DIR/results`
(back it up with the rest of `DATA_DIR`, or delete old folders: a request whose file is gone renders its
result again from the database).

Server-to-server clients do not send an `Origin` header and are accepted; a browser page on another site is
refused like for the interface (`ALLOWED_ORIGINS`, `Sec-Fetch-Site`).
