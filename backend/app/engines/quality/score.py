"""Quality score of a passage, 0–100, from signals Libris already records; no model call.

A passage starts at 100 and loses points for what points to a weak translation: unresolved alerts of the
automatic checks (locked terms first), open review critiques and doubts, a length far from the source,
failed model calls, recovery steps, points the autopilot closed without a correction, a failure or a
retained original. A passage a person validated scores 100: someone has read it. The signals are stored
with the score, so every screen and report can say why a passage ranks low.
"""

import re
import time
from collections import defaultdict
from dataclasses import dataclass, field

from sqlalchemy import and_, case, delete, func, literal_column, or_, select

from app.engines.epub.text import plain
from app.models import AutopilotDecision, Chapter, Issue, Project, RequestLog, Segment
from app.models.quality import FAILED_CALLS, PassageQuality

BATCH = 500
# Bands, by lowest score: the dashboard, the reports and the API use the same ones.
BANDS = (("good", 85), ("fair", 70), ("weak", 50), ("poor", 0))
# Passages under this score are proposed for review, weakest first.
REVIEW_BELOW = 70
REVIEW_LIMIT = 20
CHAPTER_LIMIT = 100
CJK = re.compile(r"[㐀-鿿぀-ヿ가-힯]")
# Same bounds as the automatic length check (app.engines.quality.checks), plus a milder band.
HARD_RATIO = (0.25, 3.5)
SOFT_RATIO = (0.45, 2.4)
# Alerts that other signals already count.
COUNTED_ELSEWHERE = {"source_retained", "length", "locked_term", "content_refusal", "invalid_response"}
DECISION_STAGES = ("recovery", "arbitration", "settle")


@dataclass
class Signals:
    items: dict[str, list[int]] = field(default_factory=dict)  # code -> [count, penalty]

    def add(self, code: str, penalty: int, count: int = 1, cap: int | None = None) -> None:
        entry = self.items.setdefault(code, [0, 0])
        entry[0] += count
        entry[1] = min(entry[1] + penalty, cap) if cap is not None else entry[1] + penalty

    def result(self) -> tuple[int, list[dict]]:
        signals = [
            {"code": code, "count": count, "penalty": penalty}
            for code, (count, penalty) in self.items.items()
        ]
        signals.sort(key=lambda item: (-item["penalty"], item["code"]))
        return max(0, 100 - sum(item["penalty"] for item in signals)), signals


def band(score: int) -> str:
    return next(name for name, low in BANDS if score >= low)


def length_penalty(source: str, translation: str) -> int:
    source, translation = plain(source).strip(), plain(translation).strip()
    if len(source) <= 80 or not translation:
        return 0
    ratio = len(translation) / len(source)
    if not HARD_RATIO[0] <= ratio <= HARD_RATIO[1]:
        return 15
    # Ideographic sources legitimately grow a lot in alphabetic languages: only the hard bounds apply.
    if not CJK.search(source) and not SOFT_RATIO[0] <= ratio <= SOFT_RATIO[1]:
        return 5
    return 0


def score_passage(
    segment: dict, issues: list[tuple[str, str]], decisions: list[tuple[str, str]], failed_calls: int
) -> tuple[int, list[dict]]:
    """`segment`: the passage's columns; `issues`: its unresolved (code, severity); `decisions`: (stage, action)."""
    signals = Signals()
    if segment["validated"]:
        return 100, [{"code": "validated", "count": 1, "penalty": 0}]
    if segment["retained_source"]:
        signals.add("source_retained", 60)
    elif segment["status"] in ("error", "refused"):
        signals.add("failed", 40)
    for code, severity in issues:
        if code == "locked_term":
            signals.add("locked_term", 20, cap=40)
        elif code in COUNTED_ELSEWHERE:
            continue
        elif severity == "error":
            signals.add("alert_error", 15, cap=30)
        else:
            signals.add("alert_warning", 8, cap=24)
    for item in segment["critique"] or []:
        if isinstance(item, dict) and not item.get("queued"):
            signals.add("critique", 10 if item.get("severity") == "error" else 5, cap=25)
    if segment["uncertainties"]:
        signals.add("doubt", 5 * len(segment["uncertainties"]), count=len(segment["uncertainties"]), cap=15)
    if not segment["retained_source"]:
        penalty = length_penalty(segment["source"], segment["translation"])
        if penalty:
            signals.add("length_ratio", penalty)
    if failed_calls:
        signals.add("retry", 3 * failed_calls, count=failed_calls, cap=12)
    for stage, action in decisions:
        if stage == "recovery" and action == "recovered":
            signals.add("recovery", 10, cap=20)
        elif stage == "recovery" and action == "kept_translation":
            signals.add("recovery", 12, cap=20)
        elif stage == "settle":
            signals.add("open_points_closed", 12, cap=12)
        elif stage == "arbitration" and action in ("applied", "accepted", "rejected", "deferred"):
            signals.add("arbitration", {"deferred": 4, "rejected": 2}.get(action, 3), cap=12)
    if segment["error"] and not segment["retained_source"] and segment["status"] not in ("error", "refused"):
        signals.add("error_note", 5)
    return signals.result()


def _batches(ids: list[str]):
    for start in range(0, len(ids), BATCH):
        yield ids[start : start + BATCH]


def _upsert(db, rows: list[dict]) -> None:
    table = PassageQuality.__table__
    if db.get_bind().dialect.name == "postgresql":
        from sqlalchemy.dialects.postgresql import insert
    else:
        from sqlalchemy.dialects.sqlite import insert
    statement = insert(table).values(rows)
    updated = ("project_id", "chapter_id", "score", "band", "signals", "revision", "computed_at")
    db.execute(
        statement.on_conflict_do_update(
            index_elements=[table.c.segment_id], set_={name: statement.excluded[name] for name in updated}
        )
    )


def refresh(db, segment_ids) -> None:
    """Recomputes the score of these passages (in batches, in a stable order against lock waits)."""
    for ids in _batches(sorted(set(segment_ids))):
        _refresh(db, ids)


def _refresh(db, ids: list[str]) -> None:
    columns = (
        Segment.id, Segment.project_id, Segment.chapter_id, Segment.source, Segment.translation, Segment.status,
        Segment.retained_source, Segment.validated, Segment.critique, Segment.uncertainties, Segment.error,
        Segment.revision,
    )  # fmt: skip
    segments = [row._asdict() for row in db.execute(select(*columns).where(Segment.id.in_(ids)))]
    scored = [s for s in segments if s["translation"] or s["retained_source"]]
    unscored = [s["id"] for s in segments if not (s["translation"] or s["retained_source"])]
    if unscored:
        db.execute(delete(PassageQuality.__table__).where(PassageQuality.segment_id.in_(unscored)))
    if not scored:
        return
    wanted = [s["id"] for s in scored]
    issues: dict[str, list] = defaultdict(list)
    for sid, code, severity in db.execute(
        select(Issue.segment_id, Issue.code, Issue.severity).where(
            Issue.segment_id.in_(wanted), Issue.resolved.is_(False)
        )
    ):
        issues[sid].append((code, severity))
    decisions: dict[str, list] = defaultdict(list)
    for sid, stage, action in db.execute(
        select(AutopilotDecision.segment_id, AutopilotDecision.stage, AutopilotDecision.action).where(
            AutopilotDecision.segment_id.in_(wanted), AutopilotDecision.stage.in_(DECISION_STAGES)
        )
    ):
        decisions[sid].append((stage, action))
    failed = dict(
        db.execute(
            select(RequestLog.segment_id, func.count())
            .where(RequestLog.segment_id.in_(wanted), RequestLog.status.in_(FAILED_CALLS))
            .group_by(RequestLog.segment_id)
        ).all()
    )
    now = time.time()
    rows = []
    for segment in scored:
        value, signals = score_passage(
            segment,
            issues.get(segment["id"], []),
            decisions.get(segment["id"], []),
            failed.get(segment["id"], 0),
        )
        rows.append(
            {
                "segment_id": segment["id"],
                "project_id": segment["project_id"],
                "chapter_id": segment["chapter_id"],
                "score": value,
                "band": band(value),
                "signals": signals,
                "revision": segment["revision"],
                "computed_at": now,
            }
        )
    _upsert(db, rows)


def repair(db, project_ids: list[str]) -> int:
    """Scores passages that have none, or one computed on an older revision (books translated before the
    scores existed); drops the scores of passages no longer translated. The caller commits."""
    if not project_ids:
        return 0
    translated = or_(Segment.translation != literal_column("''"), Segment.retained_source.is_(True))
    stale = db.scalars(
        select(Segment.id)
        .outerjoin(PassageQuality, PassageQuality.segment_id == Segment.id)
        .where(
            Segment.project_id.in_(project_ids),
            or_(
                and_(
                    translated,
                    or_(PassageQuality.segment_id.is_(None), PassageQuality.revision != Segment.revision),
                ),
                and_(~translated, PassageQuality.segment_id.is_not(None)),
            ),
        )
    ).all()
    if stale:
        refresh(db, stale)
    return len(stale)


def _scope(project_ids: list[str] | None, chapter_ids: list[str] | None):
    if chapter_ids is not None:
        return PassageQuality.chapter_id.in_(chapter_ids)
    return PassageQuality.project_id.in_(project_ids or [])


def summary(db, project_ids: list[str] | None = None, chapter_ids: list[str] | None = None) -> dict:
    """Counts and distribution of the scores in scope (volumes, or chapters)."""
    where = _scope(project_ids, chapter_ids)
    histogram = [0] * 10
    bands = {name: 0 for name, _ in BANDS}
    total, weighted, lowest = 0, 0, None
    for value, count in db.execute(
        select(PassageQuality.score, func.count()).where(where).group_by(PassageQuality.score)
    ):
        total += count
        weighted += value * count
        lowest = value if lowest is None else min(lowest, value)
        histogram[min(value // 10, 9)] += count
        bands[band(value)] += count
    review = db.scalar(
        select(func.count())
        .select_from(PassageQuality)
        .join(Segment, Segment.id == PassageQuality.segment_id)
        .where(where, PassageQuality.score < REVIEW_BELOW, Segment.validated.is_(False))
    )
    return {
        "scored": total,
        "average": round(weighted / total, 1) if total else None,
        "minimum": lowest,
        "bands": bands,
        # Ten buckets: 0–9, 10–19, … 90–100.
        "histogram": histogram,
        "to_review": review or 0,
        "review_below": REVIEW_BELOW,
    }


def weakest_passages(
    db, project_ids: list[str] | None = None, chapter_ids: list[str] | None = None, limit: int = REVIEW_LIMIT
) -> list[dict]:
    """Passages to review first: below REVIEW_BELOW, not validated by a person, weakest first."""
    rows = db.execute(
        select(
            PassageQuality.segment_id, PassageQuality.project_id, PassageQuality.chapter_id, PassageQuality.score,
            PassageQuality.band, PassageQuality.signals, Segment.position, func.substr(Segment.source, 1, 200),
            Chapter.title, Chapter.external_id, Project.title,
        )
        .join(Segment, Segment.id == PassageQuality.segment_id)
        .join(Chapter, Chapter.id == PassageQuality.chapter_id)
        .join(Project, Project.id == PassageQuality.project_id)
        .where(_scope(project_ids, chapter_ids), PassageQuality.score < REVIEW_BELOW, Segment.validated.is_(False))
        .order_by(PassageQuality.score, Project.volume_number, Segment.position)
        .limit(limit)
    ).all()  # fmt: skip
    return [
        {
            "segment_id": sid,
            "project_id": pid,
            "project_title": project_title,
            "chapter_id": cid,
            "chapter_title": title,
            "chapter_external_id": external_id,
            "position": position,
            "score": value,
            "band": name,
            "signals": signals,
            "excerpt": plain(excerpt or ""),
        }
        for sid, pid, cid, value, name, signals, position, excerpt, title, external_id, project_title in rows
    ]


def weakest_chapters(
    db, project_ids: list[str] | None = None, chapter_ids: list[str] | None = None, limit: int = CHAPTER_LIMIT
) -> tuple[list[dict], int]:
    """Chapters ranked weakest first (lowest average, then lowest passage); the total ranked."""
    where = _scope(project_ids, chapter_ids)
    weak = func.coalesce(func.sum(case((PassageQuality.score < REVIEW_BELOW, 1), else_=0)), 0)
    average = func.avg(PassageQuality.score)
    grouped = (
        select(
            PassageQuality.chapter_id,
            func.count().label("scored"),
            average.label("average"),
            func.min(PassageQuality.score).label("minimum"),
            weak.label("weak"),
        )
        .where(where)
        .group_by(PassageQuality.chapter_id)
        .subquery()
    )
    total = db.scalar(select(func.count()).select_from(grouped)) or 0
    rows = db.execute(
        select(
            grouped.c.chapter_id, grouped.c.scored, grouped.c.average, grouped.c.minimum, grouped.c.weak,
            Chapter.title, Chapter.position, Chapter.external_id, Chapter.chapter_number, Project.id, Project.title,
            Project.volume_number,
        )
        .join(Chapter, Chapter.id == grouped.c.chapter_id)
        .join(Project, Project.id == Chapter.project_id)
        .order_by(grouped.c.average, grouped.c.minimum, Project.volume_number, Chapter.position)
        .limit(limit)
    ).all()  # fmt: skip
    passages = dict(
        db.execute(
            select(Segment.chapter_id, func.count())
            .where(Segment.chapter_id.in_([row[0] for row in rows]))
            .group_by(Segment.chapter_id)
        ).all()
    )
    return [
        {
            "chapter_id": cid,
            "project_id": pid,
            "project_title": project_title,
            "volume_number": volume,
            "title": title,
            "position": position,
            "external_id": external_id,
            "number": number,
            "passages": passages.get(cid, 0),
            "scored": scored,
            "average": round(float(avg), 1),
            "minimum": minimum,
            "weak": int(weak_count),
        }
        for cid, scored, avg, minimum, weak_count, title, position, external_id, number, pid, project_title, volume
        in rows
    ], total  # fmt: skip


def volumes(db, project_ids: list[str]) -> list[dict]:
    """Per volume: passages scored, average and weakest score (series dashboard)."""
    if not project_ids:
        return []
    stats = {
        pid: (scored, avg, minimum, int(weak))
        for pid, scored, avg, minimum, weak in db.execute(
            select(
                PassageQuality.project_id,
                func.count(),
                func.avg(PassageQuality.score),
                func.min(PassageQuality.score),
                func.coalesce(func.sum(case((PassageQuality.score < REVIEW_BELOW, 1), else_=0)), 0),
            )
            .where(PassageQuality.project_id.in_(project_ids))
            .group_by(PassageQuality.project_id)
        )
    }
    projects = db.execute(
        select(Project.id, Project.title, Project.volume_number)
        .where(Project.id.in_(project_ids))
        .order_by(Project.volume_number, Project.created_at)
    ).all()
    result = []
    for pid, title, number in projects:
        scored, avg, minimum, weak = stats.get(pid, (0, None, None, 0))
        result.append(
            {
                "project_id": pid,
                "title": title,
                "volume_number": number,
                "scored": scored,
                "average": round(float(avg), 1) if avg is not None else None,
                "minimum": minimum,
                "weak": weak,
            }
        )
    return result


def dashboard(db, project_ids: list[str]) -> dict:
    """Everything the quality dashboard of a volume or a series shows; repairs missing scores first."""
    if repair(db, project_ids):
        db.commit()
    chapters, ranked = weakest_chapters(db, project_ids)
    return {
        "summary": summary(db, project_ids),
        "chapters": chapters,
        "chapters_total": ranked,
        "review_first": weakest_passages(db, project_ids),
        "bands": [{"band": name, "from": low} for name, low in BANDS],
    }


def report(db, project_ids: list[str] | None = None, chapter_ids: list[str] | None = None) -> dict:
    """The quality part of a completion report: distribution, weakest chapters and passages (bounded)."""
    return {
        **summary(db, project_ids, chapter_ids),
        "weakest_chapters": weakest_chapters(db, project_ids, chapter_ids, limit=10)[0],
        "review_first": weakest_passages(db, project_ids, chapter_ids, limit=10),
    }
