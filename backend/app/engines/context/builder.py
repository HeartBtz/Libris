import json
import re
from dataclasses import dataclass

from sqlalchemy import select

from app.db import SessionLocal
from app.engines.context.config import memory_config
from app.engines.context.providers import ContextItem, HybridContextProvider
from app.engines.context.series import prior_volumes, series_decisions, series_terms
from app.engines.memory.identities import effective_names, plausible_name
from app.models import Chapter, CharacterRelation, Entity, Glossary, Memory, Project, Provider, Segment
from app.providers.llm import LLMError, estimate_tokens, json_schema, load_prompt
from app.schemas import (
    AskResult,
    ChapterAnalysis,
    ContextNeeds,
    FinalReviewResult,
    ReviewResult,
    TranslationResult,
)


def mentioned(name: str, text: str) -> bool:
    # Unicode word boundaries avoid "Ann" matching "Anna"; CJK falls back to substring matching.
    if re.search(r"[\u3400-\u9fff\u3040-\u30ff]", name):
        return name.casefold() in text.casefold()
    return bool(name and re.search(r"(?<!\w)" + re.escape(name) + r"(?!\w)", text, re.IGNORECASE))


def context_query(source: str, neighbors: str, names: list[str]) -> str:
    focus = ", ".join(names[:15])
    return (
        "Retrieve earlier evidence relevant to this passage, preserving the current knowledge of each character. "
        "Identify speakers and pronoun referents; earlier ownership, exchanges and revelations concerning "
        "objects mentioned; changes in relationships; established names and dialogue register. "
        "Distinguish what was believed before from what is known now. Ignore unrelated facts.\n"
        f"Entities: {focus}\nNearby source: {neighbors[:1500]}\nPassage: {source[:4500]}"
    )


NEIGHBORS = {"PREVIOUS_CONTEXT", "NEXT_CONTEXT"}


def section(name: str, content: str) -> str:
    # json.dumps leaves "<" and ">" alone: a book quoting "</TARGET_TEXT>" would close the section and
    # speak as the prompt. The JSON escapes decode to the same characters for the model.
    escaped = content.replace("<", "\\u003c").replace(">", "\\u003e")
    return f"<{name}>\n{escaped}\n</{name}>"


def cost(name: str, content: str) -> int:
    """Tokens a section costs once serialized in the request, escapes included."""
    return estimate_tokens(json.dumps(section(name, content), ensure_ascii=False))


RESPONSE_MODELS = {
    "translation": TranslationResult,
    "translation_revision": TranslationResult,
    "polishing": TranslationResult,
    "translation_review": ReviewResult,
    "final_review": FinalReviewResult,
    "chapter_analysis": ChapterAnalysis,
    "context_planner": ContextNeeds,
    "ask": AskResult,
}


def schema_size(operation: str) -> int:
    model = RESPONSE_MODELS.get(operation, ChapterAnalysis)
    return estimate_tokens(json.dumps(json_schema(model), ensure_ascii=False))


def response_reserve(operation: str) -> int:
    """What llm.complete adds around the prompt: the response schema, its margin and the envelope."""
    return schema_size(operation) + 512 + 64


class ContextTooLarge(LLMError):
    """The passage and its mandatory rules do not fit the provider window, whatever the optional context."""

    def __init__(self, message: str, *, available: int, fixed: int, target: int):
        super().__init__(message)
        self.available, self.fixed, self.target = available, fixed, target


def fit_neighbors(values: list[dict], limit: int, previous: bool, name: str = "") -> list[dict]:
    """Keep the passages closest to the target; shorten the last one rather than lose the neighbourhood."""

    def size(items):
        content = json.dumps(items, ensure_ascii=False)
        return cost(name, content) if name else estimate_tokens(content)

    values = [dict(value) for value in values]
    while len(values) > 1 and size(values) > limit:
        values.pop(0 if previous else -1)
    if values and size(values) > limit:
        item = values[0]
        fields = [key for key in ("source", "translation") if isinstance(item.get(key), str) and item[key]]
        item["excerpt"] = True
        while fields and size(values) > limit:
            longest = max(fields, key=lambda key: len(item[key]))
            keep = len(item[longest]) * 3 // 4
            if keep < 40:
                return []
            # The end of what precedes and the start of what follows are the useful halves.
            item[longest] = item[longest][-keep:] if previous else item[longest][:keep]
        if size(values) > limit:
            return []
    return values


@dataclass
class BuiltContext:
    messages: list[dict]
    inspector: dict


async def build_context(
    project_id: str,
    segment_id: str,
    operation: str = "translation",
    *,
    deep: bool = False,
    instruction: str = "",
    extra: dict | None = None,
    needs: list[str] | None = None,
    provider_id: str | None = None,
) -> BuiltContext:
    with SessionLocal() as db:
        project = db.get(Project, project_id)
        segment = db.get(Segment, segment_id)
        chapter = db.get(Chapter, segment.chapter_id)
        # The budget must fit the provider that will receive the prompt: a job may run on a
        # recovery provider whose window differs from the project's.
        provider = db.get(Provider, provider_id or project.provider_id)
        if not provider:
            raise LLMError("Choisissez un provider avant de lancer le traitement.")
        count = 4 if deep else 2
        previous = list(
            reversed(
                db.scalars(
                    select(Segment)
                    .where(Segment.project_id == project_id, Segment.position < segment.position)
                    .order_by(Segment.position.desc())
                    .limit(count)
                ).all()
            )
        )
        following = db.scalars(
            select(Segment)
            .where(Segment.project_id == project_id, Segment.position > segment.position)
            .order_by(Segment.position)
            .limit(count)
        ).all()
        local_source = "\n".join(s.source for s in [*previous, segment, *following])
        glossary = db.scalars(
            select(Glossary).where(Glossary.project_id == project_id, Glossary.accepted.is_(True))
        ).all()
        prior = prior_volumes(db, project)
        local_locked = {g.source.casefold() for g in glossary if g.locked}
        local_sources = {g.source.casefold() for g in glossary}
        # A locked series term outranks this book's unlocked entry (showing both would offer two names);
        # an unlocked one yields to any local entry.
        series_terms_used = [
            term
            for term in series_terms(db, prior)
            if term["source"].casefold() not in local_locked
            and (term["locked"] or term["source"].casefold() not in local_sources)
            and mentioned(term["source"], local_source)
        ]
        series_locked = {t["source"].casefold() for t in series_terms_used if t["locked"]}
        applicable = [
            g
            for g in glossary
            if mentioned(g.source, local_source) and (g.locked or g.source.casefold() not in series_locked)
        ]
        series_decisions_used = series_decisions(db, prior, local_source, set(local_sources), mentioned)
        entities = db.scalars(
            select(Entity).where(Entity.project_id == project_id, Entity.merged_into_id.is_(None))
        ).all()
        selected = [
            e for e in entities if any(mentioned(n, local_source) for n in effective_names(e, entities))
        ]
        system, version = load_prompt(operation, project.source_language, project.target_language)
        target = [{"id": u["id"], "text": u["text"]} for u in segment.units]
        mandatory = {
            "USER_RULES": {
                "book": project.instructions,
                "chapter": chapter.instructions,
                "passage": segment.instructions,
                "this_request": instruction,
            },
            "LOCKED_GLOSSARY": [
                {"source": g.source, "translation": g.translation} for g in applicable if g.locked
            ],
            "TARGET_TEXT": target,
        }
        mandatory["CONFIRMED_IDENTITIES"] = [
            {"canonical_name": e.name, "aliases": e.data.get("aliases", [])}
            for e in selected
            if e.identity_validated or e.validated
        ]
        mandatory["SERIES_CONVENTIONS"] = {
            "scope": (
                "Accepted terminology and human decisions from earlier volumes only. Locked terms are "
                "mandatory unless LOCKED_GLOSSARY or USER_RULES decide otherwise; unlocked terms yield "
                "to this book's own choices."
            ),
            "terms": series_terms_used,
            "human_decisions": series_decisions_used,
        }
        if extra:
            mandatory.update(extra)
        human_notes = {}
        for neighbor in [*previous, *following]:
            if neighbor.retained_source or neighbor.status == "refused":
                note = db.scalar(
                    select(Memory)
                    .where(
                        Memory.segment_id == neighbor.id,
                        Memory.kind == "analysis",
                        Memory.validated.is_(True),
                    )
                    .order_by(Memory.created_at.desc())
                    .limit(1)
                )
                human_notes[neighbor.id] = note.content.get("summary", "") if note else ""

        def neighbor_source(neighbor):
            if neighbor.id in human_notes:
                return {"source_withheld_after_refusal": True, "human_summary": human_notes[neighbor.id]}
            return neighbor.source

        previous_text = [
            {
                "segment_id": s.id,
                "source": neighbor_source(s),
                "translation": s.translation if s.status != "error" and not s.retained_source else "",
                "human_validated": s.validated,
            }
            for s in previous
        ]
        next_text = [{"segment_id": s.id, "source": neighbor_source(s)} for s in following]
        # Current chapter's global analysis is editorial; chronological state only uses earlier checkpoints.
        narrative = [s.narrative for s in previous if s.narrative]
        chapter_summary = (
            chapter.summary
            if operation == "chapter_analysis"
            or chapter.summary.get("through_position", -1) < segment.position
            else {}
        )
        candidates = [
            ContextItem(
                "PREVIOUS_CONTEXT",
                json.dumps(previous_text, ensure_ascii=False),
                "sliding-window",
                1,
                authority=3,
            ),
            ContextItem(
                "NEXT_CONTEXT", json.dumps(next_text, ensure_ascii=False), "sliding-window", 1, authority=3
            ),
            ContextItem("CHAPTER_STATE", json.dumps(narrative, ensure_ascii=False), chapter.id, 0.9),
            ContextItem(
                "EDITORIAL_CHAPTER_CONTEXT", json.dumps(chapter_summary, ensure_ascii=False), chapter.id, 0.7
            ),
            ContextItem(
                "EDITORIAL_BOOK_CONTEXT",
                json.dumps(
                    {
                        k: v
                        for k, v in project.bible.items()
                        if k
                        not in {
                            "characters",
                            "locations",
                            "organizations",
                            "relationships",
                            "important_objects",
                            "world_specific_terms",
                        }
                    },
                    ensure_ascii=False,
                ),
                project.id,
                0.8,
                authority=4 if project.bible_validated else 6,
            ),
        ]
        for e in selected:
            candidates.append(
                ContextItem(
                    "EDITORIAL_CHARACTERS",
                    json.dumps(
                        {
                            **{k: v for k, v in e.data.items() if k != "proposed_aliases"},
                            "aliases": [a for a in effective_names(e, entities) if a != e.name],
                        },
                        ensure_ascii=False,
                    ),
                    e.id,
                    1,
                    authority=4 if e.validated else 6,
                )
            )
        if operation == "chapter_analysis":
            candidates.append(
                ContextItem(
                    "CHARACTER_REGISTRY",
                    json.dumps(
                        [
                            {
                                "canonical_name": e.name,
                                "aliases": [a for a in effective_names(e, entities) if a != e.name],
                                "role": e.data.get("role", "")[:180],
                            }
                            for e in entities[-100:]
                            if e.validated or e.identity_validated or plausible_name(e.name)
                        ],
                        ensure_ascii=False,
                    ),
                    "canonical-identities",
                    0.95,
                    authority=4,
                )
            )
        selected_ids = {e.id for e in selected}
        names_by_id = {e.id: e.name for e in entities}
        relations = db.scalars(
            select(CharacterRelation).where(
                CharacterRelation.project_id == project_id,
                CharacterRelation.active.is_(True),
                CharacterRelation.position < segment.position,
            )
        ).all()
        for relation in relations:
            if relation.source_id in selected_ids or relation.target_id in selected_ids:
                candidates.append(
                    ContextItem(
                        "CHARACTER_RELATIONSHIPS",
                        json.dumps(
                            {
                                "source": names_by_id.get(relation.source_id),
                                "target": names_by_id.get(relation.target_id),
                                "type": relation.relation_type,
                                "description": relation.description,
                                "evidence": relation.evidence,
                                "position": relation.position,
                                "human_validated": relation.validated,
                                "provenance": relation.provenance,
                            },
                            ensure_ascii=False,
                        ),
                        relation.id,
                        0.95,
                        relation.position,
                        4 if relation.validated else 6,
                    )
                )
        for g in applicable:
            if not g.locked:
                candidates.append(
                    ContextItem(
                        "GLOSSARY",
                        json.dumps(
                            {"source": g.source, "translation": g.translation, "description": g.description},
                            ensure_ascii=False,
                        ),
                        g.id,
                        0.95,
                        authority=4,
                    )
                )
        query = context_query(
            segment.source,
            json.dumps([p["source"] for p in previous_text], ensure_ascii=False),
            [e.name for e in selected],
        )
        if needs:
            query += "\nSpecific information needs:\n" + "\n".join(needs[:4])
    memory = HybridContextProvider()
    candidates += await memory.retrieve(project, query, segment.position, deep)
    # The response schema and the margin llm.complete checks are reserved besides the output itself.
    reserve = response_reserve(operation)
    budget = provider.context_window - provider.max_output_tokens - reserve
    sizes = {key: cost(key, json.dumps(value, ensure_ascii=False)) for key, value in mandatory.items()}
    target_size = sizes["TARGET_TEXT"]
    system_size = estimate_tokens(json.dumps(system, ensure_ascii=False))
    mandatory_size = system_size + sum(sizes.values())
    if mandatory_size > budget:
        fixed = mandatory_size - target_size
        raise ContextTooLarge(
            f"Fenêtre de {provider.context_window} tokens trop petite pour ce passage : après "
            f"{provider.max_output_tokens} tokens réservés à la réponse et {reserve} au format de réponse, "
            f"il reste {max(budget, 0)} tokens, alors que le prompt système ({system_size}), le texte du "
            f"passage ({target_size}) et les règles obligatoires ({fixed - system_size}) en demandent "
            f"{mandatory_size} (estimation prudente : 1 token par octet). Choisissez un fournisseur avec "
            f"une fenêtre d’au moins {mandatory_size + provider.max_output_tokens + reserve + 2000} tokens "
            "ou réduisez sa sortie maximale ; aucun texte n’a été retiré.",
            available=budget,
            fixed=fixed,
            target=target_size,
        )
    remaining = optional_budget = min(budget - mandatory_size, memory_config()["context_budget"])
    retrieval_remaining = memory_config()["retrieval_budget"]
    # Raw neighbours outrank everything and would swallow the whole allowance on their own: when
    # character sheets, glossary or chapter state compete, they keep a share of it.
    contested = any(c.source not in NEIGHBORS and c.content not in ("{}", "[]", "") for c in candidates)
    neighbor_remaining = optional_budget * 3 // 5 if contested else optional_budget
    previous_share = neighbor_remaining * 2 // 3 if following else neighbor_remaining
    kept, dropped, seen = [], [], set()
    # Sliding context is guaranteed a small allocation before optional long-term memories.
    for item in sorted(candidates, key=lambda c: (c.authority, -c.relevance, c.origin)):
        normalized = " ".join(item.content.casefold().split())
        reason = ""
        if normalized in seen or normalized in {"{}", "[]", ""}:
            reason = "duplicate_or_empty"
        elif item.relevance < 0.05:
            reason = "low_relevance"
        length = cost(item.source, item.content)
        if item.source.startswith("OPENVIKING_") and length > retrieval_remaining:
            reason = "retrieval_budget"
        if not reason and item.source in NEIGHBORS:
            before = item.source == "PREVIOUS_CONTEXT"
            allowance = min(remaining, previous_share if before else neighbor_remaining)
            if length > allowance:
                values = fit_neighbors(json.loads(item.content), allowance, before, item.source)
                if values:
                    item.content = json.dumps(values, ensure_ascii=False)
                    length = cost(item.source, item.content)
                else:
                    reason = "budget"
            if not reason:
                neighbor_remaining -= length
        elif not reason and length > remaining:
            reason = "budget"
        detail = dict(item.dump(), tokens_estimate=length)
        if reason:
            dropped.append(dict(detail, reason=reason))
        else:
            remaining -= length
            if item.source.startswith("OPENVIKING_"):
                retrieval_remaining -= length
            seen.add(normalized)
            kept.append(detail)
    if (previous or following) and not any(i["source"] in {"PREVIOUS_CONTEXT", "NEXT_CONTEXT"} for i in kept):
        raise LLMError(
            f"Fenêtre de {provider.context_window} tokens trop petite pour garder le voisinage du passage : "
            f"une fois le passage et les règles placés, il reste {optional_budget} tokens de contexte, "
            "moins qu’un extrait des passages voisins. Choisissez un fournisseur avec une fenêtre plus "
            "grande ou réduisez sa sortie maximale."
        )
    sections = [section(item["source"], item["content"]) for item in kept]
    sections += [section(key, json.dumps(value, ensure_ascii=False)) for key, value in mandatory.items()]
    messages = [{"role": "system", "content": system}, {"role": "user", "content": "\n\n".join(sections)}]
    return BuiltContext(
        messages,
        {
            "prompt": operation,
            "prompt_version": version,
            "retrieval": memory.trace,
            "selected": kept,
            "discarded": dropped,
            "mandatory": mandatory,
            "tokenizer": "conservative_utf8_bytes",
            # What llm.complete counts before sending: the serialized messages and the schema.
            "input_estimate": estimate_tokens(json.dumps(messages, ensure_ascii=False)) + schema_size(operation),
            "output_reservation": provider.max_output_tokens,
            "context_window": provider.context_window,
        },
    )
