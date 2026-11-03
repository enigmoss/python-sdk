"""``client.wallets``: static wallets."""

from __future__ import annotations

from collections.abc import Iterator
from datetime import datetime
from typing import Any

from enigmoss import _validate as v
from enigmoss._http import Endpoint, KeySupport
from enigmoss._options import RequestOptions
from enigmoss._page import Page, walk
from enigmoss._retry import ITERATE_MAX_RETRY_AFTER
from enigmoss.errors import InvalidArgumentError
from enigmoss.models.wallets import Wallet, WalletStatus
from enigmoss.resources._base import Resource

__all__ = ["WalletsResource"]

_CREATE = Endpoint.create("/wallets", "payment", KeySupport.SUPPORTED)
_GET = Endpoint.get("/wallets/{id}", "payment")
_LIST = Endpoint.get("/wallets", "payment")
_BLOCK = Endpoint.action("/wallets/{id}/block", "payment")


class WalletsResource(Resource):
    """Static wallets: a permanent deposit address per customer. Needs the **payment key**."""

    __slots__ = ()

    def create(
        self,
        *,
        currency_code: str,
        network_code: str,
        order_id: str,
        callback_url: str | None = None,
        referral_code: str | None = None,
        options: RequestOptions | None = None,
    ) -> Wallet:
        """Creates a static wallet. Safe to repeat with the same ``order_id`` (derived key).

        Deposits arrive as ``payment.*`` webhooks with ``wallet_id`` set and no ``order_id``.
        ``POST /api/v4/wallets``, payment key, ``payment_create_static_wallet``.

        Raises:
            ConflictError: ``is_order_id_taken``: the ``order_id`` belongs to another wallet.
            IdempotencyError: Same ``order_id`` sent before with other data, or still in progress.
            InvalidArgumentError: An argument is malformed; nothing was sent.
            EnigmossError: Any other failure; check ``outcome_unknown`` first.
        """
        body = v.without_none(
            {
                "currency_code": currency_code,
                "network_code": network_code,
                "order_id": v.order_id(order_id),
                "callback_url": callback_url,
                "referral_code": referral_code,
            }
        )
        return self._one(_CREATE, Wallet._parse, body=body, options=options, order_id=order_id)

    def get(self, wallet_id: str, *, options: RequestOptions | None = None) -> Wallet:
        """Returns one wallet. ``GET /api/v4/wallets/{id}``, payment key, ``info_payments``.

        Raises:
            NotFoundError: No such wallet in this project.
            EnigmossError: Any other failure.
        """
        return self._one(
            _GET, Wallet._parse, path_params={"id": v.uuid(wallet_id, "wallet_id")}, options=options
        )

    def find_by_order_id(self, order_id: str, *, options: RequestOptions | None = None) -> Wallet | None:
        """Returns the wallet of an ``order_id``, or ``None``.

        Raises:
            EnigmossError: The lookup failed.
        """
        page = self.list(order_id=order_id, limit=1, options=options)
        return page.items[0] if page.items else None

    def list(
        self,
        *,
        order_id: str | None = None,
        status: WalletStatus | str | None = None,
        currency_code: str | None = None,
        network_code: str | None = None,
        created_from: datetime | None = None,
        created_to: datetime | None = None,
        limit: int | None = None,
        cursor: str | None = None,
        options: RequestOptions | None = None,
    ) -> Page[Wallet]:
        """One page of wallets, newest first. ``GET /api/v4/wallets``, payment key, ``info_payments``.

        Args:
            order_id: Only the wallet of this order.
            status: Only this status.
            currency_code: Only this currency.
            network_code: Only this network.
            created_from: Created at or after (timezone-aware).
            created_to: Created at or before (timezone-aware).
            limit: Page size, 1-50.
            cursor: ``next_cursor`` of the previous page.
            options: Per-call overrides.

        Raises:
            EnigmossError: The request failed.
        """
        query = _query(order_id, status, currency_code, network_code, created_from, created_to, limit, cursor)
        return self._page(_LIST, query, options, Wallet._parse)

    def iterate(
        self,
        *,
        order_id: str | None = None,
        status: WalletStatus | str | None = None,
        currency_code: str | None = None,
        network_code: str | None = None,
        created_from: datetime | None = None,
        created_to: datetime | None = None,
        limit: int | None = None,
        options: RequestOptions | None = None,
    ) -> Iterator[Wallet]:
        """Every wallet matching the filters (see :meth:`list`), fetching pages lazily.

        Raises:
            EnigmossError: A page request failed.
        """
        query = _query(order_id, status, currency_code, network_code, created_from, created_to, limit, None)
        return walk(
            lambda cursor: self._page(
                _LIST, {**query, "cursor": cursor}, options, Wallet._parse, ITERATE_MAX_RETRY_AFTER
            )
        )

    def block(
        self, wallet_id: str, *, refund_incoming: bool = False, options: RequestOptions | None = None
    ) -> Wallet:
        """Blocks a wallet: new deposits are not credited.

        With ``refund_incoming=True`` they are refunded automatically. To refund what the wallet
        already holds, call ``client.refunds.create_for_wallet()`` (payout key).
        ``POST /api/v4/wallets/{id}/block``, payment key, ``wallet_block``.

        Raises:
            ConflictError: The wallet is deactivated, or already blocked with another
                ``refund_incoming``.
            EnigmossError: Any other failure.
        """
        if not isinstance(refund_incoming, bool):
            raise InvalidArgumentError("refund_incoming must be True or False.")
        return self._one(
            _BLOCK,
            Wallet._parse,
            path_params={"id": v.uuid(wallet_id, "wallet_id")},
            body={"refund_incoming": refund_incoming},
            options=options,
        )


def _query(
    order_id: str | None,
    status: WalletStatus | str | None,
    currency_code: str | None,
    network_code: str | None,
    created_from: datetime | None,
    created_to: datetime | None,
    limit: int | None,
    cursor: str | None,
) -> dict[str, Any]:
    return {
        "order_id": v.opt_order_id(order_id),
        "status": v.enum(status, WalletStatus, "status"),
        "currency_code": currency_code,
        "network_code": network_code,
        "created_from": v.query_time(created_from, "created_from"),
        "created_to": v.query_time(created_to, "created_to"),
        "limit": v.int_range(limit, "limit", 1, 50),
        "cursor": cursor,
    }
