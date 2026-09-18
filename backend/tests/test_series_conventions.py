import time

from epubs import epub_bytes
from sqlalchemy import select

from app.db import SessionLocal
from app.engines.context.builder import build_context
from app.engines.context.series import enforced_glossary, human_choices
from app.engines.quality.checks import checks, locked_term_error
from app.engines.translation.versions import save_version
from app.models import Glossary, Memory, Project, Provider, Segment, User
from app.security import password_hash

TEXT = (
    "Alice entered the Silver Tower with the Veilstone and met the Ashen Guard near the Moonwell. "
    "The Glass Order was mentioned by Lord Hakon. Marker {tag}."
)


def volume(db, user, provider, tag, series, number, language="en"):
    from app.api.projects import import_book

    project = import_book(db, user.id, epub_bytes(f"<h1>Prologue</h1><p>{TEXT.format(tag=tag)}</p>", tag))
    project.title, project.series_name, project.volume_number = tag, series, number
    project.provider_id, project.context_backend, project.source_language = provider.id, "internal", language
    db.flush()
    return project


def term(db, project, source, translation, locked=False):
    db.add(Glossary(project_id=project.id, source=source, translation=translation, locked=locked, accepted=True))
    db.flush()


def setup_series():
    with SessionLocal() as db:
        user = User(username="u1", password_hash=password_hash("test-password-123456789"), admin=True)
        other = User(username="u2", password_hash=password_hash("test-password-123456789"))
        provider = Provider(name="Mock", base_url="https://llm.test/v1", model="m", capabilities={}, context_window=64000)
        db.add_all([user, other, provider])
        db.flush()
        v1 = volume(db, user, provider, "V1", "Saga", 1)
        v2 = volume(db, user, provider, "V2", " saga ", 2, "en-US")
        v3 = volume(db, user, provider, "V3", "SAGA", 3)
        v4 = volume(db, user, provider, "V4", "Saga", 4)
        foreign = volume(db, other, provider, "Other", "Saga", 1)
        term(db, v1, "Silver Tower", "Tour d’Argent", locked=True)
        time.sleep(0.01)
        term(db, v2, "Silver Tower", "Tour Argentée")
        term(db, v2, "Veilstone", "Pierre-voile")
        term(db, v4, "Ashen Guard", "Garde FUTURE", locked=True)
        term(db, foreign, "Lord Hakon", "Seigneur ÉTRANGER", locked=True)
        db.commit()
        return {p.title: p.id for p in (v1, v2, v3, v4, foreign)}


def passage(project_id):
    with SessionLocal() as db:
        return db.scalar(
            select(Segment.id).where(Segment.project_id == project_id, Segment.source.contains("Alice"))
        )


async def test_locked_term_of_an_earlier_volume_wins_and_matching_is_normalized():
    ids = setup_series()
    built = await build_context(ids["V3"], passage(ids["V3"]), "translation")
    conventions = built.inspector["mandatory"]["SERIES_CONVENTIONS"]
    terms = {t["source"]: (t["translation"], t["locked"], t["source_volume"]) for t in conventions["terms"]}
    # V2 is more recent but unlocked; "en-US" and " saga " still belong to the series.
    assert terms["Silver Tower"] == ("Tour d’Argent", True, 1)
    assert terms["Veilstone"] == ("Pierre-voile", False, 2)
    user = built.messages[1]["content"]
    assert "FUTURE" not in user and "ÉTRANGER" not in user


async def test_series_locked_terms_are_checked_in_the_output():
    ids = setup_series()
    with SessionLocal() as db:
        project = db.get(Project, ids["V3"])
        glossary = enforced_glossary(db, project)
        segment = db.get(Segment, passage(ids["V3"]))
        units = segment.units
    wrong = [{"id": u["id"], "text": u["text"].replace("Silver Tower", "Tour Argentée")} for u in units]
    assert "Silver Tower" in (locked_term_error(checks(units, wrong, glossary, "en", "fr")) or "")
    # Tolerance of the book checks applies: straight apostrophe for a curly one.
    right = [{"id": u["id"], "text": u["text"].replace("Silver Tower", "Tour d'Argent")} for u in units]
    assert locked_term_error(checks(units, right, glossary, "en", "fr")) is None


async def test_local_lock_overrides_the_series_and_hides_it():
    ids = setup_series()
    with SessionLocal() as db:
        term(db, db.get(Project, ids["V3"]), "Silver Tower", "Tour Argent", locked=True)
        db.commit()
    built = await build_context(ids["V3"], passage(ids["V3"]), "translation")
    assert "Silver Tower" not in {t["source"] for t in built.inspector["mandatory"]["SERIES_CONVENTIONS"]["terms"]}
    assert {"source": "Silver Tower", "translation": "Tour Argent"} in built.inspector["mandatory"]["LOCKED_GLOSSARY"]


async def test_human_corrections_of_an_earlier_volume_become_reusable_choices():
    ids = setup_series()
    with SessionLocal() as db:
        segment = db.get(Segment, passage(ids["V1"]))
        machine = [{"id": u["id"], "text": "Alice entra dans la Tour Argentée avec la Pierre."} for u in segment.units]
        save_version(db, segment.id, machine, "translation", segment.revision)
        db.refresh(segment)
        human = [{"id": u["id"], "text": "Alice entra dans la Tour d’Argent avec la Pierre."} for u in segment.units]
        assert save_version(db, segment.id, human, "human", segment.revision, validated=True)
        db.commit()
        memory = db.scalar(select(Memory).where(Memory.kind == "human_decision"))
        assert {"before": "Argentée", "after": "d’Argent"} in memory.content["choices"]
        assert "Silver Tower" in memory.content["anchors"]
    built = await build_context(ids["V3"], passage(ids["V3"]), "translation")
    decisions = built.inspector["mandatory"]["SERIES_CONVENTIONS"]["human_decisions"]
    assert decisions and decisions[0]["source_volume"] == 1
    assert {"before": "Argentée", "after": "d’Argent"} in decisions[0]["choices"]
    # Nothing flows backwards: volume 1 sees no series conventions at all.
    earlier = await build_context(ids["V1"], passage(ids["V1"]), "translation")
    assert earlier.inspector["mandatory"]["SERIES_CONVENTIONS"]["terms"] == []


def test_human_choices_ignore_rewritten_sentences():
    units = [{"id": "a", "text": "The Silver Tower fell."}]
    before = [{"id": "a", "text": "La Tour Argentée tomba."}]
    after = [{"id": "a", "text": "La Tour d’Argent s’effondra dans un fracas terrible et définitif, sans témoin."}]
    result = human_choices(units, before, after)
    assert all(len(c["before"]) < 60 for c in result.get("choices", []))
