"""Enhanced provider reliability with exponential backoff and circuit breaker."""

import random
import time


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
        - Respects provider Retry-After header first
        - Uses exponential backoff: base * 2^min(outage_count, 6)
        - Adds jitter (0-10%) to avoid thundering herd
        - Caps at max_delay
    """
    # Provider signal takes absolute priority
    if retry_after > 0:
        return min(int(retry_after), 86400)  # Cap at 24h

    # First retry uses base_delay directly (no exponential), subsequent retries use backoff
    if outage_count == 1:
        return base_delay
    
    # Exponential backoff with cap at 6 doublings (60s -> 3840s)
    backoff_delay = base_delay * (2 ** min(outage_count - 1, 6))
    capped_delay = min(backoff_delay, max_delay)

    # Add jitter to avoid thundering herd (0-10% of delay)
    jitter = random.uniform(0, capped_delay * 0.1)

    return int(capped_delay + jitter)


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
