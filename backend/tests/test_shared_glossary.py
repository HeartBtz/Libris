"""Glossary CSV previews and imports at every level, shared glossaries and their precedence."""

import json
import time

import pytest
from epubs import epub_bytes
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.db import SessionLocal
from app.engines.context.series import InheritedTerm, enforced_glossary, prior_volumes, series_terms
from app.main import app
from app.models import (
    AuditEntry,
    AutopilotDecision,
    Glossary,
    Job,
    Project,
    Provider,
    Segment,
    Series,
    SeriesSharedGlossary,
    SeriesTerm,
    SharedGlossary,
    SharedTerm,
    User,
)
from app.security import password_hash

PASSWORD = "test-password-123456789"
TEXT = "Alice entered the Silver Tower with the Veilstone and met the Ashen Guard near the Moonwell."


def login(client: TestClient, username: str) -> TestClient:
    assert (
        client.post("/api/auth/login", json={"username": username, "password": PASSWORD}).status_code == 200
    )
    return client


@pytest.fixture
def world():
    """Two volumes of a series, a third series of the same universe and a stranger."""
    from app.api.projects import import_book

    with SessionLocal() as db:
        user = User(username="owner", password_hash=password_hash(PASSWORD), admin=True)
        other = User(username="stranger", password_hash=password_hash(PASSWORD))
        provider = Provider(
            name="Mock", base_url="https://llm.test/v1", model="m", capabilities={}, context_window=64000
        )
        db.add_all([user, other, provider])
        db.flush()
        saga = Series(owner_id=user.id, name="Saga", normalized_name="saga", authors=[], bible={},
                      source_language="en", target_language="fr")  # fmt: skip
        spin = Series(owner_id=user.id, name="Spin-off", normalized_name="spin-off", authors=[], bible={})
        german = Series(owner_id=user.id, name="Saga DE", normalized_name="saga de", authors=[], bible={},
                        source_language="en", target_language="de")  # fmt: skip
        db.add_all([saga, spin, german])
        db.flush()
        volumes = []
        for number in (1, 2):
            project = import_book(
                db, user.id, epub_bytes(f"<h1>Prologue</h1><p>{TEXT} V{number}</p>", f"V{number}")
            )
            project.title, project.series_id, project.volume_number = f"V{number}", saga.id, number
            project.provider_id, project.context_backend = provider.id, "internal"
            project.source_language, project.target_language = "en", "fr"
            volumes.append(project)
        db.commit()
        return {"user": user.id, "other": other.id, "saga": saga.id, "spin": spin.id, "german": german.id,
                "v1": volumes[0].id, "v2": volumes[1].id}  # fmt: skip


def csv_file(text: str, name: str = "terms.csv", encoding: str = "utf-8-sig") -> dict:
    return {"file": (name, text.encode(encoding), "text/csv")}


SPREADSHEET = (
    "Terme source;Traduction;Catégorie;Verrouillé\r\n"
    "Silver Tower;Tour d’Argent;lieu;oui\r\n"
    "Veilstone;Pierre-voile;objet;non\r\n"
    "Moonwell;Puits-de-Lune;lieu;peut-être\r\n"
    "silver tower;Tour Argentée;lieu;non\r\n"
    "Ashen Guard;Garde cendrée;groupe;non\r\n"
)


def book_terms(pid: str) -> dict:
    with SessionLocal() as db:
        return {
            term.source: (term.translation, term.locked)
            for term in db.scalars(select(Glossary).where(Glossary.project_id == pid))
        }


def seed_book(pid: str, *terms: tuple[str, str, bool]) -> None:
    with SessionLocal() as db:
        for source, translation, locked in terms:
            db.add(
                Glossary(project_id=pid, source=source, translation=translation, locked=locked, accepted=True)
            )
        db.commit()


def test_csv_preview_reports_detection_conflicts_duplicates_and_errors_without_writing(world):
    seed_book(world["v1"], ("Veilstone", "Pierre de voile", True), ("Ashen Guard", "Garde cendrée", False))
    with TestClient(app) as client:
        login(client, "owner")
        preview = client.post(
            f"/api/projects/{world['v1']}/glossary/import/preview",
            files=csv_file(SPREADSHEET),
            data={"skip_invalid": "true", "strategy": "replace"},
            headers={"Accept-Language": "en"},
        )
    assert preview.status_code == 200, preview.text
    report = preview.json()
    assert (report["format"], report["encoding"], report["delimiter"], report["header"]) == (
        "csv",
        "utf-8-sig",
        ";",
        True,
    )
    assert report["columns"] == ["Terme source", "Traduction", "Catégorie", "Verrouillé"]
    assert report["mapping"] == {"source": 0, "translation": 1, "category": 2, "locked": 3}
    assert report["counts"] == {"terms": 4, "new": 1, "unchanged": 0, "conflicts": 2, "replaced": 1, "kept": 1,
                                "duplicates": 1, "errors": 1}  # fmt: skip
    assert [term["source"] for term in report["new"]] == ["Silver Tower"]
    locked, unlocked = report["conflicts"]
    # "replace" never touches a locked term; a category change alone is reported too.
    assert locked["source"] == "Veilstone" and locked["locked"] and locked["action"] == "keep"
    assert locked["fields"] == ["translation", "category", "locked"]
    assert (unlocked["source"], unlocked["fields"], unlocked["action"]) == (
        "Ashen Guard",
        ["category"],
        "replace",
    )
    assert report["duplicates"] == [{"line": 5, "source": "silver tower", "first_line": 2}]
    assert report["errors"] == [{"line": 4, "message": "Unrecognised value for locked: “peut-être”"}]
    assert report["applied"] is False and "imported" not in report
    assert book_terms(world["v1"]) == {
        "Veilstone": ("Pierre de voile", True),
        "Ashen Guard": ("Garde cendrée", False),
    }


@pytest.mark.parametrize(
    ("strategy", "expected"),
    [
        ("skip", {"Veilstone": ("Pierre de voile", True), "Moonwell": ("Puits", False)}),
        ("replace", {"Veilstone": ("Pierre de voile", True), "Moonwell": ("Puits-de-Lune", False)}),
        ("replace_all", {"Veilstone": ("Pierre-voile", False), "Moonwell": ("Puits-de-Lune", False)}),
    ],
)
def test_import_strategies_never_touch_locked_terms_unless_asked(world, strategy, expected):
    seed_book(world["v1"], ("Veilstone", "Pierre de voile", True), ("Moonwell", "Puits", False))
    content = (
        "source,translation\nVeilstone,Pierre-voile\nMoonwell,Puits-de-Lune\nSilver Tower,Tour d’Argent\n"
    )
    with TestClient(app) as client:
        login(client, "owner")
        response = client.post(
            f"/api/projects/{world['v1']}/glossary/import",
            files=csv_file(content),
            data={"strategy": strategy},
        )
    assert response.status_code == 200, response.text
    result = response.json()
    replaced = {"skip": 0, "replace": 1, "replace_all": 2}[strategy]
    assert (result["imported"], result["replaced"], result["skipped"]) == (1, replaced, 2 - replaced)
    terms = book_terms(world["v1"])
    assert {key: terms[key] for key in expected} == expected
    assert terms["Silver Tower"] == ("Tour d’Argent", False)


def test_column_mapping_reads_unknown_headers_and_invalid_rows_can_be_left_out(world):
    content = "EN\tFR\tRemarque libre\nSilver Tower\tTour d’Argent\tarticle\nVeilstone\t\t\n"
    files = csv_file(content, "base.txt", "utf-8")
    with TestClient(app) as client:
        login(client, "owner")
        # Unknown headers: read as a headerless file, whose third row has no translation.
        refused = client.post(f"/api/projects/{world['v1']}/glossary/import", files=files)
        assert refused.status_code == 422 and "n° 3" in refused.json()["detail"]
        detected = client.post(f"/api/projects/{world['v1']}/glossary/import/preview", files=files).json()
        assert (detected["header"], detected["columns"]) == (False, ["EN", "FR", "Remarque libre"])
        mapping = json.dumps({"source": 0, "translation": 1, "description": 2})
        strict = client.post(
            f"/api/projects/{world['v1']}/glossary/import", files=files, data={"mapping": mapping}
        )
        assert strict.status_code == 422
        result = client.post(
            f"/api/projects/{world['v1']}/glossary/import",
            files=files,
            data={"mapping": mapping, "skip_invalid": "true", "header": "true"},
        ).json()
        assert (result["imported"], result["counts"]["errors"], result["delimiter"]) == (1, 1, "\t")
        assert result["errors"][0]["line"] == 3
        bad = client.post(
            f"/api/projects/{world['v1']}/glossary/import", files=files, data={"mapping": "[1]"}
        )
        assert bad.status_code == 422 and "Correspondance de colonnes" in bad.json()["detail"]["message"]
    with SessionLocal() as db:
        [term] = db.scalars(select(Glossary).where(Glossary.project_id == world["v1"]))
        assert (term.source, term.description) == ("Silver Tower", "article")


def test_spreadsheet_export_uses_a_byte_order_mark_and_semicolons_and_comes_back_identical(world):
    seed_book(world["v1"], ("Silver Tower", "Tour d’Argent; «haute»", True), ("=cmd", "formule", False))
    with TestClient(app) as client:
        login(client, "owner")
        exported = client.get(f"/api/projects/{world['v1']}/glossary/export/csv?delimiter=semicolon&bom=true")
        assert exported.status_code == 200
        assert exported.content.startswith(b"\xef\xbb\xbfsource;translation;")
        assert b"'=cmd" in exported.content
        before = book_terms(world["v1"])
        preview = client.post(
            f"/api/projects/{world['v1']}/glossary/import/preview",
            files={"file": ("g.csv", exported.content)},
        ).json()
        assert preview["counts"]["unchanged"] == 2 and preview["delimiter"] == ";"
        assert preview["encoding"] == "utf-8-sig"
    assert book_terms(world["v1"]) == before


def test_shared_glossary_lifecycle_access_and_series_attachment(world):
    with TestClient(app) as client, TestClient(app) as stranger:
        login(client, "owner")
        login(stranger, "stranger")
        created = client.post("/api/glossaries", json={"name": " Universe ", "source_language": "en",
                                                        "target_language": "fr-FR"})  # fmt: skip
        assert created.status_code == 201, created.text
        glossary = created.json()
        assert (glossary["name"], glossary["term_count"], glossary["terms"]) == ("Universe", 0, [])
        duplicate = client.post("/api/glossaries", json={"name": "universe"})
        assert duplicate.status_code == 409 and duplicate.json()["detail"]["code"] == "glossary_exists"
        gid = glossary["id"]
        term = client.post(f"/api/glossaries/{gid}/terms", json={"source": "Moonwell", "translation": "Puits-de-Lune",
                                                                  "locked": True})  # fmt: skip
        assert term.status_code == 201
        assert (
            client.post(
                f"/api/glossaries/{gid}/terms", json={"source": "moonwell", "translation": "x"}
            ).status_code
            == 409
        )
        edited = client.put(f"/api/glossaries/{gid}/terms/{term.json()['id']}",
                            json={"source": "Moonwell", "translation": "Puits de Lune", "locked": True})  # fmt: skip
        assert edited.json()["translation"] == "Puits de Lune"
        # Another account sees nothing of it and cannot attach it.
        assert stranger.get(f"/api/glossaries/{gid}").status_code == 404
        assert stranger.get("/api/glossaries").json() == []
        assert (
            stranger.put(
                f"/api/series/{world['saga']}/shared-glossary", json={"glossary_id": gid}
            ).status_code
            == 404
        )
        # A series of another language pair refuses it; the others follow it.
        mismatch = client.put(f"/api/series/{world['german']}/shared-glossary", json={"glossary_id": gid})
        assert mismatch.status_code == 409 and mismatch.json()["detail"]["code"] == "language_mismatch"
        for series in ("saga", "spin"):
            attached = client.put(f"/api/series/{world[series]}/shared-glossary", json={"glossary_id": gid})
            assert (
                attached.status_code == 200
                and attached.json()["glossary"]["terms"][0]["source"] == "Moonwell"
            )
        listed = client.get("/api/glossaries").json()
        assert [s["name"] for s in listed[0]["series"]] == ["Saga", "Spin-off"] and listed[0][
            "locked_count"
        ] == 1
        assert client.put(
            f"/api/series/{world['spin']}/shared-glossary", json={"glossary_id": None}
        ).json() == {"glossary": None}
        assert client.get(f"/api/series/{world['spin']}/shared-glossary").json() == {"glossary": None}
        # Import and export of the shared glossary itself.
        imported = client.post(
            f"/api/glossaries/{gid}/import", files=csv_file("source;traduction\nVeilstone;Pierre-voile\n")
        ).json()
        assert imported["imported"] == 1
        exported = client.get(f"/api/glossaries/{gid}/export/tbx")
        assert exported.status_code == 200 and b'xml:lang="fr-FR"' in exported.content
        assert client.delete(f"/api/glossaries/{gid}").json() == {"ok": True}
        assert client.get(f"/api/series/{world['saga']}/shared-glossary").json() == {"glossary": None}
    with SessionLocal() as db:
        assert (
            db.scalars(select(SharedTerm)).all() == []
            and db.scalars(select(SeriesSharedGlossary)).all() == []
        )
        actions = [entry.action for entry in db.scalars(select(AuditEntry))]
    assert "series.shared_glossary_attached" in actions and "shared_glossary.deleted" in actions


def attach_universe(world, *terms: tuple[str, str, bool]) -> str:
    with SessionLocal() as db:
        glossary = SharedGlossary(owner_id=world["user"], name="Universe", normalized_name="universe")
        db.add(glossary)
        db.flush()
        for source, translation, locked in terms:
            db.add(SharedTerm(glossary_id=glossary.id, source=source, translation=translation, locked=locked))
        db.add(SeriesSharedGlossary(series_id=world["saga"], glossary_id=glossary.id))
        db.commit()
        return glossary.id


def test_precedence_book_then_series_then_shared_with_locked_terms_respected(world):
    attach_universe(
        world,
        ("Silver Tower", "Tour (univers)", True),  # beaten by the series decision
        ("Veilstone", "Pierre (univers)", True),  # beats the unlocked term of volume 1
        ("Moonwell", "Puits (univers)", False),  # beaten by the book's own unlocked term
        ("Ashen Guard", "Garde (univers)", True),  # beaten by the book's locked term
        ("Glass Order", "Ordre de verre", True),  # only in the universe
    )
    seed_book(world["v1"], ("Veilstone", "Pierre (vol. 1)", False))
    seed_book(world["v2"], ("Moonwell", "Puits (livre)", False), ("Ashen Guard", "Garde (livre)", True))
    with SessionLocal() as db:
        db.add(SeriesTerm(series_id=world["saga"], source="Silver Tower", translation="Tour (série)", origin="human",
                          accepted=True, locked=False))  # fmt: skip
        db.commit()
        project = db.get(Project, world["v2"])
        terms = {t["source"]: t for t in series_terms(db, prior_volumes(db, project), project)}
        assert terms["Silver Tower"]["translation"] == "Tour (série)"
        assert (terms["Veilstone"]["translation"], terms["Veilstone"]["origin"]) == (
            "Pierre (univers)",
            "shared_glossary",
        )
        assert terms["Glass Order"]["source_project"] == "Universe"
        enforced = {t.source: t for t in enforced_glossary(db, project)}
        assert (
            isinstance(enforced["Glass Order"], InheritedTerm)
            and enforced["Glass Order"].origin == "shared_glossary"
        )
        assert enforced["Ashen Guard"].translation == "Garde (livre)"
        assert "Moonwell" in enforced and enforced["Moonwell"].translation == "Puits (livre)"
    with TestClient(app) as client:
        login(client, "owner")
        effective = client.get(f"/api/projects/{world['v2']}/glossary/effective").json()
    assert effective["shared_glossary"]["name"] == "Universe"
    levels = {term["source"]: (term["level"], term["translation"]) for term in effective["terms"]}
    assert levels == {
        "Ashen Guard": ("book", "Garde (livre)"),
        "Glass Order": ("shared", "Ordre de verre"),
        "Moonwell": ("book", "Puits (livre)"),
        "Silver Tower": ("series", "Tour (série)"),
        "Veilstone": ("shared", "Pierre (univers)"),
    }
    veilstone = next(term for term in effective["terms"] if term["source"] == "Veilstone")
    assert veilstone["overridden"] == [
        {
            "level": "series",
            "translation": "Pierre (vol. 1)",
            "locked": False,
            "origin": "volume",
            "volume": 1,
        }
    ]


async def test_translation_context_offers_shared_terms_and_the_checks_enforce_the_locked_ones(world):
    from app.engines.context.builder import build_context
    from app.engines.quality.checks import checks, locked_term_error

    attach_universe(world, ("Moonwell", "Puits-de-Lune", True), ("Ashen Guard", "Garde cendrée", False))
    with SessionLocal() as db:
        segment = db.scalar(
            select(Segment).where(Segment.project_id == world["v1"], Segment.source.contains("Moonwell"))
        )
    built = await build_context(world["v1"], segment.id, "translation")
    terms = {t["source"]: t for t in built.inspector["mandatory"]["SERIES_CONVENTIONS"]["terms"]}
    assert (terms["Moonwell"]["translation"], terms["Moonwell"]["origin"]) == (
        "Puits-de-Lune",
        "shared_glossary",
    )
    assert terms["Ashen Guard"]["locked"] is False
    with SessionLocal() as db:
        glossary = enforced_glossary(db, db.get(Project, world["v1"]))
    wrong = [{"id": u["id"], "text": "Le puits brillait."} for u in segment.units]
    assert "Moonwell" in (locked_term_error(checks(segment.units, wrong, glossary, "en", "fr")) or "")
    right = [{"id": u["id"], "text": "Le Puits-de-Lune brillait."} for u in segment.units]
    assert locked_term_error(checks(segment.units, right, glossary, "en", "fr")) is None


def test_autopilot_withdraws_proposals_that_contradict_a_locked_shared_term(world):
    from app.engines.autopilot.memory import decide_glossary

    attach_universe(world, ("Moonwell", "Puits-de-Lune", True))
    with SessionLocal() as db:
        db.add(
            Glossary(project_id=world["v1"], source="Moonwell", translation="Source lunaire", accepted=False)
        )
        db.add(
            Glossary(project_id=world["v1"], source="Veilstone", translation="Pierre-voile", accepted=False)
        )
        job = Job(project_id=world["v1"], operation="analyze", status="analyzing", lease_owner="w",
                  lease_until=time.time() + 600)  # fmt: skip
        db.add(job)
        db.commit()
        db.refresh(job)
        db.expunge(job)
    decide_glossary(job, "w")
    with SessionLocal() as db:
        terms = {
            t.source: t.accepted
            for t in db.scalars(select(Glossary).where(Glossary.project_id == world["v1"]))
        }
        reasons = [
            d.reason for d in db.scalars(select(AutopilotDecision).where(AutopilotDecision.job_id == job.id))
        ]
    assert "Moonwell" not in terms
    [withdrawn] = [reason for reason in reasons if reason.startswith("« Moonwell »")]
    assert "glossaire partagé" in withdrawn and "Puits-de-Lune" in withdrawn
    assert not any("verrouillé" in reason for reason in reasons if reason.startswith("« Veilstone »"))


def test_series_glossary_import_records_human_decisions(world):
    with SessionLocal() as db:
        db.add(SeriesTerm(series_id=world["saga"], source="Veilstone", translation="Pierre", origin="volume",
                          accepted=True))  # fmt: skip
        db.commit()
    with TestClient(app) as client, TestClient(app) as stranger:
        login(client, "owner")
        login(stranger, "stranger")
        files = csv_file("source,translation,locked\nVeilstone,Pierre-voile,yes\nMoonwell,Puits,no\n")
        preview = client.post(f"/api/series/{world['saga']}/glossary/import/preview", files=files).json()
        assert preview["counts"]["conflicts"] == 1 and preview["conflicts"][0]["action"] == "keep"
        assert stranger.post(f"/api/series/{world['saga']}/glossary/import", files=files).status_code == 404
        result = client.post(
            f"/api/series/{world['saga']}/glossary/import", files=files, data={"strategy": "replace"}
        )
        assert result.json()["replaced"] == 1
        exported = client.get(f"/api/series/{world['saga']}/glossary/export/json").json()
    assert {term["source"]: term["translation"] for term in exported} == {
        "Moonwell": "Puits",
        "Veilstone": "Pierre-voile",
    }
    with SessionLocal() as db:
        assert {
            t.origin for t in db.scalars(select(SeriesTerm).where(SeriesTerm.series_id == world["saga"]))
        } == {"human"}


def test_automation_api_manages_shared_glossaries_with_token_scopes(world):
    with TestClient(app) as client:
        login(client, "owner")
        reader = client.post("/api/tokens", json={"name": "read", "scopes": ["series:read"]}).json()["token"]
        writer = client.post(
            "/api/tokens", json={"name": "write", "scopes": ["content:write", "series:read"]}
        ).json()["token"]
    with TestClient(app) as api:
        read, write = ({"Authorization": f"Bearer {token}"} for token in (reader, writer))
        refused = api.post("/api/v1/glossaries", json={"name": "Universe"}, headers=read)
        assert refused.status_code == 403 and refused.json()["detail"]["code"] == "insufficient_scope"
        created = api.post("/api/v1/glossaries", json={"name": "Universe"}, headers=write)
        assert created.status_code == 201
        gid = created.json()["id"]
        files = csv_file("source;traduction\nMoonwell;Puits-de-Lune\n")
        dry = api.post(f"/api/v1/glossaries/{gid}/import?dry_run=true", files=files, headers=write).json()
        assert dry["applied"] is False and dry["counts"]["new"] == 1
        assert api.get(f"/api/v1/glossaries/{gid}", headers=read).json()["term_count"] == 0
        done = api.post(f"/api/v1/glossaries/{gid}/import", files=files, headers=write).json()
        assert done["imported"] == 1
        attached = api.put(
            f"/api/v1/series/{world['saga']}/shared-glossary", json={"glossary_id": gid}, headers=write
        )
        assert attached.json()["glossary"]["term_count"] == 1
        assert (
            api.get(f"/api/v1/series/{world['saga']}/shared-glossary", headers=read).json()["glossary"]["id"]
            == gid
        )
        missing = api.get("/api/v1/glossaries/unknown", headers=read)
        assert missing.status_code == 404 and missing.json()["detail"]["code"] == "glossary_not_found"
        invalid = api.post(
            f"/api/v1/glossaries/{gid}/import",
            files=csv_file("source;valeur\nsword;epee\n"),
            headers={**write, "Accept-Language": "en"},
        )
        assert invalid.status_code == 422 and invalid.json()["detail"] == {
            "code": "invalid_glossary",
            "message": "Invalid CSV glossary: source and translation columns are expected.",
        }
        exported = api.get(f"/api/v1/glossaries/{gid}/export/csv", headers=read)
        assert exported.text.splitlines()[1].startswith("Moonwell,Puits-de-Lune")
