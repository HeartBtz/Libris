"""What the autopilot does instead of stopping when a model call fails for a reason other than an outage.

An analysis, a consistency sample, a context plan, a review or a polish only improves the book: when the
model refuses it or keeps answering badly, the step is skipped (with a recorded reason) and the book goes
on. Outages and credentials are not degraded here: they go to the provider fallback.
"""

from app.db import SessionLocal
from app.engines.autopilot.decisions import record
from app.jobs.queue import fence
from app.models import Job, Provider
from app.providers.llm import LLMError, ProviderAuthenticationRequired, ProviderUnavailable


def degradable(job: Job, exc: BaseException) -> bool:
    """True when the autopilot may skip the failed step (refusal, invalid answers, context too large)."""
    return (
        bool(job.options.get("autopilot"))
        and isinstance(exc, LLMError | ValueError)
        and not isinstance(exc, ProviderUnavailable | ProviderAuthenticationRequired)
    )


def reason_of(exc: BaseException) -> str:
    return f"{type(exc).__name__} : {str(exc)[:600]}"


def note(
    job: Job,
    owner: str,
    *,
    stage: str,
    kind: str,
    action: str,
    reason: str,
    segment_id: str | None = None,
    mark=None,
) -> None:
    """Records a decision of the running job (fenced: a paused or reclaimed job writes nothing).

    `mark(db)` writes, in the same transaction, the job state that keeps a resumed job from retrying.
    """
    with SessionLocal() as db:
        current = fence(db, job.id, owner)
        if mark is not None:
            mark(db)
        record(
            db,
            job.project_id,
            job_id=job.id,
            segment_id=segment_id,
            stage=stage,
            kind=kind,
            action=action,
            reason=reason,
            provider=db.get(Provider, current.provider_id) if current.provider_id else None,
        )
        db.commit()
