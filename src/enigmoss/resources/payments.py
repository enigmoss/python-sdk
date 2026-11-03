"""``client.payments``: invoices."""

from __future__ import annotations

import builtins
import re
from collections.abc import Iterator, Mapping, Sequence
from datetime import datetime
from typing import Any

from enigmoss import _validate as v
from enigmoss._http import Endpoint, KeySupport
from enigmoss._options import RequestOptions
from enigmoss._page import Page, walk
from enigmoss._retry import ITERATE_MAX_RETRY_AFTER
from enigmoss.errors import InvalidArgumentError
from enigmoss.models._common import PaymentMethodRef
from enigmoss.models.payments import AmlVerificationLink, Payment, PaymentStatus, Theme
from enigmoss.resources._base import Resource

__all__ = ["PaymentsResource"]

_CREATE = Endpoint.create("/payments", "payment", KeySupport.SUPPORTED)
_GET = Endpoint.get("/payments/{id}", "payment")
_LIST = Endpoint.get("/payments", "payment")
_CANCEL = Endpoint.action("/payments/{id}/cancel", "payment")
_MARK_PAID = Endpoint.action("/payments/{id}/mark-paid", "payment")
_AML_LINKS = Endpoint.action("/payments/{id}/aml-verification-links", "payment")

_PERCENT = re.compile(r"\d+(\.\d{1,2})?")


class PaymentsResource(Resource):
    """Invoices. Needs the **payment key**."""

    __slots__ = ()

    def create(
        self,
        *,
        amount: v.Amount,
        currency_code: str,
        order_id: str,
        settlement_currency_code: str | None = None,
        network_code: str | None = None,
        return_url: str | None = None,
        success_url: str | None = None,
        callback_url: str | None = None,
        expires_in: int | None = None,
        allow_multiple_payments: bool | None = None,
        customer_fee_percent: int | None = None,
        underpayment_tolerance_percent: str | None = None,
        discount_percent: int | None = None,
        allowed_methods: Sequence[PaymentMethodRef] | None = None,
        excluded_methods: Sequence[PaymentMethodRef] | None = None,
        customer_email: str | None = None,
        metadata: Mapping[str, Any] | None = None,
        description: str | None = None,
        theme: Theme | str | None = None,
        referral_code: str | None = None,
        options: RequestOptions | None = None,
    ) -> Payment:
        """Creates an invoice and returns it with the hosted ``payment_url``.

        Safe to repeat: the ``Idempotency-Key`` derives from ``order_id``, so repeating the same
        call within 24 hours returns the same invoice. One ``order_id`` is one invoice, forever.
        ``POST /api/v4/payments``, payment key, permission ``payment_create_invoice``.

        An invoice moves no money: creating one on a live project is safe, cancel it with
        :meth:`cancel`.

        Args:
            amount: Decimal amount in ``currency_code``: ``"10.50"``, ``Decimal("10.50")`` or an
                int. Never a float. Sent exactly as given.
            currency_code: Invoice currency, e.g. ``"USDT"`` or ``"USD"``.
            order_id: Your unique order ID, 1-128 letters, digits, ``-`` or ``_``.
            settlement_currency_code: Fix the currency the customer pays in.
            network_code: Fix the network the customer pays on, e.g. ``"tron"``.
            return_url: "Back to shop" link on the payment page.
            success_url: Where the customer goes after paying.
            callback_url: Public URL for the ``payment.*`` webhooks of this invoice.
            expires_in: Lifetime in seconds, 300-43200 (default 3600).
            allow_multiple_payments: Accept several payments to the invoice.
            customer_fee_percent: Share of the fee paid by the customer, 0-100.
            underpayment_tolerance_percent: ``"0"``-``"5.00"``: an underpayment within it counts
                as paid.
            discount_percent: -99..90; positive is a discount, negative a surcharge.
            allowed_methods: Offer only these currencies/networks.
            excluded_methods: Do not offer these currencies/networks.
            customer_email: The customer's email.
            metadata: Any JSON object, returned unchanged in the payment and its webhooks.
            description: Up to 150 characters, shown to the customer.
            theme: Payment page theme: ``"light"``, ``"dark"`` or ``"system"``.
            referral_code: Referral code.
            options: Per-call overrides.

        Returns:
            The invoice. ``payment_url`` may be ``None``: never redirect to an empty URL.

        Raises:
            ConflictError: ``is_order_id_taken``: the ``order_id`` belongs to another invoice
                (``existing_resource_id``).
            IdempotencyError: Same ``order_id`` sent before with other data, or still in progress.
            APIError: Validation (``violations``) or a business rule (limits, currency, network).
            InvalidArgumentError: An argument is malformed; nothing was sent.
            EnigmossError: Any other failure; check ``outcome_unknown`` first.
        """
        if underpayment_tolerance_percent is not None and not (
            isinstance(underpayment_tolerance_percent, str)
            and _PERCENT.fullmatch(underpayment_tolerance_percent)
        ):
            raise InvalidArgumentError(
                'underpayment_tolerance_percent must be a decimal string such as "1.5".'
            )
        body = v.without_none(
            {
                "amount": v.amount(amount),
                "currency_code": currency_code,
                "order_id": v.order_id(order_id),
                "settlement_currency_code": settlement_currency_code,
                "network_code": network_code,
                "return_url": return_url,
                "success_url": success_url,
                "callback_url": callback_url,
                "expires_in": v.int_range(expires_in, "expires_in", 300, 43200),
                "allow_multiple_payments": v.opt_bool(allow_multiple_payments, "allow_multiple_payments"),
                "customer_fee_percent": v.int_range(customer_fee_percent, "customer_fee_percent", 0, 100),
                "underpayment_tolerance_percent": underpayment_tolerance_percent,
                "discount_percent": v.int_range(discount_percent, "discount_percent", -99, 90),
                "allowed_methods": _methods(allowed_methods, "allowed_methods"),
                "excluded_methods": _methods(excluded_methods, "excluded_methods"),
                "customer_email": customer_email,
                "metadata": v.metadata(metadata),
                "description": v.length(description, "description", 0, 150),
                "theme": v.enum(theme, Theme, "theme"),
                "referral_code": referral_code,
            }
        )
        return self._one(_CREATE, Payment._parse, body=body, options=options, order_id=order_id)

    def get(self, payment_id: str, *, options: RequestOptions | None = None) -> Payment:
        """Returns one payment. ``GET /api/v4/payments/{id}``, payment key, ``info_payments``.

        Raises:
            NotFoundError: No such payment in this project.
            EnigmossError: Any other failure.
        """
        return self._one(
            _GET, Payment._parse, path_params={"id": v.uuid(payment_id, "payment_id")}, options=options
        )

    def find_by_order_id(self, order_id: str, *, options: RequestOptions | None = None) -> Payment | None:
        """Returns the invoice of an ``order_id``, or ``None`` if there is none.

        Use it to reconcile after an error with ``outcome_unknown``. Static wallet deposits have
        no ``order_id``: list them with ``wallet_id=``.

        Raises:
            EnigmossError: The lookup failed.
        """
        page = self.list(order_id=order_id, limit=1, options=options)
        return page.items[0] if page.items else None

    def list(
        self,
        *,
        order_id: str | None = None,
        wallet_id: str | None = None,
        status: PaymentStatus | str | None = None,
        created_from: datetime | None = None,
        created_to: datetime | None = None,
        limit: int | None = None,
        cursor: str | None = None,
        options: RequestOptions | None = None,
    ) -> Page[Payment]:
        """One page of payments, newest first. ``GET /api/v4/payments``, payment key, ``info_payments``.

        Args:
            order_id: Only the invoice of this order.
            wallet_id: Only deposits to this static wallet.
            status: Only this status.
            created_from: Created at or after (timezone-aware).
            created_to: Created at or before (timezone-aware).
            limit: Page size, 1-50 (default 20).
            cursor: ``next_cursor`` of the previous page.
            options: Per-call overrides.

        Raises:
            EnigmossError: The request failed.
        """
        query = _query(order_id, wallet_id, status, created_from, created_to, limit, cursor)
        return self._page(_LIST, query, options, Payment._parse)

    def iterate(
        self,
        *,
        order_id: str | None = None,
        wallet_id: str | None = None,
        status: PaymentStatus | str | None = None,
        created_from: datetime | None = None,
        created_to: datetime | None = None,
        limit: int | None = None,
        options: RequestOptions | None = None,
    ) -> Iterator[Payment]:
        """Every payment matching the filters (see :meth:`list`), fetching pages lazily.

        Raises:
            EnigmossError: A page request failed.
        """
        query = _query(order_id, wallet_id, status, created_from, created_to, limit, None)
        return walk(
            lambda cursor: self._page(
                _LIST, {**query, "cursor": cursor}, options, Payment._parse, ITERATE_MAX_RETRY_AFTER
            )
        )

    def cancel(self, payment_id: str, *, options: RequestOptions | None = None) -> Payment:
        """Cancels an unpaid invoice (pending or processing, nothing received yet).

        ``POST /api/v4/payments/{id}/cancel``, payment key, ``payment_cancel``.

        Raises:
            ConflictError: The invoice can no longer be cancelled.
            EnigmossError: Any other failure.
        """
        return self._one(
            _CANCEL, Payment._parse, path_params={"id": v.uuid(payment_id, "payment_id")}, options=options
        )

    def mark_paid(self, payment_id: str, *, options: RequestOptions | None = None) -> Payment:
        """Accepts an underpaid invoice as paid.

        For ``underpaid_waiting`` the response may still be ``underpaid_waiting``: wait for the
        ``payment.*`` webhook. ``POST /api/v4/payments/{id}/mark-paid``, payment key,
        ``payment_mark_paid``.

        Raises:
            ConflictError: The invoice is not in a status that can be marked paid.
            EnigmossError: Any other failure.
        """
        return self._one(
            _MARK_PAID, Payment._parse, path_params={"id": v.uuid(payment_id, "payment_id")}, options=options
        )

    def create_aml_verification_links(
        self, payment_id: str, *, options: RequestOptions | None = None
    ) -> builtins.list[AmlVerificationLink]:
        """Returns the AML verification links of a locked payment, creating one when none is active.

        ``POST /api/v4/payments/{id}/aml-verification-links``, payment key, ``info_payments``.

        Raises:
            ConflictError: The payment is not locked, or verification already started.
            EnigmossError: Any other failure.
        """
        return self._all(
            _AML_LINKS,
            AmlVerificationLink._parse,
            path_params={"id": v.uuid(payment_id, "payment_id")},
            options=options,
        )


def _query(
    order_id: str | None,
    wallet_id: str | None,
    status: PaymentStatus | str | None,
    created_from: datetime | None,
    created_to: datetime | None,
    limit: int | None,
    cursor: str | None,
) -> dict[str, Any]:
    return {
        "order_id": v.opt_order_id(order_id),
        "wallet_id": v.checked_uuid(wallet_id, "wallet_id"),
        "status": v.enum(status, PaymentStatus, "status"),
        "created_from": v.query_time(created_from, "created_from"),
        "created_to": v.query_time(created_to, "created_to"),
        "limit": v.int_range(limit, "limit", 1, 50),
        "cursor": cursor,
    }


def _methods(refs: Sequence[PaymentMethodRef] | None, name: str) -> list[dict[str, str]] | None:
    if refs is None:
        return None
    if not isinstance(refs, list | tuple) or not all(isinstance(ref, PaymentMethodRef) for ref in refs):
        raise InvalidArgumentError(f"{name} must be a list of enigmoss.PaymentMethodRef.")
    return [ref._to_json() for ref in refs]
