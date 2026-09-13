import json

import httpx
import pytest
import respx

from app.db import SessionLocal
from app.engines.context.config import memory_config
from app.engines.context.providers import HybridContextProvider, OpenVikingContextProvider
from app.models import AppSetting, Outbox, Project
from app.providers.openviking import OpenVikingClient, event_uri, project_uri, validate_root


def configured(pid):
    with SessionLocal() as db:
        db.add(
            AppSetting(
                key="openviking",
                value={"base_url": "https://memory.test", "root_uri": "viking://resources/epub-tests"},
            )
        )
        project = db.get(Project, pid)
        project.context_backend = "hybrid"
        before = Outbox(
            project_id=pid,
            event_key="before",
            session_name="chapter-1",
            payload={"position": 1, "content": {"fact": "Alice holds the pendant"}},
            status="sent",
        )
        future = Outbox(
            project_id=pid,
            event_key="future",
            session_name="chapter-2",
            payload={"position": 50, "content": {"fact": "Bob is her brother"}},
            status="sent",
        )
        db.add_all([before, future])
        db.commit()
        return project, before, future


@respx.mock
async def test_uri_temporal_isolation_and_lazy_read(seeded):
    project, before, future = configured(seeded[0])
    good, later = event_uri(project, before), event_uri(project, future)
    foreign = "viking://resources/another-user/private.json"
    response = {
        "status": "ok",
        "result": {
            "resources": [
                {"uri": good, "score": 0.9, "abstract": "Pendant"},
                {"uri": later, "score": 0.99},
                {"uri": foreign, "score": 0.99},
                {"uri": project_uri(project) + "/.overview.md", "score": 0.99},
            ]
        },
    }
    search = respx.post("https://memory.test/api/v1/search/find").respond(200, json=response)
    read = respx.get("https://memory.test/api/v1/content/read").respond(
        200, json={"status": "ok", "result": json.dumps(before.payload)}
    )
    provider = OpenVikingContextProvider()
    result = await provider.retrieve(project, "Alice pendant", 10)
    assert len(result) == 1 and result[0].origin == good
    assert read.call_count == 1
    assert read.calls[0].request.url.params["uri"] == good
    assert json.loads(search.calls[0].request.content)["target_uri"] == project_uri(project) + "/events"
    assert len(provider.trace["rejected"]) == 3


@respx.mock
async def test_deep_search_always_uses_project_scoped_list_mode(seeded):
    project, _, _ = configured(seeded[0])
    route = respx.post("https://memory.test/api/v1/search/search").respond(
        200, json={"status": "ok", "result": {"resources": []}}
    )
    await OpenVikingContextProvider().retrieve(project, "pendant", 10, deep=True)
    payload = json.loads(route.calls[0].request.content)
    assert payload["mode"] == "list" and payload["target_uri"] == project_uri(project) + "/events"


@respx.mock
async def test_ingestion_idempotent_event_uri(seeded):
    project, before, _ = configured(seeded[0])
    write = respx.post("https://memory.test/api/v1/content/write").respond(
        200, json={"status": "ok", "result": {}}
    )
    provider = OpenVikingContextProvider()
    await provider.ingest(before)
    await provider.ingest(before)
    payloads = [json.loads(call.request.content) for call in write.calls]
    assert payloads[0] == payloads[1]
    assert payloads[0]["mode"] == "replace" and payloads[0]["wait"] is False
    assert payloads[0]["uri"].startswith(project_uri(project) + "/")


@respx.mock
async def test_openviking_outage_falls_back_to_internal(seeded):
    project, _, _ = configured(seeded[0])
    respx.post("https://memory.test/api/v1/search/find").mock(side_effect=httpx.ConnectError("offline"))
    provider = HybridContextProvider()
    await provider.retrieve(project, "Alice", 10)
    assert provider.trace["effective_backend"] == "internal"
    assert "OpenViking" in provider.trace["error"]


@pytest.mark.parametrize(
    "uri",
    [
        "viking://",
        "viking://resources",
        "viking://resources/a/../b",
        "viking://resources/a/%2e%2e/b",
        "viking://resources/a?other",
    ],
)
def test_namespace_root_validation(uri):
    with pytest.raises(ValueError):
        validate_root(uri)


@respx.mock
async def test_older_openviking_explicit_creation(seeded):
    configured(seeded[0])
    route = respx.post("https://memory.test/api/v1/content/write").mock(
        side_effect=[
            httpx.Response(404, json={"status": "error"}),
            httpx.Response(200, json={"status": "ok", "result": {"semantic_status": "queued"}}),
        ]
    )
    async with OpenVikingClient(memory_config()) as client:
        await client.write("viking://resources/epub-tests/event.json", '{"text":"test"}')
    assert [json.loads(c.request.content)["mode"] for c in route.calls] == ["replace", "create"]
