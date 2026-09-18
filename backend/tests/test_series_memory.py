"""Series memory: identities across volumes, temporal barrier, OpenViking series space and its replay."""

import json

import respx
from epubs import epub_bytes
from sqlalchemy import select

from app.db import SessionLocal
from app.engines.context.builder import build_context
from app.engines.context.providers import InternalContextProvider, OpenVikingContextProvider
from app.engines.memory.events import canonical_events, ensure_events, layout_outdated, refresh_layouts
from app.engines.memory.store import remember
from app.engines.series.bible import refresh_series
from app.models import (
    AppSetting,
    Entity,
    Glossary,
    Memory,
    Outbox,
    Project,
    Provider,
    Segment,
    Series,
    SeriesEntity,
    SeriesEntityLink,
    SeriesTerm,
    User,
)
from app.providers.openviking import memory_event_uri, project_uri, search_root, series_uri
from app.security import password_hash

TEXT = "Alice met Kay near the Moonwell while the Ashen Guard watched. Marker {tag}."


def volume(db, user, provider, tag, number, series="Saga"):
    from app.api.projects import import_book

    project = import_book(db, user.id, epub_bytes(f"<h1>Prologue</h1><p>{TEXT.format(tag=tag)}</p>", tag))
    project.title, project.series_name, project.volume_number = tag, series, number
    project.provider_id, project.context_backend = provider.id, "hybrid"
    db.flush()
    return project


def character(db, project, name, aliases=(), position=0):
    db.add(
        Entity(
            project_id=project.id,
            name=name,
            category="character",
            data={"canonical_name": name, "aliases": list(aliases), "first_position": position, "role": "r"},
        )
    )
    db.flush()


def setup():
    with SessionLocal() as db:
        user = User(username="u1", password_hash=password_hash("test-password-123456789"), admin=True)
        provider = Provider(name="M", base_url="https://llm.test/v1", model="m", capabilities={}, context_window=64000)
        db.add_all([user, provider])
        db.flush()
        v1, v2, v3 = (volume(db, user, provider, f"V{n}", n) for n in (1, 2, 3))
        character(db, v1, "Alice Moreau", ["Alice"])
        character(db, v2, "Alice")
        character(db, v3, "Kay the Late", ["Kay"])
        db.add(Glossary(project_id=v3.id, source="Ashen Guard", translation="Garde FUTURE", locked=True, accepted=True))
        db.add(Glossary(project_id=v1.id, source="Moonwell", translation="Puits-de-Lune", locked=True, accepted=True))
        db.commit()
        return {p.title: p.id for p in (v1, v2, v3)}, user.id


def passage(project_id):
    with SessionLocal() as db:
        return db.scalar(select(Segment.id).where(Segment.project_id == project_id, Segment.source.contains("Alice")))


def test_identities_link_across_volumes_without_merging_ambiguous_names():
    ids, _ = setup()
    with SessionLocal() as db:
        series_id = db.get(Project, ids["V1"]).series_id
        refresh_series(db, series_id)
        db.commit()
        alice = db.scalar(select(SeriesEntity).where(SeriesEntity.name == "Alice Moreau"))
        links = db.scalars(select(SeriesEntityLink).where(SeriesEntityLink.series_entity_id == alice.id)).all()
        assert {link.project_id for link in links if link.status == "linked"} == {ids["V1"], ids["V2"]}
        assert alice.first_volume_number == 1
        # Two series identities answer to "Kay": a later volume's "Kay" stays a proposal for both.
        db.add(SeriesEntity(series_id=series_id, name="Kay Rowan", category="character", aliases=["Kay"], data={}))
        db.add(SeriesEntity(series_id=series_id, name="Kay Stone", category="character", aliases=["Kay"], data={}))
        v2 = db.get(Project, ids["V2"])
        character(db, v2, "Kay")
        db.commit()
        refresh_series(db, series_id)
        db.commit()
        kay = db.scalar(select(Entity).where(Entity.project_id == ids["V2"], Entity.name == "Kay"))
        statuses = {link.status for link in db.scalars(select(SeriesEntityLink).where(SeriesEntityLink.entity_id == kay.id))}
        assert statuses == {"proposed"}
        assert db.scalar(select(SeriesEntity).where(SeriesEntity.name == "Kay")) is None
        bible = db.get(Series, series_id).bible
        assert {c["name"] for c in bible["characters"]} >= {"Alice Moreau", "Kay Rowan", "Kay Stone"}


async def test_a_volume_sees_earlier_volumes_only():
    ids, _ = setup()
    with SessionLocal() as db:
        refresh_series(db, db.get(Project, ids["V1"]).series_id)
        series_id = db.get(Project, ids["V1"]).series_id
        db.add(SeriesTerm(series_id=series_id, source="Kay", translation="Kaï", locked=True, accepted=True, origin="human"))
        db.commit()
    built = await build_context(ids["V2"], passage(ids["V2"]), "translation")
    conventions = built.inspector["mandatory"]["SERIES_CONVENTIONS"]
    terms = {t["source"]: t["translation"] for t in conventions["terms"]}
    assert terms == {"Moonwell": "Puits-de-Lune", "Kay": "Kaï"}
    identities = {i["canonical_name"]: i["aliases"] for i in conventions["known_identities"]}
    assert identities == {"Alice Moreau": ["Alice"]}
    content = built.messages[1]["content"]
    assert "FUTURE" not in content and "Kay the Late" not in content


async def test_a_volume_override_wins_over_a_locked_series_term():
    ids, _ = setup()
    with SessionLocal() as db:
        db.add(Glossary(project_id=ids["V2"], source="Moonwell", translation="Source-Lune", accepted=True, series_override=True))
        db.commit()
    built = await build_context(ids["V2"], passage(ids["V2"]), "translation")
    mandatory = built.inspector["mandatory"]
    assert {t["source"] for t in mandatory["SERIES_CONVENTIONS"]["terms"]} == set()
    with SessionLocal() as db:
        from app.engines.context.series import enforced_glossary

        enforced = {term.source: term.translation for term in enforced_glossary(db, db.get(Project, ids["V2"]))}
        assert enforced == {"Moonwell": "Source-Lune"}


def memories():
    ids, _ = setup()
    with SessionLocal() as db:
        found = {}
        for tag, summary in (("V1", "Alice lost the pendant"), ("V2", "Alice found a key"), ("V3", "Kay betrayed Alice")):
            project = db.get(Project, ids[tag])
            segment = db.scalar(select(Segment).where(Segment.project_id == project.id).order_by(Segment.position.desc()))
            remember(db, project, segment, {"summary": summary}, "analysis")
            found[tag] = db.scalar(select(Memory).where(Memory.project_id == project.id)).id
        db.add(AppSetting(key="openviking", value={"base_url": "https://memory.test", "root_uri": "viking://resources/t"}))
        db.commit()
    return ids, found


async def test_internal_retrieval_reads_earlier_volumes_and_never_later_ones():
    ids, _ = memories()
    with SessionLocal() as db:
        project = db.get(Project, ids["V2"])
    items = await InternalContextProvider().retrieve(project, "Alice pendant key betrayed", 0)
    texts = " ".join(item.content for item in items)
    assert "lost the pendant" in texts and "betrayed" not in texts
    # The passage's own later analysis is not visible at position 0 either.
    assert "found a key" not in texts


@respx.mock
async def test_openviking_series_search_admits_earlier_volumes_only():
    ids, found = memories()
    with SessionLocal() as db:
        v1, v2, v3 = (db.get(Project, ids[t]) for t in ("V1", "V2", "V3"))
        documents = canonical_events(
            db, [db.get(Memory, found[t]) for t in ("V1", "V3")], {v1.id: v1, v3.id: v3}
        )
    earlier, later = memory_event_uri(v1, found["V1"]), memory_event_uri(v3, found["V3"])
    assert earlier.startswith(series_uri(v1.owner_id, v1.series_id) + "/volumes/")
    search = respx.post("https://memory.test/api/v1/search/find").respond(
        200, json={"status": "ok", "result": {"resources": [{"uri": earlier, "score": 0.9}, {"uri": later, "score": 0.99}]}}
    )
    respx.get("https://memory.test/api/v1/content/read").respond(
        200, json={"status": "ok", "result": json.dumps(documents[found["V1"]])}
    )
    provider = OpenVikingContextProvider()
    items = await provider.retrieve(v2, "Alice pendant", 0)
    assert [item.origin for item in items] == [earlier]
    assert json.loads(search.calls[0].request.content)["target_uri"] == search_root(v2)
    assert provider.trace["rejected"] == [{"uri": later, "score": 0.99, "reason": "outside_narrative_allowlist"}]


def test_moving_a_volume_or_upgrading_from_05_replays_events_without_remote_deletion():
    ids, found = memories()
    with SessionLocal() as db:
        v1 = db.get(Project, ids["V1"])
        # State left by 0.5: written at the old place, without metadata; one row purged by the retention.
        row = db.scalar(select(Outbox).where(Outbox.event_key == found["V1"]))
        row.status, row.uri, row.payload = "sent", None, {"type": "analysis", "position": 0, "content": {}}
        db.delete(db.scalar(select(Outbox).where(Outbox.event_key == found["V2"])))
        db.commit()
    assert refresh_layouts() >= 2
    with SessionLocal() as db:
        v1 = db.get(Project, ids["V1"])
        row = db.scalar(select(Outbox).where(Outbox.event_key == found["V1"]))
        assert row.status == "pending" and row.payload["series_id"] == v1.series_id
        assert db.scalar(select(Outbox).where(Outbox.event_key == found["V2"])) is not None
        assert db.get(AppSetting, "openviking_layout").value == {"version": 2}
        # Written at its new place, then the volume leaves the series: its space moves again.
        row.status, row.uri = "sent", memory_event_uri(v1, found["V1"])
        db.commit()
        assert not layout_outdated(db, v1)
        v1.series_id = None
        db.flush()
        assert project_uri(v1).endswith(f"/standalone/{v1.id}")
        assert layout_outdated(db, v1)
        assert ensure_events(db, v1) >= 1
