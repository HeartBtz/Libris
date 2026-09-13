from sqlalchemy import select

from app.db import SessionLocal
from app.engines.context.builder import build_context
from app.engines.memory.identities import identities, merge, resolve, upsert_profiles
from app.engines.memory.relations import backfill, collect
from app.models import CharacterRelation, Entity, EntityMerge, Project, Segment


def test_explicit_alias_reuses_identity_and_keeps_canonical_name(seeded):
    pid = seeded[0]
    with SessionLocal() as db:
        upsert_profiles(
            db, pid, [{"canonical_name": "Rudy", "aliases": [], "description": "Reincarnated child"}], 1
        )
        upsert_profiles(db, pid, [{"canonical_name": "Rudeus Greyrat", "aliases": ["Rudy", "Rudeus"]}], 2)
        db.commit()
        rows = identities(db, pid)
        assert len(rows) == 1 and rows[0].name == "Rudeus Greyrat"
        assert resolve(db, pid, "Rudy").id == resolve(db, pid, "Rudeus").id
        assert rows[0].data["description"] == "Reincarnated child"


def test_similar_names_alone_never_merge(seeded):
    with SessionLocal() as db:
        upsert_profiles(db, seeded[0], [{"canonical_name": "Rudy"}, {"canonical_name": "Rudeus"}], 0)
        assert len(identities(db, seeded[0])) == 2


def test_human_merge_does_not_confirm_inherited_model_alias_noise(seeded):
    pid = seeded[0]
    with SessionLocal() as db:
        upsert_profiles(
            db,
            pid,
            [{"canonical_name": "Rudeus"}, {"canonical_name": "Rudy"}, {"canonical_name": "Sylph"}],
            0,
        )
        target = resolve(db, pid, "Rudeus")
        target.data = {**target.data, "aliases": ["I", "Sylph", "Sylph’s friend", "Ruru"]}
        source = db.scalar(select(Entity).where(Entity.project_id == pid, Entity.name == "Rudy"))
        target = merge(db, pid, target.id, [source.id], "User confirms Rudy = Rudeus", True)
        assert target.data["aliases"] == ["Rudy"]
        upsert_profiles(db, pid, [{"canonical_name": "Rudeus", "aliases": ["Sylph", "I", "Ruru"]}], 3)
        assert target.data["aliases"] == ["Rudy"]
        assert "Ruru" in target.data["proposed_aliases"]
        assert resolve(db, pid, "Sylph").id != target.id


async def test_human_merge_preserves_provenance_rewires_graph_and_injects_aliases(seeded):
    pid = seeded[0]
    with SessionLocal() as db:
        upsert_profiles(
            db, pid, [{"canonical_name": n} for n in ["Rudy", "Rudeus", "Rudeus Greyrat", "Paul"]], 0
        )
        people = {e.name: e for e in identities(db, pid)}
        db.add(
            CharacterRelation(
                project_id=pid,
                source_id=people["Rudy"].id,
                target_id=people["Paul"].id,
                relation_type="child_of",
                description="Son of Paul",
            )
        )
        target = merge(
            db,
            pid,
            people["Rudeus Greyrat"].id,
            [people["Rudy"].id, people["Rudeus"].id],
            "Rudy et Rudeus sont la même personne.",
            True,
        )
        db.commit()
        assert len(identities(db, pid)) == 2
        assert target.identity_validated and not target.validated
        assert db.get(Entity, people["Rudy"].id).merged_into_id == target.id
        assert db.scalar(select(EntityMerge)).snapshots
        assert db.scalar(select(CharacterRelation)).source_id == target.id
        upsert_profiles(db, pid, [{"canonical_name": "Rudy", "aliases": []}], 4)
        assert len(identities(db, pid)) == 2
        segment = db.scalar(select(Segment).where(Segment.project_id == pid).order_by(Segment.position))
        segment.source = "Rudy entered the room."
        db.commit()
        sid = segment.id
    built = await build_context(pid, sid)
    confirmed = built.inspector["mandatory"]["CONFIRMED_IDENTITIES"]
    assert confirmed[0]["canonical_name"] == "Rudeus Greyrat" and "Rudy" in confirmed[0]["aliases"]


def test_relationship_evidence_checked_against_source(seeded):
    pid = seeded[0]
    with SessionLocal() as db:
        upsert_profiles(db, pid, [{"canonical_name": "Alice"}, {"canonical_name": "Bob"}], 0)
        segment = db.scalar(select(Segment).where(Segment.project_id == pid))
        collect(
            db,
            pid,
            [
                {
                    "source": "Alice",
                    "target": "Bob",
                    "relation_type": "friend_of",
                    "evidence": "A fabricated quote",
                }
            ],
            segment,
        )
        db.flush()
        edge = db.scalar(select(CharacterRelation))
        assert edge.evidence == "" and not edge.validated


def test_legacy_relationship_links_are_unvalidated_suggestions(seeded):
    pid = seeded[0]
    with SessionLocal() as db:
        upsert_profiles(
            db,
            pid,
            [{"canonical_name": "Rudy", "relationships": ["Son of Paul"]}, {"canonical_name": "Paul"}],
            0,
        )
        assert backfill(db, pid) == 1
        assert backfill(db, pid) == 0
        edge = db.scalar(select(CharacterRelation))
        assert edge.provenance == "profile_suggestion" and not edge.validated


def test_project_export_reimport_keeps_canonical_identities_and_edges(seeded):
    from fastapi.testclient import TestClient

    from app.main import app

    pid = seeded[0]
    with SessionLocal() as db:
        db.get(Project, pid).bible = {"summary": "Test"}
        upsert_profiles(
            db, pid, [{"canonical_name": "Rudy"}, {"canonical_name": "Rudeus"}, {"canonical_name": "Paul"}], 0
        )
        people = {e.name: e for e in identities(db, pid)}
        target = merge(db, pid, people["Rudeus"].id, [people["Rudy"].id], "Confirmed", True)
        db.add(
            CharacterRelation(
                project_id=pid,
                source_id=target.id,
                target_id=people["Paul"].id,
                relation_type="child_of",
                validated=True,
                provenance="human",
            )
        )
        db.commit()
    with TestClient(app) as client:
        assert (
            client.post(
                "/api/auth/login", json={"username": "tester", "password": "test-password-123456789"}
            ).status_code
            == 200
        )
        archive = client.get(f"/api/projects/{pid}/export/project")
        result = client.post("/api/projects/import", files={"file": ("project.zip", archive.content)})
        assert result.status_code == 201, result.text
        graph = client.get(f"/api/projects/{result.json()['id']}/characters/graph").json()
        assert len(graph["nodes"]) == 2 and len(graph["edges"]) == 1
        rudeus = next(n for n in graph["nodes"] if n["name"] == "Rudeus")
        assert rudeus["identity_validated"] and "Rudy" in rudeus["data"]["aliases"]
        assert graph["edges"][0]["source_id"] == rudeus["id"]
        assert len(graph["merges"]) == 1
