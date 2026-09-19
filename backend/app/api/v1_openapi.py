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
                "scope": s("string", "The missing scope (`insufficient_scope`)."),
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
            },
            "How many chapters the request created, found unchanged or replaced, and each chapter's progress.",
            required=["items"],
        ),
        "additionalProperties": True,
    },
    "StoredResult": obj(
        {
            "format": s("string", enum=["epub", "json", "txt", "txt-zip"]),
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
            },
            "What was translated, what kept its source and why, what it cost and how long it took.",
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
            },
            "The JSON result (`?format=json`).",
        ),
        "additionalProperties": True,
    },
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
        "result": nullable(ref("StoredResult")),
        "report": nullable(ref("CompletionReport")),
        "webhook": nullable(ref("WebhookState")),
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
    "VolumeReference.number": "Volume number, 1–10000.",
    "UploadOptions.volume": "Volume number, 1–10000. Required for TXT; for an EPUB in a series, taken from the "
    "file name when free, otherwise the next number.",
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
    "quality": "`fast`, `normal`, `high` or `maximum`.",
    "context_backend": "`internal`, `openviking` or `hybrid`.",
    "final_review": "Run the final review (never when the server disables it).",
    "output": "Default format of the result.",
    "output_format": "Default format of the result; `epub` only for an EPUB (and its default).",
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
    },  # fmt: skip
    "options": {"start": True, "final_review": True, "output_format": "json"},
    "result": None,
    "report": None,
    "webhook": {"state": "pending", "attempts": 0, "error": ""},
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
        "Without the `pipeline:start` scope, send `start=false` to import only.",
        "x-libris-scopes": ["content:write", "pipeline:start"],
        "scope_note": "`content:write`, and `pipeline:start` to start the pipeline",
        "errors": {
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
        "cancelled. Answers with the status document.",
        "errors": {
            "404": ("Unknown, or owned by someone else.", ["request_not_found"]),
            "409": ("The job's state does not allow the action.", ["not_started", "conflict"]),
        },
        "schema": "RequestDetail",
    },
    ("get", "/api/v1/translation-requests/{request_id}/result"): {
        "operationId": "getTranslationResult",
        "tags": ["Results"],
        "summary": "Download the result",
        "description": "The chapters of the request in reading order (the whole book for an EPUB), as an EPUB "
        "(EPUB requests only), a JSON document, one UTF-8 text file, or a ZIP of one text file per chapter "
        "with a `manifest.json`. `?format=` wins, then the `Accept` header, then the request's own format.\n\n"
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
}

RESULT_MEDIA = {
    "application/epub+zip": ({"type": "string", "format": "binary"}, "The translated EPUB (`format=epub`)."),
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
    if codes == ["rate_limited"] or "result_not_ready" in codes:
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
        if "description" not in parameter and parameter["name"] in PARAMETERS:
            parameter["description"] = PARAMETERS[parameter["name"]]
        parameters.append(parameter)
    if (method, path) == ("post", f"{PREFIX}/translation-requests"):
        parameters += raw_epub_parameters(options)
    if parameters:
        shaped["parameters"] = parameters
    if (method, path) == ("post", f"{PREFIX}/translation-requests"):
        shaped["requestBody"] = upload_body(options)
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


def webhooks() -> dict:
    headers = {
        "X-Libris-Event": "`translation_request.finished`.",
        "X-Libris-Delivery": "`<request id>:<attempt number>`.",
        "X-Libris-Timestamp": "Unix time in seconds.",
        "X-Libris-Signature": "`sha256=<hex>`: HMAC-SHA256 of `<timestamp>.<raw body>` with the token's webhook "
        "secret, or the server's `API_WEBHOOK_SECRET`.",
    }
    return {
        "translationRequestFinished": {
            "post": {
                "operationId": "translationRequestFinished",
                "tags": ["Translation requests"],
                "summary": "A request ended",
                "description": "Sent by the worker to the request's `callback_url` when it ends. Check the "
                "signature over the raw body and refuse old timestamps. Any `2xx` counts as delivered; anything "
                "else is retried with an exponential backoff (30 s, 60 s… up to one hour) at most "
                "`API_WEBHOOK_MAX_ATTEMPTS` times. Redirects are not followed.",
                "parameters": [
                    {
                        "name": name,
                        "in": "header",
                        "required": True,
                        "description": text,
                        "schema": {"type": "string"},
                    }
                    for name, text in headers.items()
                ],  # fmt: skip
                "requestBody": {
                    "required": True,
                    "content": {"application/json": {"schema": ref("WebhookEvent")}},
                },
                "responses": {"2XX": {"description": "Delivered."}},
                # Authenticated by its signature header, not by a token.
                "security": [],
            }
        }
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
