"""Webhook verification: vector, forgery, window, rotation, str instead of bytes."""

from __future__ import annotations

import json
from decimal import Decimal

import pytest

import enigmoss
from enigmoss import WebhookFailureReason, WebhookVerificationError, webhooks
from enigmoss._signing import sign_webhook
from enigmoss.models import Payment, Payout, Refund
from tests.support import PAYMENT_KEY, PROJECT_ID, make_client, response

NOW = 1767225600


@pytest.fixture(autouse=True)
def frozen_clock(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(webhooks, "_now", lambda: float(NOW))


def signed(
    event: str, data: object, *, key: str = PAYMENT_KEY, timestamp: int = NOW
) -> tuple[bytes, dict[str, str]]:
    body = json.dumps(
        {
            "event": event,
            "event_id": "019b7710-0000-7000-8000-000000000001",
            "created_at": "2026-01-01T00:05:00Z",
            "data": data,
        }
    ).encode()
    headers = {
        "X-Project-Uuid": PROJECT_ID,
        "X-Timestamp": str(timestamp),
        "X-Signature": sign_webhook(key, str(timestamp), PROJECT_ID, body),
    }
    return body, headers


def construct(body: bytes, headers: object, **kwargs: object) -> enigmoss.WebhookEvent:
    options: dict[str, object] = {"project_id": PROJECT_ID, "payment_key": PAYMENT_KEY} | kwargs
    return webhooks.construct_event(body, headers, **options)  # type: ignore[arg-type]


def test_payment_event_through_the_client() -> None:
    body, headers = signed("payment.paid", response("paid_payment"))
    client = make_client()
    event = client.webhooks.construct_event(body, headers)
    assert event.id == "019b7710-0000-7000-8000-000000000001"
    assert event.type == "payment.paid"
    assert event.created_at is not None and event.created_at.isoformat() == "2026-01-01T00:05:00+00:00"
    payment = event.object
    assert isinstance(payment, Payment)
    assert payment.is_successful is True
    assert payment.paid.amount == Decimal("10.50")
    assert event.object is payment  # parsed once


@pytest.mark.parametrize(
    ("event_type", "fixture", "cls"),
    [("payout.completed", "payout", Payout), ("refund.completed", "refund", Refund)],
)
def test_payout_and_refund_events(event_type: str, fixture: str, cls: type) -> None:
    body, headers = signed(event_type, response(fixture))
    assert isinstance(construct(body, headers).object, cls)


def test_unknown_and_test_events_do_not_raise() -> None:
    body, headers = signed("something.new", {"weird": True})
    event = construct(body, headers)
    assert event.object is None
    assert event.data == {"weird": True}
    body, headers = signed("webhook.test", None)
    assert construct(body, headers).object is None


def test_headers_in_any_case_and_shape() -> None:
    body, headers = signed("webhook.test", None)
    lower = {k.lower(): v for k, v in headers.items()}
    assert construct(body, lower).type == "webhook.test"
    assert construct(body, list(headers.items())).type == "webhook.test"
    as_lists = {k: [v] for k, v in headers.items()}
    assert construct(body, as_lists).type == "webhook.test"
    raw = [(k.lower().encode(), v.encode()) for k, v in headers.items()]
    assert construct(body, raw).type == "webhook.test"


def test_forged_body_or_signature() -> None:
    body, headers = signed("payment.paid", response("paid_payment"))
    with pytest.raises(WebhookVerificationError) as caught:
        construct(body.replace(b"10.50", b"99.50"), headers)
    assert caught.value.reason is WebhookFailureReason.SIGNATURE_MISMATCH
    body, headers = signed("payment.paid", response("paid_payment"), key="another-key")
    with pytest.raises(WebhookVerificationError):
        construct(body, headers)


def test_re_encoded_json_does_not_verify() -> None:
    body, headers = signed("payment.paid", response("paid_payment"))
    reencoded = json.dumps(json.loads(body), separators=(",", ":")).encode()
    with pytest.raises(WebhookVerificationError):
        construct(reencoded, headers)


@pytest.mark.parametrize("offset", [-301, 301])
def test_timestamp_window(offset: int) -> None:
    body, headers = signed("webhook.test", None, timestamp=NOW + offset)
    with pytest.raises(WebhookVerificationError) as caught:
        construct(body, headers)
    assert caught.value.reason is WebhookFailureReason.TIMESTAMP_OUT_OF_TOLERANCE
    body, headers = signed("webhook.test", None, timestamp=NOW + offset)
    assert construct(body, headers, tolerance=400).type == "webhook.test"


def test_checks_run_in_order() -> None:
    body, headers = signed("webhook.test", None, key="another-key", timestamp=NOW - 1000)
    with pytest.raises(WebhookVerificationError) as caught:
        construct(body, {**headers, "X-Project-Uuid": "00000000-0000-4000-8000-000000000000"})
    assert caught.value.reason is WebhookFailureReason.PROJECT_MISMATCH
    with pytest.raises(WebhookVerificationError) as caught:
        construct(body, {"X-Timestamp": headers["X-Timestamp"]})
    assert caught.value.reason is WebhookFailureReason.MISSING_HEADER
    assert "X-Project-Uuid, X-Signature" in str(caught.value)


def test_key_rotation_accepts_old_and_new_keys() -> None:
    body, headers = signed("webhook.test", None, key="old-key")
    assert construct(body, headers, payment_key=["new-key", "old-key"]).type == "webhook.test"
    assert construct(body, headers, payment_key=[enigmoss.Secret("old-key")]).type == "webhook.test"


def test_str_instead_of_bytes_is_a_type_error() -> None:
    body, headers = signed("webhook.test", None)
    with pytest.raises(TypeError, match="raw request body"):
        construct(body.decode(), headers)  # type: ignore[arg-type]
    with pytest.raises(TypeError):
        construct(json.loads(body), headers)
    assert construct(bytearray(body), headers).type == "webhook.test"  # type: ignore[arg-type]


def test_valid_signature_over_a_non_object_body() -> None:
    body = b"[1, 2]"
    headers = {
        "X-Project-Uuid": PROJECT_ID,
        "X-Timestamp": str(NOW),
        "X-Signature": sign_webhook(PAYMENT_KEY, str(NOW), PROJECT_ID, body),
    }
    with pytest.raises(WebhookVerificationError) as caught:
        construct(body, headers)
    assert caught.value.reason is WebhookFailureReason.MALFORMED_PAYLOAD


def test_client_without_payment_key_cannot_verify() -> None:
    client = make_client(payment_key=None)
    body, headers = signed("webhook.test", None)
    with pytest.raises(enigmoss.ConfigurationError):
        client.webhooks.construct_event(body, headers)
