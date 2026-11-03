"""Balances, transactions and transfers to the personal account."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from typing import Any

from enigmoss._parse import Reader
from enigmoss.models._common import ApiEnum

__all__ = ["Balance", "Transaction", "TransactionType", "Transfer"]


@dataclass(frozen=True, slots=True)
class Balance:
    """The balance of one currency."""

    currency_code: str
    balance: Decimal
    balance_usd: Decimal | None
    raw: Mapping[str, Any] = field(default_factory=dict, repr=False, compare=False)

    @classmethod
    def _parse(cls, r: Reader) -> Balance:
        return cls(
            currency_code=r.string("currency_code"),
            balance=r.decimal("balance"),
            balance_usd=r.opt_decimal("balance_usd"),
            raw=r.data,
        )


class TransactionType(ApiEnum):
    """Type of a balance transaction. ``OTHER`` and ``UNKNOWN`` cannot be used as filters."""

    PAYMENT = "payment"
    PAYMENT_HOLD = "payment_hold"
    PAYOUT = "payout"
    REFUND = "refund"
    TRANSFER = "transfer"
    CONVERSION_OUT = "conversion_out"
    CONVERSION_IN = "conversion_in"
    OTHER = "other"
    UNKNOWN = "unknown"


@dataclass(frozen=True, slots=True)
class Transaction:
    """One balance movement."""

    id: str
    type: TransactionType
    amount: Decimal
    fee: Decimal
    currency_code: str
    network_code: str | None
    payment_id: str | None
    payout_id: str | None
    refund_id: str | None
    transfer_id: str | None
    created_at: datetime
    raw: Mapping[str, Any] = field(default_factory=dict, repr=False, compare=False)

    @classmethod
    def _parse(cls, r: Reader) -> Transaction:
        return cls(
            id=r.string("id"),
            type=r.enum("type", TransactionType),
            amount=r.decimal("amount"),
            fee=r.decimal("fee"),
            currency_code=r.string("currency_code"),
            network_code=r.opt_string("network_code"),
            payment_id=r.opt_string("payment_id"),
            payout_id=r.opt_string("payout_id"),
            refund_id=r.opt_string("refund_id"),
            transfer_id=r.opt_string("transfer_id"),
            created_at=r.dt("created_at"),
            raw=r.data,
        )


@dataclass(frozen=True, slots=True)
class Transfer:
    """A transfer from the project balance to the personal account."""

    id: str
    order_id: str
    amount: Decimal
    currency_code: str
    transaction_id: str | None
    created_at: datetime
    raw: Mapping[str, Any] = field(default_factory=dict, repr=False, compare=False)

    @classmethod
    def _parse(cls, r: Reader) -> Transfer:
        return cls(
            id=r.string("id"),
            order_id=r.string("order_id"),
            amount=r.decimal("amount"),
            currency_code=r.string("currency_code"),
            transaction_id=r.opt_string("transaction_id"),
            created_at=r.dt("created_at"),
            raw=r.data,
        )
