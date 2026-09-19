"""End-to-end series journeys on the real worker with a synthetic model: webnovel chapters added later
without retranslation, identities reused by later volumes, nothing from a later volume in the prompts."""

import json
import re
import zipfile
from io import BytesIO

import httpx
import pytest
import respx
from fastapi.testclient import TestClient
from sqlalchemy import select
from test_pipeline import run_job

from app.db import SessionLocal
from app.engines.exports.text import chapters_zip, volume_texts
from app.jobs.launch import PIPELINE
from app.main import app
from app.models import Chapter, Project, Provider, RequestLog, Segment, SeriesEntity, User
from app.schemas import BookOverview, ChapterAnalysis, ReviewResult, TranslationResult
from app.security import password_hash

PASSWORD = "test-password-123456789"


def synthetic(request):
    body = json.loads(request.content)
    name = body.get("response_format", {}).get("json_schema", {}).get("name", "TranslationResult")
    text = "\n".join(m["content"] for m in body["messages"])
    target = re.search(r"<TARGET_TEXT>\n(.*?)\n</TARGET_TEXT>", text, re.S)
    target_text = target[1] if target else ""
    if name == "ChapterAnalysis":
        characters = []
        if "Alice" in target_text:
            characters.append({"canonical_name": "Alice Moreau", "aliases": ["Alice"]})
        if "Zed" in target_text:
            characters.append({"canonical_name": "Zed Future", "aliases": ["Zed"]})
        result = ChapterAnalysis(summary="A synthetic chapter.", characters=characters).model_dump()
    elif name in {"BookBible", "BookOverview"}:
        result = BookOverview(summary="A synthetic serial.", tone="Plain").model_dump()
    elif name == "ReviewResult":
        result = ReviewResult().model_dump()
    elif name == "FinalReviewResult":
        result = {"decision": "accept", "issues": [], "uncertainties": [], "explanation": "ok", "search_queries": []}
    else:
        units = [{"id": u["id"], "text": "FR " + u["text"]} for u in json.loads(target_text)]
        result = TranslationResult(units=units).model_dump()
    return httpx.Response(
        200,
        json={
            "choices": [{"finish_reason": "stop", "message": {"content": json.dumps(result)}}],
            "usage": {"prompt_tokens": 50, "completion_tokens": 20},
        },
    )


@pytest.fixture
def client():
    with SessionLocal() as db:
        db.add(User(username="writer", password_hash=password_hash(PASSWORD), admin=True))
        db.add(Provider(id="p-mock", name="Mock", base_url="https://llm.test/v1", model="synthetic",
                        capabilities={"supports_json_schema": True}, context_window=64000))
        db.commit()
    with TestClient(app) as client:
        client.post("/api/auth/login", json={"username": "writer", "password": PASSWORD})
        yield client


def import_chapters(client, series: str, chapters: dict[int, str], target=None) -> str:
    session = client.post("/api/imports", json={"format": "txt"}).json()
    for number, text in chapters.items():
        client.post(f"/api/imports/{session['id']}/files", files={"file": (f"Chapter {number}.txt", text.encode())})
    proposal = client.get(f"/api/imports/{session['id']}").json()["proposal"]
    body = {
        "destination": {"mode": "series", "series_name": series},
        "target": target or {"mode": "serial"},
        "items": [{"index": i["index"], "chapter_number": i["chapter_number"]} for i in proposal["items"]],
        "settings": {"provider_id": "p-mock", "target_language": "fr", "quality": "fast"},
        "start": "none",
    }
    response = client.post(f"/api/imports/{session['id']}/commit", json=body)
    assert response.status_code == 200, response.text
    return response.json()["projects"][0]["id"]


def calls_by_segment(project_id: str) -> dict[str, int]:
    with SessionLocal() as db:
        counts: dict[str, int] = {}
        for log in db.scalars(select(RequestLog).where(RequestLog.project_id == project_id)):
            if log.segment_id and log.operation == "translation":
                counts[log.segment_id] = counts.get(log.segment_id, 0) + 1
        return counts


@respx.mock
async def test_webnovel_chapters_are_translated_exported_and_extended_without_retranslation(client):
    respx.post("https://llm.test/v1/chat/completions").mock(side_effect=synthetic)
    first = {n: f"Chapter {n}\n\nAlice walked to gate {n}.\n\n* * *\n\nShe waited.\n" for n in (3, 1, 2)}
    pid = import_chapters(client, "Endless Road", first)
    job = await run_job(pid, "analyze", **PIPELINE)
    assert job.status == "completed", job.error
    with SessionLocal() as db:
        project = db.get(Project, pid)
        texts = volume_texts(db, project)
        assert [t.number for t in texts] == [1, 2, 3] and all(t.complete for t in texts)
        archive = zipfile.ZipFile(BytesIO(chapters_zip(project, texts)))
        names = sorted(name for name in archive.namelist() if name.startswith("chapters/"))
        assert names == ["chapters/001 - FR Chapter 1.txt", "chapters/002 - FR Chapter 2.txt", "chapters/003 - FR Chapter 3.txt"]
        assert archive.read(names[0]).decode() == "FR Chapter 1\n\nFR Alice walked to gate 1.\n\n* * *\n\nFR She waited.\n"
        assert "⟦" not in archive.read(names[1]).decode()
        before = {s.id: s.translation for s in db.scalars(select(Segment).where(Segment.project_id == pid))}
    calls_before = calls_by_segment(pid)
    # Chapter 4 arrives later: only its passages reach the model.
    same = import_chapters(client, "Endless Road", {4: "Chapter 4\n\nAlice opened gate 4.\n"})
    assert same == pid
    job = await run_job(pid, "analyze", **PIPELINE)
    assert job.status == "completed", job.error
    with SessionLocal() as db:
        after = {s.id: s.translation for s in db.scalars(select(Segment).where(Segment.project_id == pid))}
        new = set(after) - set(before)
        chapter = db.scalar(select(Chapter).where(Chapter.project_id == pid, Chapter.chapter_number == 4))
        assert new == {s.id for s in db.scalars(select(Segment).where(Segment.chapter_id == chapter.id))}
        assert all(after[sid] == translation for sid, translation in before.items())
    calls_after = calls_by_segment(pid)
    assert {sid: n for sid, n in calls_after.items() if sid in calls_before} == calls_before
    assert set(calls_after) - set(calls_before) == new


@respx.mock
async def test_later_volumes_reuse_identities_and_never_see_later_ones(client):
    respx.post("https://llm.test/v1/chat/completions").mock(side_effect=synthetic)
    v1 = import_chapters(client, "Twin Moons", {1: "Chapter 1\n\nAlice found the map.\n"},
                         {"mode": "new_volume", "volume_number": 1})
    v3 = import_chapters(client, "Twin Moons", {1: "Chapter 1\n\nZed crossed the river. Alice slept.\n"},
                         {"mode": "new_volume", "volume_number": 3})
    v2 = import_chapters(client, "Twin Moons", {1: "Chapter 1\n\nAlice read the map again.\n"},
                         {"mode": "new_volume", "volume_number": 2})
    for pid in (v1, v3):
        job = await run_job(pid, "analyze", **PIPELINE)
        assert job.status == "completed", job.error
    with SessionLocal() as db:
        names = set(db.scalars(select(SeriesEntity.name)))
        assert {"Alice Moreau", "Zed Future"} <= names
    job = await run_job(v2, "analyze", **PIPELINE)
    assert job.status == "completed", job.error
    with SessionLocal() as db:
        prompts = [json.dumps(log.messages, ensure_ascii=False) for log in db.scalars(
            select(RequestLog).where(RequestLog.project_id == v2, RequestLog.operation == "translation")
        )]
    assert prompts and all("Alice Moreau" in prompt for prompt in prompts)
    assert not any("Zed" in prompt for prompt in prompts)
