"""The request loop: build, sign, send, retry safely, map the response.

The body bytes and the Idempotency-Key are fixed before the first attempt and never change; the
timestamp and the signature are fresh on every attempt.
"""

from __future__ import annotations

import json
import math
import re
import threading
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from enum import Enum
from typing import Any, Literal, TypeVar

from enigmoss import _retry, _signing
from enigmoss._options import RequestOptions
from enigmoss._retry import RetryClass
from enigmoss._secret import Secret
from enigmoss._transport import HttpRequest, HttpResponse, Transport
from enigmoss.errors import (
    APIError,
    AuthenticationError,
    ConfigurationError,
    ConflictError,
    ErrorCode,
    IdempotencyError,
    InvalidArgumentError,
    NotFoundError,
    PermissionDeniedError,
    RateLimitError,
    ServerError,
    TransportError,
    UnexpectedResponseError,
    Violation,
)

API_PREFIX = "/api/v4"
PAYOUT_TIMEOUT = 180.0
"""Creating a payout (and the payout leg of a refund) may take up to ~160 s on the server."""

T = TypeVar("T")

KeyScope = Literal["payment", "payout", "any"]
IdempotencyMode = Literal["order_id", "random"] | None
QueryValue = str | int | bool | None


class KeySupport(Enum):
    NONE = "none"
    SUPPORTED = "supported"
    REQUIRED = "required"


@dataclass(frozen=True, slots=True)
class Endpoint:
    """Static description of one API operation. ``path`` is relative to /api/v4."""

    method: str
    path: str
    scope: KeyScope
    retry: RetryClass
    idempotency: KeySupport = KeySupport.NONE
    key_from_order_id: bool = False
    min_timeout: float | None = None

    @staticmethod
    def get(path: str, scope: KeyScope) -> Endpoint:
        return Endpoint("GET", path, scope, RetryClass.READ)

    @staticmethod
    def create(path: str, scope: KeyScope, support: KeySupport, min_timeout: float | None = None) -> Endpoint:
        """A POST that creates an object identified by order_id; its key derives from the order_id."""
        return Endpoint("POST", path, scope, RetryClass.MONEY, support, True, min_timeout)

    @staticmethod
    def action(path: str, scope: KeyScope) -> Endpoint:
        """A POST action protected by a random per-call key."""
        return Endpoint("POST", path, scope, RetryClass.MONEY, KeySupport.SUPPORTED)

    @staticmethod
    def non_idempotent(path: str, scope: KeyScope) -> Endpoint:
        return Endpoint("POST", path, scope, RetryClass.NON_IDEMPOTENT)

    @staticmethod
    def calculation(path: str, scope: KeyScope) -> Endpoint:
        """A side-effect-free POST: retried like a read."""
        return Endpoint("POST", path, scope, RetryClass.READ)

    @staticmethod
    def patch(path: str, scope: KeyScope) -> Endpoint:
        return Endpoint("PATCH", path, scope, RetryClass.READ)


@dataclass(frozen=True, slots=True)
class Config:
    origin: str
    project_id: str
    payment_key: Secret | None
    payout_key: Secret | None
    timeout: float
    max_retries: int
    idempotency: IdempotencyMode
    user_agent: str


@dataclass(frozen=True, slots=True)
class ApiResult:
    data: Any
    meta: Mapping[str, Any]
    status: int
    request_id: str | None
    idempotency_key: str | None = None

    def parse(self, parse: Callable[[Any], T]) -> T:
        """Runs a model parser; a mismatch after a 2xx keeps the context (outcome unknown)."""
        try:
            return parse(self.data)
        except UnexpectedResponseError as exc:
            if exc.status is not None:
                raise
            raise UnexpectedResponseError(
                str(exc), status=self.status, request_id=self.request_id, idempotency_key=self.idempotency_key
            ) from exc


class ClockSkew:
    """Corrects request timestamps when the local clock drifts; the only mutable client state."""

    MIN_DRIFT = 30
    MAX_DRIFT = 86_400

    def __init__(self) -> None:
        self.offset = 0
        self._lock = threading.Lock()

    def timestamp(self, now: float) -> int:
        return math.floor(now) + self.offset

    def adjust(self, server_time: int, now: float) -> bool:
        """Returns True when the offset changed and the request should be signed again."""
        drift = server_time - math.floor(now)
        if abs(drift) < self.MIN_DRIFT:
            return False
        offset = max(-self.MAX_DRIFT, min(self.MAX_DRIFT, drift))
        with self._lock:
            if offset == self.offset:
                return False
            self.offset = offset
        return True


class Requester:
    def __init__(self, config: Config, transport: Transport) -> None:
        self.config = config
        self.transport = transport
        self.clock_skew = ClockSkew()
        # Injectable for tests.
        self.now: Callable[[], float] = time.time
        self.sleep: Callable[[float], None] = time.sleep
        self.random: Callable[[], float] = _retry.default_random

    def execute(
        self,
        endpoint: Endpoint,
        *,
        path_params: Mapping[str, str] | None = None,
        query: Mapping[str, QueryValue] | None = None,
        body: Mapping[str, Any] | None = None,
        options: RequestOptions | None = None,
        order_id: str | None = None,
        max_retry_after: float = _retry.MAX_RETRY_AFTER,
    ) -> ApiResult:
        options = options or RequestOptions()
        config = self.config

        pairs = _query_pairs(_merge(query or {}, options.extra_query, "query parameter"))
        body_bytes: bytes | None = None
        if endpoint.method != "GET":
            body_bytes = _signing.encode_body(_merge(body or {}, options.extra_body, "body field"))

        path = API_PREFIX + _resolve_path(endpoint.path, path_params or {})
        request_uri = _signing.build_request_uri(path, pairs)
        key = self._key(endpoint.scope, endpoint.method, request_uri)
        idempotency_key = _idempotency_key(
            endpoint, request_uri, order_id, options.idempotency_key, config.idempotency
        )

        max_retries = options.max_retries if options.max_retries is not None else config.max_retries
        timeout = (
            options.timeout
            if options.timeout is not None
            else max(config.timeout, endpoint.min_timeout or 0.0)
        )
        deadline = self.now() + timeout * (max_retries + 1) + max_retry_after * max_retries
        url = config.origin + request_uri

        attempt = 0
        clock_corrected = False
        while True:
            attempt += 1
            timestamp = self.clock_skew.timestamp(self.now())
            headers = {
                "Accept": "application/json",
                "User-Agent": config.user_agent,
                "X-Project-Uuid": config.project_id,
                "X-Timestamp": str(timestamp),
                "X-Signature": _signing.sign_request(
                    key.reveal(),
                    timestamp,
                    config.project_id,
                    endpoint.method,
                    request_uri,
                    body_bytes or b"",
                ),
            }
            if body_bytes is not None:
                headers["Content-Type"] = "application/json"
            if idempotency_key is not None:
                headers["Idempotency-Key"] = idempotency_key

            request = HttpRequest(
                method=endpoint.method,
                url=url,
                headers=headers,
                body=body_bytes,
                timeout=timeout,
                keep_alive=endpoint.retry is RetryClass.READ,
            )

            try:
                response = self.transport.send(request)
            except TransportError as exc:
                exc.idempotency_key = idempotency_key
                retry = _retry.after_transport_failure(
                    endpoint.retry, exc, has_key=idempotency_key is not None
                )
                wait = _retry.delay(attempt, None, max_retry_after, self.random) if retry else None
                if not self._can_retry(wait, attempt, max_retries, deadline):
                    raise
                self.sleep(wait or 0.0)
                continue

            if 200 <= response.status < 300:
                return _success(response, idempotency_key)

            error, has_envelope = map_error(response, idempotency_key)

            if (
                isinstance(error, AuthenticationError)
                and error.code == ErrorCode.TIMESTAMP_EXPIRED
                and not clock_corrected
                and error.server_time is not None
                and self.clock_skew.adjust(error.server_time, self.now())
            ):
                # Authentication runs before any business logic: re-signing is always safe and
                # does not count as a retry.
                clock_corrected = True
                attempt -= 1
                continue

            wait = None
            if isinstance(error, APIError) and _retry.after_error_response(
                endpoint.retry, error, has_envelope=has_envelope, has_key=idempotency_key is not None
            ):
                wait = _retry.delay(attempt, error.retry_after, max_retry_after, self.random)
            if not self._can_retry(wait, attempt, max_retries, deadline):
                raise error
            self.sleep(wait or 0.0)

    def _can_retry(self, wait: float | None, attempt: int, max_retries: int, deadline: float) -> bool:
        return wait is not None and attempt <= max_retries and self.now() + wait < deadline

    def _key(self, scope: KeyScope, method: str, path: str) -> Secret:
        config = self.config
        if scope == "payment":
            key = config.payment_key
        elif scope == "payout":
            key = config.payout_key
        else:
            key = config.payment_key or config.payout_key
        if key is None:
            name = "payout_key" if scope == "payout" else "payment_key"
            raise ConfigurationError(f"{method} {path} requires the {scope} key; pass {name} to Enigmoss().")
        return key


def _idempotency_key(
    endpoint: Endpoint,
    request_uri: str,
    order_id: str | None,
    explicit: str | None,
    mode: IdempotencyMode,
) -> str | None:
    if endpoint.idempotency is KeySupport.NONE:
        return None
    if explicit is not None:
        return explicit
    if mode is None and endpoint.idempotency is KeySupport.SUPPORTED:
        return None
    if endpoint.key_from_order_id and order_id is not None and mode != "random":
        return _signing.derived_idempotency_key(request_uri, order_id)
    return _signing.random_idempotency_key()


def _merge(known: Mapping[str, Any], extra: Mapping[str, Any], what: str) -> dict[str, Any]:
    merged = dict(known)
    for name, value in extra.items():
        if name in merged:
            raise InvalidArgumentError(
                f'Extra {what} "{name}" would override a field the SDK sets; pass it as an argument.'
            )
        merged[name] = value
    return merged


def _query_pairs(query: Mapping[str, Any]) -> list[tuple[str, str]]:
    pairs: list[tuple[str, str]] = []
    for name, value in query.items():
        if value is None:
            continue
        if isinstance(value, bool):
            text = "true" if value else "false"
        elif isinstance(value, str | int):
            text = str(value)
        else:
            raise InvalidArgumentError(f'Query parameter "{name}" must be a string, an integer or a boolean.')
        pairs.append((name, text))
    return pairs


_PLACEHOLDER = re.compile(r"\{([a-z_]+)\}")


def _resolve_path(path: str, params: Mapping[str, str]) -> str:
    def replace(match: re.Match[str]) -> str:
        return _signing.quote_component(params[match.group(1)])

    return _PLACEHOLDER.sub(replace, path)


def _success(response: HttpResponse, idempotency_key: str | None) -> ApiResult:
    request_id = response.header("X-Request-Id")
    try:
        decoded = json.loads(response.body)
    except (ValueError, UnicodeDecodeError) as exc:
        raise UnexpectedResponseError(
            f"The response body is not valid JSON: {exc}",
            status=response.status,
            request_id=request_id,
            idempotency_key=idempotency_key,
        ) from exc
    if not isinstance(decoded, dict) or "data" not in decoded:
        raise UnexpectedResponseError(
            'The response has no "data" member.',
            status=response.status,
            request_id=request_id,
            idempotency_key=idempotency_key,
        )
    meta = decoded.get("meta")
    meta = meta if isinstance(meta, dict) else {}
    if isinstance(meta.get("request_id"), str):
        request_id = meta["request_id"]
    return ApiResult(decoded["data"], meta, response.status, request_id, idempotency_key)


_HINTS = {
    ErrorCode.SIGNATURE_INVALID: (
        "Check that the key type fits the endpoint (payouts, refunds and transfers need the payout key), "
        "that the key belongs to this project, and that the body is sent as signed."
    ),
    ErrorCode.TIMESTAMP_EXPIRED: "Sync the server clock (NTP).",
    ErrorCode.PROJECT_UNKNOWN: "Check project_id.",
    ErrorCode.KEY_INACTIVE: "Enable the API for the project in the dashboard.",
    ErrorCode.IP_FORBIDDEN: (
        "Add this server's IP address to the IP whitelist of the project in the dashboard."
    ),
    ErrorCode.DOMAIN_FORBIDDEN: "Use the API host shown in your dashboard as base_url.",
    ErrorCode.IDEMPOTENCY_PAYLOAD_MISMATCH: (
        "This order_id (Idempotency-Key) was sent before with different data: "
        "use a new order_id for a new object."
    ),
    ErrorCode.IDEMPOTENCY_IN_PROGRESS: (
        "The first request with this key is still running: reconcile later with find_by_order_id(), "
        "do not create a new order_id."
    ),
}


def _hint(code: str, details: Mapping[str, Any], retry_after: int | None) -> str | None:
    if code == ErrorCode.PERMISSION_DENIED:
        permission = details.get("permission")
        if isinstance(permission, str):
            return f'Enable the "{permission}" API permission in the dashboard.'
        return "Enable the API permission of this endpoint in the dashboard."
    if code == ErrorCode.RATE_LIMIT_EXCEEDED and retry_after is not None:
        return f"Retry in {retry_after} s."
    return _HINTS.get(code)


_BY_TYPE: dict[str, type[APIError]] = {
    "authentication_error": AuthenticationError,
    "authorization_error": PermissionDeniedError,
    "not_found": NotFoundError,
    "conflict": ConflictError,
    "rate_limit_error": RateLimitError,
}


def _class_for_status(status: int) -> type[APIError]:
    if status >= 500:
        return ServerError
    return {
        401: AuthenticationError,
        403: PermissionDeniedError,
        404: NotFoundError,
        409: ConflictError,
        429: RateLimitError,
    }.get(status, APIError)


def map_error(
    response: HttpResponse, idempotency_key: str | None
) -> tuple[APIError | UnexpectedResponseError, bool]:
    """Turns a non-2xx response into an exception; the flag tells whether it had the API envelope."""
    status = response.status
    request_id = response.header("X-Request-Id")
    retry_after = _retry_after_header(response.header("Retry-After"))

    if 300 <= status < 400:
        return UnexpectedResponseError(
            f"HTTP {status} redirect: the SDK does not follow redirects. Check base_url.",
            status=status,
            request_id=request_id,
            idempotency_key=idempotency_key,
        ), False

    try:
        decoded = json.loads(response.body)
    except (ValueError, UnicodeDecodeError):
        decoded = None
    if not isinstance(decoded, dict):
        decoded = {}
    error = decoded.get("error")
    if not isinstance(error, dict) or not isinstance(error.get("code"), str):
        return _without_envelope(status, request_id, retry_after, idempotency_key), False

    meta = decoded["meta"] if isinstance(decoded.get("meta"), dict) else {}
    if isinstance(meta.get("request_id"), str):
        request_id = meta["request_id"]
    if isinstance(meta.get("retry_after"), int) and not isinstance(meta["retry_after"], bool):
        retry_after = meta["retry_after"]

    code: str = error["code"]
    error_type = error["type"] if isinstance(error.get("type"), str) else "unknown"
    message = error["message"] if isinstance(error.get("message"), str) else code
    details = error["details"] if isinstance(error.get("details"), dict) else {}

    cls = _BY_TYPE.get(error_type)
    if cls is ConflictError and code.startswith("idempotency."):
        cls = IdempotencyError
    if cls is None:
        cls = _class_for_status(status)
        if cls is ConflictError and code.startswith("idempotency."):
            cls = IdempotencyError

    exc = cls(
        message,
        status=status,
        code=code,
        type=error_type,
        details=details,
        violations=_violations(error.get("violations")),
        retryable=error.get("retryable") is True,
        retry_after=retry_after,
        request_id=request_id,
        idempotency_key=idempotency_key,
    )
    hint = _hint(code, details, retry_after)
    if hint is not None:
        exc.add_note(hint)
    return exc, True


def _without_envelope(
    status: int, request_id: str | None, retry_after: int | None, idempotency_key: str | None
) -> APIError | UnexpectedResponseError:
    if status < 500 and status != 429:
        hint = " Check base_url: the host does not serve this API." if status == 404 else ""
        return UnexpectedResponseError(
            f"Unexpected HTTP {status} response without an API error body.{hint}",
            status=status,
            request_id=request_id,
            idempotency_key=idempotency_key,
        )
    cls = RateLimitError if status == 429 else ServerError
    return cls(
        f"HTTP {status} from the API host or a proxy in front of it.",
        status=status,
        code=f"http.{status}",
        type="rate_limit_error" if status == 429 else "unknown",
        retryable=status == 429 or 502 <= status <= 504,
        retry_after=retry_after,
        request_id=request_id,
        idempotency_key=idempotency_key,
    )


def _violations(raw: Any) -> tuple[Violation, ...]:
    if not isinstance(raw, list):
        return ()
    result = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        result.append(
            Violation(
                field=item["field"] if isinstance(item.get("field"), str) else None,
                rule=item["rule"] if isinstance(item.get("rule"), str) else "unknown",
                message=item["message"] if isinstance(item.get("message"), str) else "",
                params=item["params"] if isinstance(item.get("params"), dict) else {},
            )
        )
    return tuple(result)


_DIGITS = re.compile(r"\d{1,10}")


def _retry_after_header(value: str | None) -> int | None:
    if value is None or not _DIGITS.fullmatch(value.strip()):
        return None
    return int(value.strip())
