import json

from sqlalchemy import select

from app.db import SessionLocal
from app.engines.context.builder import build_context
from app.engines.memory.identities import identities
from app.engines.memory.relations import collect
from app.engines.memory.store import characters, propose_terms, remember
from app.jobs.queue import checkpoint, fence
from app.models import BibleRevision, Chapter, Job, Memory, Project, Segment
from app.providers.llm import llm, load_prompt
from app.schemas import BookOverview, ChapterAnalysis


async def analyze(job: Job, owner: str) -> None:
    with SessionLocal() as db:
        ids = list(
            db.scalars(
                select(Segment.id).where(Segment.project_id == job.project_id).order_by(Segment.position)
            )
        )
    for index, sid in enumerate(ids):
        checkpoint(
            job.id,
            owner,
            {"step": "chapter_analysis", "current": index + 1, "total": len(ids), "segment_id": sid},
        )
        with SessionLocal() as db:
            source = db.get(Segment, sid)
            done = db.scalar(select(Memory).where(Memory.segment_id == sid, Memory.kind == "analysis"))
            if done:
                continue
            project = db.get(Project, job.project_id)
        built = await build_context(project.id, sid, "chapter_analysis")
        result = await llm.complete(
            project_id=project.id,
            provider_id=project.provider_id,
            segment_id=sid,
            operation="chapter_analysis",
            messages=built.messages,
            response_model=ChapterAnalysis,
            context=built.inspector,
            temperature=0.2,
        )
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
    # Hierarchical consolidation, one chapter at a time: no full-book context explosion.
    with SessionLocal() as db:
        if db.get(Project, job.project_id).bible_validated:
            fence(db, job.id, owner)
            for chapter in db.scalars(select(Chapter).where(Chapter.project_id == job.project_id)):
                chapter.analyzed = True
            db.commit()
            return
        chapter_ids = list(
            db.scalars(
                select(Chapter.id).where(Chapter.project_id == job.project_id).order_by(Chapter.position)
            )
        )
    for chapter_index, cid in enumerate(chapter_ids):
        checkpoint(
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
        with SessionLocal() as db:
            chapter = db.get(Chapter, cid)
            if chapter.analyzed:
                continue
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
        for offset in range(0, len(evidence), 4):
            current_job = checkpoint(
                job.id, owner, {"batch_current": offset // 4 + 1, "batch_total": (len(evidence) + 3) // 4}
            )
            batch_key = f"{cid}:{offset}"
            if batch_key in current_job.checkpoint.get("analysis_batches", []):
                continue
            with SessionLocal() as db:
                current = db.get(Project, project.id)
                bible = current.bible
                registry = [
                    {"canonical_name": e.name, "aliases": e.data.get("aliases", [])}
                    for e in identities(db, project.id)
                ]
            system, version = load_prompt("book_analysis", project.source_language, project.target_language)
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
                provider_id=project.provider_id,
                operation="book_analysis",
                messages=messages,
                response_model=BookOverview,
                context={"prompt_version": version, "chapter_id": cid},
                temperature=0.2,
            )
            with SessionLocal() as db:
                current_job = fence(db, job.id, owner)
                current = db.get(Project, project.id)
                db.add(BibleRevision(project_id=project.id, content=result.model_dump()))
                if not current.bible_validated:
                    current.bible = result.model_dump()
                current_job.checkpoint = {
                    **current_job.checkpoint,
                    "analysis_batches": list(
                        dict.fromkeys([*current_job.checkpoint.get("analysis_batches", []), batch_key])
                    ),
                }
                current_job.outage_count = 0
                db.commit()
        with SessionLocal() as db:
            fence(db, job.id, owner)
            db.get(Chapter, cid).analyzed = True
            db.commit()
