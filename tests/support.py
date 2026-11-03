"""Test helpers: a recording fake transport and fixture loading."""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

import enigmoss
from enigmoss import HttpRequest, HttpResponse, TransportError

FIXTURES = Path(__file__).parent / "fixtures"
PROJECT_ID = "3f2b8c1e-5d4a-4e7b-9c6f-1a2b3c4d5e6f"
PAYMENT_KEY = "test-payment-secret"
PAYOUT_KEY = "test-payout-secret"
BASE_URL = "https://api.example.com"


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


def make_client(transport: FakeTransport | None = None, **overrides: Any) -> enigmoss.Enigmoss:
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
