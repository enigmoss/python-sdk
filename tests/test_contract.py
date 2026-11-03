"""The shared contract vectors: signatures, idempotency keys and exact body bytes.

The server compares a repeated request with the same Idempotency-Key byte for byte, so these
bytes must match the other official SDKs exactly.
"""

from __future__ import annotations

import hashlib
import re
from datetime import datetime
from typing import Any

import pytest

import enigmoss
from enigmoss import _signing, webhooks
from tests.support import FakeTransport, contract, make_client, ok, response

SIGNATURES = contract("signature-vectors.json")
KEYS = contract("idempotency-keys.json")
BODIES = contract("request-bodies.json")

UUID4 = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}")

RESPONSES = {
    "payments.create": "payment",
    "payments.cancel": "payment",
    "payments.mark_paid": "paid_payment",
    "wallets.create": "wallet",
    "wallets.block": "wallet",
    "payouts.create": "payout",
    "payouts.quote": "payout_quote",
    "refunds.create_for_payment": "refund",
    "refunds.create_for_wallet": "refund",
    "transfers.create": "transfer",
    "payment_methods.update_discount": "payment_method",
    "events.redeliver_bulk": "bulk_redelivery_result",
    "webhooks.send_test": "event_record",
}


def call(client: enigmoss.Enigmoss, operation: str, path: dict[str, str], params: dict[str, Any]) -> object:
    p = dict(params)
    for name in ("allowed_methods", "excluded_methods"):
        if name in p:
            p[name] = [enigmoss.PaymentMethodRef(**ref) for ref in p[name]]
    for name in ("created_from", "created_to"):
        if name in p:
            p[name] = datetime.fromisoformat(p[name])
    match operation:
        case "payments.create":
            return client.payments.create(**p)
        case "payments.cancel":
            return client.payments.cancel(path["id"])
        case "payments.mark_paid":
            return client.payments.mark_paid(path["id"])
        case "wallets.create":
            return client.wallets.create(**p)
        case "wallets.block":
            return client.wallets.block(path["id"], **p)
        case "payouts.create":
            return client.payouts.create(**p)
        case "payouts.quote":
            return client.payouts.quote(**p)
        case "refunds.create_for_payment":
            return client.refunds.create_for_payment(path["id"], **p)
        case "refunds.create_for_wallet":
            return client.refunds.create_for_wallet(path["id"], **p)
        case "transfers.create":
            return client.transfers.create(**p)
        case "payment_methods.update_discount":
            return client.payment_methods.update_discount(path["currency_code"], path["network_code"], **p)
        case "events.redeliver_bulk":
            return client.events.redeliver_bulk(**p)
        case "webhooks.send_test":
            return client.webhooks.send_test(**p)
    raise AssertionError(f"unknown operation {operation}")


@pytest.mark.parametrize("vector", SIGNATURES["request"]["vectors"], ids=lambda v: v["name"])
def test_request_signature_vectors(vector: dict[str, Any]) -> None:
    request = SIGNATURES["request"]
    secret = request["secrets"][vector["key_type"]]
    body = vector["body"].encode()
    assert len(body) == vector["body_bytes"]
    signature = _signing.sign_request(
        secret, int(request["timestamp"]), request["project"], vector["method"], vector["path"], body
    )
    assert signature == vector["signature"]


@pytest.mark.parametrize("vector", SIGNATURES["webhook"]["vectors"], ids=lambda v: v["name"])
def test_webhook_signature_vectors(vector: dict[str, Any], monkeypatch: pytest.MonkeyPatch) -> None:
    body = vector["body"].encode()
    assert (
        _signing.sign_webhook(vector["secret"], vector["timestamp"], vector["project"], body)
        == vector["signature"]
    )
    monkeypatch.setattr(webhooks, "_now", lambda: float(vector["timestamp"]))
    headers = {
        "X-Project-Uuid": vector["project"],
        "X-Timestamp": vector["timestamp"],
        "X-Signature": vector["signature"],
    }
    event = webhooks.construct_event(
        body, headers, project_id=vector["project"], payment_key=vector["secret"]
    )
    assert event.type == "webhook.test"


def test_sdk_sends_the_signature_of_the_vector() -> None:
    request = SIGNATURES["request"]
    vector = next(v for v in request["vectors"] if v["name"] == "post-with-body-and-query")
    transport = FakeTransport(ok(response("payment")))
    client = make_client(transport, project_id=request["project"])
    client._requester.now = lambda: float(request["timestamp"])
    client.request(
        "POST",
        "/payments",
        body={"amount": "10.50", "currency_code": "USDT", "order_id": "order-1"},
        query={"expand": "payer", "limit": 10},
    )
    sent = transport.last
    assert sent.url == "https://api.example.com" + vector["path"]
    assert sent.body == vector["body"].encode()
    assert sent.headers["X-Timestamp"] == request["timestamp"]
    assert sent.headers["X-Signature"] == vector["signature"]


@pytest.mark.parametrize("vector", KEYS["vectors"], ids=lambda v: v["name"])
def test_idempotency_key_vectors(vector: dict[str, Any]) -> None:
    assert (
        _signing.derived_idempotency_key(vector["request_uri"], vector["order_id"])
        == vector["idempotency_key"]
    )

    transport = FakeTransport(ok(response(RESPONSES[vector["operation"]])))
    client = make_client(transport)
    params: dict[str, Any] = {
        "payments.create": {"amount": "1", "currency_code": "USDT"},
        "wallets.create": {"currency_code": "USDT", "network_code": "tron"},
        "payouts.create": {
            "amount": "1",
            "currency_code": "USDT",
            "address": "TRecipientabcdefghijklmnopqrstuvwx",
        },
        "transfers.create": {"amount": "1", "currency_code": "USDT"},
        "refunds.create_for_payment": {"address": "TCustomerabcdefghijklmnopqrstuvwxy"},
        "refunds.create_for_wallet": {"address": "TCustomerabcdefghijklmnopqrstuvwxy"},
    }[vector["operation"]]
    call(client, vector["operation"], vector["path_params"], {**params, "order_id": vector["order_id"]})
    assert transport.request_uri() == vector["request_uri"]
    assert transport.last.headers["Idempotency-Key"] == vector["idempotency_key"]


@pytest.mark.parametrize("vector", BODIES["vectors"], ids=lambda v: v["name"])
def test_request_body_vectors(vector: dict[str, Any]) -> None:
    transport = FakeTransport(ok(response(RESPONSES[vector["operation"]])))
    client = make_client(transport)
    call(client, vector["operation"], vector["path_params"], vector["params"])

    sent = transport.last
    expected = vector["body"].encode()
    assert sent.method == vector["method"]
    assert transport.request_uri() == vector["request_uri"]
    assert sent.body == expected
    assert len(expected) == vector["body_bytes"]
    assert hashlib.sha256(expected).hexdigest() == vector["body_sha256"]
    assert sent.timeout == vector["timeout"]

    key = sent.headers.get("Idempotency-Key")
    if vector["idempotency_key"] is None:
        assert key is None
    elif vector["idempotency_key"] == "random":
        assert key is not None and UUID4.fullmatch(key)
    else:
        assert key == vector["idempotency_key"]


def test_contract_snapshot_matches_the_shared_directory() -> None:
    """When the SDK lives next to the shared contract directory, the copy must be in sync."""
    from pathlib import Path

    shared = Path(__file__).resolve().parents[2] / "contract"
    if not shared.is_dir():
        pytest.skip("the shared contract directory is not checked out next to the SDK")
    local = Path(__file__).parent / "fixtures" / "contract"
    for path in sorted(shared.glob("*.json")):
        assert (local / path.name).read_bytes() == path.read_bytes(), (
            f"run scripts/sync_contract.py ({path.name})"
        )
