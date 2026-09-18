import json

from sqlalchemy import select

from app.db import SessionLocal
from app.engines.context.builder import build_context, fit_neighbors
from app.models import Entity, Glossary, Segment


def test_a_long_neighbour_is_shortened_not_dropped():
    values = [{"segment_id": "a", "source": "s" * 5000, "translation": "t" * 5000, "human_validated": False}]
    before = fit_neighbors(values, 3000, previous=True)
    assert before and before[0]["excerpt"] and len(json.dumps(before)) < 3000
    assert values[0]["source"] == "s" * 5000  # the caller's data is left alone
    numbered = [{"segment_id": "n", "source": "".join(f"{i:05d}" for i in range(1000))}]
    assert fit_neighbors(numbered, 2000, previous=True)[0]["source"].endswith("00999")
    assert fit_neighbors(numbered, 2000, previous=False)[0]["source"].startswith("00000")
    assert fit_neighbors(numbered, 40, previous=False) == []


def test_the_closest_neighbours_are_kept_first():
    values = [{"segment_id": str(i), "source": "x" * 1000} for i in range(3)]
    assert [v["segment_id"] for v in fit_neighbors(values, 2300, previous=True)] == ["1", "2"]
    assert [v["segment_id"] for v in fit_neighbors(values, 2300, previous=False)] == ["0", "1"]


async def test_large_neighbours_leave_room_for_characters_and_glossary(seeded):
    pid = seeded[0]
    with SessionLocal() as db:
        segments = db.scalars(select(Segment).where(Segment.project_id == pid).order_by(Segment.position)).all()
        target = segments[len(segments) // 2]
        for other in segments:
            if other.id != target.id:
                other.source = "Filler narration about nothing in particular. " * 150
                other.translation = "Narration de remplissage sans importance. " * 150
        target.source = "Bell met Hestia near the Guild."
        target.units = [{**target.units[0], "text": target.source}] + target.units[1:]
        db.add(Entity(project_id=pid, category="character", name="Hestia", validated=True, data={"role": "goddess"}))
        db.add(Glossary(project_id=pid, source="Guild", translation="Guilde", accepted=True, locked=False))
        db.commit()
        sid = target.id
    built = await build_context(pid, sid, "translation")
    kept = {item["source"] for item in built.inspector["selected"]}
    assert {"PREVIOUS_CONTEXT", "EDITORIAL_CHARACTERS", "GLOSSARY"} <= kept
    neighbours = sum(
        i["tokens_estimate"] for i in built.inspector["selected"] if i["source"].endswith("_CONTEXT")
        and i["source"] in ("PREVIOUS_CONTEXT", "NEXT_CONTEXT")
    )
    assert neighbours <= 12000 * 3 // 5
    prompt = sum(len(m["content"].encode()) for m in built.messages)
    assert abs(built.inspector["input_estimate"] - prompt) < 1500


def test_lexical_relevance_ignores_the_instruction_of_the_retrieval_query():
    from app.engines.context.builder import context_query
    from app.engines.context.providers import relevance

    query = context_query("Bell raised the dagger toward the minotaur.", "[]", ["Bell"])
    template_echo = "Earlier relationships between characters; objects mentioned; what is known now."
    about_the_passage = "Hestia forged the dagger for Bell before the minotaur appeared."
    assert relevance(template_echo, query) == 0
    assert relevance(about_the_passage, query) > 0.3
    assert relevance("dagger", "a plain query about a dagger") == 1  # queries without the template still work
