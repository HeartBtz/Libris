"""Global autopilot and webhook settings saved from the administration override the environment."""

from fastapi.testclient import TestClient

from app.automation_settings import autopilot_config, webhook_config, webhook_secret
from app.config import settings
from app.db import SessionLocal
from app.engines.autopilot.providers import chain
from app.engines.delivery.webhooks import host_allowed, private_networks, require_signing
from app.jobs.launch import autopilot_default
from app.main import app
from app.models import ApiToken, AppSetting, Project, Provider, User
from app.security import password_hash

PASSWORD = "test-password-123456789"
SECRET = "s" * 40


def signed_in(client: TestClient, username: str = "tester") -> TestClient:
    assert client.post("/api/auth/login", json={"username": username, "password": PASSWORD}).status_code == 200
    return client


def autopilot_body(**change) -> dict:
    return {
        "enabled": True,
        "max_rounds": 3,
        "fallback_providers": [],
        "outage_max_retries": 5,
        "outage_max_wait_seconds": 3600,
        "glossary_min_confidence": 0.75,
        "identity_min_confidence": 0.8,
        "bible_min_coverage": 0.8,
        "stale_min_coverage": 0.5,
        **change,
    }


def test_settings_are_for_administrators_only(seeded):
    with SessionLocal() as db:
        db.add(User(username="reader", password_hash=password_hash(PASSWORD), admin=False))
        db.commit()
    with TestClient(app) as client:
        for path in ("/api/settings/autopilot", "/api/settings/webhooks"):
            assert client.get(path).status_code == 401
        signed_in(client, "reader")
        for path in ("/api/settings/autopilot", "/api/settings/webhooks"):
            assert client.get(path).status_code == 403
            assert client.delete(path).status_code == 403
        assert client.put("/api/settings/autopilot", json=autopilot_body()).status_code == 403
        assert client.put("/api/settings/webhooks", json={}).status_code == 403


def test_autopilot_defaults_come_from_the_environment_until_saved(seeded, monkeypatch):
    project_id, _, provider_id = seeded
    monkeypatch.setattr(settings(), "autopilot_max_rounds", 4)
    monkeypatch.setattr(settings(), "autopilot_fallback_providers", "Backup, missing")
    with SessionLocal() as db:
        backup = Provider(name="Backup", base_url="https://backup.test/v1", model="m")
        spare = Provider(name="Spare", base_url="https://spare.test/v1", model="m")
        db.add_all([backup, spare])
        db.commit()
        backup_id, spare_id = backup.id, spare.id
    with TestClient(app) as client:
        signed_in(client)
        view = client.get("/api/settings/autopilot").json()
        assert view["saved"] is False
        assert view["values"]["max_rounds"] == 4 and view["defaults"]["max_rounds"] == 4
        assert view["values"]["fallback_providers"] == ["Backup", "missing"]
        assert view["fallback_provider_ids"] == [backup_id]
        with SessionLocal() as db:
            assert chain(db, db.get(Project, project_id)) == [provider_id, backup_id]

        saved = client.put(
            "/api/settings/autopilot",
            json=autopilot_body(enabled=False, max_rounds=6, fallback_providers=["Spare", backup_id]),
        )
        assert saved.status_code == 200, saved.text
        view = saved.json()
        assert view["saved"] is True and view["defaults"]["max_rounds"] == 4
        # Names are stored as ids: renaming a provider does not break the chain.
        assert view["values"]["fallback_providers"] == [spare_id, backup_id]
        assert view["fallback_provider_ids"] == [spare_id, backup_id]
        assert autopilot_config()["max_rounds"] == 6
        with SessionLocal() as db:
            project = db.get(Project, project_id)
            assert chain(db, project) == [provider_id, spare_id, backup_id]
            # The global switch is the default of volumes without their own choice…
            assert autopilot_default(project) is False
            project.config = {**project.config, "autopilot": True}
            db.commit()
            # …and a volume's own choice still wins.
            assert autopilot_default(db.get(Project, project_id)) is True
        report = client.get(f"/api/projects/{project_id}/autopilot").json()
        assert report["settings"]["max_rounds"] == 6

        reset = client.delete("/api/settings/autopilot").json()
        assert reset["saved"] is False and reset["values"]["max_rounds"] == 4
        assert autopilot_config()["fallback_providers"] == ["Backup", "missing"]


def test_autopilot_settings_are_validated_in_french_and_english(seeded):
    with TestClient(app) as client:
        signed_in(client)
        unknown = client.put("/api/settings/autopilot", json=autopilot_body(fallback_providers=["nobody"]))
        assert unknown.status_code == 422 and unknown.json()["detail"] == "Fournisseur de secours inconnu."
        english = client.put(
            "/api/settings/autopilot",
            json=autopilot_body(fallback_providers=["nobody"]),
            headers={"Accept-Language": "en"},
        )
        assert english.json()["detail"] == "Unknown fallback provider."
        for change in ({"max_rounds": 0}, {"max_rounds": 11}, {"glossary_min_confidence": 1.5}):
            assert client.put("/api/settings/autopilot", json=autopilot_body(**change)).status_code == 422
        with SessionLocal() as db:
            assert db.get(AppSetting, "autopilot") is None


def test_webhook_settings_override_the_environment_and_keep_the_secret_write_only(seeded, monkeypatch):
    monkeypatch.setattr(settings(), "api_webhook_hosts", "hooks.example.org")
    monkeypatch.setattr(settings(), "api_webhook_secret", "")
    with SessionLocal() as db:
        token = ApiToken(name="client", owner_id=seeded[1], prefix="lbr_test", token_hash="x" * 64, scopes=[])
        db.add(token)
        db.commit()
    with TestClient(app) as client:
        signed_in(client)
        view = client.get("/api/settings/webhooks").json()
        assert view["values"]["hosts"] == ["hooks.example.org"] and view["saved"] is False
        assert view["secret"] == {"configured": False, "source": "none"}
        assert host_allowed("hooks.example.org") and not host_allowed("ci.example.net")

        body = {
            "hosts": ["*.Example.NET.", "ci.example.net", "10.0.0.5"],
            "private_networks": ["10.0.0.0/8", "fd00::/8"],
            "max_attempts": 3,
            "timeout_seconds": 5,
            "secret": SECRET,
        }
        answer = client.put("/api/settings/webhooks", json=body)
        assert answer.status_code == 200, answer.text
        assert SECRET not in answer.text and SECRET not in client.get("/api/settings/webhooks").text
        view = answer.json()
        assert view["values"]["hosts"] == ["*.example.net", "ci.example.net", "10.0.0.5"]
        assert view["secret"] == {"configured": True, "source": "saved"}
        assert host_allowed("api.example.net") and not host_allowed("hooks.example.org")
        assert [str(network) for network in private_networks()] == ["10.0.0.0/8", "fd00::/8"]
        assert webhook_config()["max_attempts"] == 3 and webhook_secret() == SECRET
        with SessionLocal() as db:
            assert SECRET not in str(db.get(AppSetting, "webhooks").value)
            require_signing(db.get(ApiToken, token.id))  # the saved secret signs tokens without their own

        # Saving without a secret keeps it; clear_secret forgets it.
        kept = client.put("/api/settings/webhooks", json={**body, "secret": None, "max_attempts": 4}).json()
        assert kept["secret"]["source"] == "saved" and webhook_secret() == SECRET
        cleared = client.put("/api/settings/webhooks", json={**body, "secret": None, "clear_secret": True}).json()
        assert cleared["secret"] == {"configured": False, "source": "none"} and webhook_secret() == ""

        monkeypatch.setattr(settings(), "api_webhook_secret", "e" * 40)
        reset = client.delete("/api/settings/webhooks").json()
        assert reset["saved"] is False and reset["values"]["hosts"] == ["hooks.example.org"]
        assert reset["secret"] == {"configured": True, "source": "environment"}


def test_webhook_settings_are_validated_in_french_and_english(seeded):
    with TestClient(app) as client:
        signed_in(client)
        cases = [
            ({"hosts": ["https://hooks.example.org/path"]}, "Hôte de webhook invalide : https://hooks.example.org/path",
             "Invalid webhook host: https://hooks.example.org/path"),
            ({"hosts": ["*"]}, "Hôte de webhook invalide : *", "Invalid webhook host: *"),
            ({"private_networks": ["10.0.0.0/33"]}, "Réseau privé invalide (notation CIDR attendue) : 10.0.0.0/33",
             "Invalid private network (CIDR notation expected): 10.0.0.0/33"),
            ({"secret": "short"}, "Le secret des webhooks doit contenir au moins 32 caractères.",
             "The webhook secret must contain at least 32 characters."),
        ]  # fmt: skip
        for body, french, english in cases:
            answer = client.put("/api/settings/webhooks", json=body)
            assert answer.status_code == 422 and answer.json()["detail"] == french
            answer = client.put("/api/settings/webhooks", json=body, headers={"Accept-Language": "en"})
            assert answer.json()["detail"] == english
        assert client.put("/api/settings/webhooks", json={"max_attempts": 0}).status_code == 422
        assert client.put("/api/settings/webhooks", json={"timeout_seconds": 61}).status_code == 422
        with SessionLocal() as db:
            assert db.get(AppSetting, "webhooks") is None
