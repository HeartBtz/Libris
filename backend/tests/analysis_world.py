"""A synthetic serial with known ground truth, a simulated analyst, and the scores of an analysis.

Used by the tests of the analysis modes (`test_parallel_analysis.py`) and by the evaluation and
benchmark scripts (`scripts/evaluate_analysis_modes.py`, `scripts/benchmark_analysis.py`).

The world: a handful of characters met under several names. Some links are only told late:

* `Graymask` is a separate masked figure until the reveal passage says it was Mira Voss;
* `Little Fox` is Ren's nickname from the passage that says so, used alone afterwards;
* Bob calls himself `Lord Robert` from a given passage on (in the second volume of a series);
* `Mira`/`Ilse` are the short forms of `Mira Voss`/`Ilse Brandt`, used once the full name is known.

Passages that only say "She…"/"He…" refer to the last character of that gender named before them,
sometimes several passages back or across a chapter break.

The simulated analyst stands in for the model. It is deterministic and knows only what its prompt
holds: names are recognised (perfect named-entity recognition, gender known from the name), but an
identity link or a pronoun referent is only made from the passage itself or from the context sections
it received. It therefore measures what information each analysis mode delivers to each call, not the
intelligence of a real model.
"""

from __future__ import annotations

import json
import random
import re
from dataclasses import dataclass, field

# ---------------------------------------------------------------- the world

GENDER = {
    "Mira Voss": "f",
    "Mira": "f",
    "Graymask": "f",
    "Ilse Brandt": "f",
    "Ilse": "f",
    "Sister Ana": "f",
    "Ren": "m",
    "Little Fox": "m",
    "Bob": "m",
    "Lord Robert": "m",
    "Tomas": "m",
    "Captain Hale": "m",
}
TRUTH = {
    "Mira Voss": "mira",
    "Mira": "mira",
    "Graymask": "mira",
    "Ilse Brandt": "ilse",
    "Ilse": "ilse",
    "Sister Ana": "ana",
    "Ren": "ren",
    "Little Fox": "ren",
    "Bob": "bob",
    "Lord Robert": "bob",
    "Tomas": "tomas",
    "Captain Hale": "hale",
}
NAMES = sorted(GENDER, key=len, reverse=True)  # longest first: "Mira Voss" before "Mira"
TERMS = {
    "Jade Gate": "Porte de Jade",
    "Ember Guild": "Guilde des Braises",
    "Silent Tide": "Marée silencieuse",
    "Moon Well": "Puits de lune",
    "Salt Road": "Route du sel",
}
RELATIONS = ("brother", "sister", "teacher", "friend")

NAMED = [
    "{a} crossed the {term} before dawn.",
    "{a} spoke with {b} near the {term}.",
    "{a} waited for {b} in the rain.",
    "{a} counted the coins twice.",
    "{a} laughed at the old song.",
]
PLAIN = [
    "The wind turned cold over the roofs.",
    "Nobody said a word for a long time.",
    "Lanterns swayed above the market.",
]
PRONOUN = [
    "{p} did not answer.",
    "{p} folded the letter and hid it.",
    "{p} listened to the bells until they stopped.",
    "{p} was too tired to argue.",
]


@dataclass
class Passage:
    text: str
    present: set[str]  # truth ids named or referred to by a pronoun
    named: list[str]  # surface names in text order
    referent: str | None = None  # truth id of the pronoun-only passage
    pronoun_distance: int = 0  # passages back to the last naming of the referent (0: named here)


@dataclass
class Volume:
    number: int
    chapters: list[list[Passage]]


@dataclass
class World:
    volumes: list[Volume]
    # (name a, name b) -> global passage index from which the text says they are the same person
    links: dict[frozenset, int] = field(default_factory=dict)
    relations: set[tuple[str, str, str]] = field(default_factory=set)
    terms: dict[str, str] = field(default_factory=dict)

    def passages(self) -> list[Passage]:
        return [p for v in self.volumes for chapter in v.chapters for p in chapter]


def build_world(
    chapters: int = 24, volumes: int = 1, per_chapter: tuple[int, int] = (2, 3), seed: int = 7
) -> World:
    """`chapters` per volume. Events are placed at fixed fractions of the whole serial."""
    rng = random.Random(seed)
    counts = [[rng.randint(*per_chapter) for _ in range(chapters)] for _ in range(volumes)]
    total = sum(sum(c) for c in counts)
    graymask_from, reveal = int(total * 0.12), int(total * 0.55)
    nickname = int(total * 0.25)
    rename = int(total * 0.62) if volumes > 1 else int(total * 0.45)
    world = World([])
    known: set[str] = set()  # full names already written: short forms may follow
    last = {"f": None, "m": None}  # (truth id, index) of the last naming per gender
    index = 0

    def surface(identity: str) -> str:
        if identity == "mira":
            if index < reveal and index >= graymask_from and rng.random() < 0.5:
                return "Graymask"
            return "Mira" if "Mira Voss" in known and rng.random() < 0.5 else "Mira Voss"
        if identity == "ilse":
            return "Ilse" if "Ilse Brandt" in known and rng.random() < 0.5 else "Ilse Brandt"
        if identity == "ren":
            return "Little Fox" if index > nickname and rng.random() < 0.45 else "Ren"
        if identity == "bob":
            return "Lord Robert" if index > rename and rng.random() < 0.6 else "Bob"
        return {"ana": "Sister Ana", "tomas": "Tomas", "hale": "Captain Hale"}[identity]

    def link(a: str, b: str, at: int) -> None:
        world.links.setdefault(frozenset((a, b)), at)

    cast = ["mira", "ilse", "ren", "bob", "tomas", "hale", "ana"]
    run = None
    for volume_index in range(volumes):
        volume = Volume(volume_index + 1, [])
        for chapter_size in counts[volume_index]:
            chapter: list[Passage] = []
            for _ in range(chapter_size):
                sentences: list[str] = []
                named: list[str] = []
                present: set[str] = set()
                referent = None
                distance = 0
                if index == reveal:
                    sentences.append("Graymask lowered the hood: it was Mira Voss.")
                    named += ["Graymask", "Mira Voss"]
                    link("Graymask", "Mira Voss", index)
                elif index == nickname:
                    sentences.append("Ren, whom everyone called Little Fox, slipped through the crowd.")
                    named += ["Ren", "Little Fox"]
                    link("Ren", "Little Fox", index)
                elif index == rename:
                    sentences.append("From that day on, Bob called himself Lord Robert.")
                    named += ["Bob", "Lord Robert"]
                    link("Bob", "Lord Robert", index)
                kind = rng.random()
                pronoun_gender = rng.choice(["f", "m"])
                if run:
                    # A scene told in pronouns often goes on for several passages.
                    pronoun_gender, kind = run, 0.0
                run = None
                if not named and kind < 0.15 and last[pronoun_gender]:
                    if rng.random() < 0.4:
                        run = pronoun_gender
                    who, at = last[pronoun_gender]
                    word = "She" if pronoun_gender == "f" else "He"
                    sentences += [rng.choice(PRONOUN).format(p=word) for _ in range(rng.randint(2, 3))]
                    referent, distance = who, index - at
                    present.add(who)
                else:
                    for _ in range(rng.randint(1, 3)):
                        a, b = rng.sample(cast, 2)
                        sa, sb = surface(a), surface(b)
                        term = rng.choice(list(TERMS)) if rng.random() < 0.35 else "square"
                        template = rng.choice(NAMED)
                        sentences.append(template.format(a=sa, b=sb, term=term))
                        named.append(sa)
                        if "{b}" in template:
                            named.append(sb)
                        if term in TERMS:
                            world.terms[term] = TERMS[term]
                    if rng.random() < 0.15:
                        a, b = rng.sample(cast, 2)
                        sa, sb = surface(a), surface(b)
                        relation = rng.choice(RELATIONS)
                        sentences.append(f"{sa} is the {relation} of {sb}.")
                        named += [sa, sb]
                        world.relations.add((TRUTH[sa], TRUTH[sb], f"{relation}_of"))
                    if rng.random() < 0.3:
                        sentences.append(rng.choice(PLAIN))
                for name in named:
                    present.add(TRUTH[name])
                    if name in ("Mira Voss", "Ilse Brandt"):
                        known.add(name)
                        short = name.split()[0]
                        link(name, short, index)
                    last[GENDER[name]] = (TRUTH[name], index)
                chapter.append(Passage(" ".join(sentences), present, named, referent, distance))
                index += 1
            volume.chapters.append(chapter)
        world.volumes.append(volume)
    # Transitive links (Graymask ~ Mira through Mira Voss) are known once both of their links are.
    for a in GENDER:
        for b in GENDER:
            if a < b and TRUTH[a] == TRUTH[b] and frozenset((a, b)) not in world.links:
                via = [
                    max(world.links[frozenset((a, c))], world.links[frozenset((c, b))])
                    for c in GENDER
                    if c not in (a, b)
                    and frozenset((a, c)) in world.links
                    and frozenset((c, b)) in world.links
                ]
                if via:
                    world.links[frozenset((a, b))] = min(via)
    return world


# ---------------------------------------------------------------- the simulated analyst


def sections(text: str) -> dict[str, object]:
    found = {}
    for name, body in re.findall(r"<([A-Z_]+)>\n(.*?)\n</\1>", text, re.S):
        body = body.replace("\\u003c", "<").replace("\\u003e", ">")
        try:
            found[name] = json.loads(body)
        except ValueError:
            found[name] = body
    return found


def names_in(text: str) -> list[str]:
    """Surface names in text order, longest match first."""
    found = []
    pattern = "|".join(re.escape(n) for n in NAMES)
    for match in re.finditer(rf"(?<!\w)({pattern})(?!\w)", text):
        found.append(match.group(1))
    return found


def identity_records(context: dict) -> list[list[str]]:
    """Every identity the prompt shows, as lists of names, wherever it comes from."""
    records: list[list[str]] = []

    def add(item):
        if isinstance(item, dict) and item.get("canonical_name"):
            records.append([item["canonical_name"], *(item.get("aliases") or [])])

    for key in ("CHARACTER_REGISTRY", "CONFIRMED_IDENTITIES", "KNOWN_IDENTITIES", "EDITORIAL_CHARACTERS"):
        value = context.get(key)
        for item in value if isinstance(value, list) else [value]:
            add(item)
    conventions = context.get("SERIES_CONVENTIONS")
    if isinstance(conventions, dict):
        for item in conventions.get("known_identities") or []:
            add(item)
    return records


def summary_fields(summary: str) -> dict[str, str]:
    return dict(re.findall(r"(Woman|Man): ([^.]+)\.", summary or ""))


class Analyst:
    """Answers chapter analyses and book syntheses from the prompt alone."""

    def __init__(self):
        self.calls: dict[str, int] = {}

    def answer(self, schema: str, messages: list[dict]) -> dict:
        text = "\n".join(m["content"] for m in messages if m["role"] == "user")
        context = sections(text)
        if schema == "ChapterAnalysis":
            return self.analysis(context)
        return {"summary": "Synthetic overview.", "tone": "Plain", "translation_guidelines": ["Keep names."]}

    def analysis(self, context: dict) -> dict:
        target = " ".join(u["text"] for u in context.get("TARGET_TEXT") or [])
        # Union-find of names: what the prompt links, then what the passage says.
        parent: dict[str, str] = {}

        def find(name):
            parent.setdefault(name, name)
            while parent[name] != name:
                parent[name] = parent[parent[name]]
                name = parent[name]
            return name

        def union(a, b):
            ra, rb = find(a), find(b)
            if ra != rb:
                # The name met first (a registry canonical, a full name) stays the root.
                parent[rb] = ra

        for record in identity_records(context):
            find(record[0])
            for name in record[1:]:
                union(record[0], name)
        previous = context.get("PREVIOUS_CONTEXT") or []
        previous_text = " ".join(p.get("source", "") for p in previous if isinstance(p.get("source"), str))
        for source in (previous_text, target):
            if m := re.search(r"(\w[\w ]*?) lowered the hood: it was ([\w ]+)\.", source):
                union(m.group(2), m.group(1))
            if m := re.search(r"(\w[\w ]*?), whom everyone called ([\w ]+?),", source):
                union(m.group(1), m.group(2))
            if m := re.search(r"(\w[\w ]*?) called himself ([\w ]+)\.", source):
                union(m.group(1), m.group(2))
        # A short form joins the full name the prompt or the passage shows.
        visible = set(parent) | set(names_in(target)) | set(names_in(previous_text))
        for short in [n for n in visible if " " not in n]:
            for full in visible:
                if (
                    " " in full
                    and full.split()[0] == short
                    and not full.startswith(("Sister", "Captain", "Lord"))
                ):
                    union(full, short)
        named = names_in(target)
        # Pronoun-only passage: the last character of that gender named before it.
        referent = None
        pronoun = re.match(r"(She|He) ", target)
        if not named and pronoun:
            gender = "f" if pronoun.group(1) == "She" else "m"
            referent = next((n for n in reversed(names_in(previous_text)) if GENDER[n] == gender), None)
            if referent is None:
                key = "Woman" if gender == "f" else "Man"
                summaries = []
                chapter = context.get("EDITORIAL_CHAPTER_CONTEXT")
                if isinstance(chapter, dict):
                    summaries.append(chapter.get("summary", ""))
                for item in reversed(context.get("EARLIER_PASSAGES") or []):
                    summaries.append(item.get("summary", ""))
                for summary in summaries:
                    if value := summary_fields(summary).get(key):
                        referent = value
                        break
            if referent is None:
                for item in context.get("RECENT_CHARACTERS") or []:
                    if (item.get("gender") or " ")[0] == gender:
                        referent = item["canonical_name"]
                        break
        people: dict[str, set[str]] = {}
        for name in [*named, *([referent] if referent else [])]:
            people.setdefault(find(name), set()).add(name)
        characters = [
            {
                "canonical_name": root,
                "aliases": sorted(n for n in names if n != root),
                "gender": {"f": "female", "m": "male"}.get(GENDER.get(root, ""), ""),
            }
            for root, names in people.items()
        ]
        relationships = [
            {
                "source": find(m.group(1).strip()),
                "target": find(m.group(3)),
                "relation_type": f"{m.group(2)}_of",
                "evidence": m.group(0),
            }
            for m in re.finditer(r"([\w ]+?) is the (\w+) of ([\w ]+?)\.", target)
            if m.group(1).strip() in GENDER and m.group(3) in GENDER
        ]
        terms = [
            {"source": term, "translation": translation, "category": "lieu"}
            for term, translation in TERMS.items()
            if term in target
        ]
        # Rolling summary: the last woman and man named (or resolved) so far in the chapter.
        fields = {}
        chapter = context.get("EDITORIAL_CHAPTER_CONTEXT")
        if isinstance(chapter, dict):
            fields.update(summary_fields(chapter.get("summary", "")))
        for name in [*named, *([referent] if referent else [])]:
            fields["Woman" if GENDER[name] == "f" else "Man"] = find(name)
        summary = f"Named: {', '.join(dict.fromkeys(named)) or 'nobody'}." + "".join(
            f" {key}: {value}." for key, value in sorted(fields.items())
        )
        events = (
            []
            if not pronoun or referent
            else [{"text": "Unresolved pronoun referent.", "kind": "unresolved"}]
        )
        return {
            "summary": summary,
            "events": events,
            "characters": characters,
            "terms": terms,
            "style_notes": [],
            "relationships": relationships,
        }


# ---------------------------------------------------------------- scores


def ratio(hits: int, total: int) -> float:
    return round(hits / total, 4) if total else 1.0


def score(world: World, project_ids: list[str], debug: bool = False) -> dict:
    """Precision and recall of the memory an analysis left, against the ground truth."""
    from sqlalchemy import select

    from app.db import SessionLocal
    from app.models import CharacterRelation, Entity, Glossary, Memory, RequestLog, Segment

    passages = world.passages()
    with SessionLocal() as db:
        segments = []
        for pid in project_ids:
            segments += list(
                db.scalars(select(Segment).where(Segment.project_id == pid).order_by(Segment.position))
            )
        index_of = {s.id: i for i, s in enumerate(segments)}
        memories = {
            m.segment_id: m.content
            for m in db.scalars(
                select(Memory).where(Memory.project_id.in_(project_ids), Memory.kind == "analysis")
            )
        }
        entities = list(
            db.scalars(
                select(Entity).where(
                    Entity.project_id.in_(project_ids),
                    Entity.category == "character",
                    Entity.merged_into_id.is_(None),
                )
            )
        )
        relations = list(
            db.scalars(
                select(CharacterRelation).where(
                    CharacterRelation.project_id.in_(project_ids), CharacterRelation.active.is_(True)
                )
            )
        )
        glossary = list(db.scalars(select(Glossary).where(Glossary.project_id.in_(project_ids))))
        logs = [
            (log.segment_id, log.messages)
            for log in db.scalars(
                select(RequestLog).where(
                    RequestLog.project_id.in_(project_ids),
                    RequestLog.operation.in_(
                        ("chapter_analysis", "chapter_extraction", "chapter_reconciliation")
                    ),
                    RequestLog.cached.is_(False),
                )
            )
        ]

    def spoils(names: list[str], at: int) -> bool:
        return any(
            world.links.get(frozenset((a, b)), -1) > at
            for i, a in enumerate(names)
            for b in names[i + 1 :]
            if a in TRUTH and b in TRUTH and TRUTH[a] == TRUTH[b]
        )

    # Per passage: who is there (pronoun referents included), and spoilers in the stored analysis.
    hits = predicted = expected = spoilers = 0
    pronoun_total = pronoun_hits = far_total = far_hits = alias_total = alias_hits = 0
    misses: list = []
    for segment in segments:
        truth = passages[index_of[segment.id]]
        content = memories.get(segment.id) or {}
        found: set[str] = set()
        for character in content.get("characters", []):
            names = [character["canonical_name"], *character.get("aliases", [])]
            found |= {TRUTH[n] for n in names if n in TRUTH}
            spoilers += spoils(names, index_of[segment.id])
        predicted += len(found)
        expected += len(truth.present)
        hits += len(found & truth.present)
        if truth.referent:
            pronoun_total += 1
            pronoun_hits += truth.referent in found
            if truth.pronoun_distance >= 3:
                far_total += 1
                far_hits += truth.referent in found
        # A later form (a short name, a nickname, a new name, a revealed mask) already tied by the text
        # to an earlier one: the analysis of the passage must say who it is.
        at = index_of[segment.id]
        for form in dict.fromkeys(truth.named):
            if not any(
                TRUTH[other] == TRUTH[form] and at_link <= at
                for pair, at_link in world.links.items()
                if form in pair
                for other in pair - {form}
            ):
                continue
            if form in {"Mira Voss", "Ilse Brandt", "Ren", "Bob"}:
                continue  # the first name of a character needs no resolution
            alias_total += 1
            resolved = any(
                form in names and any(TRUTH.get(n) == TRUTH[form] and n != form for n in names)
                for names in (
                    [c["canonical_name"], *c.get("aliases", [])] for c in content.get("characters", [])
                )
            )
            alias_hits += resolved
            if not resolved:
                misses.append((at, form, content.get("characters", [])))
    # Spoilers in what the prompts showed: an identity record linking names before the text does.
    prompt_spoilers = 0
    for segment_id, messages in logs:
        if segment_id not in index_of:
            continue
        context = sections("\n".join(m["content"] for m in messages or [] if m["role"] == "user"))
        prompt_spoilers += sum(spoils(record, index_of[segment_id]) for record in identity_records(context))
        for item in context.get("KNOWN_IDENTITIES") or []:
            prompt_spoilers += spoils(
                [item["canonical_name"], *item.get("aliases", [])], index_of[segment_id]
            )
    # Identities, volume by volume: pairs of names kept together in the registry of the volume,
    # against the pairs the text links by the end of that volume among the names the volume uses.
    kept_pairs: set[tuple[int, frozenset]] = set()
    true_pairs: set[tuple[int, frozenset]] = set()
    by_entity: dict[str, str] = {}
    start = 0
    for number, (pid, volume) in enumerate(zip(project_ids, world.volumes, strict=True)):
        size = sum(len(chapter) for chapter in volume.chapters)
        appeared = {n for p in passages[start : start + size] for n in p.named}
        true_pairs |= {
            (number, pair) for pair, at in world.links.items() if pair <= appeared and at < start + size
        }
        start += size
        for entity in entities:
            if entity.project_id != pid:
                continue
            names = [n for n in [entity.name, *entity.data.get("aliases", [])] if n in TRUTH]
            kept_pairs |= {
                (number, frozenset((a, b))) for i, a in enumerate(names) for b in names[i + 1 :] if a != b
            }
            votes = [TRUTH[n] for n in names]
            if votes:
                by_entity[entity.id] = max(sorted(set(votes)), key=votes.count)
    pair_hits = len(kept_pairs & true_pairs)
    found_relations = {
        (by_entity.get(r.source_id), by_entity.get(r.target_id), r.relation_type) for r in relations
    }
    relation_hits = len(found_relations & world.relations)
    terms = {g.source: g.translation for g in glossary}
    term_hits = sum(1 for source, translation in terms.items() if world.terms.get(source) == translation)
    return {
        "passages": len(segments),
        "attribution_precision": ratio(hits, predicted),
        "attribution_recall": ratio(hits, expected),
        "pronoun_recall": ratio(pronoun_hits, pronoun_total),
        "pronoun_passages": pronoun_total,
        "far_pronoun_recall": ratio(far_hits, far_total),
        "far_pronoun_passages": far_total,
        "alias_resolution": ratio(alias_hits, alias_total),
        "alias_mentions": alias_total,
        "identity_precision": ratio(pair_hits, len(kept_pairs)),
        "identity_recall": ratio(pair_hits, len(true_pairs)),
        "relation_precision": ratio(relation_hits, len(found_relations)),
        "relation_recall": ratio(relation_hits, len(world.relations)),
        "glossary_precision": ratio(term_hits, len(terms)),
        "glossary_recall": ratio(term_hits, len(world.terms)),
        "spoilers_in_memory": spoilers,
        "spoilers_in_prompts": prompt_spoilers,
    } | ({"alias_misses": misses} if debug else {})


# ---------------------------------------------------------------- running an analysis on the world

ANALYSIS_OPERATIONS = ("chapter_analysis", "chapter_extraction", "chapter_reconciliation", "book_analysis")


def create_world(world: World, *, capacity: int = 16) -> tuple[list[str], str]:
    """The volumes of the world as books (one segment per passage) of a series; returns their ids."""
    from app.db import SessionLocal
    from app.models import Chapter, Project, Provider, Segment, Series, User

    with SessionLocal() as db:
        user = User(username=f"world-{random.random()}", password_hash="unused", admin=True)
        provider = Provider(
            name=f"World {random.random()}",
            base_url="https://llm.world/v1",
            model="simulated-analyst",
            capabilities={"supports_json_schema": True},
            context_window=200000,
            max_output_tokens=4000,
            max_concurrency=capacity,
        )
        db.add_all([user, provider])
        db.flush()
        series = None
        if len(world.volumes) > 1:
            series = Series(owner_id=user.id, name="World", normalized_name="world", kind="books")
            db.add(series)
            db.flush()
        ids = []
        for volume in world.volumes:
            project = Project(
                owner_id=user.id,
                title=f"Volume {volume.number}",
                original_hash="0" * 64,
                original_path="/dev/null",
                source_language="en",
                target_language="fr",
                provider_id=provider.id,
                quality="fast",
                context_backend="internal",
                series_id=series.id if series else None,
                volume_number=volume.number if series else None,
            )
            db.add(project)
            db.flush()
            position = 0
            for number, passages in enumerate(volume.chapters):
                chapter = Chapter(
                    project_id=project.id,
                    position=number,
                    title=f"Chapter {number + 1}",
                    resource=f"c{number}.xhtml",
                )
                db.add(chapter)
                db.flush()
                for passage in passages:
                    unit = f"v{volume.number}p{position}"
                    db.add(
                        Segment(
                            project_id=project.id,
                            chapter_id=chapter.id,
                            position=position,
                            source=passage.text,
                            units=[{"id": unit, "text": passage.text}],
                        )
                    )
                    position += 1
            ids.append(project.id)
        db.commit()
        return ids, provider.id


def simulated_provider(analyst: Analyst, latency: float = 0.0):
    """A respx side effect: the analyst's answer after `latency` seconds (a model call's duration)."""
    import asyncio

    import httpx

    async def respond(request):
        body = json.loads(request.content)
        schema = body.get("response_format", {}).get("json_schema", {}).get("name", "")
        if latency:
            await asyncio.sleep(latency)
        result = analyst.answer(schema, body["messages"])
        prompt = sum(len(m["content"]) for m in body["messages"])
        return httpx.Response(
            200,
            json={
                "choices": [{"finish_reason": "stop", "message": {"content": json.dumps(result)}}],
                "usage": {"prompt_tokens": prompt // 4, "completion_tokens": len(json.dumps(result)) // 4},
            },
        )

    return respond


async def analyse(project_ids: list[str], options: dict, *, rounds: int = 50) -> None:
    """Runs one analysis job per volume, in volume order, until each one ends."""
    import time

    from app.db import SessionLocal
    from app.jobs.queue import claim, enqueue
    from app.jobs.worker import execute
    from app.models import Job, Project

    for pid in project_ids:
        with SessionLocal() as db:
            job = enqueue(db, db.get(Project, pid), "analyze", dict(options))
            db.commit()
            job_id = job.id
        for _ in range(rounds):
            with SessionLocal() as db:
                current = db.get(Job, job_id)
                if current.status in {"completed", "failed", "blocked", "paused", "cancelled"}:
                    break
                current.next_attempt = min(current.next_attempt, time.time())
                db.commit()
            claimed = claim()
            assert claimed is not None and claimed[0] == job_id, claimed
            await execute(*claimed)
        with SessionLocal() as db:
            current = db.get(Job, job_id)
            assert current.status == "completed", (current.status, current.error)


def usage(project_ids: list[str]) -> dict:
    """Model calls and tokens of the analyses, cached answers excluded."""
    from sqlalchemy import func, select

    from app.db import SessionLocal
    from app.models import RequestLog

    with SessionLocal() as db:
        rows = db.execute(
            select(RequestLog.operation, func.count(), func.coalesce(func.sum(RequestLog.prompt_tokens), 0),
                   func.coalesce(func.sum(RequestLog.completion_tokens), 0))
            .where(RequestLog.project_id.in_(project_ids), RequestLog.cached.is_(False),
                   RequestLog.status == "success")
            .group_by(RequestLog.operation)
        ).all()  # fmt: skip
    calls = {operation: count for operation, count, _, _ in rows}
    return {
        "calls": sum(calls.values()),
        "calls_by_operation": dict(sorted(calls.items())),
        "prompt_tokens": sum(int(p) for _, _, p, _ in rows),
        "completion_tokens": sum(int(c) for _, _, _, c in rows),
    }
