import io
import json

from fastapi.testclient import TestClient
from sqlalchemy import select

from app.db import SessionLocal
from app.main import app
from app.models import Entity, Issue, Segment


def client_for():
    client = TestClient(app)
    client.__enter__()
    assert client.post(
        "/api/auth/login", json={"username": "tester", "password": "test-password-123456789"}
    ).status_code == 200
    return client


def test_restoring_a_retained_source_version_keeps_its_status(seeded):
    pid = seeded[0]
    client = client_for()
    segment = client.get(f"/api/projects/{pid}/segments").json()[0]
    sid = segment["id"]
    retained = client.post(f"/api/segments/{sid}/retain-source", json={"revision": segment["revision"]})
    assert retained.status_code == 200, retained.text
    with SessionLocal() as db:
        assert db.get(Segment, sid).status == "source_retained"
        assert db.scalar(select(Issue).where(Issue.segment_id == sid, Issue.code == "source_retained")).resolved is False
    # A human translation replaces the retained original: the alert is settled.
    current = client.get(f"/api/projects/{pid}/segments").json()[0]
    units = [{"id": unit["id"], "text": "Traduit."} for unit in current["units"]]
    edited = client.put(f"/api/segments/{sid}", json={"revision": current["revision"], "units": units, "validated": True})
    assert edited.status_code == 200, edited.text
    with SessionLocal() as db:
        segment_row = db.get(Segment, sid)
        assert (segment_row.status, segment_row.retained_source) == ("ok", False)
        assert db.scalar(select(Issue).where(Issue.segment_id == sid, Issue.code == "source_retained")).resolved is True
    # Restoring the "retained source" version brings the dedicated status back, even when asked as validated.
    versions = client.get(f"/api/segments/{sid}/versions").json()
    target = next(version for version in versions if version["origin"] == "source_retained")
    revision = client.get(f"/api/projects/{pid}/segments").json()[0]["revision"]
    restored = client.post(
        f"/api/segments/{sid}/versions/{target['id']}/restore", json={"revision": revision, "units": [], "validated": True}
    )
    assert restored.status_code == 200, restored.text
    with SessionLocal() as db:
        segment_row = db.get(Segment, sid)
        assert (segment_row.status, segment_row.retained_source, segment_row.validated) == ("source_retained", True, False)
    assert client.get(f"/api/projects/{pid}/completion").json()["retained"] == 1


def test_glossary_import_rejects_a_json_object_root(seeded):
    pid = seeded[0]
    client = client_for()
    wrapped = json.dumps({"terms": [{"source": "Silver Tower", "translation": "Tour d’argent"}]}).encode()
    refused = client.post(f"/api/projects/{pid}/glossary/import", files={"file": ("g.json", io.BytesIO(wrapped))})
    assert refused.status_code == 422 and "liste de termes" in refused.json()["detail"]
    listed = json.dumps([{"source": "Silver Tower", "translation": "Tour d’argent"}]).encode()
    accepted = client.post(f"/api/projects/{pid}/glossary/import", files={"file": ("g.json", io.BytesIO(listed))})
    assert accepted.status_code == 200 and accepted.json() == {"imported": 1, "skipped": 0}


def test_character_update_validates_names_and_alias_collisions(seeded):
    pid = seeded[0]
    with SessionLocal() as db:
        alice = Entity(project_id=pid, category="character", name="Alice", data={"canonical_name": "Alice", "aliases": []}, validated=True, identity_validated=True)
        bob = Entity(project_id=pid, category="character", name="Bob", data={"canonical_name": "Bob", "aliases": []}, validated=True, identity_validated=True)
        db.add_all([alice, bob])
        db.commit()
        bob_id = bob.id
    client = client_for()
    base = {"canonical_name": "Bob", "aliases": []}
    assert client.put(f"/api/projects/{pid}/characters/{bob_id}", json={**base, "aliases": ["Alice"]}).status_code == 409
    assert client.put(f"/api/projects/{pid}/characters/{bob_id}", json={**base, "canonical_name": "  "}).status_code == 422
    assert client.put(f"/api/projects/{pid}/characters/{bob_id}", json={**base, "canonical_name": "B" * 1000}).status_code == 422
    saved = client.put(f"/api/projects/{pid}/characters/{bob_id}", json={**base, "canonical_name": " Bobby ", "aliases": ["Rob"]})
    assert saved.status_code == 200 and saved.json()["name"] == "Bobby"
