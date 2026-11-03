"""``client.payouts``."""

from __future__ import annotations

from collections.abc import Iterator
from datetime import datetime
from typing import Any

from enigmoss import _validate as v
from enigmoss._http import PAYOUT_TIMEOUT, Endpoint, KeySupport
from enigmoss._options import RequestOptions
from enigmoss._page import Page, walk
from enigmoss._retry import ITERATE_MAX_RETRY_AFTER
from enigmoss.models.payouts import Payout, PayoutPriority, PayoutQuote, PayoutStatus
from enigmoss.resources._base import Resource

__all__ = ["PayoutsResource"]

_CREATE = Endpoint.create("/payouts", "payout", KeySupport.REQUIRED, PAYOUT_TIMEOUT)
_QUOTE = Endpoint.calculation("/payouts/quote", "payout")
_GET = Endpoint.get("/payouts/{id}", "payout")
_LIST = Endpoint.get("/payouts", "payout")


class PayoutsResource(Resource):
    """Payouts to external addresses. Needs the **payout key**; the IP whitelist applies."""

    __slots__ = ()

    def quote(
        self,
        *,
        amount: v.Amount,
        currency_code: str,
        address: str,
        network_code: str | None = None,
        recipient_currency_code: str | None = None,
        source_currency_code: str | None = None,
        fee_deducted_from_amount: bool | None = None,
        rate_source: str | None = None,
        priority: PayoutPriority | str | None = None,
        options: RequestOptions | None = None,
    ) -> PayoutQuote:
        """Calculates a payout without creating it: what the recipient gets and what is charged.

        ``POST /api/v4/payouts/quote``, payout key, ``payout_create``. Takes the same arguments
        as :meth:`create` except ``order_id``, ``memo`` and ``callback_url``.

        Raises:
            APIError: Validation or a business rule (limits, unsupported network).
            EnigmossError: Any other failure.
        """
        body = v.without_none(
            {
                "amount": v.amount(amount),
                "currency_code": currency_code,
                "network_code": network_code,
                "address": v.length(address, "address", 10, 128),
                "recipient_currency_code": recipient_currency_code,
                "source_currency_code": source_currency_code,
                "fee_deducted_from_amount": v.opt_bool(fee_deducted_from_amount, "fee_deducted_from_amount"),
                "rate_source": rate_source,
                "priority": v.enum(priority, PayoutPriority, "priority"),
            }
        )
        return self._one(_QUOTE, PayoutQuote._parse, body=body, options=options)

    def create(
        self,
        *,
        order_id: str,
        amount: v.Amount,
        currency_code: str,
        address: str,
        network_code: str | None = None,
        memo: str | None = None,
        recipient_currency_code: str | None = None,
        source_currency_code: str | None = None,
        fee_deducted_from_amount: bool | None = None,
        rate_source: str | None = None,
        priority: PayoutPriority | str | None = None,
        callback_url: str | None = None,
        options: RequestOptions | None = None,
    ) -> Payout:
        """Sends funds to an address. This moves money.

        Safe to repeat with the same ``order_id``: the ``Idempotency-Key`` derives from it, so a
        repeat returns the stored payout instead of paying twice. The call may take up to ~160 s;
        the SDK waits at least 180 s. ``POST /api/v4/payouts``, payout key, ``payout_create``.

        Args:
            order_id: Your unique ID of this payout. One ``order_id`` is one payout, forever.
            amount: Decimal amount: ``"100.00"``, ``Decimal`` or int. Never a float.
            currency_code: Currency to send, e.g. ``"USDT"``.
            address: Recipient address, 10-128 characters.
            network_code: Network, e.g. ``"tron"``.
            memo: Memo/tag for networks that use one, up to 64 characters.
            recipient_currency_code: Currency the recipient gets, when converting.
            source_currency_code: Balance currency to pay from, when converting.
            fee_deducted_from_amount: Deduct the fee from ``amount`` instead of adding it.
            rate_source: Exchange rate source for conversions, as listed by the API.
            priority: Network fee priority: ``"recommended"``, ``"economy"``, ``"high"``,
                ``"highest"``.
            callback_url: Public URL for the ``payout.*`` webhooks of this payout, 6-150 chars.
            options: Per-call overrides.

        Raises:
            ConflictError: ``is_order_id_taken``: the ``order_id`` belongs to another payout.
            IdempotencyError: Same ``order_id`` sent before with other data, or still in progress.
            PermissionDeniedError: Payouts disabled, or this IP is not whitelisted
                (``auth.ip_forbidden``).
            APIError: Validation or a business rule (insufficient funds, limits).
            EnigmossError: Any other failure. If ``outcome_unknown`` is ``True``, do not create a
                new ``order_id``: reconcile with :meth:`find_by_order_id` or the webhook.
        """
        body = v.without_none(
            {
                "order_id": v.order_id(order_id),
                "amount": v.amount(amount),
                "currency_code": currency_code,
                "network_code": network_code,
                "address": v.length(address, "address", 10, 128),
                "memo": v.length(memo, "memo", 0, 64),
                "recipient_currency_code": recipient_currency_code,
                "source_currency_code": source_currency_code,
                "fee_deducted_from_amount": v.opt_bool(fee_deducted_from_amount, "fee_deducted_from_amount"),
                "rate_source": rate_source,
                "priority": v.enum(priority, PayoutPriority, "priority"),
                "callback_url": v.length(callback_url, "callback_url", 6, 150),
            }
        )
        return self._one(_CREATE, Payout._parse, body=body, options=options, order_id=order_id)

    def get(self, payout_id: str, *, options: RequestOptions | None = None) -> Payout:
        """Returns one payout. ``GET /api/v4/payouts/{id}``, payout key, ``info_payout``.

        Raises:
            NotFoundError: No such payout in this project.
            EnigmossError: Any other failure.
        """
        return self._one(
            _GET, Payout._parse, path_params={"id": v.uuid(payout_id, "payout_id")}, options=options
        )

    def find_by_order_id(self, order_id: str, *, options: RequestOptions | None = None) -> Payout | None:
        """Returns the payout of an ``order_id``, or ``None``. Use it to reconcile ``outcome_unknown``.

        Raises:
            EnigmossError: The lookup failed.
        """
        page = self.list(order_id=order_id, limit=1, options=options)
        return page.items[0] if page.items else None

    def list(
        self,
        *,
        order_id: str | None = None,
        status: PayoutStatus | str | None = None,
        created_from: datetime | None = None,
        created_to: datetime | None = None,
        limit: int | None = None,
        cursor: str | None = None,
        options: RequestOptions | None = None,
    ) -> Page[Payout]:
        """One page of payouts, newest first. ``GET /api/v4/payouts``, payout key, ``info_payouts_list``.

        Args:
            order_id: Only the payout of this order.
            status: Only this status.
            created_from: Created at or after (timezone-aware).
            created_to: Created at or before (timezone-aware).
            limit: Page size, 1-100.
            cursor: ``next_cursor`` of the previous page.
            options: Per-call overrides.

        Raises:
            EnigmossError: The request failed.
        """
        return self._page(
            _LIST, _query(order_id, status, created_from, created_to, limit, cursor), options, Payout._parse
        )

    def iterate(
        self,
        *,
        order_id: str | None = None,
        status: PayoutStatus | str | None = None,
        created_from: datetime | None = None,
        created_to: datetime | None = None,
        limit: int | None = None,
        options: RequestOptions | None = None,
    ) -> Iterator[Payout]:
        """Every payout matching the filters (see :meth:`list`), fetching pages lazily.

        Raises:
            EnigmossError: A page request failed.
        """
        query = _query(order_id, status, created_from, created_to, limit, None)
        return walk(
            lambda cursor: self._page(
                _LIST, {**query, "cursor": cursor}, options, Payout._parse, ITERATE_MAX_RETRY_AFTER
            )
        )


def _query(
    order_id: str | None,
    status: PayoutStatus | str | None,
    created_from: datetime | None,
    created_to: datetime | None,
    limit: int | None,
    cursor: str | None,
) -> dict[str, Any]:
    return {
        "order_id": v.opt_order_id(order_id),
        "status": v.enum(status, PayoutStatus, "status"),
        "created_from": v.query_time(created_from, "created_from"),
        "created_to": v.query_time(created_to, "created_to"),
        "limit": v.int_range(limit, "limit", 1, 100),
        "cursor": cursor,
    }
