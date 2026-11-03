"""When a failed attempt is retried, and after which delay.

One table, three classes of endpoints:

=================  ==========================================================================
READ               network failure, 429, 502-504, an error the server marks ``retryable``
MONEY              (creates and actions) not sent; a transport failure when an
                   Idempotency-Key protects the call; the codes in ``SAFE_RETRY_CODES``.
                   Business errors never: a failed payout keeps its order_id, so a retry
                   would hide the real cause behind a 409. Without a key nothing that was
                   sent is retried.
NON_IDEMPOTENT     (redeliveries, test webhooks) only when nothing was sent
=================  ==========================================================================
"""

from __future__ import annotations

import random
from collections.abc import Callable
from enum import Enum

from enigmoss.errors import APIError, ErrorCode, SendPhase, TransportError

BASE_DELAY = 0.5
MAX_BACKOFF = 8.0
MAX_RETRY_AFTER = 10.0
"""The longest Retry-After a single call waits for; a longer one is raised to the caller."""
ITERATE_MAX_RETRY_AFTER = 60.0
"""Long scans hit the per-IP rate limit; ``iterate()`` waits out a longer Retry-After."""

SAFE_RETRY_CODES = frozenset(
    {
        ErrorCode.RATE_LIMIT_EXCEEDED,
        ErrorCode.LOCK_NOT_ACQUIRED,
        ErrorCode.IDEMPOTENCY_IN_PROGRESS,
        ErrorCode.DEPENDENCY_UNAVAILABLE,
    }
)
"""Codes after which a money call is retried: the server did not execute the request."""


class RetryClass(Enum):
    READ = "read"
    MONEY = "money"
    NON_IDEMPOTENT = "non_idempotent"


def after_transport_failure(cls: RetryClass, error: TransportError, *, has_key: bool) -> bool:
    if error.phase is SendPhase.NOT_SENT:
        return True
    if cls is RetryClass.READ:
        return True
    return cls is RetryClass.MONEY and has_key


def after_error_response(cls: RetryClass, error: APIError, *, has_envelope: bool, has_key: bool) -> bool:
    if cls is RetryClass.NON_IDEMPOTENT:
        return False
    if cls is RetryClass.MONEY and not has_key:
        return False
    if not has_envelope:
        return error.status == 429 or 502 <= error.status <= 504
    if cls is RetryClass.READ:
        return error.retryable
    return error.code in SAFE_RETRY_CODES


def delay(
    attempt: int, retry_after: int | None, max_retry_after: float, rand: Callable[[], float]
) -> float | None:
    """Seconds to wait before the next attempt, or ``None`` when the wait is too long.

    ``attempt`` is the 1-based number of the attempt that failed. Exponential backoff with full
    jitter, 0.5 s doubling up to 8 s; a server ``Retry-After`` wins when it is short enough.
    """
    if retry_after is not None:
        return float(retry_after) if retry_after <= max_retry_after else None
    cap = min(MAX_BACKOFF, BASE_DELAY * 2.0 ** max(0, attempt - 1))
    return round(rand() * cap, 3)


default_random: Callable[[], float] = random.random
