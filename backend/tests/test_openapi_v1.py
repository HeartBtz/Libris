"""The published OpenAPI description of /api/v1: complete, self-consistent, true to the real answers,
and identical to the committed docs/openapi/libris-v1.json."""

from pathlib import Path
from typing import Annotated

import respx
import test_api_v1
from fastapi import APIRouter, Depends, FastAPI
from test_api_delivery import LLM, REQUESTS, epub_fields
from test_api_v1 import bearer, new_token, payload, run_pending_job
from test_pipeline import mock_completion

from app.api import v1, v1_openapi
from app.api.tokens import Caller, require
from app.main import app

provider_id, owner, api = test_api_v1.provider_id, test_api_v1.owner, test_api_v1.api
COMMITTED = Path(__file__).resolve().parents[2] / "docs/openapi/libris-v1.json"
COMMON = {"401", "403", "429", "500"}


def spec() -> dict:
    return v1_openapi.build(app)


def resolve(document: dict, schema: dict) -> dict:
    while "$ref" in schema:
        schema = document["components"]["schemas"][schema["$ref"].removeprefix(v1_openapi.REF)]
    return schema


def conforms(document: dict, schema: dict, value, where: str = "$") -> list[str]:
    """A small JSON Schema check (type, enum, required, properties, items, anyOf): enough to prove the
    description does not lie about what Libris answers. Undocumented extra fields are allowed."""
    schema = resolve(document, schema)
    if "anyOf" in schema:
        if any(not conforms(document, option, value, where) for option in schema["anyOf"]):
            return []
        return [f"{where}: matches no anyOf option"]
    kinds = schema.get("type")
    kinds = [kinds] if isinstance(kinds, str) else kinds
    names = {
        "object": dict, "array": list, "string": str, "boolean": bool, "null": type(None),
        "integer": int, "number": (int, float),
    }  # fmt: skip
    if kinds and not any(
        isinstance(value, names[kind]) and not (kind in {"integer", "number"} and isinstance(value, bool))
        for kind in kinds
    ):
        return [f"{where}: {type(value).__name__} is not {kinds}"]
    if "enum" in schema and value not in schema["enum"]:
        return [f"{where}: {value!r} not in {schema['enum']}"]
    problems = []
    if isinstance(value, dict):
        for key in schema.get("required", []):
            if key not in value:
                problems.append(f"{where}: missing {key}")
        for key, child in (schema.get("properties") or {}).items():
            if key in value:
                problems += conforms(document, child, value[key], f"{where}.{key}")
    if isinstance(value, list) and "items" in schema:
        for index, item in enumerate(value):
            problems += conforms(document, schema["items"], item, f"{where}[{index}]")
    return problems


def answer_schema(
    document: dict, path: str, method: str, status: str, media: str = "application/json"
) -> dict:
    return document["paths"][path][method]["responses"][status]["content"][media]["schema"]


def test_the_committed_file_is_up_to_date():
    assert COMMITTED.read_text(encoding="utf-8") == v1_openapi.document_text(app), (
        "docs/openapi/libris-v1.json does not match the code of /api/v1: "
        "run `python3 scripts/export_openapi.py` and commit the file."
    )


def test_every_v1_operation_and_nothing_else_is_described():
    document = spec()
    routes = v1_openapi.endpoints(app)
    described = {(method, path) for path, item in document["paths"].items() for method in item}
    assert described == set(routes) and len(described) >= 7
    assert all(path.startswith("/api/v1/") for path in document["paths"])
    tags = {tag["name"] for tag in document["tags"]}
    ids = []
    for path, item in document["paths"].items():
        for method, operation in item.items():
            where = f"{method.upper()} {path}"
            ids.append(operation["operationId"])
            assert operation["summary"] and set(operation["tags"]) <= tags, where
            assert operation["security"] == [{"bearerToken": []}] and operation["x-libris-scopes"], where
            assert all(f"`{scope}`" in operation["description"] for scope in operation["x-libris-scopes"]), (
                where
            )
            assert COMMON <= set(operation["responses"]), where
            assert any(code.startswith("2") for code in operation["responses"]), where
            for code, response in operation["responses"].items():
                if code[0] in "45":
                    assert answer_schema(document, path, method, code) == {
                        "$ref": "#/components/schemas/Error"
                    }
            # The token is the security scheme, never a documented parameter.
            assert all(p["name"].casefold() != "authorization" for p in operation.get("parameters", [])), (
                where
            )
    assert len(ids) == len(set(ids))
    # The session API of the interface, and its schemas, stay out.
    assert not any(name.startswith(("HTTPValidation", "Body_")) for name in document["components"]["schemas"])
    assert document["paths"]["/api/v1/translation-requests"]["post"]["x-libris-scopes"] == [
        "content:write", "pipeline:start",
    ]  # fmt: skip


def test_every_reference_resolves_and_every_schema_is_used():
    document = spec()
    found: set[str] = set()
    v1_openapi.references(document, found)
    schemas = document["components"]["schemas"]
    assert found <= set(schemas), found - set(schemas)
    assert set(schemas) <= found, set(schemas) - found
    # Examples follow their own schemas.
    request = schemas["TranslationPayload"]
    assert not conforms(document, request, request["example"])
    status = document["paths"]["/api/v1/translation-requests/{request_id}"]["get"]["responses"]["200"]
    assert not conforms(document, status["content"]["application/json"]["schema"],
                        status["content"]["application/json"]["example"])  # fmt: skip


def test_a_new_v1_route_is_described_with_no_other_change():
    """What another feature gets for free: tag, summary, scope, errors, typed parameters."""
    extra = APIRouter(prefix="/api/v1")

    @extra.get("/widget-reports/{report_id}")
    def widget_report(
        report_id: str, caller: Annotated[Caller, Depends(require("results:read"))], days: int = 7
    ):
        """Get a widget report.

        Everything about one report."""

    trial = FastAPI()
    trial.include_router(v1.router)
    trial.include_router(extra)
    operation = v1_openapi.build(trial)["paths"]["/api/v1/widget-reports/{report_id}"]["get"]
    assert operation["operationId"] == "widgetReport" and operation["tags"] == ["Widget reports"]
    assert operation["summary"] == "Get a widget report"
    assert operation["description"].startswith("Everything about one report.")
    assert operation["x-libris-scopes"] == ["results:read"] and "`results:read`" in operation["description"]
    assert COMMON | {"422"} <= set(operation["responses"])
    assert [p["name"] for p in operation["parameters"]] == ["report_id", "days"]


@respx.mock
async def test_the_description_matches_what_libris_answers(owner, api, provider_id, book_bytes):
    respx.post(LLM).mock(side_effect=mock_completion)
    document = spec()
    secret = new_token(owner)
    base = "/api/v1/translation-requests"

    created = api.post(REQUESTS, headers=bearer(secret), json=payload(provider_id, pipeline={"start": False}))
    assert created.status_code == 202, created.text
    assert not conforms(document, answer_schema(document, base, "post", "202"), created.json())
    status = api.get(created.json()["status_url"], headers=bearer(secret)).json()
    assert not conforms(document, answer_schema(document, base + "/{request_id}", "get", "200"), status)

    epub = api.post(REQUESTS, headers=bearer(secret), data=epub_fields(provider_id),
                    files={"file": ("The Silver Tower.epub", book_bytes, "application/epub+zip")})  # fmt: skip
    assert epub.status_code == 202, epub.text
    assert (await run_pending_job()).status == "completed"
    finished = api.get(epub.json()["status_url"] + "?wait=5", headers=bearer(secret)).json()
    assert finished["status"] == "completed" and finished["report"] and finished["result"]
    assert not conforms(document, answer_schema(document, base + "/{request_id}", "get", "200"), finished)
    result = api.get(epub.json()["result_url"] + "?format=json", headers=bearer(secret))
    schema = answer_schema(document, base + "/{request_id}/result", "get", "200")
    assert not conforms(document, schema, result.json())
    assert set(document["paths"][base + "/{request_id}/result"]["get"]["responses"]["200"]["content"]) == {
        "application/epub+zip", "application/json", "text/plain", "application/zip",
    }  # fmt: skip

    for path, url in (("/api/v1/series", "/api/v1/series"), ("/api/v1/providers", "/api/v1/providers")):
        listed = api.get(url, headers=bearer(secret)).json()
        assert listed and not conforms(document, answer_schema(document, path, "get", "200"), listed)
    series_id = api.get("/api/v1/series", headers=bearer(secret)).json()[0]["id"]
    detail = api.get(f"/api/v1/series/{series_id}", headers=bearer(secret)).json()
    assert detail["volume_list"]
    assert not conforms(document, answer_schema(document, "/api/v1/series/{series_id}", "get", "200"), detail)

    missing = api.get(base + "/unknown", headers=bearer(secret))
    assert missing.status_code == 404
    assert not conforms(
        document, answer_schema(document, base + "/{request_id}", "get", "404"), missing.json()
    )
    invalid = api.post(REQUESTS, headers=bearer(secret), json={"chapters": []})
    assert invalid.status_code == 422
    assert not conforms(document, answer_schema(document, base, "post", "422"), invalid.json())
    codes = document["paths"][base]["post"]["responses"]["422"]["description"]
    assert f"`{invalid.json()['detail']['code']}`" in codes
