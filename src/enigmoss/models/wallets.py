"""Static wallets: a permanent deposit address per customer."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from enigmoss._parse import Reader
from enigmoss.models._common import ApiEnum

__all__ = ["Wallet", "WalletStatus"]


class WalletStatus(ApiEnum):
    """Status of a static wallet."""

    ACTIVE = "active"
    BLOCKED = "blocked"
    INACTIVE = "inactive"
    UNKNOWN = "unknown"


@dataclass(frozen=True, slots=True)
class Wallet:
    """A static wallet. Deposits to it arrive as ``payment.*`` webhooks with ``wallet_id`` set."""

    id: str
    order_id: str | None
    currency_code: str
    network_code: str
    address: str
    status: WalletStatus
    refund_incoming: bool
    """``True`` when deposits to a blocked wallet are refunded automatically."""
    callback_url: str | None
    payment_url: str
    created_at: datetime | None
    updated_at: datetime | None
    raw: Mapping[str, Any] = field(default_factory=dict, repr=False, compare=False)

    @classmethod
    def _parse(cls, r: Reader) -> Wallet:
        return cls(
            id=r.string("id"),
            order_id=r.opt_string("order_id"),
            currency_code=r.string("currency_code"),
            network_code=r.string("network_code"),
            address=r.string("address"),
            status=r.enum("status", WalletStatus),
            refund_incoming=r.boolean("refund_incoming"),
            callback_url=r.opt_string("callback_url"),
            payment_url=r.string("payment_url"),
            created_at=r.opt_dt("created_at"),
            updated_at=r.opt_dt("updated_at"),
            raw=r.data,
        )
