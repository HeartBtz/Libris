"""Opt-in OpenViking cleanup: deleted volumes and series, orphans, and what must never be removed."""

import asyncio
import time
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest
import respx
from epubs import epub_bytes
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.config import settings
from app.db import SessionLocal
from app.engines.memory.cleanup import cleanup_enabled, run_cleanups, scan_orphans
from app.main import app
from app.models import AppSetting, OpenVikingCleanup, Outbox, Project, Series, User
from app.providers.openviking import OpenVikingClient, cleanup_scope, project_uri, series_uri
from app.security import password_hash

ROOT = "viking://resources/epub-tests"
BASE = "https://memory.test"
PASSWORD = "test-password-123456789"
OTHER_OWNER = "0f0f0f0f-0000-4000-8000-000000000001"
GONE = "0f0f0f0f-0000-4000-8000-00000000dead"


class FakeOpenViking:
    """An in-memory OpenViking file tree answering ls and recursive rm like the real HTTP API."""

    def __init__(self):
        self.files: set[str] = set()
        self.removed: list[str] = []
        self.down = False

    def add(self, *prefixes: str):
        for prefix in prefixes:
            self.files |= {f"{prefix}/events/a.json", f"{prefix}/book.md"}

    def under(self, uri: str) -> list[str]:
        return sorted(f for f in self.files if f.startswith(uri.rstrip("/") + "/"))

    def handle(self, request: httpx.Request) -> httpx.Response:
        if self.down:
            raise httpx.ConnectError("offline")
        query = {k: v[0] for k, v in parse_qs(urlsplit(str(request.url)).query).items()}
        uri = query["uri"].rstrip("/")
        inside = self.under(uri)
        if not inside:
            return httpx.Response(404, json={"status": "error", "error": {"code": "NOT_FOUND"}})
        if request.method == "DELETE":
            assert query["recursive"] == "true"
            self.files -= set(inside)
            self.removed.append(uri)
            return httpx.Response(
                200, json={"status": "ok", "result": {"uri": uri, "estimated_deleted_count": len(inside)}}
            )
        entries: dict[str, bool] = {}
        for path in inside:
            parts = path[len(uri) + 1 :].split("/")
            depth = len(parts) if query.get("recursive") == "true" else 1
            for index in range(min(depth, len(parts))):
                child = uri + "/" + "/".join(parts[: index + 1])
                entries[child] = index < len(parts) - 1
        result = [
            {"uri": u + ("/" if d else ""), "isDir": d, "name": u.rsplit("/", 1)[1]}
            for u, d in entries.items()
        ]
        return httpx.Response(200, json={"status": "ok", "result": result})

    def mount(self):
        respx.get(f"{BASE}/api/v1/fs/ls").mock(side_effect=self.handle)
        respx.delete(f"{BASE}/api/v1/fs").mock(side_effect=self.handle)


def configure(enabled: bool | None = True):
    with SessionLocal() as db:
        db.add(AppSetting(key="openviking", value={"base_url": BASE, "root_uri": ROOT}))
        if enabled is not None:
            db.add(AppSetting(key="openviking_cleanup", value={"enabled": enabled}))
        db.commit()


def login() -> TestClient:
    client = TestClient(app)
    assert (
        client.post("/api/auth/login", json={"username": "tester", "password": PASSWORD}).status_code == 200
    )
    return client


def second_volume(owner_id: str, series: str = "") -> str:
    from app.api.projects import import_book

    with SessionLocal() as db:
        tag = f"Volume {series or 'standalone'}"
        project = import_book(db, owner_id, epub_bytes(f"<h1>One</h1><p>Synthetic text of {tag}.</p>", tag))
        project.series_name = series
        db.commit()
        return project.id


def uri_of(pid: str) -> str:
    with SessionLocal() as db:
        return project_uri(db.get(Project, pid))


def due_now():
    with SessionLocal() as db:
        for row in db.scalars(select(OpenVikingCleanup)):
            row.next_attempt = 0
        db.commit()


def run():
    due_now()
    return asyncio.run(run_cleanups())


def only_row() -> OpenVikingCleanup:
    with SessionLocal() as db:
        rows = list(db.scalars(select(OpenVikingCleanup)))
        assert len(rows) == 1
        return rows[0]


def test_scope_accepts_only_whole_item_directories_under_the_root():
    owner, series, pid = OTHER_OWNER, GONE, "0f0f0f0f-0000-4000-8000-000000000002"
    assert cleanup_scope(f"{ROOT}/{owner}/series/{series}", ROOT) == ("series", (owner, series))
    assert cleanup_scope(f"{ROOT}/{owner}/series/{series}/volumes/{pid}", ROOT)[0] == "series_volume"
    assert cleanup_scope(f"{ROOT}/{owner}/standalone/{pid}", ROOT)[0] == "standalone"
    assert cleanup_scope(f"{ROOT}/{owner}/{pid}", ROOT)[0] == "legacy"
    for unsafe in (
        ROOT,
        f"{ROOT}/",
        f"{ROOT}/{owner}",
        f"{ROOT}/{owner}/series",
        f"{ROOT}/{owner}/standalone",
        f"{ROOT}/{owner}/standalone/{pid}/events",
        f"{ROOT}/{owner}/standalone/{pid}/",
        f"{ROOT}/{owner}/standalone/notes",
        f"{ROOT}/{owner}/standalone/../{pid}",
        f"{ROOT}/{owner}/series/{series}/volumes",
        f"viking://resources/another-root/{owner}/standalone/{pid}",
        f"viking://resources/epub-tests-2/{owner}/standalone/{pid}",
        "viking://resources",
    ):
        assert cleanup_scope(unsafe, ROOT) is None, unsafe
    assert cleanup_scope(f"{ROOT}/{owner}/{pid}", "viking://resources") is None


@respx.mock
async def test_the_client_refuses_to_remove_anything_outside_an_item_directory():
    route = respx.delete(f"{BASE}/api/v1/fs").respond(200, json={"status": "ok", "result": {}})
    async with OpenVikingClient({"base_url": BASE, "root_uri": ROOT, "timeout": 5}) as client:
        for unsafe in (ROOT, f"{ROOT}/{OTHER_OWNER}", f"{ROOT}/{OTHER_OWNER}/standalone"):
            with pytest.raises(ValueError):
                await client.remove_tree(unsafe)
    assert not route.called


@respx.mock
def test_off_by_default_deleting_leaves_openviking_untouched(seeded):
    pid, owner, _ = seeded
    configure(enabled=None)
    fake = FakeOpenViking()
    fake.add(uri_of(pid))
    fake.mount()
    assert cleanup_enabled() is False
    with login() as client:
        answer = client.delete(f"/api/projects/{pid}")
    assert answer.status_code == 200
    assert answer.json()["openviking_cleanup_id"] is None
    assert "se gère séparément" in answer.json()["message"]
    assert run() == 0
    assert fake.under(ROOT) and not fake.removed


def test_setting_follows_the_environment_until_saved(seeded, monkeypatch):
    configure(enabled=None)
    monkeypatch.setattr(settings(), "openviking_cleanup_on_delete", True)
    with login() as client:
        view = client.get("/api/settings/memory/cleanup").json()
        assert view == {"enabled": True, "default": True, "saved": False, "configured": True}
        view = client.put("/api/settings/memory/cleanup", json={"enabled": False}).json()
        assert (view["enabled"], view["saved"]) == (False, True)
        assert cleanup_enabled() is False
        view = client.delete("/api/settings/memory/cleanup").json()
        assert (view["enabled"], view["saved"]) == (True, False)


def test_only_administrators_manage_the_cleanup(seeded):
    configure()
    with SessionLocal() as db:
        db.add(User(username="reader", password_hash=password_hash("reader-password-12345"), admin=False))
        db.commit()
    with TestClient(app) as client:
        client.post("/api/auth/login", json={"username": "reader", "password": "reader-password-12345"})
        assert client.get("/api/settings/memory/cleanup").status_code == 403
        assert client.put("/api/settings/memory/cleanup", json={"enabled": False}).status_code == 403
        assert client.get("/api/settings/memory/cleanups").status_code == 403
        assert client.post("/api/settings/memory/orphans/scan").status_code == 403
        assert client.post("/api/settings/memory/orphans/clean", json={"uris": [ROOT]}).status_code == 403


@respx.mock
def test_deleting_a_volume_removes_only_its_documents_later_and_logs_them(seeded):
    pid, owner, _ = seeded
    configure()
    sibling = second_volume(owner)
    target, kept = uri_of(pid), uri_of(sibling)
    foreign = f"{ROOT}/{OTHER_OWNER}/standalone/{pid}"
    fake = FakeOpenViking()
    fake.add(target, kept, foreign, f"viking://resources/other-root/{owner}/standalone/{pid}")
    fake.mount()
    fake.down = True  # the SQL deletion never waits for OpenViking
    with login() as client:
        answer = client.delete(f"/api/projects/{pid}")
    assert answer.status_code == 200
    assert "effacés par le worker" in answer.json()["message"]
    with SessionLocal() as db:
        assert db.get(Project, pid) is None
    row = only_row()
    assert (row.kind, row.status, row.target_id, row.owner_id) == ("volume", "pending", pid, owner)
    assert row.next_attempt > time.time()  # a grace delay lets in-flight writes land first
    assert target in row.uris and all(cleanup_scope(u, ROOT) for u in row.uris)

    assert run() == 1  # OpenViking is down: retried later, with the reason
    row = only_row()
    assert (row.status, row.attempts) == ("pending", 1)
    assert "ConnectError" in row.error and row.next_attempt > time.time()

    fake.down = False
    assert run() == 1
    row = only_row()
    assert (row.status, row.error) == ("done", "")
    assert fake.removed == [target]
    assert not fake.under(target) and fake.under(kept) and fake.under(foreign)
    assert fake.under(f"viking://resources/other-root/{owner}/standalone/{pid}")
    removed = next(entry for entry in row.results if entry["uri"] == target)
    assert removed["status"] == "removed" and removed["reason"] == "volume_deleted"
    assert removed["document_count"] == 2 and f"{target}/book.md" in removed["documents"]
    assert removed["estimated_deleted_count"] == 2
    assert {entry["status"] for entry in row.results if entry["uri"] != target} == {"absent"}
    assert run() == 0  # finished rows are never run again

    with login() as client:
        log = client.get("/api/settings/memory/cleanups").json()
    assert log[0]["id"] == row.id and log[0]["status"] == "done"


@respx.mock
def test_a_volume_moved_between_spaces_is_removed_from_each_place(seeded):
    pid, owner, _ = seeded
    configure()
    with SessionLocal() as db:
        project = db.get(Project, pid)
        old_place = project_uri(project)  # standalone
        db.add(
            Outbox(
                project_id=pid,
                event_key="moved",
                session_name="s",
                payload={},
                status="sent",
                uri=f"{old_place}/events/x.json",
            )
        )
        project.series_name = "Saga"
        db.commit()
        new_place = project_uri(db.get(Project, pid))
    assert "/series/" in new_place
    fake = FakeOpenViking()
    fake.add(old_place, new_place, f"{ROOT}/{owner}/{pid}")
    fake.mount()
    with login() as client:
        assert client.delete(f"/api/projects/{pid}").status_code == 200
    run()
    assert sorted(fake.removed) == sorted([old_place, new_place, f"{ROOT}/{owner}/{pid}"])
    assert not fake.under(ROOT)


@respx.mock
def test_deleting_a_series_removes_its_space_and_nothing_else(seeded):
    pid, owner, _ = seeded
    configure()
    other = second_volume(owner, series="Other saga")
    with SessionLocal() as db:
        db.get(Project, pid).series_name = "Saga"
        db.commit()
        series_id = db.get(Project, pid).series_id
        space = series_uri(owner, series_id)
    volume, kept = uri_of(pid), uri_of(other)
    fake = FakeOpenViking()
    fake.add(volume, kept, f"{space}/volumes/{GONE}")  # a volume deleted while the cleanup was off
    fake.mount()
    with login() as client:
        assert client.delete(f"/api/series/{series_id}").status_code == 409  # still has a volume
        assert client.delete(f"/api/projects/{pid}").status_code == 200
        answer = client.delete(f"/api/series/{series_id}")
    assert answer.status_code == 200 and answer.json()["openviking_cleanup_id"]
    assert run() == 2
    assert fake.removed == [volume, space]
    assert not fake.under(space) and fake.under(kept)
    with SessionLocal() as db:
        rows = {row.kind: row for row in db.scalars(select(OpenVikingCleanup))}
    assert rows["series"].status == "done" and rows["series"].uris == [space]


@respx.mock
def test_a_resumed_cleanup_skips_what_is_done_and_keeps_what_lives_again(seeded):
    pid, owner, _ = seeded
    configure()
    gone_a = f"{ROOT}/{owner}/standalone/{GONE}"
    gone_b = f"{ROOT}/{owner}/standalone/0f0f0f0f-0000-4000-8000-00000000beef"
    living = uri_of(pid)
    fake = FakeOpenViking()
    fake.add(gone_a, gone_b, living)
    fake.mount()
    with SessionLocal() as db:
        db.add(
            OpenVikingCleanup(
                kind="orphans",
                root_uri=ROOT,
                uris=[gone_a, gone_b, living],
                results=[{"uri": gone_a, "status": "removed", "reason": "volume_deleted"}],
                status="running",
                lease_until=time.time() - 1,  # the worker that held it stopped
            )
        )
        db.commit()
    run()
    row = only_row()
    assert row.status == "done"
    assert fake.removed == [gone_b]  # gone_a was handled before the restart
    assert fake.under(gone_a)
    assert fake.under(living)
    assert row.results[-1] == {"uri": living, "status": "kept", "reason": "in_use"}


@respx.mock
def test_a_leased_cleanup_is_not_taken_twice(seeded):
    configure()
    FakeOpenViking().mount()
    with SessionLocal() as db:
        db.add(
            OpenVikingCleanup(
                kind="orphans",
                root_uri=ROOT,
                uris=[],
                results=[],
                status="running",
                lease_until=time.time() + 300,
            )
        )
        db.commit()
    assert asyncio.run(run_cleanups()) == 0


@respx.mock
def test_orphans_dry_run_lists_then_cleans_only_what_sql_disowns(seeded):
    pid, owner, _ = seeded
    configure(enabled=False)  # the manual action does not depend on the automatic switch
    moved = second_volume(owner, series="Saga")
    with SessionLocal() as db:
        series_id = db.get(Project, moved).series_id
    living, living_series_volume = uri_of(pid), uri_of(moved)
    orphans = {
        f"{ROOT}/{owner}/standalone/{GONE}": "volume_deleted",
        f"{ROOT}/{owner}/standalone/{moved}": "volume_moved",
        f"{ROOT}/{owner}/{pid}": "legacy_layout",
        f"{ROOT}/{owner}/series/{GONE}": "series_deleted",
        f"{ROOT}/{owner}/series/{series_id}/volumes/{GONE}": "volume_deleted",
        f"{ROOT}/{OTHER_OWNER}/standalone/{GONE}": "volume_deleted",
    }
    untouched = [
        living,
        living_series_volume,
        f"{ROOT}/{owner}/notes",
        f"{ROOT}/shared/standalone/{GONE}",
        f"viking://resources/other-root/{owner}/standalone/{GONE}",
    ]
    fake = FakeOpenViking()
    fake.add(*orphans, *untouched, f"{ROOT}/{owner}/series/{GONE}/volumes/{GONE}")
    fake.mount()

    report = asyncio.run(scan_orphans())
    assert {item["uri"]: item["reason"] for item in report["orphans"]} == orphans
    assert report["total"] == len(orphans) and not fake.removed  # a dry run removes nothing

    with login() as client:
        scanned = client.post("/api/settings/memory/orphans/scan").json()
        assert len(scanned["orphans"]) == len(orphans) and not fake.removed
        refused = client.post(
            "/api/settings/memory/orphans/clean", json={"uris": [living, f"{ROOT}/{owner}"]}
        )
        assert refused.status_code == 409
        answer = client.post(
            "/api/settings/memory/orphans/clean", json={"uris": [*orphans, living, f"{ROOT}/{owner}"]}
        ).json()
    assert sorted(answer["refused"]) == sorted([living, f"{ROOT}/{owner}"])
    assert sorted(answer["cleanup"]["uris"]) == sorted(orphans)
    run()
    assert sorted(fake.removed) == sorted(orphans)
    for uri in untouched:
        assert fake.under(uri), uri
    assert asyncio.run(scan_orphans())["orphans"] == []


@respx.mock
def test_scan_errors_are_reported_without_removing_anything(seeded):
    with login() as client:
        assert client.post("/api/settings/memory/orphans/scan").status_code == 409  # not configured
    configure()
    respx.get(f"{BASE}/api/v1/fs/ls").respond(403, json={"status": "error"})
    route = respx.delete(f"{BASE}/api/v1/fs")
    with login() as client:
        answer = client.post("/api/settings/memory/orphans/scan", headers={"Accept-Language": "en"})
    assert answer.status_code == 502
    assert (
        answer.json()["detail"] == "OpenViking could not list its documents (HTTP 403). Nothing was deleted."
    )
    assert not route.called


@respx.mock
def test_a_changed_root_holds_the_cleanup_instead_of_guessing(seeded):
    pid, owner, _ = seeded
    configure()
    fake = FakeOpenViking()
    fake.add(uri_of(pid))
    fake.mount()
    with login() as client:
        client.delete(f"/api/projects/{pid}")
    with SessionLocal() as db:
        db.get(AppSetting, "openviking").value = {
            "base_url": BASE,
            "root_uri": "viking://resources/elsewhere",
        }
        db.commit()
    run()
    row = only_row()
    assert row.status == "pending" and "racine" in row.error
    assert not fake.removed
    with login() as client:
        assert client.post(f"/api/settings/memory/cleanups/{row.id}/retry").json()["next_attempt"] == 0
        assert client.post("/api/settings/memory/cleanups/unknown/retry").status_code == 404


def test_series_rows_survive_the_deleted_series(seeded):
    """The log never refers to deleted rows through a foreign key."""
    pid, owner, _ = seeded
    configure()
    with SessionLocal() as db:
        series = Series(owner_id=owner, name="S", normalized_name="s", kind="books", authors=[], bible={})
        db.add(series)
        db.commit()
        sid = series.id
    with login() as client:
        assert client.delete(f"/api/series/{sid}").status_code == 200
    assert only_row().target_id == sid
