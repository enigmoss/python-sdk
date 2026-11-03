"""An API key that never leaks into logs, reprs, tracebacks or pickles."""

from __future__ import annotations

from typing import NoReturn

from enigmoss.errors import InvalidArgumentError

__all__ = ["Secret"]


class Secret:
    """Wraps an API key. ``repr()`` and ``str()`` show ``****``; pickling is refused.

    The SDK wraps plain strings itself, so you only need this class to keep a key out of your own
    logs before it reaches the client.

    Raises:
        InvalidArgumentError: The value is empty or not a string.
    """

    __slots__ = ("__value",)

    def __init__(self, value: str) -> None:
        if not isinstance(value, str) or value == "":
            raise InvalidArgumentError("An API key must be a non-empty string.")
        self.__value = value

    def reveal(self) -> str:
        """Returns the raw key. Only for signing: never log or store the result."""
        return self.__value

    def __repr__(self) -> str:
        return "Secret('****')"

    def __str__(self) -> str:
        return "****"

    def __reduce__(self) -> NoReturn:
        raise TypeError("enigmoss.Secret cannot be pickled: keep API keys in configuration, not in payloads.")

    def __copy__(self) -> Secret:
        return self

    def __deepcopy__(self, memo: object) -> Secret:
        return self
