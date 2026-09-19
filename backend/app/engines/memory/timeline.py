"""What the passages before a given one established, rebuilt from their analyses in book order.

The parallel analysis (app.engines.translation.parallel_analysis) extracts every passage on its own,
then reconciles each one with this memory. The timeline is plain data, built in memory: the analyses
are applied in book order, and the view offered to a passage is taken before its own analysis is
applied, so it only ever holds what earlier passages said. Nothing in it depends on the order in which
the model calls finished: the same extractions always give the same views, whatever the threads.

It consolidates identities (names tied together by an analysis, first appearance, last naming),
relationships, proposed terms and the summaries of the recent passages. It never writes to SQL: the
final memory is written by the same code as the strict analysis, from the reconciled analyses.
"""

import json
from dataclasses import dataclass, field

from app.engines.context.providers import ContextItem
from app.engines.memory.identities import normalized, plausible_name

RECENT = 8  # characters last named, for pronouns and descriptions
EARLIER = 6  # summaries of the passages just before
ANCHORS = 3  # and of the passages, further back, where the recent characters were last named
MAX_IDENTITIES = 40
MAX_RELATIONS = 20
SUMMARY_CHARS = 800


@dataclass
class Identity:
    key: int
    canonical: str
    names: list[str]
    first: int
    last: int
    # Order of the last naming inside its passage (the analysis lists characters as they appear):
    # of two characters named in the same passage, the later one is the more recent.
    order: int = 0
    data: dict = field(default_factory=dict)


def _names(character: dict) -> list[str]:
    values = [character.get("canonical_name", ""), *(character.get("aliases") or [])]
    return list(dict.fromkeys(v.strip()[:300] for v in values if v and v.strip() and plausible_name(v)))


class Timeline:
    def __init__(self) -> None:
        self.identities: dict[int, Identity] = {}
        self.by_name: dict[str, int] = {}
        self.relations: list[dict] = []
        self.terms: dict[str, dict] = {}
        self.passages: list[dict] = []
        self._next = 0

    # ------------------------------------------------------------ building

    def apply(self, position: int, chapter_id: str, analysis: dict) -> None:
        """Adds what the analysis of the passage at `position` established."""
        for order, character in enumerate(analysis.get("characters") or []):
            names = _names(character)
            if not names:
                continue
            keys = sorted(
                {self.by_name[normalized(n)] for n in names if normalized(n) in self.by_name},
                key=lambda k: (self.identities[k].first, k),
            )
            if keys:
                target = self.identities[keys[0]]
                for other in keys[1:]:
                    self._merge(target, self.identities.pop(other))
            else:
                target = Identity(self._next, names[0], [], position, position)
                self.identities[target.key] = target
                self._next += 1
            for name in names:
                if normalized(name) not in {normalized(n) for n in target.names}:
                    target.names.append(name)
                self.by_name[normalized(name)] = target.key
            if (position, order) >= (target.last, target.order):
                target.last, target.order = position, order
            for key in ("gender", "pronouns", "role"):
                if character.get(key) and not target.data.get(key):
                    target.data[key] = str(character[key])[:180]
        for relation in analysis.get("relationships") or []:
            if relation.get("source") and relation.get("target") and relation.get("relation_type"):
                self.relations.append(
                    {
                        "source": relation["source"],
                        "target": relation["target"],
                        "type": relation["relation_type"],
                        "description": (relation.get("description") or "")[:300],
                        "position": position,
                    }
                )
        for term in analysis.get("terms") or []:
            source = (term.get("source") or "").strip()
            if source and term.get("translation") and source.casefold() not in self.terms:
                self.terms[source.casefold()] = {"source": source, "translation": term["translation"]}
        self.passages.append(
            {
                "position": position,
                "chapter_id": chapter_id,
                "summary": (analysis.get("summary") or "")[:SUMMARY_CHARS],
            }
        )

    def _merge(self, target: Identity, source: Identity) -> None:
        for name in source.names:
            if normalized(name) not in {normalized(n) for n in target.names}:
                target.names.append(name)
            self.by_name[normalized(name)] = target.key
        target.first = min(target.first, source.first)
        target.last, target.order = max((target.last, target.order), (source.last, source.order))
        for key, value in source.data.items():
            target.data.setdefault(key, value)

    # ------------------------------------------------------------ reading

    def canonical(self, name: str) -> str:
        key = self.by_name.get(normalized(name))
        return self.identities[key].canonical if key is not None else name

    def ambiguous(self, analysis: dict) -> bool:
        """An extraction the memory of earlier passages could change: the `flagged` reconciliation."""
        if not analysis.get("characters"):
            return True  # nobody named: pronouns and descriptions need the earlier passages
        if any(event.get("kind") == "unresolved" for event in analysis.get("events") or []):
            return True
        known_words = {word for key in self.by_name for word in key.split()}
        for character in analysis["characters"]:
            if character.get("proposed_aliases"):
                return True
            names = _names(character)
            keys = {self.by_name.get(normalized(n)) for n in names}
            if len(keys - {None}) > 1:
                return True  # names the earlier passages hold apart
            if keys == {None} and any(word in known_words for n in names for word in normalized(n).split()):
                return True  # a new name sharing a word with a known one: a short form, a title
            if keys - {None} and self.canonical(names[0]) != character.get("canonical_name"):
                return True
        named = {normalized(n) for c in analysis["characters"] for n in _names(c)}
        return any(
            normalized(r.get("source", "")) not in named or normalized(r.get("target", "")) not in named
            for r in analysis.get("relationships") or []
        )

    def _earlier(self, recent: list[Identity]) -> list[dict]:
        """The passages just before, preceded by the passages where the characters named most recently
        were last named, when those are further back (a scene told in pronouns): they say who "she" or
        "he" most likely is."""
        window = self.passages[-EARLIER:]
        inside = {p["position"] for p in window}
        by_position = {p["position"]: p for p in self.passages}
        anchors = sorted({i.last for i in recent if i.last not in inside and i.last in by_position})[
            -ANCHORS:
        ]
        return [by_position[position] for position in anchors] + window

    def view(self, position: int, chapter_id: str, text: str, analysis: dict | None) -> list[ContextItem]:
        """Context sections for the passage at `position`: only what earlier passages established."""
        from app.engines.context.builder import mentioned

        own = {normalized(n) for c in (analysis or {}).get("characters") or [] for n in _names(c)}
        ordered = sorted(self.identities.values(), key=lambda i: (-i.last, -i.order, i.key))
        recent = ordered[:RECENT]
        recent_keys = {identity.key for identity in recent}
        relevant = [
            identity
            for identity in ordered
            if identity.key in recent_keys
            or any(normalized(n) in own or mentioned(n, text) for n in identity.names)
        ][:MAX_IDENTITIES]
        keys = {identity.key for identity in relevant}
        items: list[ContextItem] = []

        def add(name: str, value, relevance: float) -> None:
            if value:
                items.append(
                    ContextItem(
                        name,
                        json.dumps(value, ensure_ascii=False),
                        "earlier-passages",
                        relevance,
                        position,
                        4,
                    )
                )

        add(
            "KNOWN_IDENTITIES",
            [
                {
                    "canonical_name": i.canonical,
                    "aliases": [n for n in i.names if n != i.canonical],
                    **{k: v for k, v in i.data.items() if v},
                    "first_passage": i.first + 1,
                    "last_named_passage": i.last + 1,
                }
                for i in relevant
            ],
            1,
        )
        add(
            "RECENT_CHARACTERS",
            [
                {
                    "canonical_name": i.canonical,
                    "gender": i.data.get("gender", ""),
                    "last_named_passage": i.last + 1,
                }
                for i in recent
            ],
            1,
        )
        relations = [
            {
                **r,
                "source": self.canonical(r["source"]),
                "target": self.canonical(r["target"]),
                "passage": r["position"] + 1,
            }
            for r in self.relations
            if self.by_name.get(normalized(r["source"])) in keys
            or self.by_name.get(normalized(r["target"])) in keys
        ][-MAX_RELATIONS:]
        add("KNOWN_RELATIONSHIPS", [{k: v for k, v in r.items() if k != "position"} for r in relations], 0.95)
        add("KNOWN_TERMS", [t for t in self.terms.values() if mentioned(t["source"], text)], 0.95)
        add(
            "EARLIER_PASSAGES",
            [
                {
                    "passage": p["position"] + 1,
                    "same_chapter": p["chapter_id"] == chapter_id,
                    "summary": p["summary"],
                }
                for p in self._earlier(recent)
            ],
            1,
        )
        return items
