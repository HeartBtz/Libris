import json

from sqlalchemy import select

from app.db import SessionLocal
from app.engines.context.builder import build_context
from app.engines.memory.identities import identities
from app.engines.memory.relations import collect
from app.engines.memory.store import characters, propose_terms, remember
from app.jobs import segment_state as state
from app.jobs.concurrency import blocking
from app.jobs.queue import checkpoint, fence
from app.models import BibleRevision, Chapter, Job, Memory, Project, Segment
from app.providers.llm import llm, load_prompt
from app.schemas import BookOverview, ChapterAnalysis


def _unanalyzed(job: Job) -> tuple[list[str], set[str]]:
    with SessionLocal() as db:
        ids = list(
            db.scalars(
                select(Segment.id).where(Segment.project_id == job.project_id).order_by(Segment.position)
            )
        )
        # A resumed job skips what is done without a checkpoint write and an event per passage.
        analyzed = set(
            db.scalars(
                select(Memory.segment_id).where(Memory.project_id == job.project_id, Memory.kind == "analysis")
            )
        )
    return ids, analyzed


def _pending_analysis(job: Job, sid: str) -> Project | None:
    with SessionLocal() as db:
        if db.scalar(select(Memory.id).where(Memory.segment_id == sid, Memory.kind == "analysis")):
            return None
        return db.get(Project, job.project_id)


def _store_analysis(job: Job, owner: str, sid: str, result: ChapterAnalysis) -> None:
    with SessionLocal() as db:
        fence(db, job.id, owner)
        source = db.get(Segment, sid)
        project = db.get(Project, job.project_id)
        chapter = db.get(Chapter, source.chapter_id)
        chapter.summary = {
            "summary": result.summary,
            "events": [f.model_dump() for f in result.events],
            "style_notes": result.style_notes,
            "through_position": source.position,
        }
        remember(db, project, source, result.model_dump(), "analysis")
        characters(db, project.id, [c.model_dump() for c in result.characters], source.position)
        collect(db, project.id, [r.model_dump() for r in result.relationships], source)
        propose_terms(db, project, [t.model_dump() for t in result.terms])
        db.commit()


def _chapters_to_consolidate(job: Job, owner: str) -> list[str]:
    with SessionLocal() as db:
        if db.get(Project, job.project_id).bible_validated:
            fence(db, job.id, owner)
            for chapter in db.scalars(select(Chapter).where(Chapter.project_id == job.project_id)):
                chapter.analyzed = True
            db.commit()
            return []
        return list(
            db.scalars(
                select(Chapter.id).where(Chapter.project_id == job.project_id).order_by(Chapter.position)
            )
        )


def _chapter_evidence(job: Job, cid: str) -> tuple[Chapter, Project, list[Memory]] | None:
    with SessionLocal() as db:
        chapter = db.get(Chapter, cid)
        if chapter.analyzed:
            return None
        project = db.get(Project, job.project_id)
        # The evidence for the chapter was compressed incrementally; retain all segment summaries
        # in bounded batches, not just the last segment's summary.
        evidence = list(
            db.scalars(
                select(Memory)
                .join(Segment, Memory.segment_id == Segment.id)
                .where(Segment.chapter_id == cid, Memory.kind == "analysis")
                .order_by(Memory.position)
            )
        )
        return chapter, project, evidence


def _bible_inputs(job: Job, project_id: str, batch_key: str) -> tuple[dict, list[dict]] | None:
    with SessionLocal() as db:
        if state.is_marked(db, job.id, state.BIBLE, key=batch_key):
            return None
        bible = db.get(Project, project_id).bible
        registry = [
            {"canonical_name": e.name, "aliases": e.data.get("aliases", [])} for e in identities(db, project_id)
        ]
        return bible, registry


def _store_bible(job: Job, owner: str, project_id: str, batch_key: str, result: BookOverview) -> None:
    with SessionLocal() as db:
        current_job = fence(db, job.id, owner)
        current = db.get(Project, project_id)
        db.add(BibleRevision(project_id=project_id, content=result.model_dump()))
        if not current.bible_validated:
            current.bible = result.model_dump()
        state.mark(db, job.id, state.BIBLE, key=batch_key)
        current_job.outage_count = 0
        db.commit()


def _refresh_series(project_id: str) -> None:
    """A volume analysed: its identities, relations and terms join the series memory."""
    from app.engines.series.bible import refresh_series

    with SessionLocal() as db:
        project = db.get(Project, project_id)
        if project and project.series_id:
            refresh_series(db, project.series_id)
            db.commit()


def _chapter_done(job: Job, owner: str, cid: str) -> None:
    with SessionLocal() as db:
        fence(db, job.id, owner)
        db.get(Chapter, cid).analyzed = True
        db.commit()


async def analyze(job: Job, owner: str) -> None:
    # Strictly in book order: each analysis reads the chapter summary, characters and relations left
    # by the passages before it, so running passages side by side would break that chronology.
    ids, analyzed = await blocking(_unanalyzed, job)
    for index, sid in enumerate(ids):
        if sid in analyzed:
            continue
        await blocking(
            checkpoint,
            job.id,
            owner,
            {"step": "chapter_analysis", "current": index + 1, "total": len(ids), "segment_id": sid},
        )
        project = await blocking(_pending_analysis, job, sid)
        if project is None:
            continue
        built = await build_context(project.id, sid, "chapter_analysis", provider_id=job.provider_id)
        result = await llm.complete(
            project_id=project.id,
            provider_id=job.provider_id,
            segment_id=sid,
            operation="chapter_analysis",
            messages=built.messages,
            response_model=ChapterAnalysis,
            context=built.inspector,
            temperature=0.2,
        )
        await blocking(_store_analysis, job, owner, sid, result)
    # Hierarchical consolidation, one chapter at a time: no full-book context explosion.
    chapter_ids = await blocking(_chapters_to_consolidate, job, owner)
    for chapter_index, cid in enumerate(chapter_ids):
        await blocking(
            checkpoint,
            job.id,
            owner,
            {
                "step": "book_bible",
                "chapter_id": cid,
                "segment_id": None,
                "current": chapter_index + 1,
                "total": len(chapter_ids),
                "unit": "sections",
                "phase": 2,
                "phases": 2,
            },
        )
        loaded = await blocking(_chapter_evidence, job, cid)
        if loaded is None:
            continue
        chapter, project, evidence = loaded
        for offset in range(0, len(evidence), 4):
            await blocking(
                checkpoint,
                job.id,
                owner,
                {"batch_current": offset // 4 + 1, "batch_total": (len(evidence) + 3) // 4},
            )
            batch_key = f"{cid}:{offset}"
            inputs = await blocking(_bible_inputs, job, project.id, batch_key)
            if inputs is None:
                continue
            bible, registry = inputs
            system, version = await blocking(
                load_prompt, "book_analysis", project.source_language, project.target_language
            )
            # Entity records hold the complete inventory; overview consolidation stays compact.
            overview = {k: v for k, v in bible.items() if k != "characters"}
            batch = [
                {k: v for k, v in m.content.items() if k != "characters"}
                for m in evidence[offset : offset + 4]
            ]
            messages = [
                {"role": "system", "content": system},
                {
                    "role": "user",
                    "content": json.dumps(
                        {
                            "existing_bible": overview,
                            "known_characters": registry,
                            "chapter": chapter.title,
                            "evidence": batch,
                        },
                        ensure_ascii=False,
                    ),
                },
            ]
            result = await llm.complete(
                project_id=project.id,
                provider_id=job.provider_id,
                operation="book_analysis",
                messages=messages,
                response_model=BookOverview,
                context={"prompt_version": version, "chapter_id": cid},
                temperature=0.2,
            )
            await blocking(_store_bible, job, owner, project.id, batch_key, result)
        await blocking(_chapter_done, job, owner, cid)
    await blocking(_refresh_series, job.project_id)
