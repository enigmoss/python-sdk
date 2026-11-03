"""The project the client is configured for."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from enigmoss._parse import Reader
from enigmoss.models._common import ApiEnum

__all__ = ["KeyType", "Project"]


class KeyType(ApiEnum):
    """Which API key signed the request."""

    PAYMENT = "payment"
    PAYOUT = "payout"
    UNKNOWN = "unknown"


@dataclass(frozen=True, slots=True)
class Project:
    """The project, the key that signed the request and the permissions it can use."""

    id: str
    name: str
    key_type: KeyType
    permissions: tuple[str, ...]
    server_time: int
    """Server Unix time; compare it with your clock to monitor drift."""
    raw: Mapping[str, Any] = field(default_factory=dict, repr=False, compare=False)

    @classmethod
    def _parse(cls, r: Reader) -> Project:
        return cls(
            id=r.string("id"),
            name=r.string("name"),
            key_type=r.enum("key_type", KeyType),
            permissions=r.strings("permissions"),
            server_time=r.integer("server_time"),
            raw=r.data,
        )
