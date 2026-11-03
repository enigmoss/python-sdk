"""Refunds of payments and of blocked static wallets."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from typing import Any

from enigmoss._parse import Reader
from enigmoss.models._common import ApiEnum

__all__ = ["Refund", "RefundFailureCode", "RefundSource", "RefundStatus"]


class RefundStatus(ApiEnum):
    """Status of a refund."""

    PENDING = "pending"
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"
    UNKNOWN = "unknown"


class RefundSource(ApiEnum):
    """What is refunded."""

    PAYMENT = "payment"
    AML = "aml"
    BLOCKED_WALLET = "blocked_wallet"
    UNKNOWN = "unknown"


class RefundFailureCode(ApiEnum):
    """Why a refund failed."""

    PAYOUT_FAILED = "payout_failed"
    SYSTEM_ERROR = "system_error"
    AML_REFUND_FAILED = "aml_refund_failed"
    GATEWAY_REJECTED = "gateway_rejected"
    UNKNOWN = "unknown"


@dataclass(frozen=True, slots=True)
class Refund:
    """A refund. Also the ``object`` of ``refund.*`` webhooks."""

    id: str
    order_id: str | None
    source: RefundSource
    payment_id: str | None
    wallet_id: str | None
    status: RefundStatus
    is_final: bool
    failure_code: RefundFailureCode | None
    amount: Decimal | None
    currency_code: str
    network_code: str
    address: str
    tx_hash: str | None
    created_at: datetime | None
    updated_at: datetime | None
    completed_at: datetime | None
    raw: Mapping[str, Any] = field(default_factory=dict, repr=False, compare=False)

    @classmethod
    def _parse(cls, r: Reader) -> Refund:
        return cls(
            id=r.string("id"),
            order_id=r.opt_string("order_id"),
            source=r.enum("source", RefundSource),
            payment_id=r.opt_string("payment_id"),
            wallet_id=r.opt_string("wallet_id"),
            status=r.enum("status", RefundStatus),
            is_final=r.boolean("is_final"),
            failure_code=r.opt_enum("failure_code", RefundFailureCode),
            amount=r.opt_decimal("amount"),
            currency_code=r.string("currency_code"),
            network_code=r.string("network_code"),
            address=r.string("address"),
            tx_hash=r.opt_string("tx_hash"),
            created_at=r.opt_dt("created_at"),
            updated_at=r.opt_dt("updated_at"),
            completed_at=r.opt_dt("completed_at"),
            raw=r.data,
        )
