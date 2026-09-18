import json
import re
import time
from abc import ABC, abstractmethod
from dataclasses import asdict, dataclass

from sqlalchemy import select

from app.db import SessionLocal
from app.engines.context.config import memory_config
from app.engines.context.series import prior_volumes
from app.engines.memory.events import (
    admitted,
    canonical_event,
    canonical_events,
    latest_human_analyses,
    still_valid,
)
from app.jobs.concurrency import blocking
from app.models import Chapter, Memory, Outbox, Project, Segment
from app.providers.openviking import MEMORY_KINDS, OpenVikingClient, event_uri, project_uri, search_root


@dataclass
class ContextItem:
    source: str
    content: str
    origin: str
    relevance: float
    position: int = -1
    authority: int = 6

    def dump(self) -> dict:
        return asdict(self)


def words(text: str) -> set[str]:
    return {w.casefold() for w in re.findall(r"[^\W\d_]{3,}", text, re.UNICODE)}


QUERY_LABELS = re.compile(r"^(Entities|Nearby source|Passage|Specific information needs):", re.M)


def book_words(query: str) -> set[str]:
    # The retrieval query opens with an instruction for the semantic search service. Lexically it is
    # noise: "relationships", "objects" or "known" would match memories whatever the passage says.
    start = QUERY_LABELS.search(query)
    return words(QUERY_LABELS.sub("", query[start.start() :])) if start else words(query)


def overlap(a: set[str], b: set[str]) -> float:
    return len(a & b) / max(1, min(len(a), len(b)))


def relevance(text: str, query: str) -> float:
    return overlap(words(text), book_words(query))


class ContextProvider(ABC):
    @abstractmethod
    async def retrieve(
        self, project: Project, query: str, position: int, deep: bool = False
    ) -> list[ContextItem]:
        pass

    @abstractmethod
    async def ingest(self, event: Outbox) -> None:
        pass

    async def remember(self, event: Outbox) -> None:
        await self.ingest(event)

    async def search(self, project: Project, query: str, position: int) -> list[ContextItem]:
        return await self.retrieve(project, query, position)

    async def get_context(self, project: Project, query: str, position: int) -> list[ContextItem]:
        return await self.retrieve(project, query, position, deep=True)

    async def get_representation(self, project: Project, query: str, position: int) -> list[ContextItem]:
        return await self.retrieve(project, query, position)


class InternalContextProvider(ContextProvider):
    async def retrieve(
        self, project: Project, query: str, position: int, deep: bool = False
    ) -> list[ContextItem]:
        # Scores every memory of the book: off the event loop.
        return await blocking(self.scored, project, query, position, deep)

    def scored(self, project: Project, query: str, position: int, deep: bool) -> list[ContextItem]:
        wanted = book_words(query)
        with SessionLocal() as db:
            # Later passages never feed this one (a human analysis shares its passage's position).
            memories = db.scalars(
                select(Memory).where(Memory.project_id == project.id, Memory.position <= position)
            ).all()
            items = []
            latest_human_analysis = {}
            for candidate in sorted(memories, key=lambda m: m.created_at):
                if candidate.kind == "analysis" and candidate.validated:
                    latest_human_analysis[candidate.segment_id] = candidate.id
            for memory in memories:
                # Human decisions may apply retrospectively, but superseded versions never do.
                if memory.validated and memory.kind == "analysis":
                    if (
                        memory.position > position
                        or latest_human_analysis.get(memory.segment_id) != memory.id
                    ):
                        continue
                elif memory.validated:
                    if memory.position > position:
                        continue
                    source = db.get(Segment, memory.segment_id) if memory.segment_id else None
                    if (
                        not source
                        or not source.validated
                        or memory.content.get("revision") != source.revision
                    ):
                        continue
                elif memory.position >= position:
                    continue
                content = memory.content
                if memory.kind == "analysis":
                    content = {k: v for k, v in content.items() if k not in {"characters", "terms"}}
                text = json.dumps(content, ensure_ascii=False)
                score = overlap(words(text), wanted)
                if score > 0:
                    items.append(
                        ContextItem(
                            "HUMAN_DECISIONS" if memory.validated else "RETRIEVED_HISTORY",
                            text,
                            memory.id,
                            score,
                            memory.position,
                            2 if memory.validated else 6,
                        )
                    )
            # Earlier volumes of the series are entirely in the past of this passage.
            for volume in prior_volumes(db, project):
                earlier = list(db.scalars(select(Memory).where(Memory.project_id == volume.id)))
                latest = latest_human_analyses(earlier)
                for memory in earlier:
                    if not still_valid(db, memory, latest):
                        continue
                    content = memory.content
                    if memory.kind == "analysis":
                        content = {k: v for k, v in content.items() if k not in {"characters", "terms"}}
                    text = json.dumps({"volume": volume.volume_number, **content}, ensure_ascii=False)
                    score = overlap(words(text), wanted)
                    if score > 0:
                        items.append(
                            ContextItem(
                                "HUMAN_DECISIONS" if memory.validated else "RETRIEVED_HISTORY",
                                text,
                                memory.id,
                                score,
                                -1,
                                2 if memory.validated else 6,
                            )
                        )
        return sorted(items, key=lambda x: (-x.relevance, -x.position))[: 24 if deep else 10]

    async def ingest(self, event: Outbox) -> None:
        return None  # Canonical write already committed by the memory engine.


class OpenVikingContextProvider(ContextProvider):
    def __init__(self):
        self.trace: dict = {}

    async def ingest(self, event: Outbox) -> None:
        await self.publish(event)

    async def publish(self, event: Outbox) -> tuple[str | None, dict | None]:
        """Writes one outbox entry; returns where and what, or (None, None) if nothing is left to write.

        A memory event is rebuilt from SQL at the moment it is written, so what OpenViking holds is
        always the canonical document of that memory at its current place.
        """
        document = event.payload
        with SessionLocal() as db:
            project = db.get(Project, event.project_id)
            if not project:
                return None, None
            if event.payload.get("type") in MEMORY_KINDS:
                memory = db.get(Memory, event.event_key)
                if memory is None:
                    return None, None
                chapter = db.get(Chapter, db.get(Segment, memory.segment_id).chapter_id) if memory.segment_id else None
                document = canonical_event(memory, project, chapter)
        async with OpenVikingClient(memory_config()) as client:
            if event.payload.get("type") == "project_catalog":
                from app.engines.memory.catalog import catalog_uri

                for filename, content in event.payload["files"].items():
                    await client.write(
                        catalog_uri(project, filename), content, processing_mode="vectors_only"
                    )
                return project_uri(project), document
            uri = event_uri(project, event)
            await client.write(uri, json.dumps(document, ensure_ascii=False))
            return uri, document

    async def retrieve(
        self, project: Project, query: str, position: int, deep: bool = False
    ) -> list[ContextItem]:
        config = memory_config()
        root = search_root(project)
        self.trace = {
            "root_uri": root,
            "query": query,
            "operation": "search" if deep else "find",
            "hits": [],
            "rejected": [],
        }
        # Server-scoped search plus an exact SQL allowlist: earlier passages of this volume and earlier
        # volumes of its series. Directory summaries, later passages or volumes and documents that differ
        # from the canonical SQL event are never injected, whatever the remote index returns.
        allowed = await blocking(self.allowed, project.id, position)
        if not allowed or not config["enable_search"]:
            return []
        async with OpenVikingClient(config) as client:
            hits = await client.find(query, root, deep=deep and config["enable_deep_search"], limit=24 if deep else 12)
            results: list[ContextItem] = []
            remaining = config["retrieval_budget"]
            for hit in hits:
                uri = hit.get("uri", "")
                score = float(hit.get("score") or 0)
                if uri not in allowed or score < config["min_score"]:
                    self.trace["rejected"].append(
                        {
                            "uri": uri,
                            "score": score,
                            "reason": "outside_narrative_allowlist" if uri not in allowed else "low_relevance",
                        }
                    )
                    continue
                # L0 summaries rank candidates; L2 is read only for admitted, relevant files.
                raw = await client.read(uri)
                try:
                    payload = json.loads(raw)
                except (ValueError, TypeError):
                    self.trace["rejected"].append({"uri": uri, "reason": "invalid_structured_memory"})
                    continue
                expected, same_volume = allowed[uri]
                if payload != expected:
                    self.trace["rejected"].append({"uri": uri, "reason": "differs_from_canonical_event"})
                    continue
                selected_content = payload.get("content", {})
                if payload.get("type") == "analysis":
                    selected_content = {
                        k: v for k, v in selected_content.items() if k not in {"characters", "terms"}
                    }
                if not same_volume:
                    selected_content = {"volume": payload.get("volume_number"), **selected_content}
                content = json.dumps(selected_content, ensure_ascii=False)
                size = len(content.encode()) + 16
                if size > remaining:
                    self.trace["rejected"].append({"uri": uri, "reason": "retrieval_budget"})
                    continue
                remaining -= size
                self.trace["hits"].append(
                    {
                        "uri": uri,
                        "score": score,
                        "abstract": hit.get("abstract", ""),
                        "level": "L2",
                        "position": payload["position"],
                        "volume": payload.get("volume_number"),
                    }
                )
                results.append(
                    ContextItem(
                        "OPENVIKING_SEARCH" if deep else "OPENVIKING_FIND",
                        content,
                        uri,
                        score,
                        payload["position"] if same_volume else -1,
                        5,
                    )
                )
            return results

    @staticmethod
    def allowed(project_id: str, position: int) -> dict[str, tuple[dict, bool]]:
        """URI -> (canonical document, same volume) for every event this passage may read."""
        with SessionLocal() as db:
            project = db.get(Project, project_id)
            entries = admitted(db, project, position)
            memories = [memory for memory, _ in entries.values()]
            projects = {volume.id: volume for _, volume in entries.values()}
            documents = canonical_events(db, memories, projects)
            return {
                uri: (documents[memory.id], volume.id == project.id)
                for uri, (memory, volume) in entries.items()
            }


class HybridContextProvider(ContextProvider):
    def __init__(self):
        self.internal = InternalContextProvider()
        self.remote = OpenVikingContextProvider()
        self.trace: dict = {}

    async def retrieve(
        self, project: Project, query: str, position: int, deep: bool = False
    ) -> list[ContextItem]:
        start = time.monotonic()
        local = await self.internal.retrieve(project, query, position, deep)
        configured = bool(memory_config()["base_url"])
        self.trace = {
            "requested_backend": project.context_backend,
            "query": query,
            "deep": deep,
            "effective_backend": "internal",
            "error": "",
            "openviking_configured": configured,
        }
        if project.context_backend != "internal" and configured:
            try:
                remote = await self.remote.retrieve(project, query, position, deep)
                self.trace["effective_backend"] = project.context_backend
                local = (
                    local if project.context_backend == "hybrid" else [m for m in local if m.authority == 2]
                )
                local += remote
                self.trace["openviking"] = self.remote.trace
            except Exception as exc:
                self.trace["error"] = (
                    f"OpenViking indisponible ({type(exc).__name__}), continuité assurée par SQL."
                )
        self.trace["duration"] = time.monotonic() - start
        return local

    async def ingest(self, event: Outbox) -> None:
        await self.internal.ingest(event)
        if memory_config()["base_url"]:
            await self.remote.ingest(event)
