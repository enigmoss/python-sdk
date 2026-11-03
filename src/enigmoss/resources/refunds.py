"""``client.refunds``."""

from __future__ import annotations

from collections.abc import Iterator
from datetime import datetime
from typing import Any

from enigmoss import _validate as v
from enigmoss._http import PAYOUT_TIMEOUT, Endpoint, KeySupport
from enigmoss._options import RequestOptions
from enigmoss._page import Page, walk
from enigmoss._retry import ITERATE_MAX_RETRY_AFTER
from enigmoss.models.refunds import Refund, RefundSource, RefundStatus
from enigmoss.resources._base import Resource

__all__ = ["RefundsResource"]

_FOR_PAYMENT = Endpoint.create("/payments/{id}/refunds", "payout", KeySupport.REQUIRED, PAYOUT_TIMEOUT)
_FOR_WALLET = Endpoint.create("/wallets/{id}/refunds", "payout", KeySupport.REQUIRED, PAYOUT_TIMEOUT)
_GET = Endpoint.get("/refunds/{id}", "payout")
_LIST = Endpoint.get("/refunds", "payout")


class RefundsResource(Resource):
    """Refunds of payments and blocked wallets. Needs the **payout key**; the IP whitelist applies."""

    __slots__ = ()

    def create_for_payment(
        self,
        payment_id: str,
        *,
        order_id: str,
        address: str,
        amount: v.Amount | None = None,
        fee_deducted_from_amount: bool | None = None,
        callback_url: str | None = None,
        options: RequestOptions | None = None,
    ) -> Refund:
        """Refunds a payment to ``address``. This moves money.

        Safe to repeat with the same ``order_id`` (derived ``Idempotency-Key``). The SDK waits at
        least 180 s. ``POST /api/v4/payments/{id}/refunds``, payout key, ``payment_refund``.

        Args:
            payment_id: The payment to refund.
            order_id: Your unique ID of this refund (not the payment's ``order_id``).
            address: Where to send the refund, 10-128 characters.
            amount: Partial amount; the whole paid amount when omitted. Never a float.
            fee_deducted_from_amount: Deduct the fee from the refunded amount.
            callback_url: Public URL for the ``refund.*`` webhooks of this refund.
            options: Per-call overrides.

        Raises:
            ConflictError: ``is_order_id_taken``, or the payment cannot be refunded now.
            IdempotencyError: Same ``order_id`` sent before with other data, or still in progress.
            APIError: Validation or a business rule (``refund.*`` codes).
            EnigmossError: Any other failure; on ``outcome_unknown`` reconcile with
                :meth:`find_by_order_id`, never with a new ``order_id``.
        """
        body = v.without_none(
            {
                "order_id": v.order_id(order_id),
                "address": v.length(address, "address", 10, 128),
                "amount": v.opt_amount(amount),
                "fee_deducted_from_amount": v.opt_bool(fee_deducted_from_amount, "fee_deducted_from_amount"),
                "callback_url": callback_url,
            }
        )
        return self._one(
            _FOR_PAYMENT,
            Refund._parse,
            path_params={"id": v.uuid(payment_id, "payment_id")},
            body=body,
            options=options,
            order_id=order_id,
        )

    def create_for_wallet(
        self,
        wallet_id: str,
        *,
        order_id: str,
        address: str,
        callback_url: str | None = None,
        options: RequestOptions | None = None,
    ) -> Refund:
        """Refunds what a blocked static wallet holds. This moves money.

        Block the wallet first with ``client.wallets.block()`` (payment key). Safe to repeat with
        the same ``order_id``. ``POST /api/v4/wallets/{id}/refunds``, payout key,
        ``payment_refund``.

        Raises:
            ConflictError: ``is_order_id_taken``, or the wallet cannot be refunded now.
            IdempotencyError: Same ``order_id`` sent before with other data, or still in progress.
            EnigmossError: Any other failure; check ``outcome_unknown`` first.
        """
        body = v.without_none(
            {
                "order_id": v.order_id(order_id),
                "address": v.length(address, "address", 10, 128),
                "callback_url": callback_url,
            }
        )
        return self._one(
            _FOR_WALLET,
            Refund._parse,
            path_params={"id": v.uuid(wallet_id, "wallet_id")},
            body=body,
            options=options,
            order_id=order_id,
        )

    def get(self, refund_id: str, *, options: RequestOptions | None = None) -> Refund:
        """Returns one refund. ``GET /api/v4/refunds/{id}``, payout key, ``payment_refund``.

        Raises:
            NotFoundError: No such refund in this project.
            EnigmossError: Any other failure.
        """
        return self._one(
            _GET, Refund._parse, path_params={"id": v.uuid(refund_id, "refund_id")}, options=options
        )

    def find_by_order_id(self, order_id: str, *, options: RequestOptions | None = None) -> Refund | None:
        """Returns the refund of an ``order_id``, or ``None``.

        Raises:
            EnigmossError: The lookup failed.
        """
        page = self.list(order_id=order_id, limit=1, options=options)
        return page.items[0] if page.items else None

    def list(
        self,
        *,
        order_id: str | None = None,
        source: RefundSource | str | None = None,
        status: RefundStatus | str | None = None,
        payment_id: str | None = None,
        wallet_id: str | None = None,
        created_from: datetime | None = None,
        created_to: datetime | None = None,
        limit: int | None = None,
        cursor: str | None = None,
        options: RequestOptions | None = None,
    ) -> Page[Refund]:
        """One page of refunds, newest first. ``GET /api/v4/refunds``, payout key, ``payment_refund``.

        Args:
            order_id: Only the refund of this order.
            source: Only this source.
            status: Only this status.
            payment_id: Only refunds of this payment.
            wallet_id: Only refunds of this wallet.
            created_from: Created at or after (timezone-aware).
            created_to: Created at or before (timezone-aware).
            limit: Page size, 1-100.
            cursor: ``next_cursor`` of the previous page.
            options: Per-call overrides.

        Raises:
            EnigmossError: The request failed.
        """
        query = _query(
            order_id, source, status, payment_id, wallet_id, created_from, created_to, limit, cursor
        )
        return self._page(_LIST, query, options, Refund._parse)

    def iterate(
        self,
        *,
        order_id: str | None = None,
        source: RefundSource | str | None = None,
        status: RefundStatus | str | None = None,
        payment_id: str | None = None,
        wallet_id: str | None = None,
        created_from: datetime | None = None,
        created_to: datetime | None = None,
        limit: int | None = None,
        options: RequestOptions | None = None,
    ) -> Iterator[Refund]:
        """Every refund matching the filters (see :meth:`list`), fetching pages lazily.

        Raises:
            EnigmossError: A page request failed.
        """
        query = _query(order_id, source, status, payment_id, wallet_id, created_from, created_to, limit, None)
        return walk(
            lambda cursor: self._page(
                _LIST, {**query, "cursor": cursor}, options, Refund._parse, ITERATE_MAX_RETRY_AFTER
            )
        )


def _query(
    order_id: str | None,
    source: RefundSource | str | None,
    status: RefundStatus | str | None,
    payment_id: str | None,
    wallet_id: str | None,
    created_from: datetime | None,
    created_to: datetime | None,
    limit: int | None,
    cursor: str | None,
) -> dict[str, Any]:
    return {
        "order_id": v.opt_order_id(order_id),
        "source": v.enum(source, RefundSource, "source"),
        "status": v.enum(status, RefundStatus, "status"),
        "payment_id": v.checked_uuid(payment_id, "payment_id"),
        "wallet_id": v.checked_uuid(wallet_id, "wallet_id"),
        "created_from": v.query_time(created_from, "created_from"),
        "created_to": v.query_time(created_to, "created_to"),
        "limit": v.int_range(limit, "limit", 1, 100),
        "cursor": cursor,
    }
