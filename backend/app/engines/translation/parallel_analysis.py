"""Parallel analysis of a volume (ANALYSIS_MODE=parallel): as much knowledge per passage as the strict
chronological analysis, with the passages worked on side by side.

1. **Extraction** (`chapter_extraction`, N passages at once): each passage is analysed on its own, with
   the raw text around it and what was known before the job (earlier volumes, confirmed identities,
   locked glossary), never with the analysis of another passage of the same run. Stored per passage
   in the job state (`extraction`), resumable.
2. **Consolidation** (in memory, deterministic): the extractions are applied in book order to a
   timeline (app.engines.memory.timeline) that ties names into identities, keeps relationships,
   proposed terms and the recent summaries, each with the passage it comes from.
3. **Reconciliation** (`chapter_reconciliation`, N passages at once): each extraction is reviewed
   against the timeline *as it was before that passage* (identities and aliases, recent characters for
   pronouns, relationships, terms, the summaries of the passages just before) and becomes the passage's
   final analysis. Every passage by default (ANALYSIS_RECONCILIATION=all), or only the ambiguous ones.
4. **Memory**: the final analyses are written in book order through the same code as the strict
   analysis (identities, relations, glossary proposals, chapter summaries, memories).
5. **Book Bible**: one synthesis per chapter, then merged four by four, level by level (a tree), each
   level in parallel.

Before step 3, a numbered volume of a series waits (the job is set aside, `earlier_volume`) while an
earlier volume of the series is still being analysed by a live job: its memory must be complete
first. Translation starts only once all of this is done: the worker runs it after `analyze` returns.

Resuming: every stage skips what the job state already holds; a model call already answered is served
from the request cache. The same extractions always give the same memory, whatever the threads.
"""

import asyncio
import json
import time
from dataclasses import dataclass

from sqlalchemy import select

from app.db import SessionLocal
from app.engines.autopilot.degrade import degradable, note, reason_of
from app.engines.context.builder import build_context
from app.engines.memory.identities import identities
from app.engines.memory.timeline import Timeline
from app.engines.translation.analysis import (
    _chapter_evidence,
    _chapters_to_consolidate,
    _refresh_series,
    _store_analysis,
)
from app.jobs import segment_state as state
from app.jobs.concurrency import blocking, in_parallel, job_share
from app.jobs.queue import ACTIVE, JobStopped, checkpoint, emit, fence
from app.models import BibleRevision, Chapter, Job, JobSegmentState, Memory, Project, Segment
from app.providers.llm import ProviderContentRefused, llm, load_prompt
from app.schemas import BookOverview, ChapterAnalysis

EXTRACTED = "extraction"  # job state: the passage's extraction (data), resumable
RECONCILED = "reconciled"  # job state: its final analysis (outcome reconciled | kept)
GROUP = 4  # Book Bible tree: syntheses merged four by four
EVIDENCE_BATCH = 4  # passage analyses per chapter synthesis call, as in the strict mode
SERIES_WAIT_SECONDS = 15
MEMORY_CHECKPOINT_EVERY = 20


@dataclass
class Passage:
    id: str
    position: int
    chapter_id: str
    source: str


def _plan(job: Job) -> tuple[list[Passage], dict[str, dict], set[str], dict[str, dict], dict[str, dict]]:
    """Passages in book order, the stored analyses, the skipped ones, the extractions and reconciliations."""
    with SessionLocal() as db:
        passages = [
            Passage(*row)
            for row in db.execute(
                select(Segment.id, Segment.position, Segment.chapter_id, Segment.source)
                .where(Segment.project_id == job.project_id)
                .order_by(Segment.position)
            )
        ]
        analyzed = {
            sid: content
            for sid, content in db.execute(
                select(Memory.segment_id, Memory.content)
                .where(Memory.project_id == job.project_id, Memory.kind == "analysis")
                .order_by(Memory.created_at)
            )
        }
        skipped = state.marked(db, job.id, state.ANALYSIS_SKIPPED)
        rows = db.execute(
            select(JobSegmentState.step, JobSegmentState.segment_id, JobSegmentState.data).where(
                JobSegmentState.job_id == job.id, JobSegmentState.step.in_((EXTRACTED, RECONCILED))
            )
        ).all()
    extracted = {sid: data for step, sid, data in rows if step == EXTRACTED}
    reconciled = {sid: data for step, sid, data in rows if step == RECONCILED}
    return passages, analyzed, skipped, extracted, reconciled


def _skipped(job: Job) -> set[str]:
    with SessionLocal() as db:
        return state.marked(db, job.id, state.ANALYSIS_SKIPPED)


def _store_step(job: Job, owner: str, step: str, sid: str, data: dict, outcome: str = "") -> None:
    with SessionLocal() as db:
        fence(db, job.id, owner)
        state.mark(db, job.id, step, sid, outcome=outcome, data=data)
        db.commit()


def _point_at(job: Job, owner: str, sid: str) -> None:
    """A refused passage: the worker's refusal handling reads the passage from the checkpoint."""
    checkpoint(job.id, owner, {"segment_id": sid})


async def _call(job: Job, owner: str, passage: Passage, operation: str, **context) -> ChapterAnalysis | None:
    """One passage call; None when the autopilot skips it after a refusal or invalid answers."""
    try:
        built = await build_context(
            job.project_id, passage.id, operation, provider_id=job.provider_id, **context
        )
        return await llm.complete(
            project_id=job.project_id,
            provider_id=job.provider_id,
            segment_id=passage.id,
            operation=operation,
            messages=built.messages,
            response_model=ChapterAnalysis,
            context=built.inspector,
            temperature=0.2,
        )
    except (JobStopped, asyncio.CancelledError):
        raise
    except Exception as exc:
        if not degradable(job, exc):
            if isinstance(exc, ProviderContentRefused):
                await blocking(_point_at, job, owner, passage.id)
            raise
        return await _degraded(job, owner, passage, operation, exc)


async def _degraded(job: Job, owner: str, passage: Passage, operation: str, exc: Exception) -> None:
    if operation == "chapter_extraction":
        reason = f"Analyse du passage abandonnée ({reason_of(exc)}) ; la traduction s’appuie sur le contexte voisin."
        mark = lambda db: state.mark(db, job.id, state.ANALYSIS_SKIPPED, passage.id)  # noqa: E731
        action = "skipped"
    else:
        reason = (
            f"Réconciliation du passage abandonnée ({reason_of(exc)}) ; son analyse isolée est conservée."
        )
        mark = None
        action = "kept_extraction"
    await blocking(
        note, job, owner, stage="analysis", kind=operation, action=action, reason=reason, segment_id=passage.id,
        mark=mark,
    )  # fmt: skip


# ---------------------------------------------------------------- series order


def _earlier_volume_analysing(job: Job) -> str | None:
    """The title of an earlier volume of the series whose analysis a live job has not finished yet."""
    from app.engines.context.series import prior_volumes

    with SessionLocal() as db:
        project = db.get(Project, job.project_id)
        for volume in reversed(prior_volumes(db, project)):
            live = db.scalar(
                select(Job.id)
                .where(Job.project_id == volume.id, Job.operation == "analyze", Job.status.in_(ACTIVE))
                .limit(1)
            )
            if live and db.scalar(
                select(Chapter.id)
                .where(Chapter.project_id == volume.id, Chapter.analyzed.is_(False))
                .limit(1)
            ):
                return volume.title
    return None


def _set_aside(job: Job, owner: str, title: str) -> None:
    """Gives the worker slot back; the job is claimed again in SERIES_WAIT_SECONDS."""
    with SessionLocal() as db:
        current = fence(db, job.id, owner)
        current.status, current.stop_reason = "waiting", "earlier_volume"
        current.error = f"En attente de la fin de l’analyse du volume précédent « {title} » de la série."
        current.next_attempt = time.time() + SERIES_WAIT_SECONDS
        current.lease_owner, current.lease_until = "", 0
        db.get(Project, job.project_id).status = "waiting"
        emit(
            db, job.project_id, job_id=job.id, status="waiting", reason="earlier_volume", error=current.error
        )
        db.commit()


# ---------------------------------------------------------------- the stages


async def analyze_parallel(job: Job, owner: str) -> None:
    passages, analyzed, skipped, extracted, reconciled = await blocking(_plan, job)
    todo = [p for p in passages if p.id not in analyzed and p.id not in skipped]
    await _extract(job, owner, todo, extracted)
    earlier = await blocking(_earlier_volume_analysing, job)
    if earlier:
        await blocking(_set_aside, job, owner, earlier)
        raise JobStopped()
    skipped = await blocking(_skipped, job)
    await _reconcile(job, owner, passages, analyzed, skipped, extracted, reconciled)
    await _write_memory(job, owner, todo, extracted, reconciled)
    await _book_bible(job, owner)
    await blocking(_refresh_series, job.project_id)
    if job.options.get("autopilot"):
        from app.engines.autopilot.memory import decide_memory

        await blocking(decide_memory, job, owner)


async def _extract(job: Job, owner: str, todo: list[Passage], extracted: dict[str, dict]) -> None:
    total = len(todo)

    async def work(passage: Passage) -> None:
        result = await _call(job, owner, passage, "chapter_extraction")
        if result is not None:
            data = result.model_dump()
            await blocking(_store_step, job, owner, EXTRACTED, passage.id, data)
            extracted[passage.id] = data

    async def launch(item: tuple[int, Passage]):
        index, passage = item
        if passage.id in extracted:
            return None
        await blocking(
            checkpoint,
            job.id,
            owner,
            {"step": "extraction", "current": index + 1, "total": total, "segment_id": passage.id},
        )
        return work(passage)

    await in_parallel(enumerate(todo), job_share(job, owner), launch)


def _reconciliation_scope() -> str:
    from app.config import settings

    return settings().analysis_reconciliation


async def _reconcile(
    job: Job,
    owner: str,
    passages: list[Passage],
    analyzed: dict[str, dict],
    skipped: set[str],
    extracted: dict[str, dict],
    reconciled: dict[str, dict],
) -> None:
    timeline = Timeline()
    scope = await blocking(_reconciliation_scope)
    todo = [p for p in passages if p.id in extracted and p.id not in analyzed]
    total = len(todo)
    await blocking(
        checkpoint, job.id, owner, {"step": "consolidation", "current": 0, "total": total, "segment_id": None}
    )
    order = {p.id: index for index, p in enumerate(todo)}
    neighbours = {p.id: (passages[i - 1].source if i else "") for i, p in enumerate(passages)}

    async def work(passage: Passage, known: list, extraction: dict) -> None:
        result = await _call(
            job,
            owner,
            passage,
            "chapter_reconciliation",
            extra={"EXTRACTED_ANALYSIS": extraction},
            known=known,
        )
        data, outcome = (result.model_dump(), "reconciled") if result is not None else (extraction, "kept")
        await blocking(_store_step, job, owner, RECONCILED, passage.id, data, outcome)
        reconciled[passage.id] = data

    def advance(passage: Passage):
        """Book order: the view of a passage is taken before its own extraction joins the timeline."""
        if passage.id in analyzed:
            timeline.apply(passage.position, passage.chapter_id, analyzed[passage.id])
            return None
        extraction = extracted.get(passage.id)
        if extraction is None or passage.id in skipped:
            return None
        known = None
        if passage.id not in reconciled and (scope == "all" or timeline.ambiguous(extraction)):
            known = timeline.view(
                passage.position,
                passage.chapter_id,
                neighbours[passage.id] + "\n" + passage.source,
                extraction,
            )
        timeline.apply(passage.position, passage.chapter_id, extraction)
        return known, extraction

    async def launch(passage: Passage):
        prepared = await blocking(advance, passage)
        if prepared is None:
            return None
        known, extraction = prepared
        if known is None:
            return None  # already reconciled, or nothing the earlier passages could change
        await blocking(
            checkpoint,
            job.id,
            owner,
            {
                "step": "reconciliation",
                "current": order[passage.id] + 1,
                "total": total,
                "segment_id": passage.id,
            },
        )
        return work(passage, known, extraction)

    await in_parallel(passages, job_share(job, owner), launch)


async def _write_memory(
    job: Job, owner: str, todo: list[Passage], extracted: dict[str, dict], reconciled: dict[str, dict]
) -> None:
    """The final analyses, in book order, through the strict mode's own writes."""
    total = len(todo)
    stored = await blocking(_stored, job)
    for index, passage in enumerate(todo):
        analysis = reconciled.get(passage.id) or extracted.get(passage.id)
        if analysis is None or passage.id in stored:
            continue
        if index % MEMORY_CHECKPOINT_EVERY == 0 or index + 1 == total:
            await blocking(
                checkpoint,
                job.id,
                owner,
                {"step": "memory", "current": index + 1, "total": total, "segment_id": passage.id},
            )
        await blocking(_store_analysis, job, owner, passage.id, ChapterAnalysis.model_validate(analysis))


def _stored(job: Job) -> set[str]:
    with SessionLocal() as db:
        return set(
            db.scalars(
                select(Memory.segment_id).where(
                    Memory.project_id == job.project_id, Memory.kind == "analysis"
                )
            )
        )


# ---------------------------------------------------------------- Book Bible, as a tree


def _tree_inputs(job: Job) -> tuple[dict, list[dict], dict[str, dict], str, str]:
    with SessionLocal() as db:
        project = db.get(Project, job.project_id)
        registry = [
            {"canonical_name": e.name, "aliases": e.data.get("aliases", [])}
            for e in identities(db, project.id)
        ]
        done = state.batches(db, job.id, state.BIBLE)
        return project.bible or {}, registry, done, project.source_language, project.target_language


def _store_node(job: Job, owner: str, key: str, overview: dict, outcome: str = "") -> None:
    with SessionLocal() as db:
        fence(db, job.id, owner)
        state.mark(db, job.id, state.BIBLE, key=key, outcome=outcome, data=overview)
        db.commit()


def _store_root(job: Job, owner: str, overview: dict, chapter_ids: list[str]) -> None:
    with SessionLocal() as db:
        current = fence(db, job.id, owner)
        project = db.get(Project, job.project_id)
        db.add(BibleRevision(project_id=project.id, content=overview))
        if not project.bible_validated:
            project.bible = overview
        for chapter in db.scalars(select(Chapter).where(Chapter.id.in_(chapter_ids))):
            chapter.analyzed = True
        current.outage_count = 0
        db.commit()


async def _synthesis(
    job: Job, owner: str, prompt: tuple[str, str], label: str, existing: dict, registry, evidence
):
    system, version = prompt
    messages = [
        {"role": "system", "content": system},
        {
            "role": "user",
            "content": json.dumps(
                {
                    "existing_bible": {k: v for k, v in existing.items() if k != "characters"},
                    "known_characters": registry,
                    "chapter": label,
                    "evidence": evidence,
                },
                ensure_ascii=False,
            ),
        },
    ]
    try:
        result = await llm.complete(
            project_id=job.project_id,
            provider_id=job.provider_id,
            operation="book_analysis",
            messages=messages,
            response_model=BookOverview,
            context={"prompt_version": version, "chapter": label},
            temperature=0.2,
        )
        return result.model_dump()
    except (JobStopped, asyncio.CancelledError):
        raise
    except Exception as exc:
        if not degradable(job, exc):
            raise
        await blocking(
            note, job, owner, stage="analysis", kind="book_bible", action="skipped",
            reason=f"Synthèse « {label} » abandonnée ({reason_of(exc)}) ; la Book Bible garde les autres.",
        )  # fmt: skip
        return None


async def _book_bible(job: Job, owner: str) -> None:
    chapter_ids = await blocking(_chapters_to_consolidate, job, owner)
    if not chapter_ids:
        return
    leaves: list[tuple[str, str, list[dict]]] = []
    for cid in chapter_ids:
        loaded = await blocking(_chapter_evidence, job, cid)
        if loaded is None:
            continue
        chapter, _, evidence = loaded
        leaves.append(
            (
                cid,
                chapter.title,
                [{k: v for k, v in m.content.items() if k != "characters"} for m in evidence],
            )
        )
    if not leaves:
        return
    previous, registry, done, source, target = await blocking(_tree_inputs, job)
    prompt = await blocking(load_prompt, "book_analysis", source, target)
    levels = _levels(len(leaves)) + (1 if previous else 0)

    async def progress(level: int, current: int, total: int) -> None:
        await blocking(
            checkpoint,
            job.id,
            owner,
            {"step": "book_bible", "level": level, "levels": levels, "current": current, "total": total,
             "segment_id": None, "unit": "sections"},
        )  # fmt: skip

    # Level 1: one synthesis per chapter, its passages four by four in order (as the strict mode does).
    nodes: list[dict | None] = [None] * len(leaves)

    async def leaf(index: int) -> None:
        cid, title, evidence = leaves[index]
        overview: dict = {}
        for offset in range(0, len(evidence), EVIDENCE_BATCH):
            key = f"tree:1:{cid}:{offset}"
            if key in done:
                overview = done[key] or overview
                continue
            result = await _synthesis(
                job, owner, prompt, title, overview, registry, evidence[offset : offset + EVIDENCE_BATCH]
            )
            await blocking(_store_node, job, owner, key, result or overview, "" if result else "skipped")
            overview = result or overview
        nodes[index] = overview

    async def launch_leaf(index: int):
        await progress(1, index + 1, len(leaves))
        return leaf(index)

    await in_parallel(range(len(leaves)), job_share(job, owner), launch_leaf)
    labels = [title for _, title, _ in leaves]
    level = 1
    while len(nodes) > 1:
        level += 1
        groups = [list(range(i, min(i + GROUP, len(nodes)))) for i in range(0, len(nodes), GROUP)]
        merged: list[dict | None] = [None] * len(groups)
        merged_labels = [f"{labels[g[0]]} – {labels[g[-1]]}" if len(g) > 1 else labels[g[0]] for g in groups]

        async def merge(
            index: int, groups=groups, merged=merged, merged_labels=merged_labels, level=level
        ) -> None:
            members = groups[index]
            key = f"tree:{level}:{index}"
            if key in done:
                merged[index] = done[key]
                return
            if len(members) == 1:
                merged[index] = nodes[members[0]]
                return
            evidence = [{"chapters": labels[m], "overview": nodes[m]} for m in members[1:]]
            result = await _synthesis(
                job, owner, prompt, merged_labels[index], nodes[members[0]] or {}, registry, evidence
            )
            merged[index] = result or nodes[members[0]]
            await blocking(_store_node, job, owner, key, merged[index] or {}, "" if result else "skipped")

        async def launch_merge(index: int, level=level, groups=groups, merge=merge):
            await progress(level, index + 1, len(groups))
            return merge(index)

        await in_parallel(range(len(groups)), job_share(job, owner), launch_merge)
        nodes, labels = merged, merged_labels
    root = nodes[0] or {}
    if previous:
        # New chapters of a volume analysed before: their synthesis joins the existing bible.
        key = "tree:root"
        if key in done:
            root = done[key]
        else:
            await progress(levels, 1, 1)
            result = await _synthesis(
                job, owner, prompt, labels[0], previous, registry, [{"chapters": labels[0], "overview": root}]
            )
            root = result or previous
            await blocking(_store_node, job, owner, key, root, "" if result else "skipped")
    await blocking(_store_root, job, owner, root, chapter_ids)


def _levels(leaves: int) -> int:
    levels = 1
    while leaves > 1:
        leaves = -(-leaves // GROUP)
        levels += 1
    return levels
