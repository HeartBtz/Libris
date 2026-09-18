"""Failed-authentication throttle. Successful requests never count against anyone."""

import time
from collections import defaultdict, deque

from fastapi import HTTPException

WINDOW = 300
ACCOUNT_LIMIT = 20  # failures for one (client, account) pair
CLIENT_LIMIT = 200  # failures for one client address, whatever the account: bounds username spraying
attempts: dict[str, deque] = defaultdict(deque)


def _recent(key: str) -> deque:
    entries = attempts[key]
    limit = time.monotonic() - WINDOW
    while entries and entries[0] < limit:
        entries.popleft()
    return entries


def _sweep() -> None:
    if len(attempts) > 2000:
        for key in [key for key in attempts if not _recent(key)]:
            del attempts[key]


def check(client: str, account: str) -> None:
    if len(_recent(f"{client}|{account}")) >= ACCOUNT_LIMIT or len(_recent(f"{client}|*")) >= CLIENT_LIMIT:
        raise HTTPException(429, "Trop de tentatives échouées. Réessayez dans cinq minutes.")


def failed(client: str, account: str) -> None:
    now = time.monotonic()
    _recent(f"{client}|{account}").append(now)
    _recent(f"{client}|*").append(now)
    _sweep()


def succeeded(client: str, account: str) -> None:
    attempts.pop(f"{client}|{account}", None)
