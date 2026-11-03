"""Verification of incoming webhooks.

With a client::

    event = client.webhooks.construct_event(raw_body, request.headers)

Without one (a separate webhook service)::

    event = enigmoss.webhooks.construct_event(raw_body, request.headers, project_id=..., payment_key=...)

Webhooks of every type (``payment.*``, ``payout.*``, ``refund.*``) are signed with the
**payment key**.
"""

from __future__ import annotations

import hmac
import json
import re
import time
from collections.abc import Iterable, Mapping, Sequence
from typing import Any

from enigmoss._secret import Secret
from enigmoss._signing import sign_webhook
from enigmoss.errors import ConfigurationError, WebhookFailureReason, WebhookVerificationError
from enigmoss.models.events import WebhookEvent

__all__ = ["DEFAULT_TOLERANCE", "WebhookEvent", "construct_event"]

DEFAULT_TOLERANCE = 300
"""Accepted age of ``X-Timestamp``, in seconds."""

_TIMESTAMP = re.compile(r"\d{1,10}")
_HEADERS = ("x-project-uuid", "x-timestamp", "x-signature")

_now = time.time


def construct_event(
    payload: bytes,
    headers: Mapping[str, Any] | Iterable[tuple[str, Any]],
    *,
    project_id: str,
    payment_key: str | Secret | Sequence[str | Secret],
    tolerance: int = DEFAULT_TOLERANCE,
) -> WebhookEvent:
    """Verifies a webhook and returns the event.

    Checks, in order: the three ``X-*`` headers are present, ``X-Project-Uuid`` is your project,
    ``X-Timestamp`` is within ``tolerance`` seconds, and ``X-Signature`` is the HMAC-SHA256 of
    ``"{timestamp}\\n{project}\\n{raw body}"`` with the payment key. After a valid signature it
    never raises because of the event content.

    Args:
        payload: The **raw** request body bytes, exactly as received.
        headers: Request headers: any mapping or ``(name, value)`` pairs, names in any case.
        project_id: Your project UUID.
        payment_key: The payment key, or a list of keys while you rotate it (new and old).
        tolerance: Accepted age of ``X-Timestamp``, in seconds.

    Returns:
        The event; deduplicate by ``event.id``.

    Raises:
        WebhookVerificationError: Answer HTTP 400 and do not process the request.
        TypeError: ``payload`` is not bytes (a ``str`` or a parsed dict).
        ConfigurationError: No payment key was given.
    """
    if isinstance(payload, str):  # type: ignore[unreachable]  # a common mistake, typed or not
        raise TypeError(
            "construct_event() needs the raw request body as bytes, not str: pass request.body (Django), "
            "request.get_data() (Flask) or await request.body() (FastAPI)."
        )
    if not isinstance(payload, bytes | bytearray | memoryview):
        raise TypeError(
            f"construct_event() needs the raw request body as bytes, got {type(payload).__name__}."
        )
    body = bytes(payload)
    keys = _keys(payment_key)

    found = _collect(headers)
    project = _first(found.get("x-project-uuid"))
    timestamp = _first(found.get("x-timestamp"))
    signatures = [s.strip().lower() for s in found.get("x-signature", []) if s.strip()]

    if project is None or timestamp is None or not signatures:
        missing = [
            name
            for name, absent in (
                ("X-Project-Uuid", project is None),
                ("X-Timestamp", timestamp is None),
                ("X-Signature", not signatures),
            )
            if absent
        ]
        raise WebhookVerificationError(
            f"The webhook has no {', '.join(missing)} header. "
            "Make sure the web server or proxy passes custom headers through.",
            WebhookFailureReason.MISSING_HEADER,
        )
    if project.lower() != project_id.strip().lower():
        raise WebhookVerificationError(
            "The webhook is addressed to another project.", WebhookFailureReason.PROJECT_MISMATCH
        )
    if not _TIMESTAMP.fullmatch(timestamp) or abs(int(_now()) - int(timestamp)) > tolerance:
        raise WebhookVerificationError(
            "The webhook timestamp is outside the accepted window. Check the server clock (NTP).",
            WebhookFailureReason.TIMESTAMP_OUT_OF_TOLERANCE,
        )

    # The signature covers the project value exactly as sent.
    if not any(
        hmac.compare_digest(sign_webhook(key, timestamp, project, body), signature)
        for key in keys
        for signature in signatures
    ):
        raise WebhookVerificationError(
            "The webhook signature does not match. Verify the raw request body (not a re-encoded dict) "
            "with the payment key of this project.",
            WebhookFailureReason.SIGNATURE_MISMATCH,
        )

    try:
        decoded = json.loads(body)
    except (ValueError, UnicodeDecodeError):
        decoded = None
    if not isinstance(decoded, dict):
        raise WebhookVerificationError(
            "The webhook body is not a JSON object.", WebhookFailureReason.MALFORMED_PAYLOAD
        )
    return WebhookEvent(decoded)


def _keys(payment_key: str | Secret | Sequence[str | Secret]) -> list[str]:
    items: Sequence[str | Secret] = [payment_key] if isinstance(payment_key, str | Secret) else payment_key
    keys = [k.reveal() if isinstance(k, Secret) else Secret(k).reveal() for k in items]
    if not keys:
        raise ConfigurationError("construct_event() needs the payment key.")
    return keys


def _collect(headers: Mapping[str, Any] | Iterable[tuple[str, Any]]) -> dict[str, list[str]]:
    items = getattr(headers, "items", None)
    pairs: Iterable[tuple[Any, Any]] = items() if callable(items) else headers  # type: ignore[assignment]
    found: dict[str, list[str]] = {}
    for name, value in pairs:
        key = name.decode("latin-1") if isinstance(name, bytes) else str(name)
        key = key.lower()
        if key not in _HEADERS:
            continue
        values = value if isinstance(value, list | tuple) else [value]
        for item in values:
            if item is None:
                continue
            text = item.decode("latin-1") if isinstance(item, bytes) else str(item)
            found.setdefault(key, []).append(text)
    return found


def _first(values: list[str] | None) -> str | None:
    if not values:
        return None
    value = values[0].strip()
    return value or None
