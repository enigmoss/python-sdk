"""The API origin in use: the configured one, or one learned from the fallback host.

When the API host cannot be reached and retries did not help, the client asks the fallback host
for the current API origin (a signed ``GET /api/v4/project/platform-brand``), remembers the answer
and repeats the call there. This module holds the state of that switch, the single probe shared
by concurrent calls, and the checks of the answer.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import re
import threading
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Literal

from enigmoss._transport import HttpResponse
from enigmoss.errors import EnigmossError

__all__ = ["OriginChange"]

PROBE_TIMEOUT = 3.0
"""Time limit of the request to the fallback host, in seconds."""
PAUSE = 300.0
"""After a fallback that did not move the client, the next one is not tried for this long."""
MIN_TTL = 3600
MAX_TTL = 604_800
"""Bounds of ``refresh_after``: a learned origin is kept for one hour to seven days."""
SIGNATURE_TOLERANCE = 300
"""Accepted distance between ``X-Response-Timestamp`` and the (corrected) local clock, seconds."""
MAX_ORIGIN_LENGTH = 2048
MAX_HOST_LENGTH = 253

# Checked on the raw string, lower-cased ASCII only: no URL parser and its quirks in between.
# https, a host of two or more labels whose last one starts with a letter (so neither an IP
# literal nor "localhost"), no port or 443, and nothing else: no userinfo, path, query, fragment.
_HTTPS_ORIGIN = re.compile(
    r"https://((?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z][a-z0-9-]{0,61}[a-z0-9])(?::443)?",
    re.ASCII,
)
_LOOPBACK_ORIGIN = re.compile(r"http://(?:localhost|127\.0\.0\.1|\[::1\])(?::[0-9]{1,5})?", re.ASCII)
_TIMESTAMP = re.compile(r"[0-9]{1,12}", re.ASCII)


@dataclass(frozen=True, slots=True)
class OriginChange:
    """Passed to ``on_origin_change`` when the client starts to use another API origin.

    Attributes:
        previous: The origin that could not be reached, e.g. ``"https://api.example.com"``.
        current: The origin the client uses from now on.
        expires_at: Until when ``current`` is kept (UTC); after that the client goes back to
            ``base_url`` and asks the fallback host again if it still cannot be reached.
    """

    previous: str
    current: str
    expires_at: datetime


@dataclass(frozen=True, slots=True)
class Outcome:
    """What one probe of the fallback host ended with; shared by every call that waited for it.

    ``moved``: ``origin`` is the new API origin, kept until ``expires_at``. ``same``: the fallback
    host names the origin that failed. ``unreachable``: the fallback host did not answer either.
    ``failed``: it answered with something unusable; ``error`` builds that error for one caller,
    given the caller's Idempotency-Key. ``response`` is the verified answer (``moved``, ``same``).
    """

    kind: Literal["moved", "same", "unreachable", "failed"]
    origin: str = ""
    expires_at: float = 0.0
    response: HttpResponse | None = None
    error: Callable[[str | None], EnigmossError] | None = None


class _Flight:
    __slots__ = ("done", "outcome")

    def __init__(self) -> None:
        self.done = threading.Event()
        self.outcome: Outcome | None = None


class OriginState:
    """The origin requests go to, and the bookkeeping of the fallback. Thread-safe.

    Mutable state of the client next to the clock correction: the learned origin with its
    expiry, the time until which the fallback is paused, and the probe in flight.
    """

    def __init__(self, primary: str, fallback: str | None) -> None:
        self.primary = primary
        self.fallback = fallback
        self._lock = threading.Lock()
        self._moved: tuple[str, float] | None = None
        self._paused_until = 0.0
        self._flight: _Flight | None = None

    def active(self, now: float) -> str:
        """The origin to send to: the learned one until it expires, else the configured one."""
        with self._lock:
            return self._active(now)

    def _active(self, now: float) -> str:
        if self._moved is not None:
            origin, expires_at = self._moved
            if now < expires_at:
                return origin
            self._moved = None
        return self.primary

    def resolve(
        self, failed: str, now: Callable[[], float], deadline: float, probe: Callable[[], Outcome]
    ) -> tuple[Outcome | None, bool]:
        """Runs ``probe`` at most once at a time and hands its outcome to every waiting call.

        Returns the outcome, or ``None`` when the fallback is skipped (paused, or the call has
        less than a probe timeout left), and whether this call ran the probe itself.
        """
        with self._lock:
            current = now()
            active = self._active(current)
            if comparable(active) != comparable(failed):
                # Another call already moved the client while this one was failing.
                return Outcome("moved", origin=active), False
            if current < self._paused_until or deadline - current < PROBE_TIMEOUT:
                return None, False
            flight = self._flight
            leader = flight is None
            if flight is None:
                flight = self._flight = _Flight()
        if not leader:
            # A probe may be repeated once after a clock correction, hence two timeouts.
            flight.done.wait(min(deadline - current, 2 * PROBE_TIMEOUT + 1.0))
            return flight.outcome, False

        outcome = Outcome("unreachable")
        try:
            outcome = probe()
        finally:
            with self._lock:
                if outcome.kind == "moved":
                    self._moved = (outcome.origin, outcome.expires_at)
                    self._paused_until = 0.0
                else:
                    # Only the time is kept, never the answer: a call during the pause fails
                    # with its own network error.
                    self._paused_until = now() + PAUSE
                self._flight = None
            flight.outcome = outcome
            flight.done.set()
        return outcome, True


def comparable(origin: str) -> str:
    """Lower case, without the default port: the form origins are stored and compared in."""
    text = origin.lower()
    if text.startswith("https://") and text.endswith(":443"):
        return text[:-4]
    if text.startswith("http://") and text.endswith(":80"):
        return text[:-3]
    return text


def validate_origin(value: object, *, allow_insecure_http: bool) -> str | None:
    """Returns the normalized origin, or ``None`` when ``value`` is not an acceptable API origin."""
    if not isinstance(value, str) or len(value) > MAX_ORIGIN_LENGTH or not value.isascii():
        return None
    text = value.lower()
    match = _HTTPS_ORIGIN.fullmatch(text)
    if match is not None:
        host = match.group(1)
        if len(host) > MAX_HOST_LENGTH or host.endswith((".local", ".localhost")):
            return None
        return f"https://{host}"
    if allow_insecure_http and _LOOPBACK_ORIGIN.fullmatch(text):
        return comparable(text)
    return None


def signature_problem(response: HttpResponse, key: str, request_signature: str, now: int) -> str | None:
    """Checks the signature of a fallback answer; returns what is wrong, or ``None``.

    The answer is signed with the key that signed the request:
    hex(hmac_sha256(key, "{X-Response-Timestamp}\\n{X-Signature of the request}\\n" + body)).
    Binding it to the request signature makes a captured answer useless for any other request.
    """
    timestamp = (response.header("X-Response-Timestamp") or "").strip()
    signature = (response.header("X-Response-Signature") or "").strip().lower()
    if not timestamp or not signature:
        return "it is not signed (X-Response-Timestamp and X-Response-Signature are required)"
    if not _TIMESTAMP.fullmatch(timestamp):
        return "its X-Response-Timestamp is not a Unix time"
    if abs(int(timestamp) - now) > SIGNATURE_TOLERANCE:
        return f"its X-Response-Timestamp is more than {SIGNATURE_TOLERANCE} s away from the local clock"
    expected = hmac.new(
        key.encode(), f"{timestamp}\n{request_signature}\n".encode() + response.body, hashlib.sha256
    ).hexdigest()
    if not hmac.compare_digest(expected.encode(), signature.encode("utf-8", "replace")):
        return "its X-Response-Signature does not match"
    return None


def read_origin(body: bytes) -> tuple[object, int]:
    """Reads ``data.api.origin`` (``None`` when absent) and the clamped ``data.refresh_after``."""
    try:
        decoded: Any = json.loads(body)
    except (ValueError, UnicodeDecodeError):
        return None, MIN_TTL
    data = decoded.get("data") if isinstance(decoded, dict) else None
    if not isinstance(data, dict):
        return None, MIN_TTL
    api = data.get("api")
    origin = api.get("origin") if isinstance(api, dict) else None
    refresh_after = data.get("refresh_after")
    if isinstance(refresh_after, bool) or not isinstance(refresh_after, int):
        return origin, MIN_TTL
    return origin, max(MIN_TTL, min(MAX_TTL, refresh_after))
