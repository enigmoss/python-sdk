"""Payments (invoices and static wallet deposits)."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from typing import Any

from enigmoss._parse import Reader
from enigmoss.models._common import ApiEnum

__all__ = [
    "AmlVerificationLink",
    "AmlVerificationLinkStatus",
    "Payment",
    "PaymentConversion",
    "PaymentDiscount",
    "PaymentFailureCode",
    "PaymentPaid",
    "PaymentPayer",
    "PaymentSettlement",
    "PaymentStatus",
    "Theme",
]


class PaymentStatus(ApiEnum):
    """Status of a payment. See the status table in the README; trust ``Payment.is_final``."""

    PENDING = "pending"
    PROCESSING = "processing"
    CONFIRMING = "confirming"
    PAID = "paid"
    OVERPAID = "overpaid"
    UNDERPAID = "underpaid"
    UNDERPAID_WAITING = "underpaid_waiting"
    EXPIRED = "expired"
    CANCELLED = "cancelled"
    FAILED = "failed"
    LOCKED = "locked"
    REFUNDING = "refunding"
    REFUNDED = "refunded"
    REFUND_FAILED = "refund_failed"
    UNKNOWN = "unknown"

    @property
    def is_paid(self) -> bool:
        """``True`` for ``PAID`` and ``OVERPAID``: the customer paid at least the invoice amount."""
        return self in (PaymentStatus.PAID, PaymentStatus.OVERPAID)


class PaymentFailureCode(ApiEnum):
    """Why a payment failed."""

    PAYMENT_FAILED = "payment_failed"
    PROCESSING_ERROR = "processing_error"
    UNKNOWN = "unknown"


class AmlVerificationLinkStatus(ApiEnum):
    """Status of an AML verification link."""

    INIT = "init"
    PENDING = "pending"
    COMPLETED = "completed"
    UNKNOWN = "unknown"


class Theme(ApiEnum):
    """Theme of the hosted payment page."""

    LIGHT = "light"
    DARK = "dark"
    SYSTEM = "system"


@dataclass(frozen=True, slots=True)
class PaymentPayer:
    """What the customer pays. Empty (all ``None``) until the customer picks a currency."""

    amount: Decimal | None
    currency_code: str | None
    network_code: str | None
    address: str | None
    """Deposit address."""
    sender_address: str | None
    exchange_rate: Decimal | None

    @classmethod
    def _parse(cls, r: Reader) -> PaymentPayer:
        return cls(
            amount=r.opt_decimal("amount"),
            currency_code=r.opt_string("currency_code"),
            network_code=r.opt_string("network_code"),
            address=r.opt_string("address"),
            sender_address=r.opt_string("sender_address"),
            exchange_rate=r.opt_decimal("exchange_rate"),
        )


@dataclass(frozen=True, slots=True)
class PaymentPaid:
    """What was received."""

    amount: Decimal | None
    amount_usd: Decimal | None

    @classmethod
    def _parse(cls, r: Reader) -> PaymentPaid:
        return cls(amount=r.opt_decimal("amount"), amount_usd=r.opt_decimal("amount_usd"))


@dataclass(frozen=True, slots=True)
class PaymentSettlement:
    """What was credited to the project balance."""

    amount: Decimal | None
    currency_code: str | None
    fee: Decimal | None

    @classmethod
    def _parse(cls, r: Reader) -> PaymentSettlement:
        return cls(
            amount=r.opt_decimal("amount"),
            currency_code=r.opt_string("currency_code"),
            fee=r.opt_decimal("fee"),
        )


@dataclass(frozen=True, slots=True)
class PaymentConversion:
    """Automatic conversion of the received funds (``conversion.from`` / ``conversion.to``)."""

    from_amount: Decimal | None
    from_currency_code: str | None
    from_network_code: str | None
    to_currency_code: str | None
    rate: Decimal | None

    @classmethod
    def _parse(cls, r: Reader) -> PaymentConversion:
        source = r.obj("from")
        target = r.obj("to")
        return cls(
            from_amount=source.opt_decimal("amount"),
            from_currency_code=source.opt_string("currency_code"),
            from_network_code=source.opt_string("network_code"),
            to_currency_code=target.opt_string("currency_code"),
            rate=r.opt_decimal("rate"),
        )


@dataclass(frozen=True, slots=True)
class PaymentDiscount:
    """Discount (positive) or surcharge (negative) applied to the invoice."""

    percent: int | None
    amount: Decimal | None

    @classmethod
    def _parse(cls, r: Reader) -> PaymentDiscount:
        return cls(percent=r.opt_integer("percent"), amount=r.opt_decimal("amount"))


@dataclass(frozen=True, slots=True)
class Payment:
    """An invoice, or a deposit to a static wallet (then ``wallet_id`` is set and ``order_id`` is not).

    Also the ``object`` of ``payment.*`` webhooks. Fulfil an order only when
    :attr:`is_successful` is ``True``.
    """

    id: str
    order_id: str | None
    """Your order ID; ``None`` for static wallet deposits."""
    wallet_id: str | None
    """Set for deposits to a static wallet; ``None`` for invoices."""
    status: PaymentStatus
    is_final: bool
    """``True`` when the status will not change any more."""
    failure_code: PaymentFailureCode | None
    amount: Decimal | None
    """Invoice amount; ``None`` for some wallet deposits."""
    currency_code: str
    payer: PaymentPayer
    paid: PaymentPaid
    settlement: PaymentSettlement
    conversion: PaymentConversion | None
    discount: PaymentDiscount
    tx_hash: str | None
    payment_url: str | None
    """The hosted payment page. May be ``None``: never redirect to an empty URL."""
    expires_at: datetime | None
    created_at: datetime | None
    updated_at: datetime | None
    paid_at: datetime | None
    description: str | None
    metadata: Mapping[str, Any]
    """Your metadata, unchanged (``{}`` when none was sent)."""
    raw: Mapping[str, Any] = field(default_factory=dict, repr=False, compare=False)
    """The JSON object as received, including fields this SDK version does not know."""

    @property
    def is_successful(self) -> bool:
        """The one fulfilment rule: paid (``PAID`` or ``OVERPAID``) **and** final.

        A paid payment can still be non-final while an automatic conversion settles.
        """
        return self.status.is_paid and self.is_final

    @classmethod
    def _parse(cls, r: Reader) -> Payment:
        conversion = r.opt_obj("conversion")
        return cls(
            id=r.string("id"),
            order_id=r.opt_string("order_id"),
            wallet_id=r.opt_string("wallet_id"),
            status=r.enum("status", PaymentStatus),
            is_final=r.boolean("is_final"),
            failure_code=r.opt_enum("failure_code", PaymentFailureCode),
            amount=r.opt_decimal("amount"),
            currency_code=r.string("currency_code"),
            payer=PaymentPayer._parse(r.obj("payer")),
            paid=PaymentPaid._parse(r.obj("paid")),
            settlement=PaymentSettlement._parse(r.obj("settlement")),
            conversion=None if conversion is None else PaymentConversion._parse(conversion),
            discount=PaymentDiscount._parse(r.obj("discount")),
            tx_hash=r.opt_string("tx_hash"),
            payment_url=r.opt_string("payment_url"),
            expires_at=r.opt_dt("expires_at"),
            created_at=r.opt_dt("created_at"),
            updated_at=r.opt_dt("updated_at"),
            paid_at=r.opt_dt("paid_at"),
            description=r.opt_string("description"),
            metadata=r.mapping("metadata"),
            raw=r.data,
        )


@dataclass(frozen=True, slots=True)
class AmlVerificationLink:
    """A link where the customer passes the AML check of a locked payment."""

    url: str | None
    status: AmlVerificationLinkStatus | None
    expires_at: datetime | None
    raw: Mapping[str, Any] = field(default_factory=dict, repr=False, compare=False)

    @classmethod
    def _parse(cls, r: Reader) -> AmlVerificationLink:
        return cls(
            url=r.opt_string("url"),
            status=r.opt_enum("status", AmlVerificationLinkStatus),
            expires_at=r.opt_dt("expires_at"),
            raw=r.data,
        )
