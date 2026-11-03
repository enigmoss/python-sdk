"""Per-call overrides."""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from enigmoss.errors import InvalidArgumentError

__all__ = ["RequestOptions"]

_KEY = re.compile(r"[\x21-\x7e]{1,64}")


@dataclass(frozen=True, slots=True, kw_only=True)
class RequestOptions:
    """Overrides for one call, passed as ``options=`` to any resource method.

    Example::

        client.payouts.create(..., options=enigmoss.RequestOptions(max_retries=0))

    Attributes:
        idempotency_key: Sends this ``Idempotency-Key`` instead of the one the SDK would choose
            (1-64 printable ASCII characters, no spaces). Also turns on the key for
            :meth:`Enigmoss.request`.
        timeout: Time limit of one attempt, in seconds.
        max_retries: Automatic retries after the first attempt; ``0`` disables them for this call.
        extra_body: Body fields the SDK does not model yet; may not override a known field.
        extra_query: Query parameters the SDK does not model yet; may not override a known one.

    Raises:
        InvalidArgumentError: An option is out of range.
    """

    idempotency_key: str | None = None
    timeout: float | None = None
    max_retries: int | None = None
    extra_body: Mapping[str, Any] = field(default_factory=dict)
    extra_query: Mapping[str, str | int | bool] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.idempotency_key is not None and not (
            isinstance(self.idempotency_key, str) and _KEY.fullmatch(self.idempotency_key)
        ):
            raise InvalidArgumentError(
                "idempotency_key must be 1-64 printable ASCII characters without spaces."
            )
        if self.timeout is not None and not self.timeout > 0:
            raise InvalidArgumentError("timeout must be a positive number of seconds.")
        if self.max_retries is not None and not (
            isinstance(self.max_retries, int)
            and not isinstance(self.max_retries, bool)
            and 0 <= self.max_retries <= 10
        ):
            raise InvalidArgumentError("max_retries must be an integer between 0 and 10.")
