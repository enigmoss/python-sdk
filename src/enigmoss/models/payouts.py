"""Payouts: sending funds to an external address."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from typing import Any

from enigmoss._parse import Reader
from enigmoss.models._common import ApiEnum

__all__ = [
    "Payout",
    "PayoutAmount",
    "PayoutCharge",
    "PayoutConversion",
    "PayoutPriority",
    "PayoutQuote",
    "PayoutStatus",
]


class PayoutStatus(ApiEnum):
    """Status of a payout."""

    PENDING = "pending"
    PROCESSING = "processing"
    AWAITING_APPROVAL = "awaiting_approval"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    UNKNOWN = "unknown"


class PayoutPriority(ApiEnum):
    """Network fee priority of a payout."""

    RECOMMENDED = "recommended"
    ECONOMY = "economy"
    HIGH = "high"
    HIGHEST = "highest"
    UNKNOWN = "unknown"


@dataclass(frozen=True, slots=True)
class PayoutAmount:
    """What the recipient gets."""

    amount: Decimal | None
    currency_code: str | None

    @classmethod
    def _parse(cls, r: Reader) -> PayoutAmount:
        return cls(amount=r.opt_decimal("amount"), currency_code=r.opt_string("currency_code"))


@dataclass(frozen=True, slots=True)
class PayoutCharge:
    """What is charged from the project balance, including the fee."""

    amount: Decimal | None
    currency_code: str | None
    fee: Decimal | None

    @classmethod
    def _parse(cls, r: Reader) -> PayoutCharge:
        return cls(
            amount=r.opt_decimal("amount"),
            currency_code=r.opt_string("currency_code"),
            fee=r.opt_decimal("fee"),
        )


@dataclass(frozen=True, slots=True)
class PayoutConversion:
    """Conversion from another balance currency."""

    source_currency_code: str | None
    source_amount: Decimal | None
    fee: Decimal | None
    rate: Decimal | None

    @classmethod
    def _parse(cls, r: Reader) -> PayoutConversion:
        return cls(
            source_currency_code=r.opt_string("source_currency_code"),
            source_amount=r.opt_decimal("source_amount"),
            fee=r.opt_decimal("fee"),
            rate=r.opt_decimal("rate"),
        )


@dataclass(frozen=True, slots=True)
class Payout:
    """A payout. Also the ``object`` of ``payout.*`` webhooks."""

    id: str
    order_id: str
    status: PayoutStatus
    is_final: bool
    failure_code: str | None
    amount: Decimal | None
    currency_code: str | None
    network_code: str | None
    address: str
    memo: str | None
    priority: PayoutPriority | None
    recipient: PayoutAmount
    charged: PayoutCharge
    conversion: PayoutConversion | None
    tx_hash: str | None
    created_at: datetime | None
    updated_at: datetime | None
    raw: Mapping[str, Any] = field(default_factory=dict, repr=False, compare=False)

    @classmethod
    def _parse(cls, r: Reader) -> Payout:
        conversion = r.opt_obj("conversion")
        return cls(
            id=r.string("id"),
            order_id=r.string("order_id"),
            status=r.enum("status", PayoutStatus),
            is_final=r.boolean("is_final"),
            failure_code=r.opt_string("failure_code"),
            amount=r.opt_decimal("amount"),
            currency_code=r.opt_string("currency_code"),
            network_code=r.opt_string("network_code"),
            address=r.string("address"),
            memo=r.opt_string("memo"),
            priority=r.opt_enum("priority", PayoutPriority),
            recipient=PayoutAmount._parse(r.obj("recipient")),
            charged=PayoutCharge._parse(r.obj("charged")),
            conversion=None if conversion is None else PayoutConversion._parse(conversion),
            tx_hash=r.opt_string("tx_hash"),
            created_at=r.opt_dt("created_at"),
            updated_at=r.opt_dt("updated_at"),
            raw=r.data,
        )


@dataclass(frozen=True, slots=True)
class PayoutQuote:
    """The calculation of a payout: what the recipient gets and what is charged."""

    amount: Decimal | None
    currency_code: str
    network_code: str
    priority: PayoutPriority
    recipient: PayoutAmount
    charged: PayoutCharge
    conversion: PayoutConversion | None
    raw: Mapping[str, Any] = field(default_factory=dict, repr=False, compare=False)

    @classmethod
    def _parse(cls, r: Reader) -> PayoutQuote:
        conversion = r.opt_obj("conversion")
        return cls(
            amount=r.opt_decimal("amount"),
            currency_code=r.string("currency_code"),
            network_code=r.string("network_code"),
            priority=r.enum("priority", PayoutPriority),
            recipient=PayoutAmount._parse(r.obj("recipient")),
            charged=PayoutCharge._parse(r.obj("charged")),
            conversion=None if conversion is None else PayoutConversion._parse(conversion),
            raw=r.data,
        )
