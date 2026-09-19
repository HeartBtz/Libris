"""The decision log: every choice the autopilot makes in a person's place is recorded with its reason."""

from sqlalchemy.orm import Session

from app.models import AutopilotDecision, Job, Provider


def record(
    db: Session,
    project_id: str,
    *,
    stage: str,
    kind: str,
    action: str,
    reason: str = "",
    job_id: str | None = None,
    segment_id: str | None = None,
    provider: str | Provider | None = None,
    model: str = "",
) -> AutopilotDecision:
    """Adds one decision to the session; the caller commits it with the change it explains."""
    if isinstance(provider, Provider):
        provider, model = provider.name, model or provider.model
    decision = AutopilotDecision(
        project_id=project_id,
        job_id=job_id,
        segment_id=segment_id,
        stage=stage[:40],
        kind=kind[:40],
        action=action[:40],
        reason=reason[:4000],
        provider=(provider or "")[:200],
        model=(model or "")[:200],
    )
    db.add(decision)
    return decision


def provider_of(db: Session, provider_id: str | None) -> Provider | None:
    return db.get(Provider, provider_id) if provider_id else None


def enabled(job: Job) -> bool:
    return bool(job.options.get("autopilot"))
