import io
import json
import time
import zipfile

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import inspect, select

from app.api.common import row
from app.api.project_archive import ARCHIVED_FIELDS, NOT_ARCHIVED
from app.config import settings
from app.db import SessionLocal
from app.engines.translation.versions import save_version
from app.jobs import segment_state as state
from app.main import app
from app.models import (
    BibleRevision,
    Chapter,
    CharacterRelation,
    Entity,
    EntityMerge,
    Glossary,
    Issue,
    Job,
    JobSegmentState,
    Membership,
    Memory,
    Project,
    RequestLog,
    Segment,
    TranslationVersion,
    User,
)
from app.security import password_hash

PASSWORD = "test-password-123456789"

# Compared field by field after a round trip, except what is deliberately not restored.
IGNORED = {"id", "project_id", "owner_id", "provider_id", "author_id", "original_path", "updated_at",
           "source_asset_id"}
REQUEST_BODIES = {"execution_owner", "fingerprint", "parameters", "messages", "context", "raw", "parsed"}


def reviewed_book(pid: str, user_id: str, provider_id: str) -> None:
    """A book in the middle of its review: every kind of work the archive must carry."""
    now = time.time()
    with SessionLocal() as db:
        project = db.get(Project, pid)
        project.title, project.author = "La Tour d’argent", "Auteure"
        project.series_name, project.volume_number = "Tours", 2
        project.target_language, project.quality, project.context_backend = "fr", "high", "hybrid"
        project.instructions = "Vouvoiement entre Alice et Bob."
        project.config = {"tone": "soutenu"}
        project.bible = {"summary": "Une tour.", "tone": "sombre"}
        project.bible_validated = True
        project.status = "translating"
        project.book_info = dict(project.book_info, validation={"available": True, "valid": True})
        chapters = list(db.scalars(select(Chapter).where(Chapter.project_id == pid).order_by(Chapter.position)))
        chapters[1].summary, chapters[1].instructions, chapters[1].analyzed = {"summary": "Début"}, "Lent", True
        segments = list(db.scalars(select(Segment).where(Segment.project_id == pid).order_by(Segment.position)))
        for index, segment in enumerate(segments):
            units = [{"id": u["id"], "text": u["text"].replace("e", "é")}
                     for u in segment.units]
            save_version(db, segment.id, units, "translation", 0, stage="translated")
            if index % 2 == 0:
                save_version(db, segment.id, units, "human", 1, author_id=user_id, validated=True, stage="done")
        db.flush()
        for segment in db.scalars(select(Segment).where(Segment.project_id == pid)):
            db.refresh(segment)
        segments[1].critique = [{"unit_id": "u", "category": "style", "severity": "warning",
                                 "description": "Lourd", "suggestion": "Alléger"}]
        segments[1].uncertainties = ["Pendentif ou médaillon ?"]
        segments[1].narrative = {"speaker": "Bob"}
        segments[1].instructions = "Garder « Hello »."
        segments[3].status, segments[3].error = "refused", "Refus du modèle"
        alice = Entity(project_id=pid, name="Alice", category="character", validated=True,
                       data={"canonical_name": "Alice", "aliases": ["Al"], "first_position": 0}, created_at=now - 50)
        ally = Entity(project_id=pid, name="Ally", category="character",
                      data={"canonical_name": "Ally", "first_position": 2}, created_at=now - 40)
        bob = Entity(project_id=pid, name="Bob", category="character",
                     data={"canonical_name": "Bob", "first_position": 1}, created_at=now - 30)
        tower = Entity(project_id=pid, name="Silver Tower", category="place", data={"note": "tour"},
                       created_at=now - 20)
        db.add_all([alice, ally, bob, tower])
        db.flush()
        ally.merged_into_id = alice.id
        db.add(EntityMerge(project_id=pid, target_id=alice.id, source_ids=[ally.id], reason="Alias",
                           snapshots=[{"id": ally.id, "name": "Ally"}], human=True))
        db.add(CharacterRelation(project_id=pid, source_id=alice.id, target_id=bob.id, segment_id=segments[5].id,
                                 position=5, relation_type="sibling", evidence="her brother", validated=True))
        db.add(Glossary(project_id=pid, source="Silver Tower", translation="Tour d’argent", locked=True))
        db.add(Glossary(project_id=pid, source="pendant", translation="pendentif", accepted=False))
        db.add(Memory(project_id=pid, segment_id=segments[0].id, position=0, kind="analysis",
                      content={"summary": "Alice entre", "segment": segments[0].id}))
        db.add(Memory(project_id=pid, segment_id=None, position=-1, kind="book", content={"note": "global"}))
        db.add(BibleRevision(project_id=pid, content={"summary": "v1"}, created_at=now - 60))
        db.add(BibleRevision(project_id=pid, content={"summary": "Une tour."}, human=True, created_at=now - 10))
        db.add(Issue(project_id=pid, segment_id=segments[1].id, severity="warning", code="length",
                     message="Trop long"))
        db.add(Issue(project_id=pid, segment_id=segments[2].id, severity="error", code="locked_term",
                     message="Terme verrouillé", resolved=True))
        done = Job(project_id=pid, provider_id=provider_id, operation="translate", status="completed",
                   options={"provider_id": provider_id, "segment_ids": [segments[3].id]}, attempts=2,
                   checkpoint={"step": "final_review", "total": 3, "review_targets": 3},
                   finished_at=now - 20, created_at=now - 30)
        paused = Job(project_id=pid, provider_id=provider_id, operation="resolve_validations", status="paused",
                     stop_reason="user_pause", checkpoint={"step": "final_review", "current": 1, "total": 3},
                     created_at=now - 5)
        db.add_all([done, paused])
        db.flush()
        state.mark_all(db, done.id, state.REVIEW_TARGET, [s.id for s in segments[:3]])
        state.mark_all(db, done.id, state.REVIEWED, [segments[0].id])
        state.mark(db, done.id, state.REVIEWED, segments[1].id, outcome="resolved", data={"revised": True})
        state.mark(db, done.id, state.REPAIR, segments[2].id, key="1:translation:0", data={"units": []})
        state.mark(db, done.id, state.BIBLE, key="chapter:0")
        for operation, prompt, completion in (("translation", 1200, 300), ("final_review", 800, 90)):
            db.add(RequestLog(project_id=pid, provider_id=provider_id, job_id=done.id, segment_id=segments[1].id,
                              operation=operation, model="test-model", fingerprint="secret-fp", status="success",
                              parameters={"temperature": 0.2}, messages=[{"role": "user", "content": "texte"}],
                              raw={"answer": "x"}, prompt_tokens=prompt, completion_tokens=completion,
                              duration=3.5, input_cost=0.5, output_cost=2.0))
        db.commit()


def labels(db, pid: str) -> dict[str, str]:
    """Stable names for identifiers, so that two copies of a book compare equal."""
    names = {}
    for chapter in db.scalars(select(Chapter).where(Chapter.project_id == pid)):
        names[chapter.id] = f"chapter:{chapter.position}"
    for segment in db.scalars(select(Segment).where(Segment.project_id == pid)):
        names[segment.id] = f"segment:{segment.position}"
    for entity in db.scalars(select(Entity).where(Entity.project_id == pid)):
        names[entity.id] = f"entity:{entity.category}:{entity.name}"
    for job in db.scalars(select(Job).where(Job.project_id == pid)):
        names[job.id] = f"job:{job.operation}:{job.created_at}"
    return names


def rename(value, names):
    if isinstance(value, str):
        return names.get(value, value)
    if isinstance(value, list):
        return [rename(item, names) for item in value]
    if isinstance(value, dict):
        return {names.get(k, k): rename(v, names) for k, v in value.items()}
    return value


def snapshot(pid: str) -> dict:
    with SessionLocal() as db:
        names = labels(db, pid)
        segment_ids = select(Segment.id).where(Segment.project_id == pid)

        def dump(model, *conditions, skip=()):
            rows = [
                rename({k: v for k, v in row(item).items() if k not in IGNORED | set(skip)}, names)
                for item in db.scalars(select(model).where(*conditions))
            ]
            return sorted(rows, key=lambda value: json.dumps(value, sort_keys=True, default=str))

        return {
            "project": rename({k: v for k, v in row(db.get(Project, pid)).items() if k not in IGNORED}, names),
            "chapters": dump(Chapter, Chapter.project_id == pid),
            "segments": dump(Segment, Segment.project_id == pid),
            "versions": dump(TranslationVersion, TranslationVersion.segment_id.in_(segment_ids)),
            "glossary": dump(Glossary, Glossary.project_id == pid),
            "entities": dump(Entity, Entity.project_id == pid),
            "relations": dump(CharacterRelation, CharacterRelation.project_id == pid),
            "merges": dump(EntityMerge, EntityMerge.project_id == pid),
            "memories": dump(Memory, Memory.project_id == pid),
            "bible_revisions": dump(BibleRevision, BibleRevision.project_id == pid),
            "issues": dump(Issue, Issue.project_id == pid),
            "jobs": dump(Job, Job.project_id == pid),
            "job_state": dump(JobSegmentState, JobSegmentState.job_id.in_(select(Job.id).where(Job.project_id == pid))),
            "requests": dump(RequestLog, RequestLog.project_id == pid, skip=REQUEST_BODIES),
        }


def login(client, username="tester"):
    assert client.post("/api/auth/login", json={"username": username, "password": PASSWORD}).status_code == 200


def zipped(original: bytes, payload) -> bytes:
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w") as archive:
        archive.writestr("original.epub", original)
        archive.writestr("project.json", payload if isinstance(payload, bytes) else json.dumps(payload))
    return output.getvalue()


def test_project_archive_round_trip_is_faithful(seeded):
    pid, user_id, provider_id = seeded
    reviewed_book(pid, user_id, provider_id)
    with SessionLocal() as db:
        db.add(User(username="friend", password_hash=password_hash(PASSWORD)))
        db.flush()
        friend = db.scalar(select(User).where(User.username == "friend"))
        db.add(Membership(project_id=pid, user_id=friend.id, role="editor"))
        db.commit()
    before = snapshot(pid)
    with TestClient(app) as client:
        login(client)
        exported = client.get(f"/api/projects/{pid}/export/project")
        assert exported.status_code == 200
        with zipfile.ZipFile(io.BytesIO(exported.content)) as archive:
            payload = json.loads(archive.read("project.json"))
        assert payload["schema_version"] == 3
        assert archive.namelist() == ["sources/1.epub", "project.json"]
        assert "secret-fp" not in exported.text and "texte" not in json.dumps(payload["requests"])
        assert client.delete(f"/api/projects/{pid}").status_code == 200
        imported = client.post("/api/projects/import", files={"file": ("p.zip", exported.content)})
        assert imported.status_code == 201, imported.text
        new_id = imported.json()["id"]
        restored = client.get(f"/api/projects/{new_id}").json()
        restored_metrics = client.get(f"/api/projects/{new_id}/metrics").json()
    after = snapshot(new_id)
    # The only deliberate difference: work that was running waits for Resume after a restore.
    assert (before["project"]["status"], after["project"]["status"]) == ("translating", "paused")
    after["project"]["status"] = "translating"
    for job in before["jobs"]:  # the provider stays behind, including the one a job was pinned to
        job["options"].pop("provider_id", None)
    assert set(after) == set(before)
    for part in before:
        assert after[part] == before[part], part
    # Unchanged meaning: validated passages stay "ok", no duplicated history, statistics intact.
    assert sum(s["status"] == "ok" for s in after["segments"]) == 4
    assert len(after["versions"]) == 11
    assert restored["progress"]["review"]["resolved"] == 1
    # The provider stays behind; what the book cost stays with its requests, at the price they recorded.
    assert restored["progress"]["estimate"]["spent_cost"] == pytest.approx(restored_metrics["cost"])
    with SessionLocal() as db:
        project = db.get(Project, new_id)
        assert project.owner_id == user_id and project.provider_id is None
        assert not db.scalars(select(Membership).where(Membership.project_id == new_id)).all()
        assert all(j.provider_id is None and "provider_id" not in j.options
                   for j in db.scalars(select(Job).where(Job.project_id == new_id)))


def test_an_archive_from_before_v05_brings_its_checkpoint_lists_back_as_job_state(seeded):
    pid, user_id, provider_id = seeded
    reviewed_book(pid, user_id, provider_id)
    before = snapshot(pid)
    with TestClient(app) as client:
        login(client)
        exported = client.get(f"/api/projects/{pid}/export/project")
        with zipfile.ZipFile(io.BytesIO(exported.content)) as archive:
            original, payload = archive.read("sources/1.epub"), json.loads(archive.read("project.json"))
        # As exported by v0.4 / early v0.5: no job_state, the same facts as lists in the checkpoint.
        payload["schema_version"] = 2
        legacy: dict[str, dict] = {}
        for item in payload.pop("job_state"):
            checkpoint = legacy.setdefault(item["job_id"], {})
            if item["step"] == state.REVIEW_TARGET:
                checkpoint.setdefault("final_review_targets", []).append(item["segment_id"])
            elif item["step"] == state.REVIEWED:
                checkpoint.setdefault("final_review_done", []).append(item["segment_id"])
                if item["outcome"]:
                    outcome = {**item["data"], "outcome": item["outcome"]}
                    checkpoint.setdefault("final_review_outcomes", {})[item["segment_id"]] = outcome
            elif item["step"] == state.REPAIR:
                revision, operation, start = item["key"].split(":")
                name = f"{item['segment_id']}:{revision}:{operation}"
                checkpoint.setdefault("repair", {}).setdefault(name, {})[start] = item["data"]
            else:
                checkpoint.setdefault("analysis_batches", []).append(item["key"])
        for job in payload["jobs"]:
            job["checkpoint"] = {
                k: v for k, v in job["checkpoint"].items() if k != "review_targets"
            } | legacy.get(job["id"], {})
        client.delete(f"/api/projects/{pid}")
        imported = client.post("/api/projects/import", files={"file": ("p.zip", zipped(original, payload))})
        assert imported.status_code == 201, imported.text
        new_id = imported.json()["id"]
        restored = client.get(f"/api/projects/{new_id}").json()
    after = snapshot(new_id)
    assert after["job_state"] == before["job_state"]
    assert after["jobs"] == [
        {**job, "options": {k: v for k, v in job["options"].items() if k != "provider_id"}} for job in before["jobs"]
    ]
    assert restored["progress"]["review"]["resolved"] == 1


def test_unfinished_work_is_restored_paused(seeded):
    pid, *_ = seeded
    with SessionLocal() as db:
        db.add(Job(project_id=pid, operation="translate", status="translating", lease_owner="w", lease_until=9e9))
        db.get(Project, pid).status = "translating"
        db.commit()
    with TestClient(app) as client:
        login(client)
        exported = client.get(f"/api/projects/{pid}/export/project").content
        client.delete(f"/api/projects/{pid}?stop_jobs=true")
        imported = client.post("/api/projects/import", files={"file": ("p.zip", exported)})
        assert imported.status_code == 201, imported.text
        new_id = imported.json()["id"]
    with SessionLocal() as db:
        job = db.scalar(select(Job).where(Job.project_id == new_id))
        assert (job.status, job.stop_reason, job.lease_owner) == ("paused", "project_restored", "")
        assert db.get(Project, new_id).status == "paused"


def test_version_1_archives_are_still_read(seeded, book_bytes):
    pid, *_ = seeded
    with SessionLocal() as db:
        segments = list(db.scalars(select(Segment).where(Segment.project_id == pid).order_by(Segment.position)))
        units = [{"id": u["id"], "text": "Bonjour"} for u in segments[0].units]
        save_version(db, segments[0].id, units, "human", 0, validated=True, stage="done")
        db.commit()
        payload = {
            "schema_version": 1,
            "project": row(db.get(Project, pid), ("id", "owner_id", "provider_id", "original_path")),
            "chapters": [row(c) for c in db.scalars(select(Chapter).where(Chapter.project_id == pid))],
            "segments": [row(s) for s in db.scalars(select(Segment).where(Segment.project_id == pid))],
            "versions": [row(v) for v in db.scalars(select(TranslationVersion))],
            "glossary": [], "entities": [], "memories": [],
        }  # fmt: skip
    with TestClient(app) as client:
        login(client)
        client.delete(f"/api/projects/{pid}")
        imported = client.post("/api/projects/import", files={"file": ("p.zip", zipped(book_bytes, payload))})
        assert imported.status_code == 201, imported.text
        new_id = imported.json()["id"]
    with SessionLocal() as db:
        first = db.scalar(select(Segment).where(Segment.project_id == new_id, Segment.position == 0))
        assert (first.translated_units, first.status, first.validated) == (units, "ok", True)
        versions = db.scalars(select(TranslationVersion).where(TranslationVersion.segment_id == first.id)).all()
        assert len(versions) == 1


@pytest.mark.parametrize(
    ("payload", "expected"),
    [
        ({"schema_version": 2}, "project"),
        ({"schema_version": 2, "project": {"quality": "extreme"}}, "project.quality"),
        ({"schema_version": 2, "project": {}, "segments": [{"id": "x"}]}, "segments.0.position"),
        ({"schema_version": 4, "project": {}}, "Version de projet non prise en charge"),
        (b"{not json", "JSON"),
    ],
)
def test_malformed_archives_are_explained_never_500(seeded, book_bytes, payload, expected):
    with TestClient(app, raise_server_exceptions=False) as client:
        login(client)
        client.delete(f"/api/projects/{seeded[0]}")
        books = set((settings().data_dir / "books").glob("*.epub"))
        response = client.post("/api/projects/import", files={"file": ("p.zip", zipped(book_bytes, payload))})
    assert response.status_code == 422, response.text
    assert expected in response.json()["detail"]
    assert set((settings().data_dir / "books").glob("*.epub")) == books


def test_export_refuses_an_archive_that_could_not_be_reimported(seeded, monkeypatch):
    pid, *_ = seeded
    with SessionLocal() as db:
        db.add(Memory(project_id=pid, kind="note", content={"text": "x" * 3 * 1024**2}))
        db.commit()
    monkeypatch.setattr(settings(), "max_unpacked_mb", 2)
    with TestClient(app) as client:
        login(client)
        response = client.get(f"/api/projects/{pid}/export/project")
    assert response.status_code == 413
    assert "MAX_UNPACKED_MB" in response.json()["detail"]


def test_every_column_is_either_archived_or_deliberately_left_out():
    for model, schema in ARCHIVED_FIELDS.items():
        columns = {column.key for column in inspect(model).columns}
        unknown = columns - set(schema.model_fields) - NOT_ARCHIVED[model]
        assert not unknown, f"{model.__name__}: decide whether to archive {sorted(unknown)}"
