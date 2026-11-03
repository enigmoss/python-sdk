"""The retry table: what is retried, what never is, and how to turn retries off."""

from __future__ import annotations

from typing import Any

import pytest

import enigmoss
from enigmoss import SendPhase, TransportError
from tests.support import FakeTransport, api_error, delays, make_client, ok, response

PAYOUT: dict[str, Any] = {
    "order_id": "withdrawal-1",
    "amount": "100.00",
    "currency_code": "USDT",
    "address": "TRecipientabcdefghijklmnopqrstuvwx",
}


def sent() -> TransportError:
    return TransportError("connection reset", phase=SendPhase.SENT)


def not_sent() -> TransportError:
    return TransportError("connection refused", phase=SendPhase.NOT_SENT)


def test_read_is_retried_after_network_failure_and_gateway_errors() -> None:
    gateway = enigmoss.HttpResponse(502, {}, b"<html>Bad gateway</html>")
    transport = FakeTransport(sent(), gateway, ok(response("payment")))
    client = make_client(transport)
    client.payments.get("7c9e6679-7425-40de-944b-e07fc1f90ae7")
    assert len(transport.requests) == 3
    assert delays(client) == [0.25, 0.5]  # full jitter at 0.5 of 0.5 s and 1 s


def test_business_error_on_a_money_create_is_never_retried() -> None:
    failure = api_error(502, "dependency.gateway", "dependency_error", retryable=True)
    transport = FakeTransport(failure, ok(response("payout")))
    client = make_client(transport)
    with pytest.raises(enigmoss.ServerError) as caught:
        client.payouts.create(**PAYOUT)
    assert len(transport.requests) == 1
    assert caught.value.outcome_unknown is True


def test_safe_codes_retry_a_money_create_with_the_same_key_and_body() -> None:
    transport = FakeTransport(
        api_error(409, "lock.not_acquired", "conflict"),
        api_error(429, "rate_limit.exceeded", "rate_limit_error", retry_after=2),
        ok(response("payout")),
    )
    client = make_client(transport)
    client.payouts.create(**PAYOUT)
    assert len(transport.requests) == 3
    assert len({r.headers["Idempotency-Key"] for r in transport.requests}) == 1
    assert len({r.body for r in transport.requests}) == 1
    assert delays(client) == [0.25, 2.0]


def test_transport_failure_after_sending_retries_a_keyed_money_create() -> None:
    transport = FakeTransport(sent(), ok(response("payout")))
    client = make_client(transport)
    client.payouts.create(**PAYOUT)
    assert len(transport.requests) == 2


def test_max_retries_zero_disables_retries() -> None:
    transport = FakeTransport(not_sent(), ok(response("payment")))
    client = make_client(transport, max_retries=0)
    with pytest.raises(TransportError):
        client.payments.get("7c9e6679-7425-40de-944b-e07fc1f90ae7")
    assert len(transport.requests) == 1


def test_per_call_max_retries_zero() -> None:
    transport = FakeTransport(not_sent(), ok(response("payment")))
    client = make_client(transport)
    with pytest.raises(TransportError):
        client.payments.get(
            "7c9e6679-7425-40de-944b-e07fc1f90ae7", options=enigmoss.RequestOptions(max_retries=0)
        )
    assert len(transport.requests) == 1


def test_idempotency_none_sends_no_key_and_never_retries_after_sending() -> None:
    transport = FakeTransport(sent(), ok(response("payment")))
    client = make_client(transport, idempotency=None)
    with pytest.raises(TransportError) as caught:
        client.payments.create(amount="1", currency_code="USDT", order_id="order-1")
    assert len(transport.requests) == 1
    assert "Idempotency-Key" not in transport.last.headers
    assert caught.value.outcome_unknown is True

    transport.replies = [api_error(429, "rate_limit.exceeded", "rate_limit_error"), ok(response("payment"))]
    with pytest.raises(enigmoss.RateLimitError):
        client.payments.create(amount="1", currency_code="USDT", order_id="order-1")


def test_idempotency_none_still_retries_what_was_not_sent() -> None:
    transport = FakeTransport(not_sent(), ok(response("payment")))
    client = make_client(transport, idempotency=None)
    client.payments.create(amount="1", currency_code="USDT", order_id="order-1")
    assert len(transport.requests) == 2


def test_idempotency_none_keeps_the_key_where_the_server_requires_it() -> None:
    transport = FakeTransport(ok(response("payout")))
    client = make_client(transport, idempotency=None)
    client.payouts.create(**PAYOUT)
    assert transport.last.headers["Idempotency-Key"].startswith("sdk1-")


def test_random_idempotency_uses_a_new_key_per_call_reused_by_retries() -> None:
    transport = FakeTransport(not_sent(), ok(response("payment")))
    client = make_client(transport, idempotency="random")
    client.payments.create(amount="1", currency_code="USDT", order_id="order-1")
    first, retry = transport.requests
    assert first.headers["Idempotency-Key"] == retry.headers["Idempotency-Key"]
    assert not first.headers["Idempotency-Key"].startswith("sdk1-")
    transport.replies = [ok(response("payment"))]
    client.payments.create(amount="1", currency_code="USDT", order_id="order-1")
    assert transport.last.headers["Idempotency-Key"] != first.headers["Idempotency-Key"]


def test_explicit_key_wins() -> None:
    transport = FakeTransport(ok(response("payment")))
    client = make_client(transport)
    client.payments.create(
        amount="1",
        currency_code="USDT",
        order_id="order-1",
        options=enigmoss.RequestOptions(idempotency_key="my-key-1"),
    )
    assert transport.last.headers["Idempotency-Key"] == "my-key-1"


def test_non_idempotent_calls_are_retried_only_when_not_sent() -> None:
    transport = FakeTransport(not_sent(), sent(), ok(response("event_record")))
    client = make_client(transport)
    with pytest.raises(TransportError):
        client.events.redeliver("019b7710-0000-7000-8000-000000000001")
    assert len(transport.requests) == 2
    assert "Idempotency-Key" not in transport.last.headers

    transport.replies = [api_error(503, "dependency.unavailable", "dependency_error", retryable=True), ok({})]
    with pytest.raises(enigmoss.ServerError):
        client.webhooks.send_test()


def test_long_retry_after_is_raised_at_once() -> None:
    transport = FakeTransport(
        api_error(429, "rate_limit.exceeded", "rate_limit_error", retry_after=30), ok({})
    )
    client = make_client(transport)
    with pytest.raises(enigmoss.RateLimitError) as caught:
        client.payments.get("7c9e6679-7425-40de-944b-e07fc1f90ae7")
    assert caught.value.retry_after == 30
    assert len(transport.requests) == 1
    assert "Retry in 30 s." in caught.value.__notes__


def test_retry_after_header_without_envelope() -> None:
    limited = enigmoss.HttpResponse(429, {"retry-after": "3"}, b"Too Many Requests")
    transport = FakeTransport(limited, ok(response("payment")))
    client = make_client(transport)
    client.payments.get("7c9e6679-7425-40de-944b-e07fc1f90ae7")
    assert delays(client) == [3.0]


def test_timestamp_and_signature_are_fresh_on_every_attempt() -> None:
    transport = FakeTransport(not_sent(), ok(response("payment")))
    client = make_client(transport)
    times = iter([1000.0, 1000.0, 1000.0, 1005.0, 1005.0, 1005.0])
    client._requester.now = lambda: next(times)
    client.payments.create(amount="1", currency_code="USDT", order_id="order-1")
    first, retry = transport.requests
    assert first.headers["X-Timestamp"] != retry.headers["X-Timestamp"]
    assert first.headers["X-Signature"] != retry.headers["X-Signature"]


def test_clock_skew_is_corrected_once_without_counting_as_a_retry() -> None:
    expired = api_error(401, "auth.timestamp_expired", "authentication_error", details={"server_time": 2000})
    transport = FakeTransport(expired, ok(response("payout")))
    client = make_client(transport, max_retries=0)
    client._requester.now = lambda: 1000.0
    client.payouts.create(**PAYOUT)
    assert [r.headers["X-Timestamp"] for r in transport.requests] == ["1000", "2000"]

    transport.replies = [expired]
    with pytest.raises(enigmoss.AuthenticationError) as caught:
        client.payouts.create(**PAYOUT)
    assert "Sync the server clock (NTP)." in caught.value.__notes__


def test_payout_and_refund_timeouts_are_at_least_180_seconds() -> None:
    transport = FakeTransport(ok(response("payout")))
    client = make_client(transport)
    client.payouts.create(**PAYOUT)
    payout_request = transport.last
    assert (payout_request.timeout, payout_request.keep_alive) == (180.0, False)
    transport.replies = [ok(response("payment"))]
    client.payments.get("7c9e6679-7425-40de-944b-e07fc1f90ae7")
    read_request = transport.last
    assert (read_request.timeout, read_request.keep_alive) == (30.0, True)
    transport.replies = [ok(response("refund"))]
    client.refunds.create_for_payment(
        "7c9e6679-7425-40de-944b-e07fc1f90ae7",
        order_id="refund-1",
        address="TCustomerabcdefghijklmnopqrstuvwxy",
        options=enigmoss.RequestOptions(timeout=200),
    )
    assert transport.last.timeout == 200
