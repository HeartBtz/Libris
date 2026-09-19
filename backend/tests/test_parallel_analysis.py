"""Parallel analysis: quality against the strict mode, no spoilers, determinism, resumption, barriers."""

import json
import time

import httpx
import pytest
import respx
from analysis_world import (
    GENDER,
    Analyst,
    analyse,
    build_world,
    create_world,
    names_in,
    score,
    sections,
    simulated_provider,
    usage,
)
from sqlalchemy import func, select
from sqlalchemy.exc import OperationalError

from app.db import SessionLocal
from app.engines.memory.timeline import Timeline
from app.engines.translation import parallel_analysis
from app.jobs import concurrency
from app.jobs.concurrency import job_parallelism, throttled
from app.jobs.queue import claim, enqueue
from app.jobs.worker import execute
from app.models import (
    Chapter,
    CharacterRelation,
    Entity,
    Glossary,
    Job,
    Memory,
    Project,
    Provider,
    RequestLog,
    Segment,
)
from app.progress import project_progress

URL = "https://llm.world/v1/chat/completions"
ANALYSIS = ("chapter_analysis", "chapter_extraction", "chapter_reconciliation")


def snapshot(project_id: str) -> dict:
    """The memory an analysis left, without row ids: what translation will read."""
    with SessionLocal() as db:
        positions = dict(
            db.execute(select(Segment.id, Segment.position).where(Segment.project_id == project_id)).all()
        )
        names = {e.id: e.name for e in db.scalars(select(Entity).where(Entity.project_id == project_id))}
        return {
            "memories": sorted(
                (positions[m.segment_id], json.dumps(m.content, sort_keys=True))
                for m in db.scalars(
                    select(Memory).where(Memory.project_id == project_id, Memory.kind == "analysis")
                )
            ),
            "entities": sorted(
                (e.name, tuple(sorted(e.data.get("aliases", []))), names.get(e.merged_into_id))
                for e in db.scalars(select(Entity).where(Entity.project_id == project_id))
            ),
            "relations": sorted(
                (names[r.source_id], names[r.target_id], r.relation_type, r.position, r.active)
                for r in db.scalars(
                    select(CharacterRelation).where(CharacterRelation.project_id == project_id)
                )
            ),
            "glossary": sorted(
                (g.source, g.translation)
                for g in db.scalars(select(Glossary).where(Glossary.project_id == project_id))
            ),
            "chapters": sorted(
                (c.position, json.dumps(c.summary, sort_keys=True), c.analyzed)
                for c in db.scalars(select(Chapter).where(Chapter.project_id == project_id))
            ),
            "bible": db.get(Project, project_id).bible,
        }


def operations(project_ids: list[str]) -> list[str]:
    with SessionLocal() as db:
        return list(
            db.scalars(
                select(RequestLog.operation).where(
                    RequestLog.project_id.in_(project_ids),
                    RequestLog.cached.is_(False),
                    RequestLog.status == "success",
                )
            )
        )


async def run(world, options: dict, *, jitter: float = 0.0, capacity: int = 8) -> list[str]:
    ids, _ = create_world(world, capacity=capacity)
    with respx.mock:
        respx.post(URL).mock(side_effect=simulated_provider(Analyst(), jitter=jitter))
        await analyse(ids, options)
    return ids


# ---------------------------------------------------------------- quality


async def test_parallel_memory_is_at_least_as_good_as_the_strict_one():
    world = build_world(chapters=14, volumes=2, seed=3)
    strict = score(world, await run(world, {"analysis_mode": "strict"}))
    parallel = score(world, await run(world, {"analysis_mode": "parallel"}))
    for key, value in strict.items():
        if key.endswith(("precision", "recall", "resolution")):
            assert parallel[key] >= value - 0.02, (key, parallel[key], value)
    assert parallel["spoilers_in_memory"] == parallel["spoilers_in_prompts"] == 0
    assert strict["spoilers_in_memory"] == strict["spoilers_in_prompts"] == 0
    # Pronouns several passages away from their referent: the earlier-passages memory finds them.
    assert parallel["far_pronoun_recall"] >= strict["far_pronoun_recall"]


async def test_no_passage_is_shown_a_fact_of_a_later_passage():
    world = build_world(chapters=10, volumes=1, seed=5)
    ids = await run(world, {"analysis_mode": "parallel"}, jitter=0.01)
    first_named: dict[str, int] = {}
    for index, passage in enumerate(world.passages()):
        for name in passage.named:
            first_named.setdefault(name, index)
    with SessionLocal() as db:
        positions = dict(
            db.execute(select(Segment.id, Segment.position).where(Segment.project_id == ids[0])).all()
        )
        logs = list(
            db.scalars(
                select(RequestLog).where(
                    RequestLog.project_id == ids[0],
                    RequestLog.operation.in_(ANALYSIS),
                    RequestLog.cached.is_(False),
                )
            )
        )
    checked = 0
    for log in logs:
        at = positions[log.segment_id]
        context = sections("\n".join(m["content"] for m in log.messages if m["role"] == "user"))
        derived = {key: context[key] for key in context if key.startswith(("KNOWN_", "RECENT_", "EARLIER_"))}
        if log.operation == "chapter_extraction":
            assert not derived  # an extraction never reads another passage's analysis
        for name in names_of(derived):
            assert first_named[name] < at, (log.operation, at, name, first_named[name])
            checked += 1
        for item in context.get("EARLIER_PASSAGES") or []:
            assert item["passage"] - 1 < at
    assert checked > 20


def names_of(value) -> list[str]:
    """Every character name written in a JSON value (keys, strings, summaries)."""
    if isinstance(value, dict):
        return [n for item in value.values() for n in names_of(item)]
    if isinstance(value, list):
        return [n for item in value for n in names_of(item)]
    return names_in(value) if isinstance(value, str) else []


async def test_the_same_memory_whatever_the_threads():
    world = build_world(chapters=9, volumes=1, seed=11)
    one = await run(world, {"analysis_mode": "parallel", "threads": 1})
    many = await run(world, {"analysis_mode": "parallel", "threads": 8}, jitter=0.02)
    assert snapshot(one[0]) == snapshot(many[0])
    assert sorted(operations(one)) == sorted(operations(many))


# ---------------------------------------------------------------- resumption


def failing_provider(analyst: Analyst, marker: str, at: int, status: int = 503):
    """Answers like the simulated provider, except the `at`-th request whose system prompt holds `marker`."""
    answer = simulated_provider(analyst)
    seen = {"count": 0, "failed": False}

    async def respond(request):
        body = json.loads(request.content)
        if marker in body["messages"][0]["content"] and not seen["failed"]:
            seen["count"] += 1
            if seen["count"] == at:
                seen["failed"] = True
                return httpx.Response(status, json={"error": "Synthetic outage"})
        return await answer(request)

    return respond, seen


@pytest.mark.parametrize(
    "marker, at",
    [
        ("analyzed on its own", 5),  # extraction
        ("Reconcile the analysis", 4),  # reconciliation
        ("preparing a Book Bible", 3),  # Book Bible tree
    ],
)
async def test_an_outage_at_any_stage_resumes_without_asking_twice(marker, at):
    world = build_world(chapters=8, volumes=1, seed=2)
    baseline = await run(world, {"analysis_mode": "parallel", "threads": 3})
    ids, _ = create_world(world, capacity=8)
    respond, seen = failing_provider(Analyst(), marker, at)
    with respx.mock:
        respx.post(URL).mock(side_effect=respond)
        await analyse(ids, {"analysis_mode": "parallel", "threads": 3})
    assert seen["failed"]
    assert snapshot(ids[0]) == snapshot(baseline[0])
    # Every answer that was saved is reused: the resumed job only asks what it had not got yet.
    assert sorted(operations(ids)) == sorted(operations(baseline))
    with SessionLocal() as db:
        job = db.scalar(select(Job).where(Job.project_id == ids[0]))
        assert job.outage_count == 0 and job.status == "completed"


async def test_a_database_outage_while_writing_the_memory_resumes_in_book_order(monkeypatch):
    world = build_world(chapters=8, volumes=1, seed=4)
    baseline = await run(world, {"analysis_mode": "parallel"})
    store = parallel_analysis._store_analysis
    calls = {"count": 0}

    def flaky(job, owner, sid, result):
        calls["count"] += 1
        if calls["count"] == 6:
            raise OperationalError("synthetic", {}, Exception("database restarting"))
        return store(job, owner, sid, result)

    monkeypatch.setattr(parallel_analysis, "_store_analysis", flaky)
    ids = await run(world, {"analysis_mode": "parallel"})
    assert calls["count"] > 6
    assert snapshot(ids[0]) == snapshot(baseline[0])
    with SessionLocal() as db:
        per_passage = db.execute(
            select(Memory.segment_id, func.count())
            .where(Memory.project_id == ids[0], Memory.kind == "analysis")
            .group_by(Memory.segment_id)
        ).all()
    assert per_passage and all(count == 1 for _, count in per_passage)


async def test_a_rate_limit_halves_the_width_of_the_resumed_job():
    world = build_world(chapters=6, volumes=1, seed=8)
    ids, _ = create_world(world, capacity=8)
    respond, _ = failing_provider(Analyst(), "analyzed on its own", 3, status=429)
    with respx.mock:
        respx.post(URL).mock(side_effect=respond)
        with SessionLocal() as db:
            job = enqueue(db, db.get(Project, ids[0]), "analyze", {"analysis_mode": "parallel"})
            db.commit()
            job_id = job.id
        await execute(*claim())
        with SessionLocal() as db:
            job = db.get(Job, job_id)
            assert job.status == "waiting" and job.stop_reason == "provider_unavailable"
            assert job.checkpoint["throttle"] == 4  # the width was 8
        with SessionLocal() as db:
            db.get(Job, job_id).next_attempt = 0
            db.commit()
        await execute(*claim())
    with SessionLocal() as db:
        assert db.get(Job, job_id).status == "completed"


# ---------------------------------------------------------------- barriers


async def test_translation_starts_only_once_the_whole_analysis_is_done():
    world = build_world(chapters=6, volumes=1, seed=9)
    ids, _ = create_world(world, capacity=8)
    log: list = []
    with respx.mock:
        respx.post(URL).mock(side_effect=simulated_provider(Analyst(), jitter=0.01, log=log))
        await analyse(ids, {"analysis_mode": "parallel", "continue_pipeline": True, "final_review": False})
    kinds = [(event, schema) for event, schema, _ in log]
    last_analysis = max(
        i for i, (event, schema) in enumerate(kinds) if schema in {"ChapterAnalysis", "BookOverview"}
    )
    first_translation = min(i for i, (event, schema) in enumerate(kinds) if schema == "TranslationResult")
    assert kinds[last_analysis][0] == "end" and last_analysis < first_translation
    with SessionLocal() as db:
        assert (
            db.scalar(select(func.count()).where(Segment.project_id == ids[0], Segment.translation == ""))
            == 0
        )


async def test_a_later_volume_waits_for_the_analysis_of_an_earlier_one():
    world = build_world(chapters=5, volumes=2, seed=6)
    ids, _ = create_world(world, capacity=8)
    first, second = ids
    with respx.mock:
        respx.post(URL).mock(side_effect=simulated_provider(Analyst()))
        with SessionLocal() as db:
            later = enqueue(db, db.get(Project, second), "analyze", {"analysis_mode": "parallel"})
            db.flush()
            earlier = enqueue(db, db.get(Project, first), "analyze", {"analysis_mode": "parallel"})
            later.queued_at, earlier.queued_at = 1, 2  # the later volume is picked first
            db.commit()
            later_id, earlier_id = later.id, earlier.id
        claimed = claim()
        assert claimed[0] == later_id
        await execute(*claimed)
        with SessionLocal() as db:
            waiting = db.get(Job, later_id)
            assert waiting.status == "waiting" and waiting.stop_reason == "earlier_volume"
            assert "Volume 1" in waiting.error
            assert waiting.outage_count == 0
            # Extraction went on meanwhile; nothing was reconciled with an incomplete series memory.
            assert set(operations([second])) == {"chapter_extraction"}
        claimed = claim()
        assert claimed[0] == earlier_id
        await execute(*claimed)
        with SessionLocal() as db:
            assert db.get(Job, earlier_id).status == "completed"
            db.get(Job, later_id).next_attempt = 0
            db.commit()
        await execute(*claim())
    with SessionLocal() as db:
        assert db.get(Job, later_id).status == "completed"
        reconciliation = db.scalars(
            select(RequestLog).where(
                RequestLog.project_id == second, RequestLog.operation == "chapter_reconciliation"
            )
        ).all()
    known = [
        sections("\n".join(m["content"] for m in log.messages if m["role"] == "user")).get(
            "SERIES_CONVENTIONS", {}
        )
        for log in reconciliation
    ]
    assert any(item.get("known_identities") for item in known)


async def test_a_paused_earlier_volume_does_not_hold_the_next_one():
    world = build_world(chapters=4, volumes=2, seed=6)
    ids, _ = create_world(world, capacity=8)
    with SessionLocal() as db:
        paused = enqueue(db, db.get(Project, ids[0]), "analyze", {})
        paused.status = "paused"
        db.commit()
    with respx.mock:
        respx.post(URL).mock(side_effect=simulated_provider(Analyst()))
        await analyse([ids[1]], {"analysis_mode": "parallel"})


# ---------------------------------------------------------------- options, width, budget, queue


async def test_strict_mode_is_kept_per_launch_and_per_volume():
    world = build_world(chapters=4, volumes=1, seed=1)
    ids = await run(world, {"analysis_mode": "strict"})
    assert set(operations(ids)) == {"chapter_analysis", "book_analysis"}
    ids, _ = create_world(world, capacity=8)
    with SessionLocal() as db:
        project = db.get(Project, ids[0])
        project.config = {**project.config, "analysis_mode": "strict"}
        db.commit()
    with respx.mock:
        respx.post(URL).mock(side_effect=simulated_provider(Analyst()))
        await analyse(ids, {})
    assert set(operations(ids)) == {"chapter_analysis", "book_analysis"}
    ids = await run(world, {})
    assert set(operations(ids)) == {"chapter_extraction", "chapter_reconciliation", "book_analysis"}


def running_job(project_id: str, **options) -> str:
    with SessionLocal() as db:
        job = enqueue(db, db.get(Project, project_id), "analyze", options)
        job.status, job.lease_owner, job.lease_until = "analyzing", "worker", time.time() + 600
        db.commit()
        return job.id


def test_threads_lower_the_width_within_the_providers_share():
    ids, provider_id = create_world(build_world(chapters=2, volumes=1), capacity=8)
    job_id = running_job(ids[0])
    assert job_parallelism(job_id, "worker", provider_id) == 8
    with SessionLocal() as db:
        project = db.get(Project, ids[0])
        project.config = {**project.config, "threads": 3}
        db.commit()
    assert job_parallelism(job_id, "worker", provider_id) == 3  # the volume's threads
    with SessionLocal() as db:
        job = db.get(Job, job_id)
        job.options = {**job.options, "threads": 5}
        db.commit()
    assert job_parallelism(job_id, "worker", provider_id) == 5  # the launch's threads win
    # Another book starts on the provider: half each, whatever the threads asked for.
    other, _ = create_world(build_world(chapters=2, volumes=1), capacity=8)
    with SessionLocal() as db:
        db.get(Project, other[0]).provider_id = provider_id
        db.commit()
    running_job(other[0])
    with SessionLocal() as db:
        db.get(Job, job_id).options = {"threads": 16}
        db.commit()
    assert job_parallelism(job_id, "worker", provider_id) == 4


def test_the_outage_throttle_halves_then_ramps_back():
    ids, provider_id = create_world(build_world(chapters=2, volumes=1), capacity=8)
    job_id = running_job(ids[0])
    assert job_parallelism(job_id, "worker", provider_id) == 8
    backoff = throttled(job_id, {})
    assert backoff["throttle"] == 4
    with SessionLocal() as db:
        job = db.get(Job, job_id)
        job.checkpoint = {**job.checkpoint, **backoff}
        db.commit()
    assert job_parallelism(job_id, "worker", provider_id) == 4
    with SessionLocal() as db:
        job = db.get(Job, job_id)
        job.checkpoint = {
            **job.checkpoint,
            "throttle_at": time.time() - concurrency.THROTTLE_RAMP_SECONDS - 1,
        }
        db.commit()
    assert job_parallelism(job_id, "worker", provider_id) == 5
    with SessionLocal() as db:
        assert db.get(Job, job_id).checkpoint["throttle"] == 5


def test_a_budget_near_its_cap_narrows_the_calls_in_flight():
    ids, provider_id = create_world(build_world(chapters=2, volumes=1), capacity=8)
    with SessionLocal() as db:
        provider = db.get(Provider, provider_id)
        provider.input_cost, provider.output_cost = 1.0, 1.0  # a reference call costs 0.015
        project = db.get(Project, ids[0])
        project.config = {**project.config, "budget_amount": 0.06}
        db.commit()
    job_id = running_job(ids[0])
    # 90 % of 0.06 leaves room for 3 reference calls: 3 in flight at most, each reserved before it starts.
    assert job_parallelism(job_id, "worker", provider_id) == 3
    with SessionLocal() as db:
        project = db.get(Project, ids[0])
        project.config = {**project.config, "budget_amount": 0.01}
        db.commit()
    assert job_parallelism(job_id, "worker", provider_id) == 1  # one call at a time: `guard` decides
    with SessionLocal() as db:
        project = db.get(Project, ids[0])
        project.config = {**project.config, "budget_amount": 0}
        db.commit()
    assert job_parallelism(job_id, "worker", provider_id) == 8  # no cap


def test_progress_shows_the_phase_of_a_parallel_analysis():
    ids, _ = create_world(build_world(chapters=2, volumes=1), capacity=8)
    job_id = running_job(ids[0])
    with SessionLocal() as db:
        job = db.get(Job, job_id)
        job.checkpoint = {"step": "reconciliation", "current": 5, "total": 10}
        db.commit()
        progress = project_progress(db, db.get(Project, ids[0]))
    assert progress["active_stage"] == "analysis"
    assert progress["analysis_phase"] == {"step": "reconciliation", "current": 5, "total": 10, "percent": 68}
    analysis = next(stage for stage in progress["stages"] if stage["key"] == "analysis")
    assert analysis["percent"] == 68
    with SessionLocal() as db:
        job = db.get(Job, job_id)
        job.checkpoint = {"step": "book_bible", "current": 1, "total": 2, "level": 2, "levels": 3}
        db.commit()
        phase = project_progress(db, db.get(Project, ids[0]))["analysis_phase"]
    assert phase["level"] == 2 and phase["levels"] == 3 and phase["percent"] == 98


def test_the_estimate_counts_extraction_reconciliation_and_the_bible_tree():
    from app.api.estimates import plan

    ids, _ = create_world(build_world(chapters=9, volumes=1, per_chapter=(2, 2)), capacity=8)
    with SessionLocal() as db:
        project = db.get(Project, ids[0])
        steps, passages, _ = plan(db, project, "analyze")
        assert passages == 18
        counts = {step.operation: step.count for step in steps}
        # 9 chapter syntheses, then 3 merges and a root.
        assert counts == {"chapter_extraction": 18, "chapter_reconciliation": 18, "book_analysis": 9 + 3 + 1}
        project.config = {**project.config, "analysis_mode": "strict"}
        steps, _, _ = plan(db, project, "analyze")
        assert {step.operation: step.count for step in steps} == {"chapter_analysis": 18, "book_analysis": 9}


# ---------------------------------------------------------------- the timeline


def test_the_timeline_only_offers_what_earlier_passages_said():
    timeline = Timeline()
    timeline.apply(
        0,
        "c1",
        {"summary": "Mira arrives.", "characters": [{"canonical_name": "Mira Voss", "gender": "female"}]},
    )
    before = timeline.view(1, "c1", "Graymask waited.", None)
    timeline.apply(
        1,
        "c1",
        {"summary": "The reveal.", "characters": [{"canonical_name": "Mira Voss", "aliases": ["Graymask"]}]},
    )
    after = timeline.view(2, "c1", "Graymask waited.", None)
    known = {item.source: json.loads(item.content) for item in before}
    assert known["KNOWN_IDENTITIES"][0]["aliases"] == []
    assert [p["passage"] for p in known["EARLIER_PASSAGES"]] == [1]
    known = {item.source: json.loads(item.content) for item in after}
    assert known["KNOWN_IDENTITIES"][0]["aliases"] == ["Graymask"]
    assert known["RECENT_CHARACTERS"][0] == {
        "canonical_name": "Mira Voss",
        "gender": "female",
        "last_named_passage": 2,
    }


def test_ambiguous_extractions_are_flagged():
    timeline = Timeline()
    timeline.apply(
        0, "c1", {"summary": "", "characters": [{"canonical_name": "Mira Voss"}, {"canonical_name": "Ren"}]}
    )
    assert timeline.ambiguous({"characters": []})  # pronouns only
    assert timeline.ambiguous({"characters": [{"canonical_name": "Mira"}]})  # a short form of a known name
    assert timeline.ambiguous(
        {"characters": [{"canonical_name": "Ren"}], "events": [{"text": "she?", "kind": "unresolved"}]}
    )
    assert not timeline.ambiguous({"characters": [{"canonical_name": "Ren"}]})
    assert not timeline.ambiguous({"characters": [{"canonical_name": "Tomas"}]})


def test_the_world_is_consistent():
    world = build_world(chapters=30, volumes=2)
    assert all(name in GENDER for passage in world.passages() for name in passage.named)
    assert usage([]) == {"calls": 0, "calls_by_operation": {}, "prompt_tokens": 0, "completion_tokens": 0}
