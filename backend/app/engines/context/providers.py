import json
import re
import time
from abc import ABC, abstractmethod
from dataclasses import asdict, dataclass

from sqlalchemy import select

from app.db import SessionLocal
from app.engines.context.config import memory_config
from app.models import Memory, Outbox, Project, Segment
from app.providers.openviking import OpenVikingClient, event_uri, project_uri


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


def relevance(text: str, query: str) -> float:
    a, b = words(text), words(query)
    return len(a & b) / max(1, min(len(a), len(b)))


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
        with SessionLocal() as db:
            memories = db.scalars(select(Memory).where(Memory.project_id == project.id)).all()
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
                score = relevance(text, query)
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
        return sorted(items, key=lambda x: (-x.relevance, -x.position))[: 24 if deep else 10]

    async def ingest(self, event: Outbox) -> None:
        return None  # Canonical write already committed by the memory engine.


class OpenVikingContextProvider(ContextProvider):
    def __init__(self):
        self.trace: dict = {}

    async def ingest(self, event: Outbox) -> None:
        with SessionLocal() as db:
            project = db.get(Project, event.project_id)
            if not project:
                return
        async with OpenVikingClient(memory_config()) as client:
            if event.payload.get("type") == "project_catalog":
                from app.engines.memory.catalog import catalog_uri

                for filename, content in event.payload["files"].items():
                    await client.write(
                        catalog_uri(project, filename), content, processing_mode="vectors_only"
                    )
                return
            await client.write(event_uri(project, event), json.dumps(event.payload, ensure_ascii=False))

    async def retrieve(
        self, project: Project, query: str, position: int, deep: bool = False
    ) -> list[ContextItem]:
        config = memory_config()
        root = project_uri(project)
        self.trace = {
            "root_uri": root,
            "query": query,
            "operation": "search" if deep else "find",
            "hits": [],
            "rejected": [],
        }
        with SessionLocal() as db:
            events = list(
                db.scalars(select(Outbox).where(Outbox.project_id == project.id, Outbox.status == "sent"))
            )
            valid_events = []
            for event in events:
                if event.payload.get("validated") and event.payload.get("type") == "analysis":
                    latest = db.scalar(
                        select(Memory.id)
                        .where(
                            Memory.segment_id == event.payload.get("segment_id"),
                            Memory.kind == "analysis",
                            Memory.validated.is_(True),
                        )
                        .order_by(Memory.created_at.desc())
                        .limit(1)
                    )
                    if latest != event.event_key:
                        continue
                elif event.payload.get("validated"):
                    sid = event.payload.get("segment_id")
                    segment = db.get(Segment, sid) if sid else None
                    if (
                        not segment
                        or not segment.validated
                        or event.payload.get("content", {}).get("revision") != segment.revision
                    ):
                        continue
                valid_events.append(event)
        # Server-scoped search plus an exact SQL allowlist. Directory summaries and future events
        # cannot bypass this boundary, even when a remote index returns an unexpected URI.
        allowed = {
            event_uri(project, e): e
            for e in valid_events
            if isinstance(e.payload.get("position"), int)
            and (
                e.payload["position"] < position
                or (
                    e.payload["position"] == position
                    and e.payload.get("validated")
                    and e.payload.get("type") == "analysis"
                )
            )
        }
        if not allowed or not config["enable_search"]:
            return []
        async with OpenVikingClient(config) as client:
            hits = await client.find(
                query, root + "/events", deep=deep and config["enable_deep_search"], limit=24 if deep else 12
            )
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
                            "reason": "outside_narrative_allowlist"
                            if uri not in allowed
                            else "low_relevance",
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
                if payload != allowed[uri].payload:
                    self.trace["rejected"].append({"uri": uri, "reason": "differs_from_canonical_event"})
                    continue
                selected_content = payload.get("content", payload)
                if payload.get("type") == "analysis":
                    selected_content = {
                        k: v for k, v in selected_content.items() if k not in {"characters", "terms"}
                    }
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
                    }
                )
                results.append(
                    ContextItem(
                        "OPENVIKING_SEARCH" if deep else "OPENVIKING_FIND",
                        content,
                        uri,
                        score,
                        payload["position"],
                        5,
                    )
                )
            return results


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
