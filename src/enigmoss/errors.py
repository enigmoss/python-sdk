"""Exceptions raised by the SDK.

Every exception derives from :class:`EnigmossError`. Check :attr:`EnigmossError.outcome_unknown`
first on calls that create objects or move money::

    try:
        payout = client.payouts.create(
            order_id=order_id, amount="100.00", currency_code="USDT", address=address
        )
    except enigmoss.EnigmossError as exc:
        if exc.outcome_unknown:
            ...  # mark the order "to reconcile"; check later with find_by_order_id(order_id)
        raise

Branch on the class or on :attr:`APIError.code` (see :class:`ErrorCode`), never on the message.
"""

from __future__ import annotations

import dataclasses
from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

__all__ = [
    "APIError",
    "AuthenticationError",
    "ConfigurationError",
    "ConflictError",
    "EnigmossError",
    "ErrorCode",
    "IdempotencyError",
    "InvalidArgumentError",
    "NotFoundError",
    "PermissionDeniedError",
    "RateLimitError",
    "SendPhase",
    "ServerError",
    "TransportError",
    "UnexpectedResponseError",
    "Violation",
    "WebhookFailureReason",
    "WebhookVerificationError",
]


class ErrorCode:
    """Known ``error.code`` values.

    The server may add codes at any time: compare :attr:`APIError.code` with these constants and
    always keep a default branch.
    """

    REQUEST_INVALID = "request.invalid"
    RESOURCE_NOT_FOUND = "resource.not_found"
    RESOURCE_CONFLICT = "resource.conflict"
    LOCK_NOT_ACQUIRED = "lock.not_acquired"
    DOMAIN_RULE_VIOLATION = "domain.rule_violation"
    VALIDATION_FAILED = "validation.failed"
    RATE_LIMIT_EXCEEDED = "rate_limit.exceeded"
    INTERNAL_SERVER_ERROR = "internal.server_error"
    INTERNAL_UNEXPECTED = "internal.unexpected"
    DEPENDENCY_GATEWAY = "dependency.gateway"
    DEPENDENCY_EXTERNAL_SERVICE = "dependency.external_service"
    DEPENDENCY_UNAVAILABLE = "dependency.unavailable"

    SIGNATURE_INVALID = "auth.signature_invalid"
    TIMESTAMP_EXPIRED = "auth.timestamp_expired"
    PROJECT_UNKNOWN = "auth.project_unknown"
    KEY_INACTIVE = "auth.key_inactive"
    IP_FORBIDDEN = "auth.ip_forbidden"
    DOMAIN_FORBIDDEN = "auth.domain_forbidden"
    PERMISSION_DENIED = "api_permission.denied"

    IDEMPOTENCY_KEY_REQUIRED = "idempotency.key_required"
    IDEMPOTENCY_KEY_INVALID = "idempotency.key_invalid"
    IDEMPOTENCY_PAYLOAD_MISMATCH = "idempotency.payload_mismatch"
    IDEMPOTENCY_IN_PROGRESS = "idempotency.in_progress"

    PAYOUT_PAYOUTS_FORBIDDEN = "payout.payouts_forbidden"
    PAYOUT_INSUFFICIENT_FUNDS = "payout.insufficient_funds"
    PAYOUT_NOT_CANCELLABLE = "payout.not_cancellable"

    REFUND_PAYOUTS_FORBIDDEN = "refund.payouts_forbidden"
    REFUND_PAYMENT_NOT_REFUNDABLE = "refund.payment_not_refundable"
    REFUND_WALLET_NOT_REFUNDABLE = "refund.wallet_not_refundable"
    REFUND_AMOUNT_EXCEEDS_PAID = "refund.amount_exceeds_paid"
    REFUND_AMOUNT_TOO_SMALL = "refund.amount_too_small"
    REFUND_INSUFFICIENT_FUNDS = "refund.insufficient_funds"
    REFUND_AML_NOT_SUPPORTED = "refund.aml_not_supported"
    REFUND_NOTHING_TO_REFUND = "refund.nothing_to_refund"

    TRANSFER_WALLET_NOT_FOUND = "transfer.wallet_not_found"
    TRANSFER_PAYOUTS_FORBIDDEN = "transfer.payouts_forbidden"
    TRANSFER_PERSONAL_WALLET_NOT_FOUND = "transfer.personal_wallet_not_found"
    TRANSFER_INSUFFICIENT_FUNDS = "transfer.insufficient_funds"
    TRANSFER_AMOUNT_BELOW_MIN = "transfer.amount_below_min"
    TRANSFER_AMOUNT_ABOVE_MAX = "transfer.amount_above_max"

    WEBHOOK_EVENT_NOT_LATEST = "webhook.event_not_latest"
    WEBHOOK_REDELIVERY_TOO_MANY_EVENTS = "webhook.redelivery_too_many_events"
    WEBHOOK_REDELIVERY_LIMIT_REACHED = "webhook.redelivery_limit_reached"
    WEBHOOK_EVENT_NOT_REDELIVERABLE = "webhook.event_not_redeliverable"


class SendPhase(StrEnum):
    """How far a failed HTTP attempt got before the transport gave up."""

    NOT_SENT = "not_sent"
    """The request never left the client (DNS, connect, TLS handshake). Safe to repeat."""
    SENT = "sent"
    """The request was sent; the server may have executed it."""
    UNKNOWN = "unknown"
    """The transport cannot tell. Treated exactly like ``SENT``."""


class WebhookFailureReason(StrEnum):
    """Why :func:`enigmoss.webhooks.construct_event` rejected a webhook."""

    MISSING_HEADER = "missing_header"
    PROJECT_MISMATCH = "project_mismatch"
    TIMESTAMP_OUT_OF_TOLERANCE = "timestamp_out_of_tolerance"
    SIGNATURE_MISMATCH = "signature_mismatch"
    MALFORMED_PAYLOAD = "malformed_payload"


@dataclass(frozen=True, slots=True)
class Violation:
    """One failed validation rule of a ``validation.failed`` response.

    Attributes:
        field: Dot path of the field (``"allowed_methods.0.currency_code"``); ``None`` for the
            whole request.
        rule: Machine name of the rule (``"required"``, ``"max"``...).
        message: Human-readable English text; do not parse it.
        params: Rule parameters, e.g. ``{"max": "128"}``.
    """

    field: str | None
    rule: str
    message: str
    params: Mapping[str, Any] = dataclasses.field(default_factory=dict)


class EnigmossError(Exception):
    """Base class of every exception the SDK raises.

    Attributes:
        outcome_unknown: ``True`` when the server may have executed the request although the call
            failed (connection lost after sending, 5xx, an unreadable 2xx, a request still in
            progress). Then do not create a new ``order_id`` and do not repeat the create in the
            same request: mark the order "to reconcile" and check later with
            ``find_by_order_id()`` or wait for the webhook. Repeating a create with the same
            ``order_id`` is safe at any time.
        request_id: The ``X-Request-Id`` of the response, if there was one. Quote it to support.
        idempotency_key: The ``Idempotency-Key`` the request was sent with, if any.
    """

    outcome_unknown: bool = False
    request_id: str | None = None
    idempotency_key: str | None = None


class APIError(EnigmossError):
    """The API answered with an error.

    Attributes:
        status: HTTP status code.
        code: ``error.code``, e.g. ``"validation.failed"``; see :class:`ErrorCode`. Responses
            without the API error body (a proxy page) get ``"http.<status>"``.
        type: ``error.type``, e.g. ``"business_error"``, or ``"unknown"``.
        message: ``error.message`` exactly as the server sent it.
        details: ``error.details`` (may be empty).
        violations: Failed validation rules of a ``validation.failed`` response.
        retryable: ``error.retryable`` as the server sent it.
        retry_after: Seconds to wait before the next attempt, when the server said so.
    """

    def __init__(
        self,
        message: str,
        *,
        status: int,
        code: str,
        type: str = "unknown",
        details: Mapping[str, Any] | None = None,
        violations: tuple[Violation, ...] = (),
        retryable: bool = False,
        retry_after: int | None = None,
        request_id: str | None = None,
        idempotency_key: str | None = None,
    ) -> None:
        self.message = message
        self.status = status
        self.code = code
        self.type = type
        self.details: Mapping[str, Any] = details if details is not None else {}
        self.violations = violations
        self.retryable = retryable
        self.retry_after = retry_after
        self.request_id = request_id
        self.idempotency_key = idempotency_key
        self.outcome_unknown = status >= 500 or code == ErrorCode.IDEMPOTENCY_IN_PROGRESS
        suffix = code if request_id is None else f"{code}, request {request_id}"
        super().__init__(f"{message.rstrip()} [{suffix}]")

    def violations_for(self, field: str) -> tuple[Violation, ...]:
        """Violations of one field, e.g. ``violations_for("amount")``."""
        return tuple(v for v in self.violations if v.field == field)


class AuthenticationError(APIError):
    """HTTP 401: bad signature, expired timestamp, unknown project or inactive key.

    A signature error with the right key usually means the wrong key type: payouts, refunds and
    transfers need the payout key, everything else the payment key.
    """

    @property
    def server_time(self) -> int | None:
        """Server Unix time from an ``auth.timestamp_expired`` error; compare it with your clock."""
        value = self.details.get("server_time")
        return value if isinstance(value, int) and not isinstance(value, bool) else None


class PermissionDeniedError(APIError):
    """HTTP 403: the API permission is disabled, the IP address or the API domain is not allowed."""


class NotFoundError(APIError):
    """HTTP 404: the object does not exist in this project."""


class ConflictError(APIError):
    """HTTP 409: the ``order_id`` is already used, or the object's state does not allow the action.

    Idempotency conflicts are :class:`IdempotencyError`, which is deliberately *not* a subclass:
    catching ``ConflictError`` never swallows "the first request is still in progress".
    """

    @property
    def existing_resource_id(self) -> str | None:
        """ID of the object that already uses this ``order_id`` (``details.id``), if returned."""
        value = self.details.get("id")
        return value if isinstance(value, str) else None

    @property
    def is_order_id_taken(self) -> bool:
        """``True`` when a create failed because the ``order_id`` already belongs to an object.

        :attr:`existing_resource_id` is that object. ``False`` for a busy lock or an object whose
        state does not allow the action.
        """
        return (
            self.code == ErrorCode.RESOURCE_CONFLICT
            and self.existing_resource_id is not None
            and "status" not in self.details
            and "reason" not in self.details
        )


class IdempotencyError(APIError):
    """HTTP 409 ``idempotency.*``: the key was reused for another request, or is still in progress.

    ``idempotency.in_progress`` has ``outcome_unknown = True``: reconcile later with
    ``find_by_order_id()``, do not create a new ``order_id``. ``idempotency.payload_mismatch``
    means the ``order_id`` was sent before with different data.
    """


class RateLimitError(APIError):
    """HTTP 429. Wait :attr:`retry_after` seconds before the next attempt."""


class ServerError(APIError):
    """HTTP 5xx: the API or a service behind it failed. ``outcome_unknown`` is always ``True``."""


class TransportError(EnigmossError):
    """No HTTP response was received (DNS, connect, TLS, timeout, connection reset).

    Custom transports must raise this class and report :attr:`phase` honestly: retries of
    money-moving calls depend on it.

    Attributes:
        phase: How far the attempt got; ``outcome_unknown`` is ``True`` unless it is ``NOT_SENT``.
        timed_out: ``True`` when the attempt hit the timeout.
    """

    def __init__(
        self, message: str, *, phase: SendPhase = SendPhase.UNKNOWN, timed_out: bool = False
    ) -> None:
        super().__init__(message)
        self.phase = SendPhase(phase)
        self.timed_out = timed_out
        self.outcome_unknown = self.phase is not SendPhase.NOT_SENT


class UnexpectedResponseError(EnigmossError):
    """The response cannot be read: not JSON, no ``data`` member, a field of an unexpected type, a redirect.

    On a 2xx the operation most likely succeeded, so ``outcome_unknown`` is ``True``.

    Attributes:
        status: HTTP status code, when there was a response.
    """

    def __init__(
        self,
        message: str,
        *,
        status: int | None = None,
        request_id: str | None = None,
        idempotency_key: str | None = None,
    ) -> None:
        super().__init__(message)
        self.status = status
        self.request_id = request_id
        self.idempotency_key = idempotency_key
        self.outcome_unknown = status is not None and 200 <= status < 300


class WebhookVerificationError(EnigmossError):
    """The webhook failed verification. Answer HTTP 400 and do not process it.

    Attributes:
        reason: What is wrong, see :class:`WebhookFailureReason`.
    """

    def __init__(self, message: str, reason: WebhookFailureReason) -> None:
        super().__init__(message)
        self.reason = reason


class InvalidArgumentError(EnigmossError, ValueError):
    """An argument is invalid; raised before anything is sent."""


class ConfigurationError(EnigmossError):
    """The client configuration is invalid or lacks the key an endpoint needs."""
