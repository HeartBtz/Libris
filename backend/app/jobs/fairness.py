"""Fair multi-user queue: the order in which waiting jobs are picked, and the quotas of each account.

The worker no longer takes the oldest job first. Among the jobs it may start (app.jobs.queue.claim),
it prefers, in this order:

1. a job whose lease expired (its worker stopped): it already held a slot;
2. the highest priority (low 0, normal 1, high 2), raised by one level for every
   `aging_minutes` spent waiting, so a low-priority job is never starved;
3. the account (the book's owner) with the fewest jobs running now, then the API token with the
   fewest; so a new account's first job goes before the tenth job of a busy one;
4. the account served least recently (round-robin between accounts of equal load);
5. the job queued first.

The provider limits (`Provider.max_concurrency`, see app.jobs.concurrency) are unchanged and checked
first. On top of them, an account may run at most `max_running` jobs at once and keep at most
`max_queued` jobs and requests waiting; an API token may have lower limits of its own. A job over the
running quota simply waits; a new job or request over the waiting quota is refused (HTTP 429).
"""

from dataclasses import dataclass, field

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.automation_settings import queue_config
from app.models import ApiToken, Job, Project, TranslationRequest, User

PRIORITIES = ("low", "normal", "high")
LOW, NORMAL, HIGH = 0, 1, 2
QUEUED = ("pending", "waiting")


def level(name: str | None, default: int = NORMAL) -> int:
    return PRIORITIES.index(name) if name in PRIORITIES else default


def label(value: int | None) -> str:
    return PRIORITIES[max(LOW, min(HIGH, NORMAL if value is None else value))]


class QueueRefused(Exception):
    """A launch the queue refuses: `status` is the HTTP code, `detail` the API error body."""

    def __init__(self, status: int, detail: dict):
        super().__init__(detail["message"])
        self.status, self.detail = status, detail


def _limit(value) -> int:
    return value if isinstance(value, int) and not isinstance(value, bool) and value > 0 else 0


def account_limits(config: dict, owner_id: str) -> dict:
    """The quotas of one account (0: no limit) and the highest priority it may ask for."""
    own = config["accounts"].get(owner_id) or {}
    return {
        "max_running": _limit(own.get("max_running", config["max_running_per_account"])),
        "max_queued": _limit(own.get("max_queued", config["max_queued_per_account"])),
        "max_priority": own.get("max_priority") if own.get("max_priority") in PRIORITIES else None,
    }


def allowed_priority(db: Session, user: User, config: dict | None = None) -> int:
    """Administrators may ask for high priority; other accounts for normal, unless an administrator
    gave the account a ceiling of its own (Settings › Queue)."""
    own = account_limits(config or queue_config(db), user.id)["max_priority"]
    if own is not None:
        return max(level(own), HIGH if user.admin else LOW)
    return HIGH if user.admin else NORMAL


def requested_priority(db: Session, user: User, requested: str | None, token: ApiToken | None = None) -> int:
    """The priority of a new job: the one asked for, within what the account (and token) allow."""
    ceiling = allowed_priority(db, user)
    if token is not None:
        ceiling = min(ceiling, level(token.max_priority))
    if requested is None:
        return min(NORMAL, ceiling)
    wanted = level(requested)
    if wanted > ceiling:
        raise QueueRefused(
            403,
            {
                "code": "priority_not_allowed",
                "message": f"Priorité « {requested} » refusée : « {label(ceiling)} » au plus pour ce compte "
                "ou ce jeton.",
                "max_priority": label(ceiling),
            },
        )
    return wanted


def waiting_count(db: Session, owner_id: str | None = None, token_id: str | None = None) -> int:
    """Jobs waiting their turn (pending, or waiting for a retry) and automation requests not started."""
    jobs = select(func.count()).select_from(Job).where(Job.status.in_(QUEUED))
    requests = (
        select(func.count())
        .select_from(TranslationRequest)
        .where(TranslationRequest.status == "queued", TranslationRequest.job_id.is_(None))
    )
    if token_id is not None:
        jobs = jobs.where(Job.token_id == token_id)
        requests = requests.where(TranslationRequest.token_id == token_id)
    else:
        jobs = jobs.join(Project, Project.id == Job.project_id).where(Project.owner_id == owner_id)
        requests = requests.where(TranslationRequest.owner_id == owner_id)
    return (db.scalar(jobs) or 0) + (db.scalar(requests) or 0)


def running_count(db: Session, now: float, owner_id: str | None = None, token_id: str | None = None) -> int:
    from app.jobs.queue import RUNNING

    query = select(func.count()).select_from(Job).where(Job.status.in_(RUNNING), Job.lease_until >= now)
    if token_id is not None:
        query = query.where(Job.token_id == token_id)
    else:
        query = query.join(Project, Project.id == Job.project_id).where(Project.owner_id == owner_id)
    return db.scalar(query) or 0


def admit(db: Session, owner_id: str, token: ApiToken | None = None) -> None:
    """Refuses a new job or request when the account (or the token) already has its quota waiting."""
    config = queue_config(db)
    limit = account_limits(config, owner_id)["max_queued"]
    if limit and waiting_count(db, owner_id=owner_id) >= limit:
        raise QueueRefused(
            429,
            {
                "code": "queue_full",
                "message": f"File d’attente pleine pour ce compte : {limit} travaux en attente au plus. "
                "Réessayez quand l’un d’eux aura démarré.",
                "scope": "account",
                "limit": limit,
            },
        )
    limit = _limit(token.max_queued) if token is not None else 0
    if limit and waiting_count(db, token_id=token.id) >= limit:
        raise QueueRefused(
            429,
            {
                "code": "queue_full",
                "message": f"File d’attente pleine pour ce jeton : {limit} travaux en attente au plus. "
                "Réessayez quand l’un d’eux aura démarré.",
                "scope": "token",
                "limit": limit,
            },
        )


@dataclass
class Candidate:
    id: str
    provider_id: str | None
    operation: str
    status: str
    priority: int
    token_id: str | None
    queued_at: float
    created_at: float
    owner_id: str
    project_id: str = ""
    next_attempt: float = 0
    lease_until: float = 0
    reclaim: bool = False


@dataclass
class Standing:
    """Who runs what now: live leases per account, token and provider, and each account's last turn."""

    accounts: dict[str, int] = field(default_factory=dict)
    tokens: dict[str, int] = field(default_factory=dict)
    providers: dict[str, int] = field(default_factory=dict)
    served: dict[str, float] = field(default_factory=dict)


def candidate_query():
    return select(
        Job.id,
        Job.provider_id,
        Job.operation,
        Job.status,
        Job.priority,
        Job.token_id,
        Job.queued_at,
        Job.created_at,
        Project.owner_id,
        Job.project_id,
        Job.next_attempt,
        Job.lease_until,
    ).join(Project, Project.id == Job.project_id)


def candidates(rows) -> list[Candidate]:
    from app.jobs.queue import RUNNING

    return [
        Candidate(
            *row[:9],
            project_id=row[9],
            next_attempt=row[10] or 0,
            lease_until=row[11] or 0,
            reclaim=row[3] in RUNNING,
        )
        for row in rows
    ]


def standing(db: Session, now: float, owners: set[str]) -> Standing:
    from app.jobs.queue import RUNNING

    found = Standing()
    running = db.execute(
        select(Project.owner_id, Job.token_id, Job.provider_id)
        .join(Project, Project.id == Job.project_id)
        .where(Job.status.in_(RUNNING), Job.lease_until >= now)
    )
    for owner_id, token_id, provider_id in running:
        found.accounts[owner_id] = found.accounts.get(owner_id, 0) + 1
        if token_id:
            found.tokens[token_id] = found.tokens.get(token_id, 0) + 1
        if provider_id:
            found.providers[provider_id] = found.providers.get(provider_id, 0) + 1
    if owners:
        found.served = dict(
            db.execute(
                select(Project.owner_id, func.max(Job.claimed_at))
                .join(Project, Project.id == Job.project_id)
                .where(Project.owner_id.in_(owners), Job.claimed_at.is_not(None))
                .group_by(Project.owner_id)
            ).all()
        )
    return found


def effective_priority(candidate: Candidate, now: float, aging_minutes: int) -> int:
    base = max(LOW, min(HIGH, candidate.priority if candidate.priority is not None else NORMAL))
    if aging_minutes <= 0:
        return base
    waited = max(0.0, now - (candidate.queued_at or candidate.created_at or now))
    return min(HIGH, base + int(waited // (aging_minutes * 60)))


def ranked(items: list[Candidate], found: Standing, now: float, aging_minutes: int) -> list[Candidate]:
    def key(item: Candidate):
        return (
            0 if item.reclaim else 1,
            -effective_priority(item, now, aging_minutes),
            found.accounts.get(item.owner_id, 0),
            found.tokens.get(item.token_id, 0) if item.token_id else 0,
            found.served.get(item.owner_id) or 0,
            item.queued_at or item.created_at or 0,
            item.created_at or 0,
            item.id,
        )

    return sorted(items, key=key)


def order(db: Session, rows, now: float) -> list[Candidate]:
    """The jobs of `rows` (from `candidate_query`) in the order the worker picks them."""
    items = candidates(rows)
    found = standing(db, now, {item.owner_id for item in items})
    return ranked(items, found, now, queue_config(db)["aging_minutes"])


def within_quota(db: Session, item: Candidate, now: float, config: dict, full: set[str]) -> bool:
    """Whether the account and the token of `item` may start one more job now.

    The account (then token) row is locked while counting, as the provider row is, so two workers
    cannot both take the last place; a row another worker holds means "not now".
    """
    limit = account_limits(config, item.owner_id)["max_running"]
    if limit:
        if f"account:{item.owner_id}" in full:
            return False
        if (
            db.scalar(select(User.id).where(User.id == item.owner_id).with_for_update(skip_locked=True))
            is None
        ):
            return False
        if running_count(db, now, owner_id=item.owner_id) >= limit:
            full.add(f"account:{item.owner_id}")
            return False
    if item.token_id:
        if f"token:{item.token_id}" in full:
            return False
        token = db.scalar(
            select(ApiToken).where(ApiToken.id == item.token_id).with_for_update(skip_locked=True)
        )
        if token is None:
            return False
        limit = _limit(token.max_running)
        if limit and running_count(db, now, token_id=item.token_id) >= limit:
            full.add(f"token:{item.token_id}")
            return False
    return True


def snapshot(db: Session, now: float, project_ids: set[str] | None = None) -> dict:
    """The queue as the worker sees it: for each waiting job, its place and why it waits.

    `position` is the job's place in the line of its provider (1: next to start when that provider
    has a free slot); `reason` says what holds it: `starting` (it starts within seconds),
    `provider_busy`, `account_limit`, `token_limit`, `retry_scheduled` (a provider outage is being
    waited out until `next_attempt`) or `provider_missing`.
    """
    from app.jobs.queue import RUNNING

    config = queue_config(db)
    rows = db.execute(
        candidate_query().where(
            or_(Job.status.in_(QUEUED), Job.status.in_(RUNNING)),
        )
    ).all()
    live, waiting, later = [], [], []
    for item in candidates(rows):
        if item.status in RUNNING:
            (live if item.lease_until >= now else waiting).append(item)
        elif item.status == "waiting" and item.next_attempt > now:
            later.append(item)
        else:
            waiting.append(item)
    found = standing(db, now, {item.owner_id for item in waiting})
    ordered = ranked(waiting, found, now, config["aging_minutes"])
    capacity = _capacities(db, {item.provider_id for item in ordered if item.provider_id})
    tokens = _token_limits(db, {item.token_id for item in ordered if item.token_id})
    providers, accounts, token_counts = dict(found.providers), dict(found.accounts), dict(found.tokens)
    lines: dict[str, int] = {}
    entries = []
    for item in ordered:
        lane = item.provider_id or f"none:{item.operation}"
        lines[lane] = lines.get(lane, 0) + 1
        reason = "starting"
        account_limit = account_limits(config, item.owner_id)["max_running"]
        if not item.provider_id and item.operation != "sync_memory":
            reason = "provider_missing"
        elif item.provider_id and providers.get(item.provider_id, 0) >= capacity.get(item.provider_id, 1):
            reason = "provider_busy"
        elif account_limit and accounts.get(item.owner_id, 0) >= account_limit:
            reason = "account_limit"
        elif (
            item.token_id
            and tokens.get(item.token_id)
            and token_counts.get(item.token_id, 0) >= tokens[item.token_id]
        ):
            reason = "token_limit"
        else:
            if item.provider_id:
                providers[item.provider_id] = providers.get(item.provider_id, 0) + 1
            accounts[item.owner_id] = accounts.get(item.owner_id, 0) + 1
            if item.token_id:
                token_counts[item.token_id] = token_counts.get(item.token_id, 0) + 1
        entries.append(_entry(item, now, config, reason, lines[lane]))
    for item in sorted(later, key=lambda item: item.next_attempt):
        entries.append(_entry(item, now, config, "retry_scheduled", None))
    visible = project_ids is None
    return {
        "waiting": [e for e in entries if visible or e["project_id"] in project_ids],
        "running": [
            _entry(item, now, config, "running", None)
            for item in live
            if visible or item.project_id in project_ids
        ],
        "totals": {"waiting": len(entries), "running": len(live)},
    }


def _capacities(db: Session, provider_ids: set[str]) -> dict[str, int]:
    from app.models import Provider

    if not provider_ids:
        return {}
    return dict(
        db.execute(select(Provider.id, Provider.max_concurrency).where(Provider.id.in_(provider_ids))).all()
    )


def _token_limits(db: Session, token_ids: set[str]) -> dict[str, int]:
    if not token_ids:
        return {}
    rows = db.execute(select(ApiToken.id, ApiToken.max_running).where(ApiToken.id.in_(token_ids))).all()
    return {token_id: _limit(limit) for token_id, limit in rows}


def _entry(item: Candidate, now: float, config: dict, reason: str, position: int | None) -> dict:
    aging = config["aging_minutes"]
    return {
        "job_id": item.id,
        "project_id": item.project_id,
        "owner_id": item.owner_id,
        "token_id": item.token_id,
        "provider_id": item.provider_id,
        "operation": item.operation,
        "status": item.status,
        "priority": label(item.priority),
        "effective_priority": label(effective_priority(item, now, aging)),
        "queued_at": item.queued_at or item.created_at,
        "next_attempt": item.next_attempt or 0,
        "position": position,
        "reason": reason,
    }
