"""Shared building blocks of the models."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any

__all__ = ["ApiEnum", "PaymentMethodRef"]


class ApiEnum(StrEnum):
    """A string enum of the API. Values unknown to this SDK version read as ``UNKNOWN``.

    The server may add values in minor releases: always keep a default branch when matching.
    """

    @classmethod
    def _missing_(cls, value: object) -> Any:
        return cls.__members__.get("UNKNOWN")


@dataclass(frozen=True, slots=True)
class PaymentMethodRef:
    """A currency, optionally on one network, for ``allowed_methods`` / ``excluded_methods``.

    Without ``network_code`` the rule covers every network of the currency.
    """

    currency_code: str
    network_code: str | None = None

    def _to_json(self) -> dict[str, str]:
        data = {"currency_code": self.currency_code}
        if self.network_code is not None:
            data["network_code"] = self.network_code
        return data
