"""Cost budgets: a spending cap per book and per API token, in the currency of the provider prices.

A book's cap is its own (`config["budget_amount"]`, 0: none) or the installation default
(BUDGET_DEFAULT_BOOK or Settings › Budgets). It covers everything the book has cost, every job included,
as the statistics count it (`usage_daily` plus the requests not rolled up yet). A token's cap
(`api_tokens.budget_amount`) covers the model calls of the requests made with it, per calendar month
(UTC) or over its whole life; an ended request counts with the cost kept on it.

Three moments:
- before a launch, the estimate (app.api.estimates) is compared with what remains: over it, the launch
  is refused or only warned about (`on_estimate`); a cap already reached always refuses;
- before every model call of a job (`guard`), the spend is compared with the cap: from the switch
  threshold on, the job moves to a cheaper provider of its fallback chain when there is one, otherwise it
  is paused (stop reason `budget_exceeded`) and resumes once the budget is raised; at the cap, it pauses;
- at the end, the reports give the estimated and the real cost (`cost_report`).

Each switch or pause is recorded in the decision log (stage "budget") with its figures.
"""

from dataclasses import dataclass
from datetime import UTC, datetime

from fastapi import HTTPException
from sqlalchemy import Float, cast, func, select
from sqlalchemy.orm import Session

from app.automation_settings import budget_config
from app.db import SessionLocal
from app.jobs.queue import JobStopped, emit, fence
from app.models import ApiToken, AutopilotDecision, Job, Project, Provider, RequestLog, TranslationRequest

STOP_PAUSED = "budget_exceeded"
STOP_SWITCHED = "budget_fallback"
# A reference call (context-heavy prompt, short answer) to compare the prices of two providers.
REFERENCE_INPUT, REFERENCE_OUTPUT = 14_000, 1_000


@dataclass
class Limit:
    scope: str  # "book" or "token"
    amount: float
    spent: float
    label: str  # "Budget du livre", "Budget du jeton d’API « name »"
    period: str = "total"  # "total" or "month"
    token_id: str | None = None

    @property
    def ratio(self) -> float:
        return self.spent / self.amount if self.amount else 0.0

    @property
    def remaining(self) -> float:
        return max(0.0, self.amount - self.spent)


def money(value: float | None) -> str:
    return f"{value or 0:.2f}"


def percent(ratio: float) -> int:
    return int(ratio * 100)


def unit_price(provider: Provider | None) -> float:
    if provider is None:
        return 0.0
    return (
        REFERENCE_INPUT * (provider.input_cost or 0) + REFERENCE_OUTPUT * (provider.output_cost or 0)
    ) / 1e6


def book_amount(db: Session, project: Project) -> float | None:
    """The book's cap: its own (0 turns the default off), else the installation's; None: no cap."""
    own = (project.config or {}).get("budget_amount")
    if own is None:
        own = budget_config(db)["default_book"]
    return float(own) if own and float(own) > 0 else None


def book_spent(db: Session, project_id: str) -> float:
    from app.maintenance.usage import usage

    rows = usage(db, ("project_id",), ("project_id", [project_id]))
    return float(sum(row[-1] or 0 for row in rows))


def period_start(period: str, now: float | None = None) -> float | None:
    if period != "month":
        return None
    moment = datetime.fromtimestamp(now, UTC) if now is not None else datetime.now(UTC)
    return moment.replace(day=1, hour=0, minute=0, second=0, microsecond=0).timestamp()


def period_end(period: str, now: float | None = None) -> float | None:
    start = period_start(period, now)
    if start is None:
        return None
    first = datetime.fromtimestamp(start, UTC)
    following = (
        first.replace(year=first.year + 1, month=1)
        if first.month == 12
        else first.replace(month=first.month + 1)
    )
    return following.timestamp()


def token_spent(db: Session, token: ApiToken, now: float | None = None) -> float:
    """Ended requests with their kept cost (in the period they ended), the others from their calls."""
    from app.maintenance.usage import request_cost

    start = period_start(token.budget_period, now)
    ended = select(func.coalesce(func.sum(TranslationRequest.cost), 0)).where(
        TranslationRequest.token_id == token.id, TranslationRequest.cost.is_not(None)
    )
    if start is not None:
        ended = ended.where(TranslationRequest.finished_at >= start)
    jobs = select(TranslationRequest.job_id).where(
        TranslationRequest.token_id == token.id,
        TranslationRequest.cost.is_(None),
        TranslationRequest.job_id.is_not(None),
    )
    live = (
        select(func.coalesce(func.sum(cast(request_cost(), Float)), 0))
        .select_from(RequestLog)
        .outerjoin(Provider, RequestLog.provider_id == Provider.id)
        .where(RequestLog.job_id.in_(jobs))
    )
    if start is not None:
        live = live.where(RequestLog.created_at >= start)
    return float(db.scalar(ended) or 0) + float(db.scalar(live) or 0)


def token_label(token: ApiToken) -> str:
    return f"Budget du jeton d’API « {token.name} »"


def token_limit(db: Session, token: ApiToken | None) -> Limit | None:
    if token is None or not token.budget_amount or token.budget_amount <= 0:
        return None
    return Limit("token", float(token.budget_amount), token_spent(db, token), token_label(token),
                 token.budget_period, token.id)  # fmt: skip


def limits(db: Session, project: Project, token: ApiToken | None = None) -> list[Limit]:
    found = []
    amount = book_amount(db, project)
    if amount:
        found.append(Limit("book", amount, book_spent(db, project.id), "Budget du livre"))
    if limit := token_limit(db, token):
        found.append(limit)
    return found


def request_token(db: Session, request_id: str | None) -> ApiToken | None:
    """The token an automation request was made with, if any."""
    request = db.get(TranslationRequest, request_id) if request_id else None
    return db.get(ApiToken, request.token_id) if request and request.token_id else None


def job_token(db: Session, job: Job) -> ApiToken | None:
    return request_token(db, (job.options or {}).get("translation_request"))


def cheaper(
    db: Session, project: Project, provider_id: str | None, tried: list[str], free: bool = False
) -> Provider | None:
    """The first provider of the fallback chain cheaper than `provider_id` (free ones only when `free`)
    and not tried yet."""
    from app.engines.autopilot.providers import chain

    current = db.get(Provider, provider_id) if provider_id else None
    if current is None:
        return None
    price = unit_price(current)
    for candidate_id in chain(db, project, provider_id):
        if candidate_id == provider_id or candidate_id in tried:
            continue
        candidate = db.get(Provider, candidate_id)
        if (
            candidate is not None
            and unit_price(candidate) < price
            and (not free or unit_price(candidate) == 0)
        ):
            return candidate
    return None


def decide(
    db: Session, project: Project, provider_id: str | None, limit: Limit, tried: list[str], switched: bool
) -> tuple[str, Provider | None]:
    """("continue" | "switch" | "pause", the provider to switch to) for a job at `limit`.

    From the threshold on, a cheaper provider takes over; once the job already switched and none is
    left, it may spend up to the cap. At the cap only a free provider (no price) may go on.
    """
    threshold = budget_config(db)["switch_threshold"]
    if limit.ratio < threshold:
        return "continue", None
    current = db.get(Provider, provider_id) if provider_id else None
    if limit.ratio >= 1:
        if current is not None and unit_price(current) == 0:
            return "continue", None
        replacement = cheaper(db, project, provider_id, tried, free=True)
        return ("switch", replacement) if replacement else ("pause", None)
    replacement = cheaper(db, project, provider_id, tried)
    if replacement:
        return "switch", replacement
    return ("continue", None) if switched else ("pause", None)


# Messages. Figures are in the currency of the provider prices.


def reached_message(limit: Limit) -> str:
    return (
        f"{limit.label} atteint ({money(limit.spent)} sur {money(limit.amount)}) : relevez-le avant de lancer "
        "ou de reprendre un travail."
    )


def pause_message(limit: Limit) -> str:
    return (
        f"{limit.label} atteint à {percent(limit.ratio)} % ({money(limit.spent)} sur {money(limit.amount)}) : "
        "travail mis en pause. Relevez le budget, puis reprenez le travail."
    )


def switch_message(limit: Limit, provider: Provider) -> str:
    return (
        f"{limit.label} atteint à {percent(limit.ratio)} % ({money(limit.spent)} sur {money(limit.amount)}) : "
        f"le travail continue avec « {provider.name} », fournisseur moins cher."
    )


def estimate_message(limit: Limit, estimate: float, refused: bool) -> str:
    if refused:
        return (
            f"Coût estimé {money(estimate)} pour {money(limit.remaining)} restants ({limit.label}) : lancement "
            "refusé. Relevez le budget ou lancez un travail plus petit."
        )
    return (
        f"Coût estimé {money(estimate)} pour {money(limit.remaining)} restants ({limit.label}) : le travail "
        "sera mis en pause près du plafond."
    )


def token_refusal_message(limit: Limit) -> str:
    period = "ce mois-ci" if limit.period == "month" else "depuis sa création"
    return (
        f"{limit.label} atteint ({money(limit.spent)} sur {money(limit.amount)}, {period}) : requête refusée. "
        "Relevez le budget du jeton ou attendez la période suivante."
    )


# Before a launch.


def forecast(db: Session, project: Project, operations: tuple[str, ...]) -> float | None:
    """Estimated cost of the operations (app.api.estimates); None without a provider."""
    from app.api.estimates import forecast as estimate

    if not project.provider_id:
        return None
    return round(sum(estimate(db, project, operation)["cost"] for operation in operations), 4)


def blocking_limit(
    db: Session, project: Project, found: list[Limit], provider_id: str | None
) -> Limit | None:
    """A cap a new job could not work under: reached, or past the threshold with no cheaper provider."""
    for limit in found:
        if decide(db, project, provider_id, limit, [], False)[0] == "pause":
            return limit
    return None


def admit(
    db: Session, project: Project, operations: tuple[str, ...], token: ApiToken | None = None,
    provider_id: str | None = None,
) -> tuple[str | None, dict]:  # fmt: skip
    """Before a job starts: (why it is refused or None, what to keep in `job.options["budget"]`).

    `operations` are the estimated ones ("analyze", "translate", "review"); empty for a job whose scope
    the estimate does not describe (a chapter, a selection), which is only checked against reached caps.
    """
    found = limits(db, project, token)
    kept: dict = {}
    estimate = forecast(db, project, operations) if operations else None
    if estimate is not None:
        kept["estimate"] = estimate
    if not found:
        return None, kept
    kept["caps"] = [
        {"scope": limit.scope, "amount": limit.amount, "spent": round(limit.spent, 6)} for limit in found
    ]
    blocked = blocking_limit(db, project, found, provider_id or project.provider_id)
    if blocked:
        return reached_message(blocked), kept
    if estimate is None:
        return None, kept
    refuse = budget_config(db)["on_estimate"] == "refuse"
    for limit in found:
        if estimate > limit.remaining:
            if refuse:
                return estimate_message(limit, estimate, True), kept
            kept["warning"] = estimate_message(limit, estimate, False)
            break
    return None, kept


def refuse_token_request(db: Session, token: ApiToken, start: bool) -> None:
    """A request that would start work is refused (HTTP 402) once its token's cap is reached."""
    if not start:
        return
    limit = token_limit(db, token)
    if limit is None or limit.spent < limit.amount:
        return
    raise HTTPException(
        402,
        {
            "code": "budget_exceeded",
            "message": token_refusal_message(limit),
            "budget": {
                "amount": limit.amount,
                "spent": round(limit.spent, 6),
                "period": limit.period,
                "resets_at": period_end(limit.period),
            },
        },
    )


def resume_refusal(db: Session, project: Project, job: Job) -> str | None:
    """Resuming a job paused for its budget is refused until the budget is raised."""
    if job.stop_reason != STOP_PAUSED:
        return None
    found = limits(db, project, job_token(db, job))
    blocked = blocking_limit(db, project, found, job.options.get("provider_id") or project.provider_id)
    return reached_message(blocked) if blocked else None


# During a job.


def guard(job_id: str, owner: str) -> None:
    """Before a model call of a job: switch provider or pause (JobStopped) when a cap is near."""
    with SessionLocal() as db:
        job = db.get(Job, job_id)
        if job is None or job.lease_owner != owner:
            return
        project = db.get(Project, job.project_id)
        found = limits(db, project, job_token(db, job))
        if not found:
            return
        if max(limit.ratio for limit in found) < budget_config(db)["switch_threshold"]:
            return
        job = fence(db, job_id, owner)
        progress = dict(job.checkpoint or {})
        tried = [value for value in progress.get("budget_providers_tried") or [] if value]
        switched = bool(progress.get("budget_switched"))
        # The strictest cap decides: a pause wins over a switch, a switch over going on.
        verdicts = [(limit, *decide(db, project, job.provider_id, limit, tried, switched)) for limit in found]
        order = {"pause": 0, "switch": 1, "continue": 2}
        limit, verdict, replacement = min(verdicts, key=lambda item: (order[item[1]], -item[0].ratio))
        if verdict == "continue":
            db.rollback()
            return
        from app.engines.autopilot.decisions import record

        failing = db.get(Provider, job.provider_id) if job.provider_id else None
        if replacement is not None:
            reason = switch_message(limit, replacement)
            job.checkpoint = {
                **progress,
                "budget_switched": True,
                "budget_providers_tried": list(dict.fromkeys([*tried, job.provider_id, replacement.id])),
            }
            job.provider_id = replacement.id
            job.status, job.stop_reason, job.next_attempt = "pending", STOP_SWITCHED, 0
            action, provider = "fallback_provider", replacement
        else:
            reason = pause_message(limit)
            job.status, job.stop_reason, job.next_attempt = "paused", STOP_PAUSED, 0
            action, provider = "paused", failing
        job.error = reason
        job.lease_owner, job.lease_until = "", 0
        project.status = job.status
        record(db, project.id, job_id=job.id, stage="budget", kind=limit.scope, action=action, reason=reason,
               provider=provider)  # fmt: skip
        emit(db, project.id, job_id=job.id, status=job.status, reason=job.stop_reason, error=reason)
        db.commit()
    raise JobStopped()


def parallel_width(job_id: str) -> int | None:
    """Calls a job may keep in flight at once without its caps being overshot together; None: no cap.

    `guard` only sees calls already paid for: N calls started side by side would all pass it and cross
    the cap together. Each call in flight is therefore reserved, before it starts, at the price of a
    reference call of the job's provider: the spend plus the reservations stays below the switch
    threshold. Near it the job narrows down to one call at a time, and `guard` decides as for a
    sequential job (app.jobs.concurrency.job_parallelism reads this before each start).
    """
    with SessionLocal() as db:
        job = db.get(Job, job_id)
        project = db.get(Project, job.project_id) if job else None
        if project is None:
            return None
        found = limits(db, project, job_token(db, job))
        price = unit_price(db.get(Provider, job.provider_id) if job.provider_id else None)
        if not found or price <= 0:
            return None
        threshold = budget_config(db)["switch_threshold"]
        room = min(limit.amount * threshold - limit.spent for limit in found)
        return max(1, int(room // price))


# Reports.


def job_cost(db: Session, job: Job) -> float | None:
    from app.engines.delivery.report import usage

    return usage(db, job)["cost"]


def cost_report(db: Session, job: Job | None) -> dict | None:
    """Estimated against real cost of a job, with the book's cap; None without a job."""
    if job is None:
        return None
    kept = (job.options or {}).get("budget") or {}
    project = db.get(Project, job.project_id)
    amount = book_amount(db, project) if project else None
    return {
        "estimated": kept.get("estimate"),
        "actual": job_cost(db, job),
        "budget": amount,
        "book_spent": round(book_spent(db, project.id), 6) if project else None,
        "warning": kept.get("warning"),
        "paused_for_budget": job.stop_reason == STOP_PAUSED,
        "provider_switches": db.scalar(
            select(func.count())
            .select_from(AutopilotDecision)
            .where(
                AutopilotDecision.job_id == job.id,
                AutopilotDecision.stage == "budget",
                AutopilotDecision.action == "fallback_provider",
            )
        ),
    }


def book_view(db: Session, project: Project) -> dict:
    """What the book panel shows: its cap, what it has cost, and its last job's estimate against reality."""
    from app.api.estimates import price_note

    config = budget_config(db)
    own = (project.config or {}).get("budget_amount")
    amount = book_amount(db, project)
    spent = book_spent(db, project.id)
    ratio = spent / amount if amount else 0.0
    state = "none" if not amount else "exceeded" if ratio >= 1 else "ok"
    if state == "ok" and ratio >= config["switch_threshold"]:
        state = "near"
    last = db.scalar(select(Job).where(Job.project_id == project.id).order_by(Job.created_at.desc()).limit(1))
    provider = db.get(Provider, project.provider_id) if project.provider_id else None
    return {
        "amount": amount,
        "own_amount": own,
        "default_amount": config["default_book"] or None,
        "spent": round(spent, 6),
        "remaining": round(max(0.0, amount - spent), 6) if amount else None,
        "ratio": round(ratio, 4),
        "state": state,
        "switch_threshold": config["switch_threshold"],
        "on_estimate": config["on_estimate"],
        "priced": bool(provider and (provider.input_cost or provider.output_cost)),
        "currency_note": price_note(provider),
        "last_job": {
            "id": last.id,
            "status": last.status,
            "stop_reason": last.stop_reason,
            **cost_report(db, last),
        }
        if last
        else None,
    }


def estimate_view(db: Session, project: Project, cost: float) -> dict | None:
    """The estimate against the book's remaining budget, for the confirmation before a launch."""
    amount = book_amount(db, project)
    if not amount:
        return None
    spent = book_spent(db, project.id)
    remaining = max(0.0, amount - spent)
    return {
        "amount": amount,
        "spent": round(spent, 6),
        "remaining": round(remaining, 6),
        "exceeds": cost > remaining,
        "on_estimate": budget_config(db)["on_estimate"],
    }


def token_view(db: Session, token: ApiToken) -> dict | None:
    if not token.budget_amount:
        return None
    return {
        "amount": token.budget_amount,
        "period": token.budget_period,
        "spent": round(token_spent(db, token), 6),
        "resets_at": period_end(token.budget_period),
    }
