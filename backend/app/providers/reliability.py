"""Enhanced provider reliability with exponential backoff and circuit breaker."""

import math
import random

RETRY_AFTER_CEILING = 86400


def calculate_retry_delay(
    outage_count: int,
    base_delay: int = 60,
    max_delay: int = 3600,
    retry_after: float = 0,
) -> int:
    """
    Calculate adaptive retry delay with exponential backoff and jitter.

    Args:
        outage_count: Number of consecutive failures
        base_delay: Base delay in seconds (default 60)
        max_delay: Maximum delay in seconds (default 3600)
        retry_after: Provider-requested retry delay (Retry-After header)

    Returns:
        Delay in seconds before next retry

    Strategy:
        - First retry uses base_delay exactly, then base * 2^min(outage_count - 1, 6)
        - Jitter (0-10%) avoids a thundering herd and never exceeds max_delay
        - A provider Retry-After can only lengthen the wait, never shorten the backoff:
          it is rounded up, honoured beyond max_delay and bounded to 24 hours
    """
    base_delay = max(1, base_delay)
    max_delay = max(base_delay, max_delay)
    if outage_count <= 1:
        delay = base_delay
    else:
        backoff = min(base_delay * (2 ** min(outage_count - 1, 6)), max_delay)
        delay = min(int(backoff + random.uniform(0, backoff * 0.1)), max_delay)
    requested = math.ceil(retry_after) if math.isfinite(retry_after) and retry_after > 0 else 0
    return max(delay, min(requested, RETRY_AFTER_CEILING))


def should_circuit_break(outage_count: int, threshold: int = 10) -> bool:
    """
    Determine if circuit breaker should trip.

    Args:
        outage_count: Number of consecutive failures
        threshold: Failure count threshold for circuit breaker

    Returns:
        True if circuit should break (stop retrying)
    """
    return outage_count >= threshold


def error_matches_pattern(errors: list[str]) -> bool:
    """
    Detect if recent errors show a repeating pattern.

    Args:
        errors: List of recent error messages

    Returns:
        True if errors show same pattern (indicating systemic issue)
    """
    if len(errors) < 3:
        return False

    # Extract error prefixes (first 100 chars) for pattern matching
    patterns = [e[:100] for e in errors]

    # Check if all patterns are identical
    return len(set(patterns)) == 1
