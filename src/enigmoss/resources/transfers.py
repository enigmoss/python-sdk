"""``client.transfers``, ``client.balances`` and ``client.transactions``."""

from __future__ import annotations

import builtins
import re
from collections.abc import Iterator
from datetime import datetime
from typing import Any

from enigmoss import _validate as v
from enigmoss._http import Endpoint, KeySupport
from enigmoss._options import RequestOptions
from enigmoss._page import Page, walk
from enigmoss._retry import ITERATE_MAX_RETRY_AFTER
from enigmoss.errors import InvalidArgumentError
from enigmoss.models.balances import Balance, Transaction, TransactionType, Transfer
from enigmoss.resources._base import Resource

__all__ = ["BalancesResource", "TransactionsResource", "TransfersResource"]

_TRANSFER_AMOUNT = re.compile(r"\d{1,11}(\.\d{1,8})?")

_CREATE = Endpoint.create("/transfers", "payout", KeySupport.REQUIRED)
_GET = Endpoint.get("/transfers/{id}", "payout")
_LIST = Endpoint.get("/transfers", "payout")
_BALANCES = Endpoint.get("/balances", "payment")
_TRANSACTIONS = Endpoint.get("/transactions", "payment")


class TransfersResource(Resource):
    """Transfers from the project balance to the personal account. Needs the **payout key**."""

    __slots__ = ()

    def create(
        self,
        *,
        order_id: str,
        amount: v.Amount,
        currency_code: str,
        options: RequestOptions | None = None,
    ) -> Transfer:
        """Transfers funds to the personal account. This moves money.

        Safe to repeat with the same ``order_id``.

        ``POST /api/v4/transfers``, payout key, ``transfer_to_personal``.

        Args:
            order_id: Your unique ID of this transfer.
            amount: Up to 11 integer and 8 decimal digits. Never a float.
            currency_code: Balance currency, e.g. ``"USDT"``.
            options: Per-call overrides.

        Raises:
            ConflictError: ``is_order_id_taken``: the ``order_id`` belongs to another transfer.
            APIError: A business rule (``transfer.*`` codes).
            EnigmossError: Any other failure; check ``outcome_unknown`` first.
        """
        body = {
            "order_id": v.order_id(order_id),
            "amount": v.amount(amount, pattern=_TRANSFER_AMOUNT),
            "currency_code": currency_code,
        }
        return self._one(_CREATE, Transfer._parse, body=body, options=options, order_id=order_id)

    def get(self, transfer_id: str, *, options: RequestOptions | None = None) -> Transfer:
        """Returns one transfer. ``GET /api/v4/transfers/{id}``, payout key.

        Raises:
            NotFoundError: No such transfer in this project.
            EnigmossError: Any other failure.
        """
        return self._one(
            _GET, Transfer._parse, path_params={"id": v.uuid(transfer_id, "transfer_id")}, options=options
        )

    def find_by_order_id(self, order_id: str, *, options: RequestOptions | None = None) -> Transfer | None:
        """Returns the transfer of an ``order_id``, or ``None``.

        Raises:
            EnigmossError: The lookup failed.
        """
        page = self.list(order_id=order_id, limit=1, options=options)
        return page.items[0] if page.items else None

    def list(
        self,
        *,
        order_id: str | None = None,
        limit: int | None = None,
        cursor: str | None = None,
        options: RequestOptions | None = None,
    ) -> Page[Transfer]:
        """One page of transfers, newest first. ``GET /api/v4/transfers``, payout key.

        Args:
            order_id: Only the transfer of this order.
            limit: Page size, 1-50.
            cursor: ``next_cursor`` of the previous page.
            options: Per-call overrides.

        Raises:
            EnigmossError: The request failed.
        """
        query = {
            "order_id": v.opt_order_id(order_id),
            "limit": v.int_range(limit, "limit", 1, 50),
            "cursor": cursor,
        }
        return self._page(_LIST, query, options, Transfer._parse)

    def iterate(
        self, *, order_id: str | None = None, limit: int | None = None, options: RequestOptions | None = None
    ) -> Iterator[Transfer]:
        """Every transfer matching the filters (see :meth:`list`), fetching pages lazily.

        Raises:
            EnigmossError: A page request failed.
        """
        query = {"order_id": v.opt_order_id(order_id), "limit": v.int_range(limit, "limit", 1, 50)}
        return walk(
            lambda cursor: self._page(
                _LIST, {**query, "cursor": cursor}, options, Transfer._parse, ITERATE_MAX_RETRY_AFTER
            )
        )


class BalancesResource(Resource):
    """Project balances. Needs the **payment key**."""

    __slots__ = ()

    def list(self, *, options: RequestOptions | None = None) -> builtins.list[Balance]:
        """Returns the balance of every currency. ``GET /api/v4/balances``, payment key, ``info_balance``.

        Raises:
            EnigmossError: The request failed.
        """
        return self._all(_BALANCES, Balance._parse, options=options)


class TransactionsResource(Resource):
    """Balance transactions. Needs the **payment key**."""

    __slots__ = ()

    def list(
        self,
        *,
        type: TransactionType | str | None = None,
        created_from: datetime | None = None,
        created_to: datetime | None = None,
        limit: int | None = None,
        cursor: str | None = None,
        options: RequestOptions | None = None,
    ) -> Page[Transaction]:
        """One page of transactions, newest first.

        ``GET /api/v4/transactions``, payment key, ``info_transactions_history``.

        Args:
            type: Only this type (not ``OTHER``).
            created_from: Created at or after (timezone-aware).
            created_to: Created at or before (timezone-aware).
            limit: Page size, 1-50.
            cursor: ``next_cursor`` of the previous page.
            options: Per-call overrides.

        Raises:
            EnigmossError: The request failed.
        """
        return self._page(
            _TRANSACTIONS, _query(type, created_from, created_to, limit, cursor), options, Transaction._parse
        )

    def iterate(
        self,
        *,
        type: TransactionType | str | None = None,
        created_from: datetime | None = None,
        created_to: datetime | None = None,
        limit: int | None = None,
        options: RequestOptions | None = None,
    ) -> Iterator[Transaction]:
        """Every transaction matching the filters (see :meth:`list`), fetching pages lazily.

        Raises:
            EnigmossError: A page request failed.
        """
        query = _query(type, created_from, created_to, limit, None)
        return walk(
            lambda cursor: self._page(
                _TRANSACTIONS,
                {**query, "cursor": cursor},
                options,
                Transaction._parse,
                ITERATE_MAX_RETRY_AFTER,
            )
        )


def _query(
    type_: TransactionType | str | None,
    created_from: datetime | None,
    created_to: datetime | None,
    limit: int | None,
    cursor: str | None,
) -> dict[str, Any]:
    value = v.enum(type_, TransactionType, "type")
    if value == TransactionType.OTHER:
        raise InvalidArgumentError('type "other" cannot be used as a filter.')
    return {
        "type": value,
        "created_from": v.query_time(created_from, "created_from"),
        "created_to": v.query_time(created_to, "created_to"),
        "limit": v.int_range(limit, "limit", 1, 50),
        "cursor": cursor,
    }
