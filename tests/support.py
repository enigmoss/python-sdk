"""Test helpers: a recording fake transport and fixture loading."""

from __future__ import annotations

import hashlib
import hmac
import json
import threading
from collections.abc import Callable
from pathlib import Path
from typing import Any

import enigmoss
from enigmoss import HttpRequest, HttpResponse, SendPhase, TransportError

FIXTURES = Path(__file__).parent / "fixtures"
PROJECT_ID = "3f2b8c1e-5d4a-4e7b-9c6f-1a2b3c4d5e6f"
PAYMENT_KEY = "test-payment-secret"
PAYOUT_KEY = "test-payout-secret"
BASE_URL = "https://api.example.com"
FALLBACK_URL = "https://api.example.org"
NEW_ORIGIN = "https://api2.example.com"
PROBE_URI = "/api/v4/project/platform-brand"


def contract(name: str) -> Any:
    return json.loads((FIXTURES / "contract" / name).read_text(encoding="utf-8"))


def response(name: str) -> Any:
    return json.loads((FIXTURES / "responses.json").read_text(encoding="utf-8"))[name]


def ok(
    data: Any, meta: dict[str, Any] | None = None, status: int = 200, headers: dict[str, str] | None = None
) -> HttpResponse:
    body = {"data": data, "meta": {"request_id": "req-1", **(meta or {})}}
    return HttpResponse(
        status, {"content-type": "application/json", **(headers or {})}, json.dumps(body).encode()
    )


def api_error(
    status: int,
    code: str,
    type_: str,
    *,
    message: str = "Something went wrong.",
    retryable: bool = False,
    details: dict[str, Any] | None = None,
    violations: list[Any] | None = None,
    retry_after: int | None = None,
    headers: dict[str, str] | None = None,
) -> HttpResponse:
    error: dict[str, Any] = {"type": type_, "code": code, "message": message, "retryable": retryable}
    if details is not None:
        error["details"] = details
    if violations is not None:
        error["violations"] = violations
    meta: dict[str, Any] = {"request_id": "req-err"}
    if retry_after is not None:
        meta["retry_after"] = retry_after
    return HttpResponse(
        status,
        {"content-type": "application/json", **(headers or {})},
        json.dumps({"error": error, "meta": meta}).encode(),
    )


Reply = HttpResponse | TransportError | Callable[[HttpRequest], HttpResponse]


class FakeTransport:
    """Returns queued replies in order (the last one repeats) and records every request."""

    def __init__(self, *replies: Reply) -> None:
        self.replies: list[Reply] = list(replies)
        self.requests: list[HttpRequest] = []

    def queue(self, *replies: Reply) -> FakeTransport:
        self.replies.extend(replies)
        return self

    def send(self, request: HttpRequest) -> HttpResponse:
        self.requests.append(request)
        if not self.replies:
            raise AssertionError(f"unexpected request {request.method} {request.url}")
        reply = self.replies.pop(0) if len(self.replies) > 1 else self.replies[0]
        if isinstance(reply, TransportError):
            raise reply
        if isinstance(reply, HttpResponse):
            return reply
        return reply(request)

    @property
    def last(self) -> HttpRequest:
        return self.requests[-1]

    def request_uri(self, index: int = -1) -> str:
        return self.requests[index].url[len(BASE_URL) :]


class HostsTransport:
    """Routes requests by origin: each origin has its own replies (the last one repeats).

    A request to an origin without replies fails the test: that host must not be contacted.
    """

    def __init__(self, routes: dict[str, list[Reply]]) -> None:
        self.routes = {origin: list(replies) for origin, replies in routes.items()}
        self.requests: list[HttpRequest] = []
        self._lock = threading.Lock()

    def send(self, request: HttpRequest) -> HttpResponse:
        origin = request.url.partition("/api/v4")[0]
        with self._lock:
            self.requests.append(request)
            replies = self.routes.get(origin)
            if not replies:
                raise AssertionError(f"unexpected request {request.method} {request.url}")
            reply = replies.pop(0) if len(replies) > 1 else replies[0]
        if isinstance(reply, TransportError):
            raise reply
        if isinstance(reply, HttpResponse):
            return reply
        return reply(request)

    def to(self, origin: str) -> list[HttpRequest]:
        with self._lock:
            return [r for r in self.requests if r.url.startswith(origin + "/")]


def unreachable() -> TransportError:
    """The API host could not be connected to: nothing was sent, and the transport is sure of it."""
    return TransportError("connection refused", phase=SendPhase.NOT_SENT, host_unreachable=True)


def brand_data(origin: Any = NEW_ORIGIN, refresh_after: Any = 86400) -> dict[str, Any]:
    return {
        "brand": None,
        "payform": {"origin": "https://pay.example.com"},
        "api": {"origin": origin},
        "refresh_after": refresh_after,
    }


def brand_answer(
    origin: Any = NEW_ORIGIN,
    *,
    refresh_after: Any = 86400,
    data: Any = None,
    body: bytes | None = None,
    key: str = PAYMENT_KEY,
    timestamp: str | None = None,
    signature: str | None = None,
    signed: bool = True,
) -> Callable[[HttpRequest], HttpResponse]:
    """The answer of the fallback host to the probe, signed the way the API signs it."""

    def reply(request: HttpRequest) -> HttpResponse:
        payload = brand_data(origin, refresh_after) if data is None else data
        raw = (
            body
            if body is not None
            else json.dumps({"data": payload, "meta": {"request_id": "req-brand"}}).encode()
        )
        headers = {"content-type": "application/json", "x-request-id": "req-brand"}
        if signed:
            stamp = timestamp if timestamp is not None else request.headers["X-Timestamp"]
            headers["x-response-timestamp"] = stamp
            headers["x-response-signature"] = signature or sign_answer(
                key, stamp, request.headers["X-Signature"], raw
            )
        return HttpResponse(200, headers, raw)

    return reply


def sign_answer(key: str, timestamp: str, request_signature: str, body: bytes) -> str:
    """Independent reference of the response signature of the platform-brand endpoint."""
    return hmac.new(
        key.encode(), f"{timestamp}\n{request_signature}\n".encode() + body, hashlib.sha256
    ).hexdigest()


class Clock:
    """A clock the test moves by hand; ``tick`` seconds pass on every reading."""

    def __init__(self, start: float = 1_767_225_600.0, tick: float = 0.0) -> None:
        self.time = start
        self.tick = tick

    def __call__(self) -> float:
        now = self.time
        self.time += self.tick
        return now

    def advance(self, seconds: float) -> None:
        self.time += seconds


def make_client(transport: enigmoss.Transport | None = None, **overrides: Any) -> enigmoss.Enigmoss:
    options: dict[str, Any] = {
        "base_url": BASE_URL,
        "project_id": PROJECT_ID,
        "payment_key": PAYMENT_KEY,
        "payout_key": PAYOUT_KEY,
        "transport": transport or FakeTransport(ok({})),
    }
    options.update(overrides)
    client = enigmoss.Enigmoss(**options)
    no_sleep(client)
    return client


def no_sleep(client: enigmoss.Enigmoss) -> list[float]:
    """Replaces sleeping with recording; returns the list of requested delays."""
    delays: list[float] = []
    client._requester.sleep = delays.append
    client._requester.random = lambda: 0.5
    client.__dict__["_test_delays"] = delays
    return delays


def delays(client: enigmoss.Enigmoss) -> list[float]:
    result: list[float] = client.__dict__["_test_delays"]
    return result
