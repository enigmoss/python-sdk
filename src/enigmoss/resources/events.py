"""``client.events`` and ``client.webhooks``."""

from __future__ import annotations

import re
from collections.abc import Callable, Iterator, Mapping
from datetime import datetime
from typing import Any

from enigmoss import _validate as v
from enigmoss._http import Endpoint
from enigmoss._options import RequestOptions
from enigmoss._page import Page, walk
from enigmoss._retry import ITERATE_MAX_RETRY_AFTER
from enigmoss.errors import InvalidArgumentError
from enigmoss.models.events import (
    BulkRedeliveryResult,
    DeliveryStatus,
    EventRecord,
    WebhookEvent,
    WebhookObjectType,
)
from enigmoss.resources._base import Resource

__all__ = ["EventsResource", "WebhooksResource"]

_LIST = Endpoint.get("/events", "payment")
_GET = Endpoint.get("/events/{id}", "payment")
_REDELIVER = Endpoint.non_idempotent("/events/{id}/redeliver", "payment")
_REDELIVER_BULK = Endpoint.non_idempotent("/events/redeliver", "payment")
_SEND_TEST = Endpoint.non_idempotent("/webhooks/test", "payment")

_EVENT = re.compile(r"[a-z_]+\.[a-z_]+")


class EventsResource(Resource):
    """The webhook events journal: replay what you missed. Needs the **payment key**, ``webhooks``."""

    __slots__ = ()

    def list(
        self,
        *,
        event: str | None = None,
        object_type: WebhookObjectType | str | None = None,
        object_id: str | None = None,
        delivery_status: DeliveryStatus | str | None = None,
        created_from: datetime | None = None,
        created_to: datetime | None = None,
        limit: int | None = None,
        cursor: str | None = None,
        options: RequestOptions | None = None,
    ) -> Page[EventRecord]:
        """One page of events, newest first. ``GET /api/v4/events``.

        Args:
            event: Only this event, e.g. ``"payment.paid"``.
            object_type: Only events about this object type.
            object_id: Only events about this object.
            delivery_status: Only this delivery status.
            created_from: Created at or after (timezone-aware).
            created_to: Created at or before (timezone-aware).
            limit: Page size, 1-100.
            cursor: ``next_cursor`` of the previous page.
            options: Per-call overrides.

        Raises:
            EnigmossError: The request failed.
        """
        query = _filters(event, object_type, object_id, delivery_status, created_from, created_to)
        query |= {"limit": v.int_range(limit, "limit", 1, 100), "cursor": cursor}
        return self._page(_LIST, query, options, EventRecord._parse)

    def iterate(
        self,
        *,
        event: str | None = None,
        object_type: WebhookObjectType | str | None = None,
        object_id: str | None = None,
        delivery_status: DeliveryStatus | str | None = None,
        created_from: datetime | None = None,
        created_to: datetime | None = None,
        limit: int | None = None,
        options: RequestOptions | None = None,
    ) -> Iterator[EventRecord]:
        """Every event matching the filters (see :meth:`list`), fetching pages lazily.

        Raises:
            EnigmossError: A page request failed.
        """
        query = _filters(event, object_type, object_id, delivery_status, created_from, created_to)
        query["limit"] = v.int_range(limit, "limit", 1, 100)
        return walk(
            lambda cursor: self._page(
                _LIST, {**query, "cursor": cursor}, options, EventRecord._parse, ITERATE_MAX_RETRY_AFTER
            )
        )

    def get(self, event_id: str, *, options: RequestOptions | None = None) -> EventRecord:
        """Returns one event with its delivery attempts. ``GET /api/v4/events/{id}``.

        Raises:
            NotFoundError: No such event.
            EnigmossError: Any other failure.
        """
        return self._one(
            _GET, EventRecord._parse, path_params={"id": v.uuid(event_id, "event_id")}, options=options
        )

    def redeliver(self, event_id: str, *, options: RequestOptions | None = None) -> EventRecord:
        """Queues the event for delivery again. ``POST /api/v4/events/{id}/redeliver``.

        Not idempotent: retried only when the request was not sent.

        Raises:
            ConflictError: The event is not the latest of its object, or cannot be redelivered.
            EnigmossError: Any other failure.
        """
        return self._one(
            _REDELIVER, EventRecord._parse, path_params={"id": v.uuid(event_id, "event_id")}, options=options
        )

    def redeliver_bulk(
        self,
        *,
        created_from: datetime,
        created_to: datetime | None = None,
        event: str | None = None,
        object_type: WebhookObjectType | str | None = None,
        object_id: str | None = None,
        delivery_status: DeliveryStatus | str | None = None,
        options: RequestOptions | None = None,
    ) -> BulkRedeliveryResult:
        """Queues every matching event for delivery again. ``POST /api/v4/events/redeliver``.

        Raises:
            APIError: Too many events, or the redelivery limit is reached (``webhook.*`` codes).
            EnigmossError: Any other failure.
        """
        if created_from is None:
            raise InvalidArgumentError("created_from is required.")
        body = v.without_none(
            _filters(event, object_type, object_id, delivery_status, created_from, created_to)
        )
        ordered: dict[str, Any] = {
            key: body[key]
            for key in ("created_from", "created_to", "event", "object_type", "object_id", "delivery_status")
            if key in body
        }
        return self._one(_REDELIVER_BULK, BulkRedeliveryResult._parse, body=ordered, options=options)


def _filters(
    event: str | None,
    object_type: WebhookObjectType | str | None,
    object_id: str | None,
    delivery_status: DeliveryStatus | str | None,
    created_from: datetime | None,
    created_to: datetime | None,
) -> dict[str, Any]:
    if event is not None and not (isinstance(event, str) and _EVENT.fullmatch(event)):
        raise InvalidArgumentError('event must look like "payment.paid".')
    return {
        "event": event,
        "object_type": v.enum(object_type, WebhookObjectType, "object_type"),
        "object_id": v.checked_uuid(object_id, "object_id"),
        "delivery_status": v.enum(delivery_status, DeliveryStatus, "delivery_status"),
        "created_from": v.query_time(created_from, "created_from"),
        "created_to": v.query_time(created_to, "created_to"),
    }


class WebhooksResource(Resource):
    """Incoming webhook verification and test deliveries. Needs the **payment key**."""

    __slots__ = ("_verify",)

    def __init__(
        self, requester: Any, verify: Callable[[bytes, Mapping[str, Any] | Any], WebhookEvent]
    ) -> None:
        super().__init__(requester)
        self._verify = verify

    def construct_event(self, payload: bytes, headers: Mapping[str, Any] | Any) -> WebhookEvent:
        """Verifies an incoming webhook and returns the event.

        Checks, in order: the three ``X-*`` headers are present, ``X-Project-Uuid`` is this
        project, ``X-Timestamp`` is within 300 s, and the HMAC signature matches the payment key.

        Args:
            payload: The **raw** request body bytes, exactly as received. Never a re-encoded dict.
            headers: The request headers: any mapping (``request.headers`` of Django, Flask,
                FastAPI/Starlette) or a list of ``(name, value)`` pairs; names in any case.

        Returns:
            The event. Deduplicate by ``event.id``.

        Raises:
            WebhookVerificationError: Answer HTTP 400; ``reason`` says what is wrong.
            TypeError: ``payload`` is a ``str``: pass the raw bytes.
            ConfigurationError: The client has no payment key.
        """
        return self._verify(payload, headers)

    def send_test(
        self, *, callback_url: str | None = None, options: RequestOptions | None = None
    ) -> EventRecord:
        """Sends one ``webhook.test`` event to ``callback_url`` or the project URL, without retries.

        Returns the event with the delivery attempt and your endpoint's response.
        ``POST /api/v4/webhooks/test``, payment key, ``webhooks``; 10 per minute.

        Raises:
            EnigmossError: The request failed.
        """
        body = {} if callback_url is None else {"callback_url": callback_url}
        return self._one(_SEND_TEST, EventRecord._parse, body=body, options=options)
