"""Webhook events and the events journal."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from enigmoss._parse import Reader
from enigmoss.models._common import ApiEnum
from enigmoss.models.payments import Payment
from enigmoss.models.payouts import Payout
from enigmoss.models.refunds import Refund

__all__ = [
    "AttemptTrigger",
    "BulkRedeliveryResult",
    "DeliveryAttempt",
    "DeliveryStatus",
    "EventRecord",
    "WebhookEvent",
    "WebhookObjectType",
]


class WebhookObjectType(ApiEnum):
    """Type of the object an event is about."""

    PAYMENT = "payment"
    WALLET = "wallet"
    PAYOUT = "payout"
    REFUND = "refund"
    UNKNOWN = "unknown"


class DeliveryStatus(ApiEnum):
    """Delivery status of an event or of one delivery attempt."""

    PENDING = "pending"
    DELIVERED = "delivered"
    FAILED = "failed"
    SUPERSEDED = "superseded"
    NO_DESTINATION = "no_destination"
    UNKNOWN = "unknown"


class AttemptTrigger(ApiEnum):
    """What started a delivery attempt."""

    AUTO = "auto"
    RETRY = "retry"
    PROXY_RETRY = "proxy_retry"
    MANUAL = "manual"
    BULK = "bulk"
    UNKNOWN = "unknown"


_PARSERS: dict[str, Any] = {"payment": Payment, "payout": Payout, "refund": Refund}


class WebhookEvent:
    """A webhook event: ``{"event", "event_id", "created_at", "data"}``.

    Never raises because of the event content: unknown types simply have ``object = None``.

    Attributes:
        id: ``event_id``, stable across retries and redeliveries. Your deduplication key.
        type: The event name, e.g. ``"payment.paid"``.
        created_at: When the event happened (UTC), or ``None`` if absent.
        data: The event object as raw JSON, or ``None``.
        raw: The whole envelope as received.
    """

    __slots__ = ("_object", "_parsed", "created_at", "data", "id", "raw", "type")

    def __init__(self, envelope: Mapping[str, Any]) -> None:
        self.raw: Mapping[str, Any] = envelope
        event_id = envelope.get("event_id")
        self.id: str = event_id if isinstance(event_id, str) else ""
        event_type = envelope.get("event")
        self.type: str = event_type if isinstance(event_type, str) else ""
        data = envelope.get("data")
        self.data: Mapping[str, Any] | None = data if isinstance(data, Mapping) else None
        self.created_at: datetime | None = _parse_time(envelope.get("created_at"))
        self._object: Payment | Payout | Refund | None = None
        self._parsed = False

    @property
    def object_type(self) -> str:
        """The part before the dot: ``"payment"``, ``"payout"``, ``"refund"``, ``"webhook"``..."""
        return self.type.partition(".")[0]

    @property
    def object(self) -> Payment | Payout | Refund | None:
        """The typed object of ``payment.*``, ``payout.*`` and ``refund.*`` events, else ``None``.

        Parsed on first access. Raises :class:`~enigmoss.errors.UnexpectedResponseError` when
        ``data`` does not match this SDK version: log ``data`` and still answer 2xx if you cannot
        act on it. Deposits to static wallets are ``payment.*`` events with ``wallet_id`` set.
        """
        if not self._parsed:
            parser = _PARSERS.get(self.object_type)
            if parser is not None and self.data is not None:
                self._object = parser._parse(Reader(self.data, "data"))
            self._parsed = True
        return self._object

    def __repr__(self) -> str:
        return f"WebhookEvent(id={self.id!r}, type={self.type!r}, created_at={self.created_at!r})"


def _parse_time(value: Any) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    return (parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)).astimezone(UTC)


@dataclass(frozen=True, slots=True)
class DeliveryAttempt:
    """One delivery attempt of an event to your endpoint."""

    number: int
    trigger: AttemptTrigger
    status: DeliveryStatus
    url: str | None
    response_status_code: int | None
    response_body: str | None
    scheduled_at: datetime | None
    created_at: datetime
    updated_at: datetime | None

    @classmethod
    def _parse(cls, r: Reader) -> DeliveryAttempt:
        return cls(
            number=r.integer("number"),
            trigger=r.enum("trigger", AttemptTrigger),
            status=r.enum("status", DeliveryStatus),
            url=r.opt_string("url"),
            response_status_code=r.opt_integer("response_status_code"),
            response_body=r.opt_string("response_body"),
            scheduled_at=r.opt_dt("scheduled_at"),
            created_at=r.dt("created_at"),
            updated_at=r.opt_dt("updated_at"),
        )


@dataclass(frozen=True, slots=True)
class EventRecord:
    """An entry of the events journal: the event, its delivery status and attempts."""

    id: str
    event: str
    object_type: WebhookObjectType | None
    object_id: str | None
    delivery_status: DeliveryStatus
    created_at: datetime
    payload: WebhookEvent
    """The webhook body as it is (or was) delivered."""
    attempts: tuple[DeliveryAttempt, ...]
    """Delivery attempts; empty in lists."""
    raw: Mapping[str, Any] = field(default_factory=dict, repr=False, compare=False)

    @classmethod
    def _parse(cls, r: Reader) -> EventRecord:
        attempts = (
            ()
            if r.data.get("attempts") is None
            else tuple(DeliveryAttempt._parse(a) for a in r.objs("attempts"))
        )
        return cls(
            id=r.string("id"),
            event=r.string("event"),
            object_type=r.opt_enum("object_type", WebhookObjectType),
            object_id=r.opt_string("object_id"),
            delivery_status=r.enum("delivery_status", DeliveryStatus),
            created_at=r.dt("created_at"),
            payload=WebhookEvent(r.mapping("payload")),
            attempts=attempts,
            raw=r.data,
        )


@dataclass(frozen=True, slots=True)
class BulkRedeliveryResult:
    """Result of a bulk redelivery."""

    queued: int
    already_pending: int
    skipped_outdated: int
    raw: Mapping[str, Any] = field(default_factory=dict, repr=False, compare=False)

    @classmethod
    def _parse(cls, r: Reader) -> BulkRedeliveryResult:
        return cls(
            queued=r.integer("queued"),
            already_pending=r.integer("already_pending"),
            skipped_outdated=r.integer("skipped_outdated"),
            raw=r.data,
        )
