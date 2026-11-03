"""Signatures, request URIs, idempotency keys and the exact JSON bytes of request bodies.

The server signs the raw request URI and body, and compares a repeated request with the same
Idempotency-Key byte for byte. Every function here must produce the same bytes as the other
official SDKs; the shared contract vectors pin them.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import math
import uuid
from collections.abc import Mapping, Sequence
from decimal import Decimal
from typing import Any
from urllib.parse import quote

from enigmoss.errors import InvalidArgumentError

DERIVED_KEY_PREFIX = "sdk1-"

# Scientific notation is used below 1e-4 and from 1e17 on, like the reference SDK.
_MIN_PLAIN_EXPONENT = -4
_MAX_PLAIN_EXPONENT = 16


def sign_request(
    key: str, timestamp: int, project_id: str, method: str, request_uri: str, body: bytes
) -> str:
    """hex(hmac_sha256(key, "{ts}\\n{project}\\n{METHOD}\\n{request_uri}\\n" + body))."""
    head = f"{timestamp}\n{project_id}\n{method.upper()}\n{request_uri}\n".encode()
    return hmac.new(key.encode(), head + body, hashlib.sha256).hexdigest()


def sign_webhook(key: str, timestamp: str, project_id: str, body: bytes) -> str:
    """hex(hmac_sha256(key, "{ts}\\n{project}\\n" + body))."""
    return hmac.new(key.encode(), f"{timestamp}\n{project_id}\n".encode() + body, hashlib.sha256).hexdigest()


def derived_idempotency_key(request_uri: str, order_id: str) -> str:
    """The default key of a create call: ``sdk1-`` + 59 hex chars of sha256("{uri}\\n{order_id}")."""
    digest = hashlib.sha256(f"{request_uri}\n{order_id}".encode()).hexdigest()
    return DERIVED_KEY_PREFIX + digest[:59]


def random_idempotency_key() -> str:
    """A random UUIDv4."""
    return str(uuid.uuid4())


def quote_component(value: str) -> str:
    """Percent-encodes everything except unreserved characters (RFC 3986), like rawurlencode."""
    return quote(value, safe="")


def build_request_uri(path: str, query: Sequence[tuple[str, str]]) -> str:
    """Path plus query; never a bare "?", which some server runtimes drop before signing."""
    if not query:
        return path
    return path + "?" + "&".join(f"{quote_component(k)}={quote_component(v)}" for k, v in query)


def encode_body(body: Mapping[str, Any]) -> bytes:
    """Encodes a request body exactly like the reference SDK.

    Compact separators, raw UTF-8, ``/`` not escaped, U+2028/U+2029 escaped, floats in the
    reference format, keys in insertion order. An empty body is ``{}``.

    Raises:
        InvalidArgumentError: A value cannot be encoded (a lone surrogate, NaN, an unsupported
            type).
    """
    parts: list[str] = []
    _encode(body, parts, "body")
    text = "".join(parts)
    try:
        return text.encode("utf-8")
    except UnicodeEncodeError as exc:  # pragma: no cover - strings are checked in _encode_str
        raise InvalidArgumentError(f"The request body is not valid Unicode: {exc}") from exc


def _encode(value: Any, out: list[str], path: str) -> None:
    if value is None:
        out.append("null")
    elif value is True:
        out.append("true")
    elif value is False:
        out.append("false")
    elif isinstance(value, str):
        out.append(_encode_str(value, path))
    elif isinstance(value, int):
        out.append(int.__repr__(value))
    elif isinstance(value, float):
        out.append(_encode_float(value, path))
    elif isinstance(value, Mapping):
        out.append("{")
        first = True
        for key, item in value.items():
            if isinstance(key, bool) or not isinstance(key, str | int):
                raise InvalidArgumentError(f"{path} has a key of type {type(key).__name__}; use str keys.")
            if not first:
                out.append(",")
            first = False
            out.append(_encode_str(str(key), path))
            out.append(":")
            _encode(item, out, f"{path}.{key}")
        out.append("}")
    elif isinstance(value, list | tuple):
        out.append("[")
        for index, item in enumerate(value):
            if index:
                out.append(",")
            _encode(item, out, f"{path}.{index}")
        out.append("]")
    elif isinstance(value, Decimal):
        raise InvalidArgumentError(f"{path} is a Decimal; pass it as a string, e.g. str(value).")
    else:
        raise InvalidArgumentError(f"{path} has type {type(value).__name__}, which is not a JSON type.")


def _encode_str(value: str, path: str) -> str:
    try:
        value.encode("utf-8")
    except UnicodeEncodeError as exc:
        raise InvalidArgumentError(f"{path} contains a lone surrogate (invalid Unicode).") from exc
    encoded = json.dumps(value, ensure_ascii=False)
    # The reference SDK escapes the JavaScript line terminators; nothing else outside ASCII.
    return encoded.replace("\u2028", "\\u2028").replace("\u2029", "\\u2029")


def _encode_float(value: float, path: str) -> str:
    """Shortest round-trip digits with a kept ".0" and exponents such as "1.0e+17" or "1.0e-5"."""
    if not math.isfinite(value):
        raise InvalidArgumentError(f"{path} is {value!r}; JSON has no NaN or Infinity.")
    sign, digits, exponent = Decimal(repr(value)).as_tuple()
    assert isinstance(exponent, int)  # noqa: S101 - finite values only
    text = "".join(map(str, digits)).rstrip("0") or "0"
    if text == "0":
        return "-0.0" if sign else "0.0"
    # Decimal exponent of the first significant digit.
    point = len(digits) + exponent - 1
    minus = "-" if sign else ""
    if point < _MIN_PLAIN_EXPONENT or point > _MAX_PLAIN_EXPONENT:
        mantissa = text[0] + "." + (text[1:] or "0")
        return f"{minus}{mantissa}e{'+' if point >= 0 else '-'}{abs(point)}"
    if point < 0:
        return f"{minus}0.{'0' * (-point - 1)}{text}"
    integer = text[: point + 1].ljust(point + 1, "0")
    fraction = text[point + 1 :] or "0"
    return f"{minus}{integer}.{fraction}"
