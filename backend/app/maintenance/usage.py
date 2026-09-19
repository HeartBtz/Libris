"""Usage aggregates: requests rolled up by day, read back together with the requests not rolled up yet.

    docker compose exec api python -m app.maintenance.usage            # roll up now
    docker compose exec api python -m app.maintenance.usage --dry-run  # count what would be

Statistics used to sum `llm_requests` on every call; the table holds one row per model call (75 000
rows and 8 GB in production before the request bodies were purged). The worker now folds finished
requests into `usage_daily` (see `app.models.usage`) with the retention pass, every hour. A request
is rolled up once it is older than ROLLUP_DELAY: by then it has its final outcome, tokens and time.
The watermark (`app_settings["usage_rollup"]`) says which requests are counted in the aggregates;
the statistics add the requests created since, a few hours of rows read through their creation-time
index. Totals are therefore the same as before, at a cost that no longer grows with the history.
"""

import argparse
import time
from datetime import UTC, datetime

from sqlalchemy import Float, cast, func, select

from app.db import SessionLocal
from app.models import AppSetting, Provider, RequestLog
from app.models.usage import UsageDaily

KEY = "usage_rollup"
DAY = 86400
# A model call ends within its provider's timeout (at most an hour); a request older than this has
# its final state. A row still "running" by then was abandoned and is counted as such.
ROLLUP_DELAY = 2 * 3600
DIMENSIONS = ("project_id", "provider_id", "operation", "model", "status", "cached")
SUMS = ("requests", "prompt_tokens", "completion_tokens", "duration", "cost")


def day_of(timestamp: float) -> str:
    return datetime.fromtimestamp(timestamp, UTC).strftime("%Y-%m-%d")


def request_cost():
    """Cost of a request at the price recorded with it (the provider's current one for older rows)."""
    return (
        RequestLog.prompt_tokens * func.coalesce(RequestLog.input_cost, Provider.input_cost, 0)
        + RequestLog.completion_tokens * func.coalesce(RequestLog.output_cost, Provider.output_cost, 0)
    ) / 1_000_000


def request_column(name: str):
    if name == "provider_id":
        return func.coalesce(RequestLog.provider_id, "")
    return getattr(RequestLog, name)


def watermark(db) -> float | None:
    """Requests created before this time are in `usage_daily`; None before the first rollup."""
    saved = db.get(AppSetting, KEY)
    return float(saved.value["through"]) if saved else None


def _first_request(db) -> float | None:
    return db.scalar(select(func.min(RequestLog.created_at)))


def rollup(now: float | None = None, dry_run: bool = False) -> int:
    """Fold the settled requests into `usage_daily`, one UTC day per transaction; the requests counted."""
    now = time.time() if now is None else now
    cutoff = now - ROLLUP_DELAY
    done = 0
    position = None  # a dry run writes no watermark: it keeps its progress here
    while True:
        with SessionLocal() as db:
            # Serializes concurrent rollups (API processes, worker): a second one waits, then sees
            # the watermark the first one moved and adds nothing twice.
            saved = db.scalar(select(AppSetting).where(AppSetting.key == KEY).with_for_update())
            if position is not None:
                start = position
            else:
                start = float(saved.value["through"]) if saved else _first_request(db)
            if start is None:
                if not dry_run:
                    db.add(AppSetting(key=KEY, value={"through": cutoff}))
                    db.commit()
                return done
            if start >= cutoff:
                return done
            # One UTC day at a time: short transactions, and each window maps to a single day.
            end = min(cutoff, (int(start) // DAY + 1) * DAY)
            groups = db.execute(
                select(
                    *(request_column(name) for name in DIMENSIONS),
                    func.count(),
                    func.coalesce(func.sum(RequestLog.prompt_tokens), 0),
                    func.coalesce(func.sum(RequestLog.completion_tokens), 0),
                    func.coalesce(func.sum(RequestLog.duration), 0),
                    func.coalesce(func.sum(cast(request_cost(), Float)), 0),
                )
                .outerjoin(Provider, RequestLog.provider_id == Provider.id)
                .where(RequestLog.created_at >= start, RequestLog.created_at < end)
                .group_by(*(request_column(name) for name in DIMENSIONS))
            ).all()
            done += sum(row[len(DIMENSIONS)] for row in groups)
            if dry_run:
                position = end
            else:
                day = day_of(start)
                for row in groups:
                    key = dict(zip(DIMENSIONS, row[: len(DIMENSIONS)], strict=True))
                    _fold(db, day, key, dict(zip(SUMS, row[len(DIMENSIONS) :], strict=True)))
                if saved is None:
                    db.add(AppSetting(key=KEY, value={"through": end}))
                else:
                    saved.value = {"through": end}
                db.commit()
            if end >= cutoff:
                return done


def _fold(db, day: str, key: dict, values: dict) -> None:
    existing = db.scalar(select(UsageDaily).filter_by(day=day, **key))
    if existing is None:
        db.add(UsageDaily(day=day, **key, **values))
        db.flush()
    else:
        for name, value in values.items():
            setattr(existing, name, getattr(existing, name) + value)


def absorb(db, project_id: str) -> int:
    """Requests added with a past date (archive restore) behind the watermark: counted now, in `db`'s
    transaction, or the statistics would never see them."""
    through = watermark(db)
    if through is None:
        return 0
    db.flush()
    rows = db.execute(
        select(
            RequestLog.created_at,
            *(request_column(name) for name in DIMENSIONS),
            RequestLog.prompt_tokens,
            RequestLog.completion_tokens,
            RequestLog.duration,
            cast(request_cost(), Float),
        )
        .outerjoin(Provider, RequestLog.provider_id == Provider.id)
        .where(RequestLog.project_id == project_id, RequestLog.created_at < through)
    ).all()
    for created_at, *row in rows:
        key = dict(zip(DIMENSIONS, row[: len(DIMENSIONS)], strict=True))
        values = dict(zip(SUMS, [1, *(value or 0 for value in row[len(DIMENSIONS) :])], strict=True))
        _fold(db, day_of(created_at), key, values)
    return len(rows)


def usage(db, by: tuple[str, ...], *filters) -> list[tuple]:
    """Sums per group: rows of `by` + (requests, prompt_tokens, completion_tokens, duration, cost).

    `by` names columns common to requests and aggregates (DIMENSIONS); `filters` are
    `(name, values)` pairs restricting a dimension to some values. Aggregates and the requests not
    rolled up yet are added together, so the result does not depend on when the rollup last ran.
    """
    through = watermark(db)
    totals: dict[tuple, list] = {}

    def add(rows):
        for row in rows:
            key = tuple(row[: len(by)])
            current = totals.setdefault(key, [0, 0, 0, 0.0, 0.0])
            for index, value in enumerate(row[len(by) :]):
                current[index] += value or 0

    if through is not None:
        aggregated = select(
            *(getattr(UsageDaily, name) for name in by),
            *(func.sum(getattr(UsageDaily, name)) for name in SUMS),
        )
        for name, values in filters:
            aggregated = aggregated.where(getattr(UsageDaily, name).in_(values))
        add(db.execute(aggregated.group_by(*(getattr(UsageDaily, name) for name in by))).all())
    recent = (
        select(
            *(request_column(name) for name in by),
            func.count(),
            func.sum(RequestLog.prompt_tokens),
            func.sum(RequestLog.completion_tokens),
            func.sum(RequestLog.duration),
            func.sum(cast(request_cost(), Float)),
        )
        .select_from(RequestLog)
        .outerjoin(Provider, RequestLog.provider_id == Provider.id)
    )
    if through is not None:
        recent = recent.where(RequestLog.created_at >= through)
    for name, values in filters:
        recent = recent.where(request_column(name).in_(values))
    add(db.execute(recent.group_by(*(request_column(name) for name in by))).all())
    return [(*key, *values) for key, values in totals.items()]


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--dry-run", action="store_true", help="count without writing")
    arguments = parser.parse_args()
    count = rollup(dry_run=arguments.dry_run)
    print(f"{count} requests {'would be' if arguments.dry_run else 'were'} rolled up into usage_daily.")
