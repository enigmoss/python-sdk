"""The API origin fallback: when the API host cannot be reached, ask the fallback host where it is."""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

import pytest

import enigmoss
from enigmoss import HttpRequest, HttpResponse, OriginChange, SendPhase, TransportError
from enigmoss._fallback import comparable, validate_origin
from enigmoss._signing import sign_request
from tests.support import (
    BASE_URL,
    FALLBACK_URL,
    NEW_ORIGIN,
    PAYMENT_KEY,
    PAYOUT_KEY,
    PROBE_URI,
    PROJECT_ID,
    Clock,
    HostsTransport,
    Reply,
    api_error,
    brand_answer,
    brand_data,
    make_client,
    ok,
    response,
    unreachable,
)

PAYMENT_ID = "7c9e6679-7425-40de-944b-e07fc1f90ae7"
PAYOUT: dict[str, Any] = {
    "order_id": "withdrawal-1",
    "amount": "100.00",
    "currency_code": "USDT",
    "address": "TRecipientabcdefghijklmnopqrstuvwx",
}
START = 1_767_225_600.0


def scenario(
    primary: list[Reply],
    fallback: list[Reply] | None = None,
    moved: list[Reply] | None = None,
    **overrides: Any,
) -> tuple[enigmoss.Enigmoss, HostsTransport, Clock, list[OriginChange]]:
    """A client with a fallback host; a host without replies must not be contacted."""
    routes = {BASE_URL: primary}
    if fallback is not None:
        routes[FALLBACK_URL] = fallback
    if moved is not None:
        routes[NEW_ORIGIN] = moved
    transport = HostsTransport(routes)
    changes: list[OriginChange] = []
    options: dict[str, Any] = {"fallback_base_url": FALLBACK_URL, "on_origin_change": changes.append}
    options.update(overrides)
    client = make_client(transport, **options)
    clock = Clock(START)
    client._requester.now = clock
    return client, transport, clock, changes


def test_without_a_fallback_host_nothing_changes() -> None:
    error = unreachable()
    transport = HostsTransport({BASE_URL: [error]})
    client = make_client(transport)
    with pytest.raises(TransportError) as caught:
        client.payments.get(PAYMENT_ID)
    assert caught.value is error
    assert len(transport.requests) == 3
    assert client.base_url == BASE_URL


def test_a_retry_on_the_api_host_helps_and_the_fallback_host_is_not_asked() -> None:
    client, transport, _, changes = scenario([unreachable(), ok(response("payment"))])
    assert client.payments.get(PAYMENT_ID).id == PAYMENT_ID
    assert len(transport.to(BASE_URL)) == 2
    assert transport.to(FALLBACK_URL) == []
    assert changes == []


def test_a_new_origin_is_used_for_the_call_remembered_and_reported() -> None:
    client, transport, _, changes = scenario(
        [unreachable()], [brand_answer()], [ok(response("payment"))], payout_key=None
    )
    assert client.payments.get(PAYMENT_ID).id == PAYMENT_ID

    assert len(transport.to(BASE_URL)) == 3  # the usual retries come first
    (probe,) = transport.to(FALLBACK_URL)
    assert (probe.method, probe.url, probe.body) == ("GET", FALLBACK_URL + PROBE_URI, None)
    assert (probe.timeout, probe.keep_alive) == (3.0, False)
    assert set(probe.headers) == {"Accept", "User-Agent", "X-Project-Uuid", "X-Timestamp", "X-Signature"}
    assert probe.headers["X-Signature"] == sign_request(
        PAYMENT_KEY, int(probe.headers["X-Timestamp"]), PROJECT_ID, "GET", PROBE_URI, b""
    )
    (repeated,) = transport.to(NEW_ORIGIN)
    assert repeated.url == f"{NEW_ORIGIN}/api/v4/payments/{PAYMENT_ID}"

    assert client.base_url == NEW_ORIGIN
    assert repr(client) == f"Enigmoss(base_url={NEW_ORIGIN!r}, project_id={PROJECT_ID!r})"
    assert changes == [
        OriginChange(BASE_URL, NEW_ORIGIN, datetime.fromtimestamp(START + 86400, tz=UTC)),
    ]

    client.payments.get(PAYMENT_ID)  # the next call goes straight to the new origin
    assert len(transport.to(BASE_URL)) == 3
    assert len(transport.to(FALLBACK_URL)) == 1
    assert len(transport.to(NEW_ORIGIN)) == 2
    assert len(changes) == 1


def test_the_probe_is_signed_with_the_payout_key_when_it_is_the_only_one() -> None:
    client, transport, _, _ = scenario(
        [unreachable()], [brand_answer(key=PAYOUT_KEY)], [ok(response("payout"))], payment_key=None
    )
    client.payouts.get("9b2d4f6a-1c3e-4a5b-8d7f-0e1a2b3c4d5e")
    (probe,) = transport.to(FALLBACK_URL)
    assert probe.headers["X-Signature"] == sign_request(
        PAYOUT_KEY, int(probe.headers["X-Timestamp"]), PROJECT_ID, "GET", PROBE_URI, b""
    )
    assert client.base_url == NEW_ORIGIN


def test_a_money_post_is_repeated_with_the_same_key_and_body_and_a_fresh_signature() -> None:
    busy = api_error(503, "dependency.unavailable", "dependency_error", retryable=True)
    client, transport, clock, _ = scenario(
        [unreachable()], [brand_answer()], [busy, busy, ok(response("payout"))]
    )
    clock.tick = 1.0  # every reading of the clock is a second later
    client.payouts.create(**PAYOUT)

    before = transport.to(BASE_URL)
    after = transport.to(NEW_ORIGIN)
    # The attempts start from zero on the new origin: three of them, as on the old one.
    assert (len(before), len(after)) == (3, 3)
    assert {r.headers["Idempotency-Key"] for r in before + after} == {before[0].headers["Idempotency-Key"]}
    assert before[0].headers["Idempotency-Key"].startswith("sdk1-")
    assert {r.body for r in before + after} == {before[0].body}
    assert after[0].url == NEW_ORIGIN + "/api/v4/payouts"
    stamps = [r.headers["X-Timestamp"] for r in before + after]
    assert len(set(stamps)) == len(stamps)
    for request in after:
        assert request.body is not None
        assert request.headers["X-Signature"] == sign_request(
            PAYOUT_KEY,
            int(request.headers["X-Timestamp"]),
            PROJECT_ID,
            "POST",
            "/api/v4/payouts",
            request.body,
        )
    assert "Idempotency-Key" not in transport.to(FALLBACK_URL)[0].headers


def proxy_page(status: int) -> HttpResponse:
    return HttpResponse(status, {"content-type": "text/html"}, b"<html>nothing here</html>")


FAILED_ANSWERS: list[tuple[str, Reply, type[enigmoss.EnigmossError], str]] = [
    (
        "401",
        api_error(401, "auth.signature_invalid", "authentication_error"),
        enigmoss.AuthenticationError,
        "",
    ),
    ("403", api_error(403, "auth.ip_forbidden", "authorization_error"), enigmoss.PermissionDeniedError, ""),
    ("404", api_error(404, "resource.not_found", "not_found"), enigmoss.NotFoundError, ""),
    ("404 page", proxy_page(404), enigmoss.UnexpectedResponseError, "Check fallback_base_url"),
    (
        "429",
        api_error(429, "rate_limit.exceeded", "rate_limit_error", retryable=True, retry_after=1),
        enigmoss.RateLimitError,
        "",
    ),
    ("500", api_error(500, "internal.server_error", "internal_error"), enigmoss.ServerError, ""),
    (
        "503",
        api_error(503, "dependency.unavailable", "dependency_error", retryable=True),
        enigmoss.ServerError,
        "",
    ),
    ("502 page", proxy_page(502), enigmoss.ServerError, ""),
    ("302", HttpResponse(302, {"location": NEW_ORIGIN}, b""), enigmoss.UnexpectedResponseError, "redirect"),
    (
        "200 without api",
        brand_answer(data={"brand": None, "payform": {"origin": "https://pay.example.com"}}),
        enigmoss.UnexpectedResponseError,
        "no data.api.origin",
    ),
    ("200 origin is null", brand_answer(None), enigmoss.UnexpectedResponseError, "no data.api.origin"),
    ("200 origin is a number", brand_answer(443), enigmoss.UnexpectedResponseError, "no data.api.origin"),
    ("200 not json", brand_answer(body=b"<html>"), enigmoss.UnexpectedResponseError, "no data.api.origin"),
    ("200 data is a list", brand_answer(data=[]), enigmoss.UnexpectedResponseError, "no data.api.origin"),
    (
        "origin http",
        brand_answer("http://api2.example.com"),
        enigmoss.UnexpectedResponseError,
        "not an https",
    ),
    ("origin ip", brand_answer("https://203.0.113.7"), enigmoss.UnexpectedResponseError, "not an https"),
    ("origin port", brand_answer("https://api2.example.com:8443"), enigmoss.UnexpectedResponseError, ""),
    ("origin path", brand_answer("https://api2.example.com/api/v4"), enigmoss.UnexpectedResponseError, ""),
    ("origin userinfo", brand_answer("https://key@api2.example.com"), enigmoss.UnexpectedResponseError, ""),
    ("origin .local", brand_answer("https://api.intranet.local"), enigmoss.UnexpectedResponseError, ""),
    ("origin localhost", brand_answer("https://localhost"), enigmoss.UnexpectedResponseError, ""),
    ("origin loopback http", brand_answer("http://localhost:8080"), enigmoss.UnexpectedResponseError, ""),
    ("unsigned", brand_answer(signed=False), enigmoss.UnexpectedResponseError, "not signed"),
    (
        "signed with another key",
        brand_answer(key="other"),
        enigmoss.UnexpectedResponseError,
        "does not match",
    ),
    (
        "signature of another body",
        brand_answer(signature="ab" * 32),
        enigmoss.UnexpectedResponseError,
        "does not",
    ),
    (
        "stale timestamp",
        brand_answer(timestamp=str(int(START) - 301)),
        enigmoss.UnexpectedResponseError,
        "300 s away",
    ),
    (
        "timestamp from the future",
        brand_answer(timestamp=str(int(START) + 1000)),
        enigmoss.UnexpectedResponseError,
        "300 s away",
    ),
    (
        "timestamp is not a number",
        brand_answer(timestamp="now"),
        enigmoss.UnexpectedResponseError,
        "Unix time",
    ),
]


@pytest.mark.parametrize(
    ("answer", "expected", "text"),
    [case[1:] for case in FAILED_ANSWERS],
    ids=[case[0] for case in FAILED_ANSWERS],
)
def test_an_unusable_answer_of_the_fallback_host_is_raised_and_nothing_is_remembered(
    answer: Reply, expected: type[enigmoss.EnigmossError], text: str
) -> None:
    error = unreachable()
    client, transport, clock, changes = scenario([error], [answer])

    with pytest.raises(enigmoss.EnigmossError) as caught:
        client.payouts.create(**PAYOUT)
    failure = caught.value
    assert type(failure) is expected
    assert failure.__cause__ is error  # the network error that started it
    assert FALLBACK_URL in str(failure) and text in str(failure)
    assert any("fallback host" in note for note in failure.__notes__)
    # The payout request never left the client, whatever the status of the fallback answer.
    assert failure.outcome_unknown is False
    assert failure.idempotency_key == transport.to(BASE_URL)[0].headers["Idempotency-Key"]

    assert len(transport.to(FALLBACK_URL)) == 1
    assert client.base_url == BASE_URL
    assert changes == []

    # The pause keeps only a time: the next call fails with its own network error, no probe.
    clock.advance(299)
    with pytest.raises(TransportError) as again:
        client.payments.get(PAYMENT_ID)
    assert again.value is error
    assert len(transport.to(FALLBACK_URL)) == 1

    clock.advance(2)  # the pause of 300 s is over
    with pytest.raises(expected):
        client.payments.get(PAYMENT_ID)
    assert len(transport.to(FALLBACK_URL)) == 2


@pytest.mark.parametrize("origin", [BASE_URL, "https://API.example.com:443"])
def test_the_same_origin_raises_the_network_error(origin: str) -> None:
    error = unreachable()
    client, transport, _, changes = scenario([error], [brand_answer(origin)])
    with pytest.raises(TransportError) as caught:
        client.payments.get(PAYMENT_ID)
    assert caught.value is error
    assert caught.value.__cause__ is None
    assert (client.base_url, changes) == (BASE_URL, [])

    with pytest.raises(TransportError):  # paused: the fallback host is not asked again
        client.payments.get(PAYMENT_ID)
    assert len(transport.to(FALLBACK_URL)) == 1


@pytest.mark.parametrize(
    "failure",
    [
        unreachable(),
        TransportError("timed out", phase=SendPhase.SENT, timed_out=True),
        TransportError("reset", phase=SendPhase.UNKNOWN),
    ],
    ids=["refused", "timeout", "reset"],
)
def test_an_unreachable_fallback_host_raises_the_network_error(failure: TransportError) -> None:
    error = unreachable()
    client, transport, _, changes = scenario([error], [failure])
    with pytest.raises(TransportError) as caught:
        client.payments.get(PAYMENT_ID)
    assert caught.value is error
    assert (client.base_url, changes) == (BASE_URL, [])
    with pytest.raises(TransportError):
        client.payments.get(PAYMENT_ID)
    assert len(transport.to(FALLBACK_URL)) == 1  # paused


@pytest.mark.parametrize(
    "failure",
    [
        # Not sent, but the transport does not say that the API host itself was unreachable:
        # a custom transport, a failing proxy.
        TransportError("proxy refused", phase=SendPhase.NOT_SENT),
        TransportError("reset", phase=SendPhase.SENT),
        TransportError("timed out", phase=SendPhase.SENT, timed_out=True),
        TransportError("no idea", phase=SendPhase.UNKNOWN, host_unreachable=True),
    ],
    ids=["not sent", "sent", "timeout", "unknown phase"],
)
def test_only_a_host_that_certainly_was_not_reached_starts_the_fallback(failure: TransportError) -> None:
    client, transport, _, _ = scenario([failure])
    with pytest.raises(TransportError) as caught:
        client.payments.get(PAYMENT_ID)
    assert caught.value is failure
    assert caught.value.host_unreachable is False
    assert len(transport.requests) == 3  # the retries, and no request to the fallback host


def test_an_error_response_never_starts_the_fallback() -> None:
    client, transport, _, _ = scenario(
        [unreachable(), unreachable(), api_error(404, "resource.not_found", "not_found")]
    )
    with pytest.raises(enigmoss.NotFoundError):
        client.payments.get(PAYMENT_ID)
    assert len(transport.requests) == 3


def test_one_fallback_per_call() -> None:
    error, second = unreachable(), unreachable()
    client, transport, _, changes = scenario([error], [brand_answer()], [second])
    with pytest.raises(TransportError) as caught:
        client.payments.get(PAYMENT_ID)
    # The new origin failed too: its error is raised, the fallback host is not asked twice.
    assert caught.value is second
    assert (len(transport.to(BASE_URL)), len(transport.to(NEW_ORIGIN))) == (3, 3)
    assert len(transport.to(FALLBACK_URL)) == 1
    assert len(changes) == 1

    # The next call fails on the remembered origin and asks again: it is told the same origin.
    with pytest.raises(TransportError) as again:
        client.payments.get(PAYMENT_ID)
    assert again.value is second
    assert len(transport.to(FALLBACK_URL)) == 2
    assert (len(transport.to(BASE_URL)), len(changes)) == (3, 1)


@pytest.mark.parametrize(
    ("refresh_after", "kept_for"),
    [(86400, 86400), (10, 3600), (0, 3600), (10**9, 604800), ("soon", 3600), (None, 3600), (True, 3600)],
)
def test_the_origin_is_kept_for_refresh_after_within_one_hour_and_seven_days(
    refresh_after: Any, kept_for: int
) -> None:
    client, transport, clock, changes = scenario(
        [unreachable()], [brand_answer(refresh_after=refresh_after)], [ok(response("payment"))]
    )
    client.payments.get(PAYMENT_ID)
    assert changes[0].expires_at == datetime.fromtimestamp(START + kept_for, tz=UTC)

    clock.advance(kept_for - 1)
    client.payments.get(PAYMENT_ID)
    assert (client.base_url, len(transport.to(BASE_URL))) == (NEW_ORIGIN, 3)

    clock.advance(2)  # expired: back to base_url, which is asked first again
    assert client.base_url == BASE_URL
    client.payments.get(PAYMENT_ID)
    assert len(transport.to(BASE_URL)) == 6
    assert len(transport.to(FALLBACK_URL)) == 2
    assert len(changes) == 2


def test_the_fallback_is_skipped_when_the_call_has_no_time_left() -> None:
    error = unreachable()
    client, transport, _, _ = scenario([error], [brand_answer()], [ok(response("payment"))])
    quick = enigmoss.RequestOptions(timeout=2.9, max_retries=0)
    with pytest.raises(TransportError) as caught:
        client.payments.get(PAYMENT_ID, options=quick)
    assert caught.value is error
    assert transport.to(FALLBACK_URL) == []

    # Skipping is not a failed fallback: nothing is paused.
    client.payments.get(PAYMENT_ID, options=enigmoss.RequestOptions(timeout=3, max_retries=0))
    assert len(transport.to(FALLBACK_URL)) == 1


def test_an_expired_timestamp_on_the_probe_is_corrected_once() -> None:
    drift = 1000
    expired = api_error(
        401, "auth.timestamp_expired", "authentication_error", details={"server_time": int(START) + drift}
    )
    client, transport, _, _ = scenario([unreachable()], [expired, brand_answer()], [ok(response("payment"))])
    client.payments.get(PAYMENT_ID)
    first, second = transport.to(FALLBACK_URL)
    assert int(first.headers["X-Timestamp"]) == int(START)
    assert int(second.headers["X-Timestamp"]) == int(START) + drift
    # The answer is checked against the corrected clock, and the repeated call is signed with it.
    assert client.base_url == NEW_ORIGIN
    assert int(transport.to(NEW_ORIGIN)[0].headers["X-Timestamp"]) == int(START) + drift


@pytest.mark.parametrize("offset", [-300, 300])
def test_the_answer_may_be_300_seconds_away_and_its_signature_in_any_case(offset: int) -> None:
    def shouting(request: HttpRequest) -> HttpResponse:
        answer = brand_answer(timestamp=str(int(START) + offset))(request)
        headers = dict(answer.headers)
        headers["x-response-signature"] = headers["x-response-signature"].upper()
        return HttpResponse(answer.status, headers, answer.body)

    client, _, _, _ = scenario([unreachable()], [shouting], [ok(response("payment"))])
    client.payments.get(PAYMENT_ID)
    assert client.base_url == NEW_ORIGIN


def test_a_second_expired_timestamp_on_the_probe_is_an_error() -> None:
    expired = api_error(
        401, "auth.timestamp_expired", "authentication_error", details={"server_time": int(START) + 1000}
    )
    client, transport, _, _ = scenario([unreachable()], [expired])
    with pytest.raises(enigmoss.AuthenticationError) as caught:
        client.payments.get(PAYMENT_ID)
    assert caught.value.code == enigmoss.ErrorCode.TIMESTAMP_EXPIRED
    assert len(transport.to(FALLBACK_URL)) == 2


def test_a_call_for_the_platform_brand_gets_the_answer_of_the_fallback_host() -> None:
    client, transport, _, changes = scenario([unreachable()], [brand_answer()])
    raw = client.request("GET", "/project/platform-brand", key="any")
    assert raw.data == brand_data()
    assert (raw.status, raw.request_id) == (200, "req-brand")
    assert transport.to(NEW_ORIGIN) == []  # no second request
    assert client.base_url == NEW_ORIGIN
    assert len(changes) == 1


def test_a_call_for_the_platform_brand_gets_the_answer_even_when_the_origin_is_the_same() -> None:
    client, transport, _, changes = scenario([unreachable()], [brand_answer(BASE_URL)])
    raw = client.request("GET", "/project/platform-brand", key="any")
    assert raw.data == brand_data(BASE_URL)
    assert (client.base_url, changes) == (BASE_URL, [])
    with pytest.raises(TransportError):  # nothing moved, so the fallback is paused
        client.payments.get(PAYMENT_ID)
    assert len(transport.to(FALLBACK_URL)) == 1


def test_a_platform_brand_call_with_a_query_is_repeated_on_the_new_origin() -> None:
    client, transport, _, _ = scenario([unreachable()], [brand_answer()], [ok({"brand": None})])
    raw = client.request("GET", "/project/platform-brand", query={"lang": "en"}, key="any")
    assert raw.data == {"brand": None}
    assert transport.to(NEW_ORIGIN)[0].url == NEW_ORIGIN + PROBE_URI + "?lang=en"


def test_a_loopback_http_origin_needs_allow_insecure_http() -> None:
    local = "http://localhost:8080"
    client, _, _, _ = scenario(
        [unreachable()],
        [brand_answer(local)],
        base_url="http://127.0.0.1:8000",
        fallback_base_url="http://127.0.0.1:8001",
        allow_insecure_http=True,
    )
    transport = client._requester.transport
    assert isinstance(transport, HostsTransport)
    transport.routes = {
        "http://127.0.0.1:8000": [unreachable()],
        "http://127.0.0.1:8001": [brand_answer(local)],
        local: [ok(response("payment"))],
    }
    client.payments.get(PAYMENT_ID)
    assert client.base_url == local

    # Even then only loopback hosts may be http.
    client, _, _, _ = scenario(
        [unreachable()], [brand_answer("http://api2.example.com")], allow_insecure_http=True
    )
    with pytest.raises(enigmoss.UnexpectedResponseError):
        client.payments.get(PAYMENT_ID)


def test_a_failing_callback_does_not_break_the_call() -> None:
    def explode(change: OriginChange) -> None:
        raise RuntimeError("logging is down")

    client, _, _, _ = scenario(
        [unreachable()], [brand_answer()], [ok(response("payment"))], on_origin_change=explode
    )
    assert client.payments.get(PAYMENT_ID).id == PAYMENT_ID
    assert client.base_url == NEW_ORIGIN


def run_in_threads(count: int, call: Callable[[], object]) -> list[object]:
    results: list[object] = [None] * count
    barrier = threading.Barrier(count)

    def work(index: int) -> None:
        barrier.wait()
        try:
            results[index] = call()
        except BaseException as exc:
            results[index] = exc

    threads = [threading.Thread(target=work, args=(i,)) for i in range(count)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)
    return results


def slow(
    reply: Reply, transport_of: list[HostsTransport], attempts: int
) -> Callable[[HttpRequest], HttpResponse]:
    """Answers once every caller has used up its attempts on the API host, and a moment later."""

    def answer(request: HttpRequest) -> HttpResponse:
        limit = time.monotonic() + 10
        while len(transport_of[0].to(BASE_URL)) < attempts and time.monotonic() < limit:
            time.sleep(0.005)
        time.sleep(0.2)
        if isinstance(reply, TransportError):
            raise reply
        return reply if isinstance(reply, HttpResponse) else reply(request)

    return answer


def test_concurrent_calls_share_one_probe() -> None:
    callers = 8
    holder: list[HostsTransport] = []
    client, transport, _, changes = scenario(
        [unreachable()], [slow(brand_answer(), holder, callers * 3)], [ok(response("payment"))]
    )
    holder.append(transport)
    client._requester.now = time.time  # the waiters need a real clock

    results = run_in_threads(callers, lambda: client.payments.get(PAYMENT_ID))

    assert [getattr(r, "id", r) for r in results] == [PAYMENT_ID] * callers
    assert len(transport.to(FALLBACK_URL)) == 1
    assert len(transport.to(NEW_ORIGIN)) == callers
    assert len(changes) == 1


def test_concurrent_calls_share_a_failed_probe_each_with_its_own_cause() -> None:
    callers = 6
    holder: list[HostsTransport] = []
    answer = api_error(503, "dependency.unavailable", "dependency_error", retryable=True)
    client, transport, _, _ = scenario([unreachable()], [slow(answer, holder, callers * 3)])
    holder.append(transport)
    client._requester.now = time.time

    results = run_in_threads(callers, lambda: client.payments.get(PAYMENT_ID))

    assert len(transport.to(FALLBACK_URL)) == 1
    shared = [r for r in results if isinstance(r, enigmoss.ServerError)]
    # A caller that arrives after the probe ended meets the pause and raises its network error.
    late = [r for r in results if isinstance(r, TransportError)]
    assert len(shared) + len(late) == callers
    assert len(shared) >= 1
    assert len({id(r) for r in shared}) == len(shared)  # everyone raises an exception of its own
    assert all(isinstance(r.__cause__, TransportError) and r.outcome_unknown is False for r in shared)


def test_a_call_that_failed_while_another_one_moved_the_client_does_not_probe() -> None:
    client, transport, _, _ = scenario([unreachable()], [brand_answer()], [ok(response("payment"))])
    requester = client._requester
    primary = transport.routes[BASE_URL]

    def moves_first(request: HttpRequest) -> HttpResponse:
        # While this call is failing, another one completes the whole fallback.
        transport.routes[BASE_URL] = [unreachable()]
        other = make_client(transport, fallback_base_url=FALLBACK_URL)
        other._requester.origins = requester.origins
        other._requester.now = requester.now
        other.payments.get(PAYMENT_ID)
        raise unreachable()

    primary[:] = [moves_first]
    client.payments.get(PAYMENT_ID, options=enigmoss.RequestOptions(max_retries=0))
    assert len(transport.to(FALLBACK_URL)) == 1
    assert client.base_url == NEW_ORIGIN


def test_configuration() -> None:
    client = make_client(fallback_base_url="https://API.example.org/api/v4/")
    assert client._config.fallback_origin == FALLBACK_URL
    assert make_client()._config.fallback_origin is None
    for bad in (
        "",
        "api.example.org",
        "http://api.example.org",
        "https://api.example.org/v1",
        "https://u:p@x.example.org",
    ):
        with pytest.raises(enigmoss.ConfigurationError, match="fallback_base_url"):
            make_client(fallback_base_url=bad)
    insecure = make_client(fallback_base_url="http://localhost:8001", allow_insecure_http=True)
    assert insecure._config.fallback_origin == "http://localhost:8001"
    with pytest.raises(enigmoss.ConfigurationError, match="on_origin_change"):
        make_client(on_origin_change="log")


def test_from_env_reads_the_fallback_host(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ENIGMOSS_BASE_URL", BASE_URL)
    monkeypatch.setenv("ENIGMOSS_PROJECT_ID", PROJECT_ID)
    monkeypatch.setenv("ENIGMOSS_PAYMENT_KEY", PAYMENT_KEY)
    monkeypatch.delenv("ENIGMOSS_PAYOUT_KEY", raising=False)
    monkeypatch.delenv("ENIGMOSS_FALLBACK_BASE_URL", raising=False)
    assert enigmoss.Enigmoss.from_env()._config.fallback_origin is None
    monkeypatch.setenv("ENIGMOSS_FALLBACK_BASE_URL", " ")
    assert enigmoss.Enigmoss.from_env()._config.fallback_origin is None
    monkeypatch.setenv("ENIGMOSS_FALLBACK_BASE_URL", FALLBACK_URL)
    assert enigmoss.Enigmoss.from_env()._config.fallback_origin == FALLBACK_URL


@pytest.mark.parametrize(
    ("value", "normalized"),
    [
        ("https://api2.example.com", "https://api2.example.com"),
        ("HTTPS://API2.Example.COM:443", "https://api2.example.com"),
        ("https://a-b.example.co", "https://a-b.example.co"),
        ("https://xn--80aswg.xn--p1ai", "https://xn--80aswg.xn--p1ai"),
        ("https://a.b.c.d.example.org", "https://a.b.c.d.example.org"),
    ],
)
def test_an_acceptable_origin_is_normalized(value: str, normalized: str) -> None:
    assert validate_origin(value, allow_insecure_http=False) == normalized
    assert validate_origin(value, allow_insecure_http=True) == normalized


@pytest.mark.parametrize(
    "value",
    [
        None,
        443,
        ["https://api2.example.com"],
        "",
        "api2.example.com",
        "//api2.example.com",
        "http://api2.example.com",
        "ftp://api2.example.com",
        "https://api2.example.com/",
        "https://api2.example.com/api/v4",
        "https://api2.example.com?x=1",
        "https://api2.example.com#top",
        "https://key@api2.example.com",
        "https://key:secret@api2.example.com",
        "https://api2.example.com:8443",
        "https://api2.example.com:",
        "https://api2.example.com:443/",
        "https://api2.example.com.",
        "https://203.0.113.7",
        "https://203.0.113.7:443",
        "https://0x7f.0.0.1",
        "https://2130706433",
        "https://[::1]",
        "https://[2001:db8::1]:443",
        "https://localhost",
        "https://example",
        "https://printer.local",
        "https://app.localhost",
        "https://-api.example.com",
        "https://api-.example.com",
        "https://api..example.com",
        "https://api_2.example.com",
        " https://api2.example.com",
        "https://api2.example.com ",
        "https://api2.example.com\n",
        "https://api2 .example.com",
        "https://api2.example.com\\@other.example.org",
        "https://api2.example.\u212aom",  # KELVIN SIGN: "k" for a careless case-insensitive match
        "https://api2.example.co\u017f",  # LATIN SMALL LETTER LONG S: "s" for the same
        "https://\u0430pi.example.com",  # CYRILLIC SMALL LETTER A
        "https://" + "a" * 64 + ".example.com",
        "https://" + ".".join(["a" * 60] * 5) + ".com",  # a host longer than 253 characters
        "https://api2.example.com" + "/" * 2048,
    ],
)
def test_an_unacceptable_origin_is_rejected(value: object) -> None:
    assert validate_origin(value, allow_insecure_http=False) is None
    assert validate_origin(value, allow_insecure_http=True) is None


def test_loopback_http_origins() -> None:
    for value, normalized in (
        ("http://localhost", "http://localhost"),
        ("http://LOCALHOST:8080", "http://localhost:8080"),
        ("http://127.0.0.1:80", "http://127.0.0.1"),
        ("http://[::1]:8000", "http://[::1]:8000"),
    ):
        assert validate_origin(value, allow_insecure_http=True) == normalized
        assert validate_origin(value, allow_insecure_http=False) is None
    for value in ("http://127.0.0.2", "http://localhost/", "http://localhost:123456", "http://api.localhost"):
        assert validate_origin(value, allow_insecure_http=True) is None


def test_origins_are_compared_without_the_default_port() -> None:
    assert comparable("https://API.example.com:443") == "https://api.example.com"
    assert comparable("https://api.example.com:8443") == "https://api.example.com:8443"
    assert comparable("http://localhost:80") == "http://localhost"
    assert comparable("https://localhost:80") == "https://localhost:80"


def test_host_unreachable_needs_a_request_that_was_not_sent() -> None:
    assert TransportError("x", phase=SendPhase.NOT_SENT, host_unreachable=True).host_unreachable is True
    assert TransportError("x", phase=SendPhase.NOT_SENT).host_unreachable is False
    assert TransportError("x", phase=SendPhase.SENT, host_unreachable=True).host_unreachable is False
    assert TransportError("x", host_unreachable=True).host_unreachable is False
