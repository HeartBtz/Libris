import respx
from fastapi.testclient import TestClient

from app.main import app
from app.providers.search import search_config


@respx.mock
def test_persistent_search_settings_and_connection_test(seeded):
    with TestClient(app) as client:
        assert client.get("/api/settings/searxng").status_code == 401
        client.post("/api/auth/login", json={"username": "tester", "password": "test-password-123456789"})
        value = {"base_url": "https://search.test/", "enabled": True}
        saved = client.put("/api/settings/searxng", json=value)
        assert saved.status_code == 200 and search_config()["base_url"] == "https://search.test"
        route = respx.get("https://search.test/search").respond(200, json={"results": []})
        assert client.post("/api/settings/searxng/test", json=value).json()["ok"]
        assert route.call_count == 1
        value["enabled"] = False
        assert client.put("/api/settings/searxng", json=value).status_code == 200
        assert not search_config()["enabled"]
        assert client.get("/api/settings/searxng").json()["base_url"] == "https://search.test"
        assert client.put("/api/settings/searxng", json={"base_url": "https://user:password@search.test", "enabled": True}).status_code == 422
