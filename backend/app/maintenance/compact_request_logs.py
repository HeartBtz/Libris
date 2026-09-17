"""Rewrite existing request logs in the compact form new rows already use.

    docker compose exec api python -m app.maintenance.compact_request_logs --dry-run
    docker compose exec api python -m app.maintenance.compact_request_logs

Idempotent and resumable: rows are processed in primary-key order and committed per batch.
PostgreSQL only returns the freed space to the operating system after
`VACUUM (FULL, ANALYZE) llm_requests;`, which locks the table: stop the worker first.
"""

import argparse
import json

from sqlalchemy import select

from app.db import SessionLocal
from app.models import RequestLog
from app.providers.tracing import compact_parameters, compact_trace


def size(value) -> int:
    return len(json.dumps(value, ensure_ascii=False))


def compact(batch: int = 500, dry_run: bool = False) -> dict:
    totals = {"rows": 0, "changed": 0, "before": 0, "after": 0}
    last = ""
    while True:
        with SessionLocal() as db:
            logs = list(
                db.scalars(
                    select(RequestLog).where(RequestLog.id > last).order_by(RequestLog.id).limit(batch)
                )
            )
            if not logs:
                return totals
            for log in logs:
                parameters, context = compact_parameters(log.parameters or {}), compact_trace(log.context or {})
                totals["rows"] += 1
                totals["before"] += size(log.parameters) + size(log.context)
                totals["after"] += size(parameters) + size(context)
                if parameters != log.parameters or context != log.context:
                    totals["changed"] += 1
                    if not dry_run:
                        log.parameters, log.context = parameters, context
            last = logs[-1].id
            if not dry_run:
                db.commit()
        print(f"{totals['rows']} rows scanned, {totals['changed']} compacted", flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--dry-run", action="store_true", help="measure without writing")
    parser.add_argument("--batch", type=int, default=500)
    arguments = parser.parse_args()
    result = compact(arguments.batch, arguments.dry_run)
    saved = result["before"] - result["after"]
    print(
        f"{'Would free' if arguments.dry_run else 'Freed'} about {saved / 1024**2:.1f} MiB of JSON "
        f"in {result['changed']}/{result['rows']} rows."
    )
