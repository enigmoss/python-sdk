"""Models: fixtures parse, unknown enum values, Decimal amounts, extra fields."""

from __future__ import annotations

import dataclasses
import json
import pickle
from datetime import UTC, datetime
from decimal import Decimal

import pytest

import enigmoss
from enigmoss.models import (
    DeliveryStatus,
    KeyType,
    Payment,
    PaymentStatus,
    PayoutPriority,
    RefundSource,
    TransactionType,
    WalletStatus,
)
from tests.support import FakeTransport, make_client, ok, response

PAYMENT_ID = "7c9e6679-7425-40de-944b-e07fc1f90ae7"


def client_returning(
    data: object, meta: dict[str, object] | None = None
) -> tuple[enigmoss.Enigmoss, FakeTransport]:
    transport = FakeTransport(ok(data, meta))
    return make_client(transport), transport


def test_payment_fixture() -> None:
    client, _ = client_returning(response("paid_payment"))
    payment = client.payments.get(PAYMENT_ID)
    assert payment.status is PaymentStatus.PAID
    assert payment.is_successful is True
    assert payment.amount == Decimal("10.50")
    assert str(payment.amount) == "10.50"
    assert payment.settlement.fee == Decimal("0.21")
    assert payment.paid_at == datetime(2026, 1, 1, 0, 5, tzinfo=UTC)
    assert payment.metadata == {}  # the API sends [] for empty metadata
    assert payment.raw["tx_hash"] == "0x7f3a9c"
    assert "raw" not in repr(payment)
    with pytest.raises(dataclasses.FrozenInstanceError):
        payment.status = PaymentStatus.FAILED  # type: ignore[misc]


def test_paid_but_not_final_is_not_successful() -> None:
    client, _ = client_returning(response("paid_payment") | {"is_final": False})
    assert client.payments.get(PAYMENT_ID).is_successful is False


def test_unknown_enum_values_and_extra_fields() -> None:
    data = response("payment") | {"status": "brand_new_status", "new_field": {"x": 1}}
    client, _ = client_returning(data)
    payment = client.payments.get(PAYMENT_ID)
    assert payment.status is PaymentStatus.UNKNOWN
    assert payment.is_successful is False
    assert payment.raw["new_field"] == {"x": 1}


def test_amounts_are_decimals_and_json_numbers_are_refused() -> None:
    client, _ = client_returning(response("payment") | {"amount": 10.5})
    with pytest.raises(enigmoss.UnexpectedResponseError, match=r"data\.amount"):
        client.payments.get(PAYMENT_ID)


def test_every_resource_fixture_parses() -> None:
    client, transport = client_returning(None)

    def reply(data: object, meta: dict[str, object] | None = None) -> None:
        transport.replies = [ok(data, meta)]

    reply(response("project"))
    project = client.project.get()
    assert project.key_type is KeyType.PAYMENT and project.permissions[0] == "payment_create_invoice"

    reply(response("wallet"))
    assert client.wallets.get("0f8fad5b-d9cb-469f-a165-70867728950e").status is WalletStatus.ACTIVE

    reply(response("payout"))
    payout = client.payouts.get("9b2d4f6a-1c3e-4a5b-8d7f-0e1a2b3c4d5e")
    assert payout.priority is PayoutPriority.RECOMMENDED and payout.charged.fee == Decimal("1.00")

    reply(response("payout_quote"))
    quote = client.payouts.quote(
        amount="100.00", currency_code="USDT", address="TRecipientabcdefghijklmnopqrstuvwx"
    )
    assert quote.recipient.amount == Decimal("99.00")

    reply(response("refund"))
    assert client.refunds.get("01970000-0000-7000-8000-000000000001").source is RefundSource.PAYMENT

    reply(response("transfer"))
    assert client.transfers.get("01970000-0000-7000-8000-000000000002").amount == Decimal("25.00")

    reply([response("balance")])
    assert client.balances.list()[0].balance == Decimal("490.50")

    reply([response("transaction")], {"cursor": {"next": None, "has_more": False}})
    assert client.transactions.list().items[0].type is TransactionType.PAYMENT

    reply([response("payment_method")])
    assert client.payment_methods.list()[0].discount_percent == -5

    reply([response("payout_method")])
    assert client.payout_methods.list()[0].fee.percent == Decimal("0.5")

    reply(response("payout_method_details"))
    details = client.payout_methods.get("USDT", "tron")
    assert details.priorities[1].priority is PayoutPriority.HIGH

    reply([response("exchange_rate")])
    rate = client.exchange_rates.list(from_="BTC")[0]
    assert rate.from_ == "BTC" and rate.rate == Decimal("65000.12")
    assert transport.request_uri() == "/api/v4/exchange-rates?from=BTC"

    reply([response("aml_verification_link")])
    assert client.payments.create_aml_verification_links(PAYMENT_ID)[0].url is not None

    reply(response("event_record_with_attempts"))
    record = client.events.get("019b7710-0000-7000-8000-000000000001")
    assert record.delivery_status is DeliveryStatus.DELIVERED
    assert record.attempts[0].response_status_code == 200
    assert isinstance(record.payload.object, Payment)

    reply(response("bulk_redelivery_result"))
    assert client.events.redeliver_bulk(created_from=datetime(2026, 1, 1, tzinfo=UTC)).queued == 3


def test_pagination_list_iterate_and_find() -> None:
    first = ok([response("payment")], {"cursor": {"next": "c2", "has_more": True}})
    second = ok([response("paid_payment")], {"cursor": {"next": None, "has_more": False}})
    transport = FakeTransport(first, second)
    client = make_client(transport)
    page = client.payments.list(status="pending", limit=1)
    assert page.has_more is True and page.next_cursor == "c2" and len(page) == 1
    assert transport.request_uri() == "/api/v4/payments?status=pending&limit=1"

    transport.replies = [first, second]
    seen = [p.status for p in client.payments.iterate(created_from=datetime(2026, 1, 1, 3, tzinfo=UTC))]
    assert seen == [PaymentStatus.PENDING, PaymentStatus.PAID]
    assert transport.request_uri(-2) == "/api/v4/payments?created_from=2026-01-01T03%3A00%3A00Z"
    assert transport.request_uri() == "/api/v4/payments?created_from=2026-01-01T03%3A00%3A00Z&cursor=c2"

    transport.replies = [ok([], {"cursor": {"has_more": False}})]
    assert client.payments.find_by_order_id("order-404") is None
    assert transport.request_uri() == "/api/v4/payments?order_id=order-404&limit=1"


def test_naive_datetime_and_unknown_filter_are_refused() -> None:
    client, _ = client_returning([])
    with pytest.raises(enigmoss.InvalidArgumentError, match="timezone-aware"):
        client.payments.list(created_from=datetime(2026, 1, 1))
    with pytest.raises(enigmoss.InvalidArgumentError, match="status"):
        client.payments.list(status="unknown")
    with pytest.raises(enigmoss.InvalidArgumentError, match="other"):
        client.transactions.list(type="other")


def test_models_and_raw_are_json_serializable() -> None:
    client, _ = client_returning(response("paid_payment"))
    payment = client.payments.get(PAYMENT_ID)
    assert json.loads(json.dumps(payment.raw)) == response("paid_payment")
    assert pickle.loads(pickle.dumps(payment)) == payment
    assert Payment.__dataclass_params__.frozen  # type: ignore[attr-defined]
