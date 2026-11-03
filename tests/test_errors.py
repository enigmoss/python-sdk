"""Error mapping: class, outcome_unknown, conflicts and idempotency errors."""

from __future__ import annotations

import pytest

import enigmoss
from enigmoss import (
    APIError,
    AuthenticationError,
    ConflictError,
    IdempotencyError,
    NotFoundError,
    PermissionDeniedError,
    RateLimitError,
    ServerError,
    UnexpectedResponseError,
)
from tests.support import FakeTransport, api_error, make_client, ok

PAYMENT_ID = "7c9e6679-7425-40de-944b-e07fc1f90ae7"


def fail(reply: enigmoss.HttpResponse) -> enigmoss.EnigmossError:
    client = make_client(FakeTransport(reply), max_retries=0)
    with pytest.raises(enigmoss.EnigmossError) as caught:
        client.payments.create(amount="1", currency_code="USDT", order_id="order-1")
    return caught.value


@pytest.mark.parametrize(
    ("status", "code", "type_", "cls", "outcome_unknown"),
    [
        (401, "auth.signature_invalid", "authentication_error", AuthenticationError, False),
        (403, "api_permission.denied", "authorization_error", PermissionDeniedError, False),
        (403, "auth.ip_forbidden", "authorization_error", PermissionDeniedError, False),
        (404, "resource.not_found", "not_found", NotFoundError, False),
        (409, "resource.conflict", "conflict", ConflictError, False),
        (409, "idempotency.payload_mismatch", "conflict", IdempotencyError, False),
        (409, "idempotency.in_progress", "conflict", IdempotencyError, True),
        (422, "validation.failed", "validation_error", APIError, False),
        (422, "domain.rule_violation", "business_error", APIError, False),
        (429, "rate_limit.exceeded", "rate_limit_error", RateLimitError, False),
        (500, "internal.unexpected", "internal_error", ServerError, True),
        (502, "dependency.gateway", "dependency_error", ServerError, True),
        (418, "teapot.new_code", "brand_new_type", APIError, False),
        (409, "idempotency.something_new", "brand_new_type", IdempotencyError, False),
    ],
)
def test_error_class_and_outcome(
    status: int, code: str, type_: str, cls: type[APIError], outcome_unknown: bool
) -> None:
    error = fail(api_error(status, code, type_, message="Server says no."))
    assert type(error) is cls
    assert isinstance(error, APIError)
    assert error.status == status
    assert error.code == code
    assert error.outcome_unknown is outcome_unknown
    assert error.request_id == "req-err"
    assert error.idempotency_key is not None and error.idempotency_key.startswith("sdk1-")
    assert str(error) == f"Server says no. [{code}, request req-err]"


def test_idempotency_error_is_not_a_conflict_error() -> None:
    error = fail(api_error(409, "idempotency.in_progress", "conflict"))
    assert not isinstance(error, ConflictError)
    assert not issubclass(IdempotencyError, ConflictError)
    assert "find_by_order_id()" in " ".join(error.__notes__)


def test_order_id_taken() -> None:
    error = fail(api_error(409, "resource.conflict", "conflict", details={"id": PAYMENT_ID}))
    assert isinstance(error, ConflictError)
    assert error.is_order_id_taken is True
    assert error.existing_resource_id == PAYMENT_ID


def test_state_conflict_is_not_order_id_taken() -> None:
    error = fail(
        api_error(409, "resource.conflict", "conflict", details={"id": PAYMENT_ID, "status": "paid"})
    )
    assert isinstance(error, ConflictError)
    assert error.is_order_id_taken is False


def test_violations_and_permission_hint() -> None:
    error = fail(
        api_error(
            422,
            "validation.failed",
            "validation_error",
            violations=[
                {"field": "amount", "rule": "max", "message": "Too big.", "params": {"max": "100"}},
                "junk",
            ],
        )
    )
    assert isinstance(error, APIError)
    assert error.violations_for("amount")[0].params == {"max": "100"}
    assert len(error.violations) == 1

    denied = fail(
        api_error(
            403,
            "api_permission.denied",
            "authorization_error",
            details={"permission": "payment_create_invoice"},
        )
    )
    assert 'Enable the "payment_create_invoice" API permission in the dashboard.' in denied.__notes__


def test_server_time_of_an_expired_timestamp() -> None:
    error = fail(api_error(401, "auth.timestamp_expired", "authentication_error", details={"server_time": 5}))
    assert isinstance(error, AuthenticationError)
    assert error.server_time == 5


def test_response_without_envelope() -> None:
    proxy = fail(enigmoss.HttpResponse(503, {}, b"<html>busy</html>"))
    assert isinstance(proxy, ServerError)
    assert proxy.code == "http.503"
    assert proxy.outcome_unknown is True

    missing = fail(enigmoss.HttpResponse(404, {"x-request-id": "r-9"}, b"Not Found"))
    assert isinstance(missing, UnexpectedResponseError)
    assert missing.outcome_unknown is False
    assert missing.request_id == "r-9"
    assert "base_url" in str(missing)


def test_redirect_is_unexpected() -> None:
    error = fail(enigmoss.HttpResponse(301, {"location": "https://elsewhere.example.com/"}, b""))
    assert isinstance(error, UnexpectedResponseError)
    assert error.outcome_unknown is False


def test_unreadable_success_has_unknown_outcome() -> None:
    for body in (b"not json", b'{"no_data": 1}', b'{"data": {"id": 42}}'):
        error = fail(enigmoss.HttpResponse(201, {}, body))
        assert isinstance(error, UnexpectedResponseError)
        assert error.outcome_unknown is True, body
        assert error.status == 201 or body == b'{"data": {"id": 42}}'


def test_wrong_field_type_names_the_path() -> None:
    client = make_client(FakeTransport(ok({"id": PAYMENT_ID, "payer": "nope"})))
    with pytest.raises(UnexpectedResponseError, match=r'"data\.status"'):
        client.payments.get(PAYMENT_ID)


def test_missing_key_is_a_configuration_error_before_sending() -> None:
    transport = FakeTransport(ok({}))
    client = make_client(transport, payout_key=None)
    with pytest.raises(enigmoss.ConfigurationError, match="payout_key"):
        client.payouts.get(PAYMENT_ID)
    assert transport.requests == []


def test_invalid_arguments_are_value_errors_before_sending() -> None:
    transport = FakeTransport(ok({}))
    client = make_client(transport)
    with pytest.raises(ValueError, match="never a float"):
        client.payments.create(amount=10.5, currency_code="USDT", order_id="order-1")  # type: ignore[arg-type]
    with pytest.raises(enigmoss.InvalidArgumentError, match="order_id"):
        client.payments.create(amount="1", currency_code="USDT", order_id="order 1")
    with pytest.raises(enigmoss.InvalidArgumentError, match="UUID"):
        client.payments.get("42")
    with pytest.raises(enigmoss.InvalidArgumentError, match="lone surrogate"):
        client.payments.create(amount="1", currency_code="USDT", order_id="order-1", description="\ud800")
    with pytest.raises(enigmoss.InvalidArgumentError, match="NaN"):
        client.payments.create(
            amount="1", currency_code="USDT", order_id="order-1", metadata={"x": float("nan")}
        )
    with pytest.raises(enigmoss.InvalidArgumentError, match="override"):
        client.payments.create(
            amount="1",
            currency_code="USDT",
            order_id="order-1",
            options=enigmoss.RequestOptions(extra_body={"amount": "2"}),
        )
    assert transport.requests == []
