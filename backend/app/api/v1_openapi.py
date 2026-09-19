"""OpenAPI description of the automation API (`/api/v1`), published as docs/openapi/libris-v1.json.

Built from the application itself, so that a route added to `/api/v1` appears on the next export
(`python scripts/export_openapi.py`) with no other change: FastAPI describes its parameters and typed
bodies, the scope comes from its `require(...)` dependency, and it receives a tag, a summary (the first
line of its docstring) and the error responses every token route shares. This module then adds what
FastAPI cannot see: the bodies the handlers read themselves (JSON, multipart and raw EPUB uploads), the
shape of the dict answers, error codes and examples, and the webhook. The session API of the web
interface is never included.
"""

import copy
import inspect
import json
import re

from fastapi import FastAPI, routing
from fastapi.openapi.utils import get_openapi
from pydantic.json_schema import models_json_schema

from app.engines.delivery.intake import UploadOptions
from app.engines.ingestion.payload import TranslationPayload

PREFIX = "/api/v1"
OPENAPI_VERSION = "3.1.0"
# The version of the contract, not of Libris: it changes with the path (`/api/v2`), never with a release.
API_VERSION = "1"
DOCS = "https://github.com/HeartBtz/Libris/blob/main/docs/api.md"
REF = "#/components/schemas/"
STATUSES = [
    "queued", "imported", "pending", "running", "paused", "waiting", "blocked", "finalizing", "completed",
    "completed_with_residuals", "failed", "cancelled",
]  # fmt: skip

DESCRIPTION = f"""\
Send an EPUB, TXT chapters or a JSON document to a Libris server, let it translate on its own, then
download the result with its completion report. Nothing needs a person along the way.

**Authentication.** Every call carries an API token: `Authorization: Bearer lbr_…`. Tokens are created
in the interface (**My account › API tokens**); each one carries scopes, named on every operation below.
The session cookie of the web interface never opens `/api/v1`.

**Asynchronous work.** A request is saved before the `202 Accepted` answer and runs in the worker.
Poll its status (`?wait=` long-polls up to 60 seconds by default) or receive a signed webhook when it
ends. A request always ends: `completed`, `completed_with_residuals`, `failed` or `cancelled`
(`imported` when it only imported chapters).

**Errors.** Every error is `{{"detail": {{"code", "message", …}}}}`. `code` is stable; `message` is in
French, or in English with `Accept-Language: en`. Validation errors never echo the submitted text.

**Limits.** Calls per token and per minute (`API_RATE_LIMIT_PER_MINUTE`, `429` with `Retry-After`), body
size (`API_MAX_PAYLOAD_MB`) and chapters per request (`API_MAX_CHAPTERS`) are set by the server's
administrator.

The full guide, with `curl` examples, is [docs/api.md]({DOCS}).
"""

TAGS = {
    "Translation requests": "Send content, follow it, pause, resume or cancel it.",
    "Results": "Download the translated book, its chapters or its JSON document.",
    "Series": "The series of the token's owner and their volumes.",
    "Providers": "The model providers a request may name.",
    "Glossaries": "Shared glossaries: the terminology of a universe several of your series follow.",
}


def ref(name: str) -> dict:
    return {"$ref": REF + name}


def nullable(schema: dict) -> dict:
    if set(schema) <= {"type", "description", "format", "enum"} and isinstance(schema.get("type"), str):
        return {**schema, "type": [schema["type"], "null"]}
    return {"anyOf": [schema, {"type": "null"}]}


def obj(properties: dict, description: str = "", required: list[str] | None = None) -> dict:
    schema = {"type": "object"}
    if description:
        schema["description"] = description
    schema["properties"] = properties
    schema["required"] = list(properties) if required is None else required
    return schema


def s(kind: str, description: str = "", **extra) -> dict:
    schema = {"type": kind, **extra}
    if description:
        schema["description"] = description
    return schema


TIME = s("number", "Unix time in seconds.")
IDENTIFIER = s("string", "Identifier (UUID).")

# Answers built as dicts by the handlers. Properties listed here are always present; new ones may be
# added without a version change (clients must ignore what they do not know).
SCHEMAS: dict[str, dict] = {
    "Error": obj(
        {"detail": ref("ErrorDetail")},
        "Every error of the automation API.",
    ),
    "ErrorDetail": {
        **obj(
            {
                "code": s("string", "Stable machine-readable code, for example `chapter_conflict`."),
                "message": s(
                    "string",
                    "Human-readable explanation, French by default, English with `Accept-Language: en`.",
                ),
                "errors": s(
                    "array",
                    "Validation problems (`invalid_payload`, `invalid_request`).",
                    items=ref("ValidationIssue"),
                ),
                "scope": s(
                    "string",
                    "The missing scope (`insufficient_scope`), or whose quota is full, `token` or `account` "
                    "(`queue_full`).",
                ),
                "limit": s("integer", "The quota that is full (`queue_full`)."),
                "max_priority": s(
                    "string",
                    "The highest priority the token and its account may ask for (`priority_not_allowed`).",
                    enum=["low", "normal", "high"],
                ),
                "budget": obj(
                    {
                        "amount": s("number", "The token's cap, in the currency of the provider prices."),
                        "spent": s("number", "What the current period has spent."),
                        "period": s(
                            "string", "`month` (calendar month, UTC) or `total`.", enum=["month", "total"]
                        ),
                        "resets_at": nullable(
                            s("number", "Start of the next month (Unix time); `null` for a total cap.")
                        ),
                    },
                    "The token's cost budget (`budget_exceeded` answered with `402`).",
                ),
                "request_id": s("string", "The request that already used this key (`idempotency_conflict`)."),
                "status": s("string", "The request's status (`result_not_ready`, `request_failed`…)."),
                "reason": s("string", "Why the request failed or was cancelled."),
                "incomplete_chapters": s("array", "Chapters not fully translated yet.", items=s("string")),
                "conflicts": s(
                    "array", "Chapters already imported with another text (`chapter_conflict`).", items={}
                ),
                "protected_segments": s(
                    "array", "Passages edited by a person that a replacement would drop.", items={}
                ),
            },
            required=["code", "message"],
        ),
        "additionalProperties": True,
    },
    "ValidationIssue": obj(
        {
            "loc": s(
                "array",
                'Where the problem is, for example `["chapters", 0, "number"]`.',
                items={"type": ["string", "integer"]},
            ),
            "msg": s("string", "What is wrong (the submitted value is never repeated)."),
            "type": s("string", "Kind of problem."),
        },
        required=["loc", "msg"],
    ),
    "RequestStatus": s(
        "string",
        "`queued`: waiting for its volume. `imported`: chapters imported, nothing started (end state). "
        "`pending`/`running`: the job waits for a worker or works. `paused`, `waiting` (provider temporarily "
        "unavailable, retried), `blocked` (needs attention). `finalizing`: the result is being built. End "
        "states: `completed`, `completed_with_residuals` (some passages kept their source, listed in the "
        "report), `failed`, `cancelled`.",
        enum=STATUSES,
    ),
    "RequestSummary": obj(
        {
            "request_id": IDENTIFIER,
            "external_id": nullable(s("string", "Your identifier of the request.")),
            "series_id": nullable(IDENTIFIER),
            "project_id": nullable(s("string", "The volume (UUID).")),
            "job_id": nullable(s("string", "The pipeline job; `null` while the request waits (`queued`).")),
            "input": s("string", "What was sent.", enum=["epub", "txt", "json"]),
            "status": ref("RequestStatus"),
            "status_url": s("string", "Path of the status document."),
            "result_url": s("string", "Path of the result."),
        },
        "A translation request, as answered when it is created.",
    ),
    "StageProgress": obj(
        {
            "key": s("string", "`import`, `analysis`, `translation`, `review` or `export`."),
            "done": s("integer"),
            "total": s("integer"),
            "percent": s("integer"),
        }
    ),
    "Progress": obj(
        {
            "segments": s("integer", "Passages of the request's chapters (the whole book for an EPUB)."),
            "translated": s("integer"),
            "percent": s("integer"),
            "stages": s("array", "The volume's progress per stage.", items=ref("StageProgress")),
        }
    ),
    "ChapterProgress": obj(
        {
            "chapter_id": IDENTIFIER,
            "external_id": nullable(s("string")),
            "number": nullable(s("number")),
            "title": s("string"),
            "segments": s("integer"),
            "translated": s("integer"),
            "validated": s("integer"),
            "flagged": s(
                "integer", "Passages still flagged (`check`, `error`, `refused`) and not validated."
            ),
            "complete": s("boolean"),
        }
    ),
    "ChapterCounts": {
        **obj(
            {
                "created": s("integer"),
                "unchanged": s("integer"),
                "replaced": s("integer"),
                "items": s("array", items=ref("ChapterProgress")),
                "new": s(
                    "array",
                    "The chapters the request created or replaced (ids): the ones a follow-up translates.",
                    items=s("string"),
                ),
            },
            "How many chapters the request created, found unchanged or replaced, and each chapter's progress.",
            required=["items", "new"],
        ),
        "additionalProperties": True,
    },
    "StoredResult": obj(
        {
            "format": s("string", enum=["epub", "json", "txt", "txt-zip", "epub-bilingual"]),
            "media_type": s("string"),
            "filename": s("string"),
            "size": s("integer", "Bytes."),
            "sha256": s("string"),
            "created_at": TIME,
        },
        "The result file stored when the request ended successfully.",
    ),
    "WebhookState": obj(
        {
            "state": s("string", enum=["pending", "delivered", "failed"]),
            "attempts": s("integer"),
            "error": s("string", "Last failure, empty when none."),
        }
    ),
    "RequestDetail": {},  # RequestSummary plus the fields below, filled in by `detail_schema()`
    "Usage": obj(
        {
            "calls": s("integer"),
            "prompt_tokens": s("integer"),
            "completion_tokens": s("integer"),
            "cached_calls": s("integer"),
            "cost": nullable(s("number", "Cost of the calls with a known price; `null` when none had one.")),
        },
        "Model calls of the request's job.",
    ),
    "Residual": {
        **obj(
            {
                "segment_id": IDENTIFIER,
                "chapter_id": IDENTIFIER,
                "chapter_external_id": nullable(s("string")),
                "chapter_title": s("string"),
                "position": s("integer"),
                "status": s("string"),
                "kept": s("string", enum=["source"]),
                "reason": s(
                    "string",
                    "The autopilot's reason, the passage's last error, or `source_retained`, "
                    "`untranslated`, `markup_mismatch`, `epubcheck_repair`.",
                ),
            },
            "A passage delivered in its source text.",
            required=["segment_id", "kept", "reason"],
        ),
    },
    "CompletionReport": {
        **obj(
            {
                "version": s("integer"),
                "outcome": s(
                    "string",
                    enum=["completed", "completed_with_residuals", "failed", "cancelled", "imported"],
                ),
                "reason": nullable(s("string", "Why the request did not complete.")),
                "passages": {
                    **obj(
                        {
                            key: s("integer")
                            for key in (
                                "total",
                                "translated",
                                "source_retained",
                                "untranslated",
                                "flagged",
                                "validated",
                                "human",
                            )
                        }
                        | {
                            "by_status": s(
                                "object", "Passages per status.", additionalProperties=s("integer")
                            )
                        },
                        "Counts over the request's passages (the whole book for an EPUB).",
                    ),
                },
                "residual_total": s("integer"),
                "residuals": s(
                    "array",
                    "At most 500; `residuals_truncated` tells when the list is cut.",
                    items=ref("Residual"),
                ),
                "residuals_truncated": s("boolean"),
                "usage": ref("Usage"),
                "durations": obj(
                    {
                        "total_seconds": s("number"),
                        "queued_seconds": nullable(s("number")),
                        "job_seconds": nullable(s("number")),
                    }
                ),
                "autopilot": nullable(s("object", "How the autopilot ended: `outcome`, `rounds`, `reason`.")),
                "decisions": obj(
                    {
                        "autopilot": nullable(s("integer", "Decisions the autopilot logged for the job.")),
                        "intake": s(
                            "array",
                            "Choices made when reading the upload (volume and chapter numbers, "
                            "encoding, reused EPUB), each with its reason.",
                            items=s("object"),
                        ),
                    }
                ),
                "delivery": nullable(
                    s(
                        "object",
                        "EPUB only: EPUBCheck `validation`, `repairs`, `inherited_errors`, and `errors` "
                        "when the delivery failed.",
                    )
                ),
                "cost": nullable(ref("CostReport")),
                "quality": nullable(ref("QualityReport")),
            },
            "What was translated, what kept its source and why, what it cost and how long it took, and the "
            "quality scores of its passages.",
            required=[
                "version",
                "outcome",
                "reason",
                "passages",
                "residual_total",
                "residuals",
                "residuals_truncated",
                "usage",
                "durations",
                "autopilot",
                "decisions",
                "delivery",
            ],  # fmt: skip
        ),
        "additionalProperties": True,
    },
    "ResultChapter": {
        **obj(
            {
                "chapter_id": IDENTIFIER,
                "external_id": nullable(s("string")),
                "number": nullable(s("number")),
                "title": s("string"),
                "translated_title": s("string"),
                "complete": s("boolean"),
                "missing_segments": s("integer"),
                "translation": s("string", "The chapter's text; missing passages keep their source text."),
                "source_sha256": nullable(
                    s("string", "SHA-256 of the normalized source text (`null` when not recorded).")
                ),
                "sha256": s("string", "SHA-256 of `translation` (UTF-8)."),
                "review": obj({"segments": s("integer"), "validated": s("integer"), "flagged": s("integer")}),
                "issues": s(
                    "array",
                    "Unresolved quality issues: `segment_id`, `severity`, `code`, `message`.",
                    items=s("object"),
                ),
                "flagged_passages": s(
                    "array", "Passages still flagged: `segment_id`, `position`, `status`.", items=s("object")
                ),
            }
        ),
        "additionalProperties": True,
    },
    "JsonResult": {
        **obj(
            {
                "schema_version": s("integer"),
                "request_id": IDENTIFIER,
                "external_id": nullable(s("string")),
                "status": ref("RequestStatus"),
                "complete": s("boolean"),
                "series": nullable(obj({"id": IDENTIFIER, "name": s("string")})),
                "volume": obj(
                    {
                        "project_id": IDENTIFIER,
                        "external_id": nullable(s("string")),
                        "number": nullable(s("integer")),
                        "title": s("string"),
                    }
                ),
                "source_language": s("string"),
                "target_language": s("string"),
                "strategy": s(
                    "object",
                    "Provider name and model (never its address or key), `quality`, "
                    "`context_backend`, `final_review`.",
                ),
                "incomplete_chapters": s("array", items=s("string")),
                "chapters": s("array", items=ref("ResultChapter")),
                "report": nullable(ref("CompletionReport")),
                "scope": s(
                    "string",
                    "What the result covers: the request's chapters, only the new ones, or the whole volume.",
                    enum=["request", "new", "volume"],
                ),
            },
            "The JSON result (`?format=json`).",
            required=[
                "schema_version",
                "request_id",
                "external_id",
                "status",
                "complete",
                "series",
                "volume",
                "source_language",
                "target_language",
                "strategy",
                "incomplete_chapters",
                "chapters",
                "report",
            ],  # fmt: skip
        ),
        "additionalProperties": True,
    },
    "CostReport": obj(
        {
            "estimated": nullable(
                s("number", "The estimate made when the job started; `null` when none was made.")
            ),
            "actual": nullable(
                s("number", "The job's real cost (`usage.cost`); `null` when no call had a price.")
            ),
            "budget": nullable(s("number", "The book's cost budget; `null` without one.")),
            "book_spent": nullable(s("number", "What the book has cost in all, every job included.")),
            "warning": nullable(
                s(
                    "string",
                    "The warning given at launch when the estimate exceeded what was left of a budget.",
                )
            ),
            "paused_for_budget": s("boolean", "Whether the job is paused because a budget was reached."),
            "provider_switches": s(
                "integer",
                "How many times the job moved to a cheaper fallback provider to stay within a budget.",
            ),
        },
        "Estimated against real cost, in the currency of the provider prices, with the book's budget.",
    ),
    "QualityReport": {
        **obj(
            {
                "scored": s("integer", "Passages with a score."),
                "average": nullable(s("number", "Average score (0–100); `null` when none is scored.")),
                "minimum": nullable(s("integer", "Lowest score.")),
                "bands": s(
                    "object",
                    "Passages per band: `good` (85 and above), `fair` (70), `weak` (50), `poor` (below).",
                    additionalProperties=s("integer"),
                ),
                "histogram": s(
                    "array", "Passages per ten-point bucket, from 0–9 to 90–100.", items=s("integer")
                ),
                "to_review": s("integer", "Passages below `review_below` that no person validated."),
                "review_below": s("integer", "The score under which a passage should be reviewed."),
                "weakest_chapters": s(
                    "array",
                    "The 10 chapters with the lowest scores: `chapter_id`, `title`, `external_id`, `number`, "
                    "`passages`, `scored`, `average`, `minimum`, `weak`…",
                    items=s("object"),
                ),
                "review_first": s(
                    "array",
                    "The 10 passages to review first: `segment_id`, `chapter_id`, `position`, `score`, `band` and "
                    "the `signals` (`code`, `count`, `penalty`) that lowered them.",
                    items=s("object"),
                ),
            },
            "Quality scores (0–100) of the passages the request covers, computed from the signals Libris "
            "records (checks, critiques, doubts, failed calls, recoveries, retained originals); no model call.",
        ),
        "additionalProperties": True,
    },
    "QueuePlace": {
        **obj(
            {
                "position": nullable(
                    s(
                        "integer",
                        "Place in the line of its provider (1: next); `null` when it waits for its volume.",
                    )
                ),
                "reason": nullable(
                    s(
                        "string",
                        "Why it waits: `starting`, `provider_busy`, `account_limit`, `token_limit`, "
                        "`retry_scheduled`, `provider_missing`, or `volume_busy` while another job holds the volume.",
                    )
                ),
                "effective_priority": s(
                    "string",
                    "Its priority, raised one level after a long wait.",
                    enum=["low", "normal", "high"],
                ),
                "next_attempt": s("number", "When a waiting job retries (Unix time, `0` when not waiting)."),
            },
            "Where a request that has not started yet stands in the fair queue.",
            required=["position", "reason"],
        ),
        "additionalProperties": True,
    },
    "ChapterEvents": obj(
        {
            "event": s("string", enum=["chapters.translated"]),
            "batches": s("integer", "Batches queued so far."),
            "delivered": s("integer"),
            "pending": s("integer"),
            "failed": s("integer"),
            "waiting_chapters": s("integer", "Chapters of the request not translated (nor announced) yet."),
            "error": s("string", "Last failure of a batch, empty when none."),
        },
        "The `chapters.translated` webhooks of the request.",
    ),
    "Provider": {
        **obj(
            {
                "id": IDENTIFIER,
                "name": s("string"),
                "kind": s(
                    "string", "`openai`, `openai_direct`, `openai_responses`, `anthropic` or `codex_chatgpt`."
                ),
                "model": s("string"),
                "created_at": TIME,
                "default_for_series": s(
                    "array", "Your series that use this provider by default.", items=s("string")
                ),
            },
            "A provider a request may name: identity and model only, never an address or a key.",
        ),
        "additionalProperties": True,
    },
    "SeriesSummary": {
        **obj(
            {
                "id": IDENTIFIER,
                "name": s("string"),
                "kind": s("string"),
                "source_language": nullable(s("string")),
                "target_language": nullable(s("string")),
                "archived": s("boolean"),
                "volumes": s("integer"),
                "created_at": TIME,
                "updated_at": TIME,
            }
        ),
        "additionalProperties": True,
    },
    "VolumeSummary": {
        **obj(
            {
                "project_id": IDENTIFIER,
                "title": s("string"),
                "volume_number": nullable(s("integer")),
                "external_id": nullable(s("string")),
                "source_format": s("string"),
                "project_kind": s("string"),
                "status": s("string"),
                "chapters": s("integer"),
            }
        ),
        "additionalProperties": True,
    },
    "SeriesDetail": {},  # SeriesSummary plus `volume_list`, filled in by `detail_schema()`
    "WebhookEvent": {
        **obj(
            {
                "event": s("string", enum=["translation_request.finished"]),
                "request_id": IDENTIFIER,
                "external_id": nullable(s("string")),
                "status": ref("RequestStatus"),
                "error": nullable(s("string")),
                "project_id": nullable(s("string")),
                "job_id": nullable(s("string")),
                "status_url": s("string"),
                "result_url": s("string"),
                "artifact": nullable(
                    obj({"format": s("string"), "size": s("integer"), "sha256": s("string")})
                ),
                "report": nullable(ref("CompletionReport")),
                "finished_at": nullable(TIME),
            },
            "Sent once when a request ends (any end state, `imported` included).",
        ),
        "additionalProperties": True,
    },
    "ChaptersTranslatedEvent": {
        **obj(
            {
                "event": s("string", enum=["chapters.translated"]),
                "request_id": IDENTIFIER,
                "external_id": nullable(s("string")),
                "series_id": nullable(s("string")),
                "project_id": nullable(s("string")),
                "batch": s("integer", "Counts from 1 per request; order batches by it."),
                "chapters": s(
                    "array",
                    "The chapters of this batch, in reading order.",
                    items=obj(
                        {
                            "chapter_id": IDENTIFIER,
                            "external_id": nullable(s("string")),
                            "number": nullable(s("number")),
                            "title": s("string"),
                        }
                    ),
                ),
                "announced": s("integer", "Chapters announced so far, this batch included."),
                "total": s("integer", "Chapters in the request."),
                "status_url": s("string"),
                "result_url": s("string", "Partial result of the request (`?partial=true`)."),
                "created_at": TIME,
            },
            "Sent while the job runs, once per batch of chapters whose passages all have a translation.",
        ),
        "additionalProperties": True,
    },
    "SharedGlossaryInput": {
        **obj(
            {
                "name": s(
                    "string",
                    "1–200 characters, unique among your shared glossaries.",
                    minLength=1,
                    maxLength=200,
                ),
                "description": s("string", "At most 4000 characters.", maxLength=4000, default=""),
                "source_language": nullable(
                    s(
                        "string",
                        "BCP 47 tag; with the target, the glossary only applies to volumes of that pair.",
                    )
                ),
                "target_language": nullable(s("string", "BCP 47 tag.")),
            },
            "A new shared glossary.",
            required=["name"],
        ),
        "additionalProperties": False,
    },
    "SharedGlossaryAttachment": {
        **obj(
            {
                "glossary_id": nullable(
                    s("string", "The shared glossary to follow; `null` detaches the series.")
                )
            },
            "The shared glossary a series follows.",
        ),
        "additionalProperties": False,
    },
    "SharedGlossary": {
        **obj(
            {
                "id": IDENTIFIER,
                "name": s("string"),
                "description": s("string"),
                "source_language": nullable(s("string")),
                "target_language": nullable(s("string")),
                "created_at": TIME,
                "updated_at": TIME,
                "term_count": s("integer"),
                "locked_count": s("integer", "Locked terms: enforced and checked in every passage."),
                "series": s(
                    "array", "The series that follow it.", items=obj({"id": IDENTIFIER, "name": s("string")})
                ),
            },
            "A named glossary several series of the same universe follow. Its accepted terms come after the "
            "book's and the series' own (book > series > shared glossary).",
        ),
        "additionalProperties": True,
    },
    "SharedGlossaryDetail": {},  # SharedGlossary plus `terms`, filled in by `detail_schema()`
    "SharedTerm": {
        **obj(
            {
                "id": IDENTIFIER,
                "source": s("string"),
                "translation": s("string"),
                "category": s("string"),
                "description": s("string"),
                "locked": s(
                    "boolean", "Enforced by the pipeline and the autopilot, checked in every passage."
                ),
                "accepted": s("boolean", "Only accepted terms are applied."),
            }
        ),
        "additionalProperties": True,
    },
    "SeriesSharedGlossary": obj(
        {"glossary": nullable(ref("SharedGlossaryDetail"))},
        "The shared glossary the series follows, with its terms; `null` when it follows none.",
    ),
    "GlossaryImportForm": {
        **obj(
            {
                "file": s(
                    "string", "The glossary: JSON, CSV or TBX (v2 and v3), 2 MB at most.", format="binary"
                ),
                "strategy": s(
                    "string",
                    "`skip` keeps the terms in place; `replace` replaces unlocked terms that differ; "
                    "`replace_all` replaces locked ones too.",
                    enum=["skip", "replace", "replace_all"],
                    default="skip",
                ),
                "delimiter": s(
                    "string", "CSV separator; detected when absent.", enum=["comma", "semicolon", "tab"]
                ),
                "mapping": s(
                    "string",
                    'JSON object from field to column number (from 0), for example `{"source": 0, "translation": 2}`; '
                    "from the headers when absent.",
                ),
                "header": s("boolean", "Whether the first row holds column names; detected when absent."),
                "skip_invalid": s(
                    "boolean", "Leave invalid rows out instead of refusing the file.", default=False
                ),
            },
            "A glossary file and how to read it. CSV files may carry a byte order mark; French or English "
            "headers are recognised.",
            required=["file"],
        ),
    },
    "GlossaryImportReport": {
        **obj(
            {
                "format": s("string", enum=["json", "csv", "tbx"]),
                "encoding": s("string"),
                "delimiter": nullable(s("string")),
                "columns": s("array", "The first row of a CSV file.", items={}),
                "header": nullable(s("boolean")),
                "mapping": nullable(s("object", "Field → column number used.")),
                "strategy": s("string", enum=["skip", "replace", "replace_all"]),
                "counts": obj(
                    {
                        key: s("integer")
                        for key in (
                            "terms",
                            "new",
                            "unchanged",
                            "conflicts",
                            "replaced",
                            "kept",
                            "duplicates",
                            "errors",
                        )  # fmt: skip
                    }
                ),
                "new": s("array", "Terms the import adds (at most 200).", items=s("object")),
                "conflicts": s(
                    "array",
                    "Sources already present with other values: `existing`, `incoming`, the differing `fields`, "
                    "`locked` and the `action` (`replace` or `keep`); at most 200.",
                    items=s("object"),
                ),
                "duplicates": s(
                    "array",
                    "A source repeated in the file (the first row counts); at most 200.",
                    items=s("object"),
                ),
                "errors": s("array", "Invalid rows: `line`, `message`; at most 200.", items=s("object")),
                "truncated": s("boolean", "Whether a list was cut at 200 items."),
                "applied": s("boolean", "`false` for a preview (`dry_run=true`)."),
                "imported": s("integer", "Applied imports only: terms added."),
                "replaced": s("integer", "Applied imports only: terms replaced."),
                "skipped": s("integer", "Applied imports only: terms left as they were."),
            },
            "The import plan: what the file adds, what conflicts with the terms in place and what is invalid.",
            required=[
                "format",
                "strategy",
                "counts",
                "new",
                "conflicts",
                "duplicates",
                "errors",
                "truncated",
                "applied",
            ],
        ),
        "additionalProperties": True,
    },
}


def detail_schema() -> None:
    summary = SCHEMAS["RequestSummary"]
    detail = {
        "created_at": TIME,
        "updated_at": TIME,
        "finished_at": nullable(TIME),
        "stage": nullable(s("string", "Current stage of the volume (`null` without a job).")),
        "step": nullable(s("string", "Current step of the job, for example `translation`, `final_review`.")),
        "progress": ref("Progress"),
        "estimate": nullable(s("object", "Remaining time and cost, once enough calls were observed.")),
        "error": s("string", "Why the job stopped or failed; empty otherwise."),
        "stop_reason": s("string"),
        "next_attempt": s("number", "When a waiting job retries (Unix time, `0` when not waiting)."),
        "chapters": ref("ChapterCounts"),
        "options": obj(
            {"start": s("boolean"), "final_review": s("boolean"), "output_format": nullable(s("string"))}
        ),
        "priority": s(
            "string",
            "The request's priority, as changed by a person in the interface if it was.",
            enum=["low", "normal", "high"],
        ),
        "queue": nullable(ref("QueuePlace")),
        "result": nullable(ref("StoredResult")),
        "report": nullable(ref("CompletionReport")),
        "webhook": nullable(ref("WebhookState")),
        "chapter_events": nullable(ref("ChapterEvents")),
    }
    SCHEMAS["RequestDetail"] = {
        **obj({**summary["properties"], **detail}, "The status document of a request."),
        "additionalProperties": True,
    }
    series = SCHEMAS["SeriesSummary"]
    SCHEMAS["SeriesDetail"] = {
        **obj(
            {
                **series["properties"],
                "volume_list": s("array", "Sorted by volume number.", items=ref("VolumeSummary")),
            },
        ),
        "additionalProperties": True,
    }
    glossary = SCHEMAS["SharedGlossary"]
    SCHEMAS["SharedGlossaryDetail"] = {
        **obj(
            {**glossary["properties"], "terms": s("array", "Sorted by source.", items=ref("SharedTerm"))},
            "A shared glossary with its terms.",
        ),
        "additionalProperties": True,
    }


detail_schema()

# Short explanations of the fields of the models FastAPI does not know about (JSON document, options):
# `Model.field` first, then `field` alone.
FIELD_NOTES = {
    "TranslationPayload.external_id": "Your identifier of the request: a letter or digit, then letters, digits "
    "and `._:/-` (200 at most). Unique per owner: the same content sent again returns the original request.",
    "UploadOptions.external_id": "Your identifier of the request (same rules as in the JSON document).",
    "ChapterInput.external_id": "Your identifier of the chapter: sending it again finds the same chapter.",
    "VolumeReference.external_id": "Your identifier of the volume.",
    "TranslationPayload.series": "The series: `id` (one you own) or `name`.",
    "SeriesReference.id": "A series you own.",
    "SeriesReference.name": "Found by name; created when missing unless `create_if_missing` is false.",
    "UploadOptions.series": "The series by name, created when missing (not with `series_id`). Required for TXT.",
    "series_id": "The series by id (not with `series`).",
    "TranslationPayload.volume": "The volume: found by `external_id`, then by `number` in the series; otherwise "
    "created.",
    "VolumeReference.number": "Volume number, 1–10000. Required unless `latest` is true.",
    "VolumeReference.latest": "Follow up a webnovel: the chapters go to the series' last numbered volume (else "
    "its continuous chapter container, else volume 1). Not with `number`.",
    "UploadOptions.volume": "Volume number, 1–10000, or `latest` for the series' last volume (TXT only). Required "
    "for TXT; for an EPUB in a series, taken from the file name when free, otherwise the next number.",
    "volume_external_id": "Your identifier of the volume.",
    "VolumeReference.title": "Title of a new volume (default: “Series — number”).",
    "ChapterInput.title": "Optional; without it the chapter is named by its number.",
    "UploadOptions.title": "Volume title (an EPUB keeps its own otherwise).",
    "author": "Author of the volume.",
    "source_language": "BCP 47 tag such as `en`, `fr-FR`, `zh-Hant`.",
    "target_language": "BCP 47 tag.",
    "chapters": "1 to `API_MAX_CHAPTERS` chapters; numbers and `external_id`s must not repeat.",
    "ChapterInput.number": "Chapter number, 0–100000; decimals such as `12.5` allowed. Chapters are ordered by it.",
    "content": "The chapter's text (not blank, at most `TEXT_CHAPTER_MAX_CHARS` characters).",
    "replace_changed_chapters": "Replace chapters already imported with another text (otherwise "
    "`409 chapter_conflict`). Unchanged passages keep their translation.",
    "discard_human": "With `replace_changed_chapters`: allow dropping passages a person edited or validated "
    "(otherwise `409 conflict`).",
    "pipeline": "How to run the pipeline.",
    "start": "Run the whole pipeline (needs the `pipeline:start` scope); false only imports.",
    "provider_id": "Provider to use; defaults to the volume's, then the series' provider.",
    "final_review": "Run the final review (never when the server disables it).",
    "output": "Default format of the result.",
    "output_format": "Default format of the result; `epub` only for an EPUB (and its default); "
    "`epub-bilingual` is a bilingual EPUB for proofreading, for any input.",
    "OutputOptions.format": "Default format of the result; `epub-bilingual` is a bilingual EPUB for proofreading.",
    "priority": "Place in the fair queue: `low`, `normal` (default) or `high`, within the token's `max_priority` and "
    "its account's ceiling (otherwise `403 priority_not_allowed`). Does not change what the request is.",
    "TranslationPayload.callback_events": "Extra webhooks on top of `translation_request.finished`: "
    "`chapters.translated` sends one per batch of chapters translated while the job runs.",
    "UploadOptions.callback_events": "Comma-separated extra webhooks, for example `chapters.translated`.",
    "callback_url": "Webhook called when the request ends; its host must be allowed by an administrator.",
    "create_if_missing": "Create the series named by `name` when it does not exist.",
}

PARAMETERS = {
    "request_id": "The request's `request_id`.",
    "series_id": "The series' `id`.",
    "action": "`pause`, `resume` or `cancel`.",
    "wait": "Long poll: answer as soon as the request ends, or after this many seconds (bounded by the "
    "server's `API_RESULT_MAX_WAIT_SECONDS`, 60 by default).",
    "partial": "Return what is translated so far instead of `409 result_not_ready` (missing passages keep "
    "their source text; `X-Libris-Complete: false`).",
    "format": "Result format; wins over the `Accept` header. Default: the request's own format (EPUB for an "
    "EPUB).",
    "Idempotency-Key": "1–200 printable characters. Sending the same content again with the same key "
    "answers `200` with the original request; different content answers `409 idempotency_conflict`.",
    "filename": "The file name, used to guess the volume number.",
    "layout": "`format=epub-bilingual` only: each source paragraph followed by its translation (`interleaved`) or "
    "next to it in two columns that stack on a narrow screen (`side-by-side`).",
    "scope": "The chapters covered: those the request sent (`request`, the whole book for an EPUB), only those it "
    "created or replaced (`new`), or every chapter of the volume (`volume`). `epub` is always the whole book.",
    "glossary_id": "The shared glossary's `id`.",
}

COMMON_ERRORS = {
    "401": (
        "A missing, unknown, revoked or expired token, or a disabled account (header `WWW-Authenticate: "
        "Bearer`).",
        [
            "missing_token",
            "invalid_token",
            "revoked_token",
            "expired_token",
            "inactive_account",
            "unauthorized",
        ],
    ),
    "403": (
        "The token lacks the scope (`scope` names it), or a browser page from another site.",
        ["insufficient_scope", "forbidden"],
    ),
    "429": ("Too many calls for this token; retry after `Retry-After` seconds.", ["rate_limited"]),
    "500": (
        "Unexpected failure; the message carries a diagnostic reference for the server logs.",
        ["server_error"],
    ),
}
VALIDATION = ("A bad parameter.", ["invalid_request"])
MESSAGES = {
    "missing_token": "Jeton d’API manquant : envoyez « Authorization: Bearer <jeton> ».",
    "insufficient_scope": "Ce jeton n’a pas la permission « jobs:read ».",
    "rate_limited": "Trop de requêtes pour ce jeton : réessayez plus tard.",
    "invalid_request": "Requête invalide.",
    "result_not_ready": "La traduction de cette requête n’est pas terminée : réessayez plus tard, ou demandez "
    "un résultat partiel (partial=true).",
    "request_not_found": "Requête de traduction introuvable.",
    "series_not_found": "Série introuvable.",
    "idempotency_conflict": "Une autre requête a déjà utilisé cette clé d’idempotence ou cet external_id avec "
    "un contenu différent.",
    "invalid_payload": "Requête de traduction invalide.",
    "glossary_not_found": "Glossaire partagé introuvable.",
    "queue_full": "File d’attente pleine pour ce jeton : 50 travaux en attente au plus. Réessayez quand l’un "
    "d’eux aura démarré.",
    "priority_not_allowed": "Priorité « high » refusée : « normal » au plus pour ce compte ou ce jeton.",
}

REQUEST_EXAMPLE = {
    "external_id": "saga-volume-12",
    "series": {"name": "The Synthetic Saga", "create_if_missing": True},
    "volume": {"external_id": "volume-12", "number": 12, "title": "Volume 12"},
    "author": "A. Author",
    "source_language": "en",
    "target_language": "fr",
    "chapters": [
        {
            "external_id": "chapter-001",
            "number": 1,
            "title": "Chapter 1",
            "content": "First paragraph.\n\nSecond paragraph.\n",
        },
    ],  # fmt: skip
    "pipeline": {"start": True, "quality": "high", "context_backend": "hybrid", "final_review": True},
    "output": {"format": "json"},
    "callback_url": "https://hooks.example.org/libris",
}
SUMMARY_EXAMPLE = {
    "request_id": "5b1c2d3e-0000-4000-8000-000000000001",
    "external_id": "saga-volume-12",
    "series_id": "5b1c2d3e-0000-4000-8000-000000000002",
    "project_id": "5b1c2d3e-0000-4000-8000-000000000003",
    "job_id": "5b1c2d3e-0000-4000-8000-000000000004",
    "input": "json",
    "status": "pending",
    "status_url": "/api/v1/translation-requests/5b1c2d3e-0000-4000-8000-000000000001",
    "result_url": "/api/v1/translation-requests/5b1c2d3e-0000-4000-8000-000000000001/result",
}
DETAIL_EXAMPLE = {
    **SUMMARY_EXAMPLE,
    "status": "running",
    "created_at": 1790000000.0,
    "updated_at": 1790000100.0,
    "finished_at": None,
    "stage": "translation",
    "step": "translation",
    "progress": {
        "segments": 412,
        "translated": 180,
        "percent": 44,
        "stages": [{"key": "translation", "done": 180, "total": 412, "percent": 44}],
    },  # fmt: skip
    "estimate": None,
    "error": "",
    "stop_reason": "",
    "next_attempt": 0,
    "chapters": {
        "created": 1,
        "unchanged": 0,
        "replaced": 0,
        "items": [
            {
                "chapter_id": "5b1c2d3e-0000-4000-8000-000000000005",
                "external_id": "chapter-001",
                "number": 1,
                "title": "Chapter 1",
                "segments": 140,
                "translated": 60,
                "validated": 0,
                "flagged": 0,
                "complete": False,
            }
        ],
        "new": ["5b1c2d3e-0000-4000-8000-000000000005"],
    },  # fmt: skip
    "options": {"start": True, "final_review": True, "output_format": "json"},
    "priority": "normal",
    "queue": None,
    "result": None,
    "report": None,
    "webhook": {"state": "pending", "attempts": 0, "error": ""},
    "chapter_events": {
        "event": "chapters.translated",
        "batches": 0,
        "delivered": 0,
        "pending": 0,
        "failed": 0,
        "waiting_chapters": 1,
        "error": "",
    },  # fmt: skip
}

# What the generic description cannot say, per operation: (method, path) → overrides.
OPERATIONS: dict[tuple[str, str], dict] = {
    ("post", "/api/v1/translation-requests"): {
        "operationId": "createTranslationRequest",
        "tags": ["Translation requests"],
        "summary": "Send a translation request",
        "description": "Send **an EPUB** (multipart field `file`, or the raw file as `application/epub+zip` with "
        "its options in the query string), **TXT chapters** (multipart, one or more `.txt` files in `file` or "
        "`files`, one chapter each) or **a JSON document**. One request carries one kind of file.\n\n"
        "The request is stored before the answer and its pipeline starts in the worker: `202 Accepted` with a "
        "`Location` header. The same content sent again with the same `Idempotency-Key` or `external_id` "
        "answers `200` with the original request and `Idempotent-Replayed: true`.\n\n"
        "Without the `pipeline:start` scope, send `start=false` to import only.\n\n"
        "Chapters sent to a volume already translated (`volume.latest`, or the same volume again) are appended "
        "and only they are translated. The request waits its turn in the fair queue at the `priority` it asks "
        "for, and is refused when its token's cost budget is reached.",
        "x-libris-scopes": ["content:write", "pipeline:start"],
        "scope_note": "`content:write`, and `pipeline:start` to start the pipeline",
        "errors": {
            "402": (
                "The token's cost budget is reached (`budget` gives the cap, the spend, the period and when it "
                "resets); a request that only imports (`start` false) is still accepted.",
                ["budget_exceeded"],
            ),
            "403": (
                "The token lacks a scope (`scope` names it), a browser page from another site, or the priority "
                "asked for is above the token's or the account's ceiling (`max_priority`).",
                ["insufficient_scope", "forbidden", "priority_not_allowed"],
            ),
            "404": (
                "The series named by `id` does not exist, or `create_if_missing` is false.",
                ["series_not_found"],
            ),
            "409": (
                "The content cannot be taken as it is.",
                [
                    "idempotency_conflict",
                    "chapter_conflict",
                    "conflict",
                    "volume_conflict",
                    "series_archived",
                    "volume_archived",
                ],
            ),
            "413": ("The body is above `API_MAX_PAYLOAD_MB`.", ["payload_too_large"]),
            "415": ("Neither JSON, EPUB nor multipart.", ["unsupported_media_type"]),
            "429": (
                "Too many calls for this token (retry after `Retry-After` seconds), or the token or its account "
                "already has its quota of requests waiting to start (`scope`, `limit`): nothing is stored, retry "
                "once one of them has started.",
                ["rate_limited", "queue_full"],
            ),
            "422": (
                "The document, the upload or an option is invalid.",
                [
                    "invalid_payload",
                    "invalid_idempotency_key",
                    "unknown_provider",
                    "provider_required",
                    "invalid_epub",
                    "callback_refused",
                ],
            ),
        },
        "success": {
            "202": ("Accepted: the request is stored and will run in the worker.", True),
            "200": ("Replayed: this content was already sent with this key or `external_id`.", True),
        },
    },
    ("get", "/api/v1/translation-requests/{request_id}"): {
        "operationId": "getTranslationRequest",
        "tags": ["Translation requests"],
        "summary": "Get the status of a request",
        "description": "Status, progress per chapter and stage, stored result and, once the request ended, its "
        "completion report. Add `?wait=` to long-poll until the request ends.",
        "errors": {"404": ("Unknown, or owned by someone else.", ["request_not_found"])},
        "schema": "RequestDetail",
        "example": DETAIL_EXAMPLE,
    },
    ("post", "/api/v1/translation-requests/{request_id}/{action}"): {
        "operationId": "controlTranslationRequest",
        "tags": ["Translation requests"],
        "summary": "Pause, resume or cancel a request",
        "description": "Same rules as the interface. A request without a job yet (`queued`) can only be "
        "cancelled. Resuming a job paused by a cost budget is refused until the budget is raised, and resuming "
        "counts as a new entry in the queue. Answers with the status document.",
        "errors": {
            "404": ("Unknown, or owned by someone else.", ["request_not_found"]),
            "409": (
                "The job's state does not allow the action, or the book's or the token's cost budget that paused "
                "it is still reached.",
                ["not_started", "conflict", "budget_exceeded"],
            ),
            "429": (
                "Too many calls for this token (retry after `Retry-After` seconds), or resuming would exceed the "
                "token's or the account's quota of waiting requests (`scope`, `limit`).",
                ["rate_limited", "queue_full"],
            ),
        },
        "schema": "RequestDetail",
    },
    ("get", "/api/v1/translation-requests/{request_id}/result"): {
        "operationId": "getTranslationResult",
        "tags": ["Results"],
        "summary": "Download the result",
        "description": "The chapters of the request in reading order (the whole book for an EPUB), as an EPUB "
        "(EPUB requests only), a bilingual EPUB for proofreading (`epub-bilingual`, any input, `layout` "
        "interleaved or side by side), a JSON document, one UTF-8 text file, or a ZIP of one text file per "
        "chapter with a `manifest.json`. `?format=` wins, then the `Accept` header, then the request's own "
        "format. `scope` chooses the chapters: the request's, only its new ones, or the whole volume.\n\n"
        "Passages without a translation keep their source text. Before the end the answer is "
        "`409 result_not_ready` (with `Retry-After`), unless `partial=true`.",
        "errors": {
            "404": ("Unknown request, or its volume was deleted.", ["request_not_found", "volume_not_found"]),
            "409": (
                "The result cannot be served yet or at all.",
                ["result_not_ready", "request_failed", "request_cancelled", "format_unavailable"],
            ),
            "422": (
                "A bad parameter, or an EPUB rendered on demand could not be built.",
                ["invalid_request", "delivery_failed"],
            ),
        },
        "result": True,
    },
    ("get", "/api/v1/providers"): {
        "operationId": "listProviders",
        "tags": ["Providers"],
        "summary": "List the providers",
        "description": "The providers a request may name in `provider_id`, sorted by name: identity and model "
        "only, never an address or a key.",
        "schema": "Provider",
        "array": True,
    },
    ("get", "/api/v1/series"): {
        "operationId": "listSeries",
        "tags": ["Series"],
        "summary": "List your series",
        "description": "The series the token's owner owns, sorted by name.",
        "schema": "SeriesSummary",
        "array": True,
    },
    ("get", "/api/v1/series/{series_id}"): {
        "operationId": "getSeries",
        "tags": ["Series"],
        "summary": "Get a series and its volumes",
        "errors": {"404": ("Unknown, or owned by someone else.", ["series_not_found"])},
        "schema": "SeriesDetail",
    },
    ("get", "/api/v1/glossaries"): {
        "operationId": "listSharedGlossaries",
        "tags": ["Glossaries"],
        "summary": "List your shared glossaries",
        "description": "Your shared glossaries, sorted by name, with their term counts and the series that "
        "follow them.",
        "schema": "SharedGlossary",
        "array": True,
    },
    ("post", "/api/v1/glossaries"): {
        "operationId": "createSharedGlossary",
        "tags": ["Glossaries"],
        "summary": "Create a shared glossary",
        "description": "An empty shared glossary; fill it with an import. Languages are optional: with them, "
        "it only applies to volumes of the same pair.",
        "body": "SharedGlossaryInput",
        "errors": {
            "409": ("You already have a shared glossary of that name.", ["glossary_exists"]),
            "422": ("A bad body, or a blank name.", ["invalid_request", "invalid_name"]),
        },
        "schema": "SharedGlossaryDetail",
    },
    ("get", "/api/v1/glossaries/{glossary_id}"): {
        "operationId": "getSharedGlossary",
        "tags": ["Glossaries"],
        "summary": "Get a shared glossary and its terms",
        "errors": {"404": ("Unknown, or owned by someone else.", ["glossary_not_found"])},
        "schema": "SharedGlossaryDetail",
    },
    ("get", "/api/v1/glossaries/{glossary_id}/export/{format}"): {
        "operationId": "exportSharedGlossary",
        "tags": ["Glossaries"],
        "summary": "Download a shared glossary",
        "description": "Its terms as JSON, CSV (for spreadsheets: `delimiter=semicolon&bom=true`) or TBX, with the "
        "fields `source`, `translation`, `category`, `description`, `locked`, `accepted`.",
        "parameters": {
            "format": "`json`, `csv` or `tbx`.",
            "delimiter": "CSV separator.",
            "bom": "Start a CSV file with a UTF-8 byte order mark (for Excel).",
        },
        "errors": {"404": ("Unknown, or owned by someone else.", ["glossary_not_found"])},
        "response": {
            "description": "The file, with a `Content-Disposition` file name.",
            "headers": {"Content-Disposition": {"description": "File name.", "schema": {"type": "string"}}},
            "content": {
                "application/json": {"schema": s("array", items=s("object"))},
                "text/csv": {"schema": s("string")},
                "application/x-tbx+xml": {"schema": s("string")},
            },
        },
    },
    ("post", "/api/v1/glossaries/{glossary_id}/import"): {
        "operationId": "importSharedGlossary",
        "tags": ["Glossaries"],
        "summary": "Import terms into a shared glossary",
        "description": "Reads a JSON, CSV or TBX file into the glossary and answers the import report. With "
        "`dry_run=true`, nothing changes: the report is a preview that lists invalid rows instead of refusing "
        "the file. Sources are matched case-insensitively.",
        "parameters": {"dry_run": "Preview the import without changing anything."},
        "requestBody": {
            "description": "The glossary file and its import options.",
            "required": True,
            "content": {"multipart/form-data": {"schema": ref("GlossaryImportForm")}},
        },
        "errors": {
            "404": ("Unknown, or owned by someone else.", ["glossary_not_found"]),
            "413": ("The file is above 2 MB.", ["glossary_too_large"]),
            "422": (
                "The file cannot be read, has invalid rows (without `skip_invalid`), or an import option is invalid.",
                ["invalid_glossary", "invalid_strategy", "invalid_mapping", "invalid_request"],
            ),
        },
        "schema": "GlossaryImportReport",
    },
    ("get", "/api/v1/series/{series_id}/shared-glossary"): {
        "operationId": "getSeriesSharedGlossary",
        "tags": ["Glossaries"],
        "summary": "Get the shared glossary a series follows",
        "errors": {"404": ("Unknown series, or owned by someone else.", ["series_not_found"])},
        "schema": "SeriesSharedGlossary",
    },
    ("put", "/api/v1/series/{series_id}/shared-glossary"): {
        "operationId": "attachSeriesSharedGlossary",
        "tags": ["Glossaries"],
        "summary": "Attach a series to a shared glossary",
        "description": "The series follows this shared glossary from its next passages on (at most one per "
        "series); `glossary_id: null` detaches it. A glossary with languages only fits a series of the same pair.",
        "body": "SharedGlossaryAttachment",
        "errors": {
            "404": (
                "Unknown series or glossary, or owned by someone else.",
                ["series_not_found", "glossary_not_found"],
            ),
            "409": ("The glossary's languages differ from the series'.", ["language_mismatch"]),
            "422": ("A bad body.", ["invalid_request"]),
        },
        "schema": "SeriesSharedGlossary",
    },
}

RESULT_MEDIA = {
    "application/epub+zip": (
        {"type": "string", "format": "binary"},
        "The translated EPUB (`format=epub`), or the bilingual EPUB (`format=epub-bilingual`).",
    ),
    "application/json": (ref("JsonResult"), "The JSON document (`format=json`)."),
    "text/plain": ({"type": "string"}, "One UTF-8 text file, chapters under their headings (`format=txt`)."),
    "application/zip": (
        {"type": "string", "format": "binary"},
        "`chapters/001 - Title.txt`… and `manifest.json` with each file's SHA-256 (`format=txt-zip`).",
    ),
}


# --- Walking the application -------------------------------------------------------------------------


def route_contexts(app: FastAPI):
    """Every route of the application, the included routers opened (FastAPI builds them lazily)."""
    walk = getattr(routing, "iter_route_contexts", None)
    return list(walk(app.routes)) if walk else list(app.routes)


def scopes_of(dependant) -> list[str]:
    """The scopes a route requires: the `scope` of each `require(...)` dependency (app.api.tokens)."""
    found = []
    for child in getattr(dependant, "dependencies", []) or []:
        call = getattr(child, "call", None)
        if call is not None and getattr(call, "__qualname__", "").startswith("require."):
            scope = inspect.getclosurevars(call).nonlocals.get("scope")
            if isinstance(scope, str) and scope not in found:
                found.append(scope)
        found += [scope for scope in scopes_of(child) if scope not in found]
    return found


def endpoints(app: FastAPI) -> dict[tuple[str, str], tuple]:
    found = {}
    for route in route_contexts(app):
        path = getattr(route, "path_format", None) or getattr(route, "path", None) or ""
        if not path.startswith(PREFIX + "/") or not getattr(route, "include_in_schema", True):
            continue
        for method in getattr(route, "methods", None) or ():
            found[(method.lower(), path)] = (route.endpoint, scopes_of(getattr(route, "dependant", None)))
    return found


# --- Shaping ---------------------------------------------------------------------------------------------


def camel(name: str) -> str:
    head, *rest = name.split("_")
    return head + "".join(part[:1].upper() + part[1:] for part in rest)


def default_tag(path: str) -> str:
    segment = path[len(PREFIX) + 1 :].split("/")[0]
    return {"translation-requests": "Translation requests"}.get(
        segment, segment.replace("-", " ").capitalize()
    )


def docstring(endpoint) -> tuple[str, str]:
    text = inspect.cleandoc(endpoint.__doc__ or "") if endpoint is not None else ""
    first, _, rest = text.partition("\n")
    return first.strip().rstrip("."), " ".join(rest.split())


def untitled(schema):
    """Without the titles pydantic and FastAPI generate from Python names (properties keep their names)."""
    if isinstance(schema, dict):
        return {
            key: {name: untitled(item) for name, item in value.items()}
            if key == "properties" and isinstance(value, dict)
            else untitled(value)
            for key, value in schema.items()
            if key != "title" or not isinstance(value, str)
        }
    if isinstance(schema, list):
        return [untitled(item) for item in schema]
    return schema


def clean(schema):
    """A parameter or a form field: an optional value is simply absent, never `null`."""
    schema = untitled(schema)
    if isinstance(schema, dict):
        schema = {key: clean(value) for key, value in schema.items()}
        options = schema.get("anyOf")
        if isinstance(options, list) and len(options) == 2 and {"type": "null"} in options:
            other = next(option for option in options if option != {"type": "null"})
            rest = {key: value for key, value in schema.items() if key != "anyOf"}
            schema = {**other, **rest}
        if isinstance(schema.get("type"), list) and "null" in schema["type"] and len(schema["type"]) == 2:
            schema["type"] = next(kind for kind in schema["type"] if kind != "null")
        if "default" in schema and schema["default"] is None:
            del schema["default"]
        return schema
    if isinstance(schema, list):
        return [clean(item) for item in schema]
    return schema


def error_response(description: str, codes: list[str]) -> dict:
    listed = ", ".join(f"`{code}`" for code in codes)
    content = {"schema": ref("Error")}
    examples = {
        code: {"value": {"detail": {"code": code, "message": MESSAGES.get(code, "…")}}}
        for code in codes
        if code in MESSAGES
    }
    if examples:
        content["examples"] = examples
    response = {"description": f"{description} Codes: {listed}.", "content": {"application/json": content}}
    if "rate_limited" in codes or "result_not_ready" in codes:
        response["headers"] = {
            "Retry-After": {"description": "Seconds to wait.", "schema": {"type": "integer"}}
        }
    if "missing_token" in codes:
        response["headers"] = {
            "WWW-Authenticate": {"description": '`Bearer realm="libris"`', "schema": {"type": "string"}}
        }
    return response


def json_response(description: str, schema: dict, example=None) -> dict:
    content = {"schema": schema}
    if example is not None:
        content["example"] = example
    return {"description": description, "content": {"application/json": content}}


def field_notes(name: str, schema: dict) -> dict:
    """Adds FIELD_NOTES to the properties of a generated model schema, when they have none."""
    for field, value in (schema.get("properties") or {}).items():
        note = FIELD_NOTES.get(f"{name}.{field}") or FIELD_NOTES.get(field)
        if isinstance(value, dict) and "description" not in value and note:
            value["description"] = note
    return schema


def nullable_types(schema):
    """`anyOf: [{"type": X, …}, {"type": "null"}]` of generated models becomes `type: [X, "null"]`."""
    if isinstance(schema, dict):
        schema = {key: nullable_types(value) for key, value in schema.items()}
        options = schema.get("anyOf")
        if isinstance(options, list) and len(options) == 2 and {"type": "null"} in options:
            other = next(option for option in options if option != {"type": "null"})
            if isinstance(other.get("type"), str):
                rest = {key: value for key, value in schema.items() if key != "anyOf"}
                schema = {**other, "type": [other["type"], "null"], **rest}
        return schema
    if isinstance(schema, list):
        return [nullable_types(item) for item in schema]
    return schema


def model_schemas() -> dict:
    _, generated = models_json_schema(
        [(TranslationPayload, "validation"), (UploadOptions, "validation")], ref_template=REF + "{model}"
    )
    schemas = {
        name: field_notes(name, nullable_types(untitled(schema)))
        for name, schema in generated.get("$defs", {}).items()
    }
    schemas["TranslationPayload"]["example"] = REQUEST_EXAMPLE
    schemas["TranslationPayload"]["description"] = (
        "A JSON translation request. Unknown fields are refused; nothing is ever downloaded from a URL it names."
    )
    options = schemas.pop("UploadOptions")
    return schemas, options


def upload_body(options: dict) -> dict:
    form = clean(copy.deepcopy(options))
    form["description"] = (
        "An EPUB (one file in `file`), TXT chapters (one or more `.txt` files in `file` or `files`; `series` or "
        "`series_id`, `volume`, `source_language` and `target_language` required) or one `.json` document in "
        "`file` with no other field. Empty fields count as not given; unknown fields are refused."
    )
    form["properties"] = {
        "file": {"type": "string", "format": "binary", "description": "The EPUB, the JSON document or a TXT "
                                                                     "chapter."},
        "files": {"type": "array", "items": {"type": "string", "format": "binary"},
                  "description": "TXT chapters, one file each, numbered from their names."},
        **form.get("properties", {}),
    }  # fmt: skip
    form.pop("required", None)
    return {
        "description": "The content to translate.",
        "required": True,
        "content": {
            "application/json": {"schema": ref("TranslationPayload"), "example": REQUEST_EXAMPLE},
            "multipart/form-data": {
                "schema": form,
                "encoding": {
                    "file": {"contentType": "application/epub+zip, application/json, text/plain"},
                    "files": {"contentType": "text/plain"},
                },
            },  # fmt: skip
            "application/epub+zip": {"schema": {"type": "string", "format": "binary"}},
        },
    }


def raw_epub_parameters(options: dict) -> list[dict]:
    parameters = []
    for name, schema in [("filename", {"type": "string"}), *options.get("properties", {}).items()]:
        schema = clean(copy.deepcopy(schema))
        note = schema.pop("description", "") or PARAMETERS.get(name, "")
        parameters.append(
            {
                "name": name,
                "in": "query",
                "required": False,
                "description": f"Raw EPUB body only (`Content-Type: application/epub+zip`). {note}".strip(),
                "schema": schema,
                # Lets a renderer group these options apart from the parameters of every call.
                "x-libris-raw-epub-only": True,
            }
        )
    return parameters


def shape(operation: dict, method: str, path: str, endpoint, scopes: list[str], options: dict) -> dict:
    extra = OPERATIONS.get((method, path), {})
    summary, description = docstring(endpoint)
    name = getattr(endpoint, "__name__", "") or f"{method}_{path}"
    shaped = {
        "operationId": extra.get("operationId") or camel(re.sub(r"\W", "_", name)),
        "tags": extra.get("tags") or [default_tag(path)],
        "summary": extra.get("summary") or summary or name.replace("_", " ").capitalize(),
    }
    description = extra.get("description") or description
    scopes = extra.get("x-libris-scopes") or scopes
    if scopes:
        needed = extra.get("scope_note") or " and ".join(f"`{scope}`" for scope in scopes)
        description = f"{description}\n\n**Scope:** {needed}." if description else f"**Scope:** {needed}."
    if description:
        shaped["description"] = description
    parameters = []
    for parameter in operation.get("parameters", []):
        if parameter.get("in") == "header" and parameter["name"].casefold() == "authorization":
            continue  # the Bearer token is the security scheme
        parameter = clean(parameter)
        note = (extra.get("parameters") or {}).get(parameter["name"]) or PARAMETERS.get(parameter["name"])
        if "description" not in parameter and note:
            parameter["description"] = note
        parameters.append(parameter)
    if (method, path) == ("post", f"{PREFIX}/translation-requests"):
        parameters += raw_epub_parameters(options)
    if parameters:
        shaped["parameters"] = parameters
    if (method, path) == ("post", f"{PREFIX}/translation-requests"):
        shaped["requestBody"] = upload_body(options)
    elif "requestBody" in extra:
        shaped["requestBody"] = extra["requestBody"]
    elif "body" in extra:
        shaped["requestBody"] = {
            "required": True,
            "content": {"application/json": {"schema": ref(extra["body"])}},
        }
    elif "requestBody" in operation:
        shaped["requestBody"] = clean(operation["requestBody"])
    responses = {}
    generated = {code: value for code, value in operation.get("responses", {}).items() if code != "422"}
    for code, value in generated.items():
        responses[code] = clean(value)
        if value.get("description") == "Successful Response":
            responses[code]["description"] = "Success."
    if "schema" in extra:
        schema = ref(extra["schema"])
        schema = {"type": "array", "items": schema} if extra.get("array") else schema
        code = next(iter(generated), "200")
        responses[code] = json_response("Success.", schema, extra.get("example"))
    if "response" in extra:
        responses[next(iter(generated), "200")] = extra["response"]
    if "success" in extra:
        responses = {}
        for code, (text, located) in extra["success"].items():
            responses[code] = json_response(text, ref("RequestSummary"), SUMMARY_EXAMPLE)
            if located:
                responses[code]["headers"] = {
                    "Location": {"description": "Path of the status document.", "schema": {"type": "string"}}
                }
        responses["200"]["headers"]["Idempotent-Replayed"] = {
            "description": "`true`.", "schema": {"type": "string"}
        }  # fmt: skip
    if extra.get("result"):
        responses["200"] = {
            "description": "The result. `X-Libris-Complete` tells whether every passage is translated.",
            "headers": {
                "X-Libris-Complete": {"description": "`true` or `false`.", "schema": {"type": "string"}},
                "X-Libris-Status": {"description": "The request's status.", "schema": ref("RequestStatus")},
                "Content-Disposition": {
                    "description": "File name (every format but JSON).",
                    "schema": {"type": "string"},
                },
            },  # fmt: skip
            "content": {media: {"schema": schema} for media, (schema, _) in RESULT_MEDIA.items()},
        }
        notes = " ".join(f"`{media}`: {text}" for media, (_, text) in RESULT_MEDIA.items())
        responses["200"]["description"] += " " + notes
    errors = dict(COMMON_ERRORS)
    if any(item["in"] != "path" for item in parameters) and "422" not in extra.get("errors", {}):
        errors["422"] = VALIDATION
    errors.update(extra.get("errors", {}))
    for code in sorted(errors):
        responses[code] = error_response(*errors[code])
    shaped["responses"] = responses
    if scopes:
        shaped["security"] = [{"bearerToken": []}]
        shaped["x-libris-scopes"] = scopes
    return shaped


def webhook_operation(
    name: str, event: str, delivery: str, summary: str, description: str, schema: str
) -> dict:
    headers = {
        "X-Libris-Event": f"`{event}`.",
        "X-Libris-Delivery": delivery,
        "X-Libris-Timestamp": "Unix time in seconds.",
        "X-Libris-Signature": "`sha256=<hex>`: HMAC-SHA256 of `<timestamp>.<raw body>` with the token's webhook "
        "secret, or the server's `API_WEBHOOK_SECRET`.",
    }
    return {
        "post": {
            "operationId": name,
            "tags": ["Translation requests"],
            "summary": summary,
            "description": description
            + " Check the signature over the raw body and refuse old timestamps. Any "
            "`2xx` counts as delivered; anything else is retried with an exponential backoff (30 s, 60 s… up to "
            "one hour) at most `API_WEBHOOK_MAX_ATTEMPTS` times. Redirects are not followed.",
            "parameters": [
                {
                    "name": header,
                    "in": "header",
                    "required": True,
                    "description": text,
                    "schema": {"type": "string"},
                }
                for header, text in headers.items()
            ],  # fmt: skip
            "requestBody": {
                "required": True,
                "content": {"application/json": {"schema": ref(schema)}},
            },
            "responses": {"2XX": {"description": "Delivered."}},
            # Authenticated by its signature header, not by a token.
            "security": [],
        }
    }


def webhooks() -> dict:
    return {
        "chaptersTranslated": webhook_operation(
            "chaptersTranslated",
            "chapters.translated",
            "`<request id>:chapters.translated:<batch>:<attempt number>`.",
            "Chapters were translated",
            "Sent by the worker to the request's `callback_url` while its job runs, once per batch of chapters "
            "whose passages all have a translation, when the request listed `chapters.translated` in "
            "`callback_events`. Batches go before the final webhook when both are due, but a retried batch can "
            "arrive after it: order them by `batch`. The text of a batch is a draft until the request ends.",
            "ChaptersTranslatedEvent",
        ),
        "translationRequestFinished": webhook_operation(
            "translationRequestFinished",
            "translation_request.finished",
            "`<request id>:<attempt number>`.",
            "A request ended",
            "Sent by the worker to the request's `callback_url` when it ends.",
            "WebhookEvent",
        ),
    }


def references(value, found: set[str]) -> None:
    if isinstance(value, dict):
        target = value.get("$ref")
        if isinstance(target, str) and target.startswith(REF):
            found.add(target[len(REF) :])
        for item in value.values():
            references(item, found)
    elif isinstance(value, list):
        for item in value:
            references(item, found)


def reachable(roots, schemas: dict) -> dict:
    """The schemas the kept operations use, directly or through other schemas."""
    found: set[str] = set()
    references(roots, found)
    pending = list(found)
    while pending:
        before = set(found)
        references(schemas.get(pending.pop(), {}), found)
        pending += sorted(found - before)
    return {name: schemas[name] for name in sorted(found) if name in schemas}


def build(app: FastAPI) -> dict:
    """The OpenAPI document of `/api/v1`, deterministic for a given code base."""
    generated = get_openapi(
        title="Libris", version=API_VERSION, openapi_version=OPENAPI_VERSION, routes=app.routes
    )
    known = endpoints(app)
    models, options = model_schemas()
    paths: dict[str, dict] = {}
    for path in sorted(generated.get("paths", {})):
        if not path.startswith(PREFIX + "/"):
            continue
        for method, operation in generated["paths"][path].items():
            endpoint, scopes = known.get((method, path), (None, []))
            paths.setdefault(path, {})[method] = shape(operation, method, path, endpoint, scopes, options)
    hooks = webhooks()
    everything = {**generated.get("components", {}).get("schemas", {}), **models, **SCHEMAS}
    schemas = reachable([paths, hooks], everything)
    used = sorted(
        {tag for item in paths.values() for operation in item.values() for tag in operation["tags"]}
    )
    tags = [{"name": name, "description": text} for name, text in TAGS.items() if name in used]
    tags += [{"name": name} for name in used if name not in TAGS]
    return {
        "openapi": OPENAPI_VERSION,
        "info": {
            "title": "Libris automation API",
            "version": API_VERSION,
            "summary": "Send books to a Libris server and get them back translated, with no human step.",
            "description": DESCRIPTION,
            "license": {"name": "AGPL-3.0-only", "identifier": "AGPL-3.0-only"},
        },
        "externalDocs": {"description": "Automation API guide", "url": DOCS},
        "servers": [
            {
                "url": "{server}",
                "description": "Your Libris server.",
                "variables": {"server": {"default": "https://libris.example.org"}},
            }
        ],
        "tags": tags,
        "paths": paths,
        "webhooks": hooks,
        "components": {
            "schemas": schemas,
            "securitySchemes": {
                "bearerToken": {
                    "type": "http",
                    "scheme": "bearer",
                    "bearerFormat": "lbr_<8 characters>_<secret>",
                    "description": "An API token created in the interface (My account › API tokens).",
                }
            },
        },
    }


def document_text(app: FastAPI) -> str:
    """The committed file's exact text: the document, indented, with a final newline."""
    return json.dumps(build(app), indent=2, ensure_ascii=False) + "\n"
