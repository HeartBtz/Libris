"""Job leases on the database clock (audit R-13).

A lease is a time after which another worker may take the job over. Written and compared with each
process's `time.time()`, it breaks when clocks disagree: a worker whose clock runs ahead grants itself
a longer lease than the others see, one whose clock jumps (NTP step, VM resume) sees every lease
expired and takes over jobs that are alive. The database is the one clock every API process and
worker shares, so leases are written from it and compared with it, in the same statement.
Durations measured inside one process (heartbeat grace, timeouts) keep `time.monotonic()`.
"""

from sqlalchemy import Float, cast, func, literal, select

from app.db import engine

LEASE_SECONDS = 60


def database_now():
    """SQL expression of the database server's current time, in seconds since the epoch."""
    if engine.dialect.name == "postgresql":
        # clock_timestamp(), not now(): the time of the statement, not of the transaction's start.
        return cast(func.extract("epoch", func.clock_timestamp()), Float)
    # SQLite runs in this process: its clock is the host's, shared by every process of the host.
    return (func.julianday(literal("now")) - 2440587.5) * 86400.0


def now(db) -> float:
    return float(db.scalar(select(database_now())))
