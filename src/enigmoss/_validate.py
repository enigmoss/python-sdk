"""Structural checks of request values (formats, never business limits: the server decides those)."""

from __future__ import annotations

import re
from collections.abc import Mapping
from datetime import UTC, datetime
from decimal import Decimal
from enum import StrEnum
from typing import Any, TypeVar

from enigmoss.errors import InvalidArgumentError

E = TypeVar("E", bound=StrEnum)

_UUID = re.compile(r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}")
_ORDER_ID = re.compile(r"[A-Za-z0-9_-]{1,128}")
_AMOUNT = re.compile(r"\d{1,11}(\.\d{1,18})?")
_CODE = re.compile(r"[A-Za-z0-9_-]{1,32}")

Amount = str | Decimal | int


def uuid(value: str, name: str) -> str:
    """A UUID in lower case: it is part of the signed request URI and of the derived key."""
    if not isinstance(value, str) or not _UUID.fullmatch(value):
        raise InvalidArgumentError(f'{name} must be a UUID, got "{value}".')
    return value.lower()


def code(value: str, name: str) -> str:
    """A currency or network code used as a path segment: never "..", "/" or empty."""
    if not isinstance(value, str) or not _CODE.fullmatch(value):
        raise InvalidArgumentError(f'{name} must be 1-32 letters, digits, "_" or "-", got {value!r}.')
    return value


def checked_uuid(value: str | None, name: str) -> str | None:
    """Validates a UUID filter but sends it as given, like the reference SDK."""
    if value is not None:
        uuid(value, name)
    return value


def order_id(value: str, name: str = "order_id") -> str:
    if not isinstance(value, str) or not _ORDER_ID.fullmatch(value):
        raise InvalidArgumentError(f'{name} must be 1-128 letters, digits, "-" or "_".')
    return value


def opt_order_id(value: str | None) -> str | None:
    return None if value is None else order_id(value)


def amount(value: Amount, name: str = "amount", pattern: re.Pattern[str] = _AMOUNT) -> str:
    """A decimal amount as sent on the wire, never normalized: ``"10.50"`` stays ``"10.50"``."""
    if isinstance(value, bool | float):
        raise InvalidArgumentError(f'{name} must be a str, Decimal or int such as "10.50", never a float.')
    if isinstance(value, Decimal):
        text = format(value, "f")
    elif isinstance(value, int):
        text = str(value)
    elif isinstance(value, str):
        text = value
    else:
        raise InvalidArgumentError(f'{name} must be a str, Decimal or int such as "10.50".')
    if not pattern.fullmatch(text):
        raise InvalidArgumentError(f'{name} must be a positive decimal such as "10.50", got "{text}".')
    return text


def opt_amount(value: Amount | None, name: str = "amount") -> str | None:
    return None if value is None else amount(value, name)


def length(value: str | None, name: str, minimum: int, maximum: int) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or not minimum <= len(value) <= maximum:
        raise InvalidArgumentError(f"{name} must be {minimum}-{maximum} characters long.")
    return value


def int_range(value: int | None, name: str, minimum: int, maximum: int) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int) or not minimum <= value <= maximum:
        raise InvalidArgumentError(f"{name} must be an integer between {minimum} and {maximum}.")
    return value


def opt_bool(value: bool | None, name: str) -> bool | None:
    if value is not None and not isinstance(value, bool):
        raise InvalidArgumentError(f"{name} must be True or False.")
    return value


def enum(value: E | str | None, cls: type[E], name: str) -> str | None:
    """An enum member or its string value; ``UNKNOWN`` is refused in requests."""
    if value is None:
        return None
    try:
        member = cls(value)
    except ValueError:
        member = None
    if member is None or member.value == "unknown":
        allowed = ", ".join(f'"{m.value}"' for m in cls if m.value != "unknown")
        raise InvalidArgumentError(f"{name} must be one of {allowed}, got {value!r}.")
    return member.value


def query_time(value: datetime | None, name: str) -> str | None:
    """UTC with a literal "Z" and no fractional seconds (the API rejects fractions)."""
    if value is None:
        return None
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise InvalidArgumentError(f"{name} must be a timezone-aware datetime, e.g. datetime.now(UTC).")
    return value.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def metadata(value: Mapping[str, Any] | None) -> Mapping[str, Any] | None:
    if value is not None and not isinstance(value, Mapping):
        raise InvalidArgumentError("metadata must be a JSON object (a dict), not a list.")
    return value


def without_none(values: Mapping[str, Any]) -> dict[str, Any]:
    """Drops unset fields, keeping the fixed wire order of the rest."""
    return {k: v for k, v in values.items() if v is not None}
