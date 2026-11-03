"""Payment methods, payout methods and exchange rates."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

from enigmoss._parse import Reader
from enigmoss.models.payouts import PayoutPriority

__all__ = [
    "ExchangeRate",
    "PaymentMethod",
    "PayoutMethod",
    "PayoutMethodDetails",
    "PayoutMethodFee",
    "PriorityFee",
]


@dataclass(frozen=True, slots=True)
class PaymentMethod:
    """A currency/network customers can pay with, and its discount."""

    currency_code: str
    network_code: str | None
    is_enabled: bool
    discount_percent: int
    raw: Mapping[str, Any] = field(default_factory=dict, repr=False, compare=False)

    @classmethod
    def _parse(cls, r: Reader) -> PaymentMethod:
        return cls(
            currency_code=r.string("currency_code"),
            network_code=r.opt_string("network_code"),
            is_enabled=r.boolean("is_enabled"),
            discount_percent=r.integer("discount_percent"),
            raw=r.data,
        )


@dataclass(frozen=True, slots=True)
class PayoutMethodFee:
    """Fee of a payout method: a fixed ``amount`` plus a ``percent``."""

    amount: Decimal
    percent: Decimal

    @classmethod
    def _parse(cls, r: Reader) -> PayoutMethodFee:
        return cls(amount=r.decimal("amount"), percent=r.decimal("percent"))


@dataclass(frozen=True, slots=True)
class PayoutMethod:
    """A currency/network payouts can be sent to."""

    currency_code: str
    network_code: str
    contract_address: str | None
    min_amount: Decimal
    max_amount: Decimal
    fee: PayoutMethodFee
    raw: Mapping[str, Any] = field(default_factory=dict, repr=False, compare=False)

    @classmethod
    def _parse(cls, r: Reader) -> PayoutMethod:
        return cls(
            currency_code=r.string("currency_code"),
            network_code=r.string("network_code"),
            contract_address=r.opt_string("contract_address"),
            min_amount=r.decimal("min_amount"),
            max_amount=r.decimal("max_amount"),
            fee=PayoutMethodFee._parse(r.obj("fee")),
            raw=r.data,
        )


@dataclass(frozen=True, slots=True)
class PriorityFee:
    """Network fee of one payout priority."""

    priority: PayoutPriority
    fee: Decimal

    @classmethod
    def _parse(cls, r: Reader) -> PriorityFee:
        return cls(priority=r.enum("priority", PayoutPriority), fee=r.decimal("fee"))


@dataclass(frozen=True, slots=True)
class PayoutMethodDetails:
    """Limits, precision and priority fees of one payout method."""

    currency_code: str
    network_code: str
    network_name: str | None
    token_standard: str | None
    min_amount: Decimal
    max_amount: Decimal
    decimals: int
    is_memo_available: bool
    priorities: tuple[PriorityFee, ...]
    raw: Mapping[str, Any] = field(default_factory=dict, repr=False, compare=False)

    @classmethod
    def _parse(cls, r: Reader) -> PayoutMethodDetails:
        return cls(
            currency_code=r.string("currency_code"),
            network_code=r.string("network_code"),
            network_name=r.opt_string("network_name"),
            token_standard=r.opt_string("token_standard"),
            min_amount=r.decimal("min_amount"),
            max_amount=r.decimal("max_amount"),
            decimals=r.integer("decimals"),
            is_memo_available=r.boolean("is_memo_available"),
            priorities=tuple(PriorityFee._parse(item) for item in r.objs("priorities")),
            raw=r.data,
        )


@dataclass(frozen=True, slots=True)
class ExchangeRate:
    """An exchange rate. ``from_`` is the wire field ``from`` (a Python keyword)."""

    from_: str
    to: str
    rate: Decimal
    raw: Mapping[str, Any] = field(default_factory=dict, repr=False, compare=False)

    @classmethod
    def _parse(cls, r: Reader) -> ExchangeRate:
        return cls(from_=r.string("from"), to=r.string("to"), rate=r.decimal("rate"), raw=r.data)
