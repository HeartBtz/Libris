"""Series follow-up: new chapters sent over time are appended, translated alone and delivered."""

import hashlib
import hmac
import io
import json
import time
import zipfile

import httpx
import respx
import test_api_delivery
import test_api_v1
from sqlalchemy import select
from test_api_delivery import LLM, PUBLIC, REQUESTS, epub_fields, txt
from test_api_v1 import bearer, new_token, payload, run_pending_job
from test_pipeline import mock_completion
from test_series_import import chapter, upload

from app.config import settings
from app.db import SessionLocal
from app.engines.delivery import chapter_events, webhooks
from app.engines.ingestion.payload import parse_payload
from app.engines.translation.pipeline import _consistency_samples
from app.jobs.follow_up import OPTION, follow_up_scope
from app.models import Chapter, Glossary, Job, Project, RequestLog, Segment, TranslationRequest, WebhookEvent

HOOK = "https://hooks.example.test/libris"
provider_id, owner, api = test_api_v1.provider_id, test_api_v1.owner, test_api_v1.api
webhook_settings = test_api_delivery.webhook_settings


def chapter_ids(project_id: str) -> dict[float, str]:
    with SessionLocal() as db:
        rows = db.execute(select(Chapter.chapter_number, Chapter.id).where(Chapter.project_id == project_id))
        return dict(rows.all())


def follow_up(provider: str, **changes) -> dict:
    body = payload(provider, external_id="saga-follow-up", volume={"latest": True}, **changes)
    body["chapters"] = [
        body["chapters"][0],  # chapter 2 again, unchanged
        {
            "external_id": "chapter-003",
            "number": 3,
            "title": "Chapter 3",
            "content": "Bob climbed the Silver Tower.\n",
        },
    ]
    return body


@respx.mock
async def test_new_chapters_are_appended_translated_alone_and_announced(owner, api, provider_id, webhook_settings,
                                                                        monkeypatch):  # fmt: skip
    respx.post(LLM).mock(side_effect=mock_completion)
    monkeypatch.setattr(settings(), "api_webhook_secret", "s" * 40)
    secret = new_token(owner)
    first = api.post(REQUESTS, headers=bearer(secret), json=payload(provider_id)).json()
    assert (await run_pending_job()).status == "completed"
    assert api.get(first["status_url"], headers=bearer(secret)).json()["status"] == "completed"

    body = follow_up(provider_id, callback_url=HOOK, callback_events=["chapters.translated"])
    created = api.post(REQUESTS, headers=bearer(secret), json=body)
    assert created.status_code == 202, created.text
    later = created.json()
    assert later["project_id"] == first["project_id"]  # "latest": the series' last volume
    numbers = chapter_ids(first["project_id"])
    assert sorted(numbers) == [1, 2, 3]
    job = await run_pending_job()
    assert job.status == "completed", job.error
    # The job only reviews the new chapter; earlier chapters cost no model call at all.
    assert job.options[OPTION] == [numbers[3]]
    with SessionLocal() as db:
        calls = db.execute(
            select(RequestLog.operation, Segment.chapter_id)
            .join(Segment, Segment.id == RequestLog.segment_id)
            .where(RequestLog.job_id == job.id)
        ).all()
    assert calls and {chapter_id for _, chapter_id in calls} == {numbers[3]}
    assert {"translation", "final_review"} <= {operation for operation, _ in calls}

    status = api.get(later["status_url"], headers=bearer(secret)).json()
    assert status["status"] == "completed"
    assert status["chapters"]["created"] == 1 and status["chapters"]["unchanged"] == 1
    assert status["chapters"]["new"] == [numbers[3]]
    assert status["chapter_events"]["batches"] == 1 and status["chapter_events"]["pending"] == 1

    def result(scope: str | None) -> list[float]:
        query = f"?format=json&scope={scope}" if scope else "?format=json"
        response = api.get(later["result_url"] + query, headers=bearer(secret))
        assert response.status_code == 200, response.text
        return [item["number"] for item in response.json()["chapters"]]

    assert result(None) == [2, 3] and result("request") == [2, 3]
    assert result("new") == [3]
    assert result("volume") == [1, 2, 3]
    zipped = api.get(later["result_url"] + "?format=txt-zip&scope=new", headers=bearer(secret))
    assert [
        name
        for name in zipfile.ZipFile(io.BytesIO(zipped.content)).namelist()
        if name.startswith("chapters/")
    ] == ["chapters/003 - Chapitre 3.txt"]
    assert api.get(later["result_url"] + "?scope=all", headers=bearer(secret)).status_code == 422

    received = []
    respx.post(host=PUBLIC, path="/libris").mock(
        side_effect=lambda request: received.append(request) or httpx.Response(204)
    )
    assert webhooks.pump(time.time()) == 2
    assert [request.headers["x-libris-event"] for request in received] == [
        "chapters.translated", "translation_request.finished",
    ]  # fmt: skip
    batch = received[0]
    expected = hmac.new(
        b"s" * 40, batch.headers["x-libris-timestamp"].encode() + b"." + batch.content, hashlib.sha256
    )
    assert batch.headers["x-libris-signature"] == "sha256=" + expected.hexdigest()
    event = json.loads(batch.content)
    assert event["batch"] == 1 and event["announced"] == 1 and event["total"] == 2
    assert [item["number"] for item in event["chapters"]] == [3]
    assert event["result_url"].endswith("/result?partial=true")
    status = api.get(later["status_url"], headers=bearer(secret)).json()
    assert status["chapter_events"]["delivered"] == 1 and status["chapter_events"]["waiting_chapters"] == 0


def test_batches_follow_the_chapters_as_they_are_translated(
    owner, api, provider_id, webhook_settings, monkeypatch
):
    monkeypatch.setattr(settings(), "api_webhook_secret", "s" * 40)
    secret = new_token(owner)
    body = payload(provider_id, callback_url=HOOK, callback_events=["chapters.translated"])
    created = api.post(REQUESTS, headers=bearer(secret), json=body).json()
    numbers = chapter_ids(created["project_id"])
    with SessionLocal() as db:
        request = db.get(TranslationRequest, created["request_id"])
        assert request.options["unannounced"] == [numbers[1], numbers[2]]
        assert chapter_events.announce(db, request) is None  # nothing translated yet
        for segment in db.scalars(select(Segment).where(Segment.chapter_id == numbers[2])):
            segment.translation = "Traduit."
        first = chapter_events.announce(db, request)
        assert (first.sequence, first.chapter_ids) == (1, [numbers[2]])
        assert chapter_events.announce(db, request) is None  # announced once
        for segment in db.scalars(select(Segment).where(Segment.chapter_id == numbers[1])):
            segment.translation = "Traduit."
        second = chapter_events.announce(db, request)
        assert (second.sequence, second.chapter_ids) == (2, [numbers[1]])
        assert request.options["unannounced"] == []
        db.commit()
    # A receiver that keeps failing: retried with the backoff, then given up; the request is unaffected.
    monkeypatch.setattr(settings(), "api_webhook_max_attempts", 2)
    respx_mock = respx.mock()
    with respx_mock:
        respx_mock.post(host=PUBLIC, path="/libris").mock(return_value=httpx.Response(503))
        now = time.time()
        assert chapter_events.pump_events(now) == 2
        assert chapter_events.pump_events(now + 1) == 0
        assert chapter_events.pump_events(now + webhooks.BACKOFF_MAX) == 2
    with SessionLocal() as db:
        rows = db.scalars(select(WebhookEvent).order_by(WebhookEvent.sequence)).all()
        assert [(row.state, row.attempts, row.error) for row in rows] == [("failed", 2, "HTTP 503")] * 2


def test_latest_volume_and_opt_in_events(owner, api, provider_id):
    secret = new_token(owner)
    imported = {"pipeline": {"start": False}}
    first = api.post(REQUESTS, headers=bearer(secret), json=payload(
        provider_id, external_id="a", volume={"latest": True}, **imported)).json()  # fmt: skip
    with SessionLocal() as db:
        assert db.get(Project, first["project_id"]).volume_number == 1  # none yet: volume 1
    second = payload(provider_id, external_id="b", volume={"number": 2}, **imported)
    second["chapters"] = [{"number": 1, "content": "Volume two starts.\n"}]
    two = api.post(REQUESTS, headers=bearer(secret), json=second).json()
    files = [txt("Chapter 2.txt", "Volume two goes on.\n")]
    fields = {"series": "Synthetic Saga", "volume": "latest", "source_language": "en", "target_language": "fr",
              "start": "false", "callback_events": "chapters.translated"}  # fmt: skip
    appended = api.post(REQUESTS, headers=bearer(secret), files=files, data=fields)
    assert appended.status_code == 202, appended.text
    assert appended.json()["project_id"] == two["project_id"]
    assert sorted(chapter_ids(two["project_id"])) == [1, 2]
    # Refused: both a number and latest, neither, an unknown event, "latest" for an EPUB.
    for volume in ({"number": 1, "latest": True}, {"title": "x"}):
        refused = api.post(
            REQUESTS, headers=bearer(secret), json=payload(provider_id, external_id="c", volume=volume)
        )
        assert refused.status_code == 422 and ["volume", "number"] in [
            e["loc"] for e in refused.json()["detail"]["errors"]
        ]
    unknown = api.post(
        REQUESTS, headers=bearer(secret), json=payload(provider_id, external_id="d", callback_events=["x"])
    )
    assert unknown.status_code == 422
    bad = api.post(REQUESTS, headers=bearer(secret), files=files, data={**fields, "callback_events": "x"})
    assert bad.status_code == 422
    epub = api.post(REQUESTS, headers=bearer(secret), files={"file": ("book.epub", b"PK", "application/epub+zip")},
                    data={**epub_fields(provider_id), "volume": "latest", "series": "Synthetic Saga"})  # fmt: skip
    assert epub.status_code == 422 and "latest" in epub.json()["detail"]["message"]


def test_older_documents_keep_their_checksum():
    body = payload(None)
    canonical = json.loads(parse_payload(body, 10).canonical())
    assert "latest" not in canonical["volume"] and "callback_events" not in canonical
    events = json.loads(
        parse_payload({**body, "callback_events": ["chapters.translated"] * 2}, 10).canonical()
    )
    assert events["callback_events"] == ["chapters.translated"]


def translate_all(project_id: str) -> None:
    with SessionLocal() as db:
        for segment in db.scalars(select(Segment).where(Segment.project_id == project_id)):
            segment.translation = "Traduit : " + segment.source
            segment.translated_units = [{"id": unit["id"], "text": "Traduit"} for unit in segment.units]
            segment.stage, segment.status = "done", "ok"
        db.commit()


def import_chapters(client, files, **extra):
    session = upload(client, "txt", files)
    items = [
        {"index": item["index"], "chapter_number": item["chapter_number"]}
        for item in session["proposal"]["items"]
    ]
    body = {"destination": {"mode": "series", "series_name": "Web Novel"}, "items": items, **extra}
    response = client.post(f"/api/imports/{session['id']}/commit", json=body)
    assert response.status_code == 200, response.text
    return response.json()


def test_chapters_imported_in_the_interface_follow_up_the_volume(owner, provider_id):
    first = import_chapters(
        owner, [chapter(1), chapter(2)], start="none", settings={"provider_id": provider_id}
    )
    project_id = first["projects"][0]["id"]
    translate_all(project_id)
    later = import_chapters(owner, [chapter(2), chapter(3)], start="pipeline")
    numbers = chapter_ids(project_id)
    assert later["projects"][0]["status"] == "updated" and len(later["jobs"]) == 1
    with SessionLocal() as db:
        job = db.get(Job, later["jobs"][0]["job_id"])
        assert job.options[OPTION] == [numbers[3]]
        project = db.get(Project, project_id)
        # A chapter still untranslated from before is part of the follow-up too; a whole new volume is not scoped.
        db.execute(Segment.__table__.update().where(Segment.chapter_id == numbers[1]).values(translation=""))
        assert follow_up_scope(db, project, [numbers[3]]) == [numbers[1], numbers[3]]
        assert follow_up_scope(db, project, list(numbers.values())) is None
        db.rollback()

    translate_all(project_id)
    exported = owner.get(f"/api/projects/{project_id}/export/txt-zip?from_chapter=3")
    assert exported.status_code == 200
    names = [
        name for name in zipfile.ZipFile(io.BytesIO(exported.content)).namelist() if name.endswith(".txt")
    ]
    assert len(names) == 1 and "3" in names[0]
    text = owner.get(f"/api/projects/{project_id}/export/txt?from_chapter=2&to_chapter=3").text
    assert "chapter 1." not in text.casefold() and text.count("Traduit") >= 2
    assert owner.get(f"/api/projects/{project_id}/export/md?from_chapter=9").status_code == 404
    assert owner.get(f"/api/projects/{project_id}/export/txt?from_chapter=3&to_chapter=1").status_code == 422


def test_the_consistency_check_of_a_follow_up_samples_the_new_chapters(owner):
    first = import_chapters(owner, [chapter(n, f"Chapter {n}\n\nAlice saw the Silver Tower again, time {n}.\n") for n in range(1, 7)],
                            start="none")  # fmt: skip
    project_id = first["projects"][0]["id"]
    translate_all(project_id)
    numbers = chapter_ids(project_id)
    with SessionLocal() as db:
        db.add(
            Glossary(project_id=project_id, source="Silver Tower", translation="Tour d’argent", accepted=True)
        )
        db.commit()
    whole = Job(project_id=project_id, operation="analyze", options={})
    follow = Job(project_id=project_id, operation="analyze", options={OPTION: [numbers[6]]})
    _, everything = _consistency_samples(whole)
    _, scoped = _consistency_samples(follow)
    with SessionLocal() as db:
        chapter_of = dict(
            db.execute(select(Segment.id, Segment.chapter_id).where(Segment.project_id == project_id)).all()
        )
    first_chapter = {chapter_of[sid] for sample in everything for sid in sample["mapping"].values()}
    assert len(first_chapter) > 2
    assert scoped and all(
        {chapter_of[sid] for sid in sample["mapping"].values()} <= {numbers[1], numbers[6]}
        for sample in scoped
    )
    assert all(numbers[6] in {chapter_of[sid] for sid in sample["mapping"].values()} for sample in scoped)
    assert not _consistency_samples(Job(project_id=project_id, operation="analyze", options={OPTION: []}))[1]
