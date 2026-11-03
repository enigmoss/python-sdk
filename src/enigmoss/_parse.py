"""Typed reader over decoded JSON. A type mismatch reports the full path ("data.payer.amount")."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from enum import StrEnum
from typing import Any, TypeVar

from enigmoss.errors import UnexpectedResponseError

E = TypeVar("E", bound=StrEnum)


class Reader:
    """Reads one JSON object. Unknown fields are ignored; a missing field reads as ``None``."""

    __slots__ = ("data", "path")

    def __init__(self, data: Any, path: str = "data") -> None:
        if not isinstance(data, Mapping):
            raise _mismatch(path, "object", data)
        self.data: Mapping[str, Any] = data
        self.path = path

    def _get(self, key: str) -> Any:
        return self.data.get(key)

    def string(self, key: str) -> str:
        value = self._get(key)
        if not isinstance(value, str):
            raise _mismatch(f"{self.path}.{key}", "string", value)
        return value

    def opt_string(self, key: str) -> str | None:
        return None if self._get(key) is None else self.string(key)

    def integer(self, key: str) -> int:
        value = self._get(key)
        if not isinstance(value, int) or isinstance(value, bool):
            raise _mismatch(f"{self.path}.{key}", "integer", value)
        return value

    def opt_integer(self, key: str) -> int | None:
        return None if self._get(key) is None else self.integer(key)

    def boolean(self, key: str) -> bool:
        value = self._get(key)
        if not isinstance(value, bool):
            raise _mismatch(f"{self.path}.{key}", "boolean", value)
        return value

    def decimal(self, key: str) -> Decimal:
        """Money is always a JSON string; a JSON number is rejected so a float never sneaks in."""
        value = self.string(key)
        try:
            result = Decimal(value)
        except InvalidOperation:
            raise _mismatch(f"{self.path}.{key}", "decimal string", value) from None
        if not result.is_finite():
            raise _mismatch(f"{self.path}.{key}", "decimal string", value)
        return result

    def opt_decimal(self, key: str) -> Decimal | None:
        return None if self._get(key) is None else self.decimal(key)

    def dt(self, key: str) -> datetime:
        value = self.string(key)
        try:
            parsed = datetime.fromisoformat(value)
        except ValueError:
            raise _mismatch(f"{self.path}.{key}", "ISO 8601 date-time", value) from None
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=UTC)
        return parsed.astimezone(UTC)

    def opt_dt(self, key: str) -> datetime | None:
        return None if self._get(key) is None else self.dt(key)

    def enum(self, key: str, cls: type[E]) -> E:
        """Values unknown to this SDK version become ``cls.UNKNOWN``."""
        return cls(self.string(key))

    def opt_enum(self, key: str, cls: type[E]) -> E | None:
        return None if self._get(key) is None else self.enum(key, cls)

    def obj(self, key: str) -> Reader:
        return Reader(self._get(key), f"{self.path}.{key}")

    def opt_obj(self, key: str) -> Reader | None:
        return None if self._get(key) is None else self.obj(key)

    def objs(self, key: str) -> list[Reader]:
        value = self._get(key)
        if not isinstance(value, list):
            raise _mismatch(f"{self.path}.{key}", "list", value)
        return [Reader(item, f"{self.path}.{key}.{i}") for i, item in enumerate(value)]

    def strings(self, key: str) -> tuple[str, ...]:
        value = self._get(key)
        if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
            raise _mismatch(f"{self.path}.{key}", "list of strings", value)
        return tuple(value)

    def mapping(self, key: str) -> dict[str, Any]:
        """A JSON object with arbitrary content (metadata). Missing, null or ``[]`` read as ``{}``."""
        value = self._get(key)
        if value is None or value == []:
            return {}
        if not isinstance(value, dict):
            raise _mismatch(f"{self.path}.{key}", "object", value)
        return value


def items(data: Any, path: str = "data") -> list[Reader]:
    """A JSON list of objects."""
    if not isinstance(data, list):
        raise _mismatch(path, "list", data)
    return [Reader(item, f"{path}.{i}") for i, item in enumerate(data)]


def _mismatch(path: str, expected: str, value: Any) -> UnexpectedResponseError:
    return UnexpectedResponseError(
        f'Unexpected type of "{path}": expected {expected}, got {type(value).__name__}.'
    )
