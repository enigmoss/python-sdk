"""Drift against the API document: every operation is in the SDK, every response field in a model."""

from __future__ import annotations

import dataclasses
import re
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

import pytest

import enigmoss
from enigmoss import models
from tests.support import BASE_URL, FakeTransport, contract, make_client, ok, response

OPERATIONS: dict[str, list[str]] = contract("openapi-v4.json")["operations"]

ID = "7c9e6679-7425-40de-944b-e07fc1f90ae7"
ADDRESS = "TRecipientabcdefghijklmnopqrstuvwx"
SINCE = datetime(2026, 1, 1, tzinfo=UTC)

Call = Callable[[enigmoss.Enigmoss], object]

# operation -> (how the SDK calls it, the model of its response data, the fixture to reply with)
SDK: dict[str, tuple[Call, type, str, bool]] = {
    "GET /project": (lambda c: c.project.get(), models.Project, "project", False),
    "POST /payments": (
        lambda c: c.payments.create(amount="1", currency_code="USDT", order_id="o-1"),
        models.Payment,
        "payment",
        False,
    ),
    "GET /payments": (lambda c: c.payments.list(), models.Payment, "payment", True),
    "GET /payments/{id}": (lambda c: c.payments.get(ID), models.Payment, "payment", False),
    "POST /payments/{id}/cancel": (lambda c: c.payments.cancel(ID), models.Payment, "payment", False),
    "POST /payments/{id}/mark-paid": (lambda c: c.payments.mark_paid(ID), models.Payment, "payment", False),
    "POST /payments/{id}/aml-verification-links": (
        lambda c: c.payments.create_aml_verification_links(ID),
        models.AmlVerificationLink,
        "aml_verification_link",
        True,
    ),
    "POST /wallets": (
        lambda c: c.wallets.create(currency_code="USDT", network_code="tron", order_id="o-1"),
        models.Wallet,
        "wallet",
        False,
    ),
    "GET /wallets": (lambda c: c.wallets.list(), models.Wallet, "wallet", True),
    "GET /wallets/{id}": (lambda c: c.wallets.get(ID), models.Wallet, "wallet", False),
    "POST /wallets/{id}/block": (lambda c: c.wallets.block(ID), models.Wallet, "wallet", False),
    "POST /payouts": (
        lambda c: c.payouts.create(order_id="o-1", amount="1", currency_code="USDT", address=ADDRESS),
        models.Payout,
        "payout",
        False,
    ),
    "POST /payouts/quote": (
        lambda c: c.payouts.quote(amount="1", currency_code="USDT", address=ADDRESS),
        models.PayoutQuote,
        "payout_quote",
        False,
    ),
    "GET /payouts": (lambda c: c.payouts.list(), models.Payout, "payout", True),
    "GET /payouts/{id}": (lambda c: c.payouts.get(ID), models.Payout, "payout", False),
    "POST /payments/{id}/refunds": (
        lambda c: c.refunds.create_for_payment(ID, order_id="r-1", address=ADDRESS),
        models.Refund,
        "refund",
        False,
    ),
    "POST /wallets/{id}/refunds": (
        lambda c: c.refunds.create_for_wallet(ID, order_id="r-1", address=ADDRESS),
        models.Refund,
        "refund",
        False,
    ),
    "GET /refunds": (lambda c: c.refunds.list(), models.Refund, "refund", True),
    "GET /refunds/{id}": (lambda c: c.refunds.get(ID), models.Refund, "refund", False),
    "POST /transfers": (
        lambda c: c.transfers.create(order_id="t-1", amount="1", currency_code="USDT"),
        models.Transfer,
        "transfer",
        False,
    ),
    "GET /transfers": (lambda c: c.transfers.list(), models.Transfer, "transfer", True),
    "GET /transfers/{id}": (lambda c: c.transfers.get(ID), models.Transfer, "transfer", False),
    "GET /balances": (lambda c: c.balances.list(), models.Balance, "balance", True),
    "GET /transactions": (lambda c: c.transactions.list(), models.Transaction, "transaction", True),
    "GET /payment-methods": (
        lambda c: c.payment_methods.list(),
        models.PaymentMethod,
        "payment_method",
        True,
    ),
    "PATCH /payment-methods/{id}/{id}": (
        lambda c: c.payment_methods.update_discount("USDT", "tron", discount_percent=5),
        models.PaymentMethod,
        "payment_method",
        False,
    ),
    "GET /payout-methods": (lambda c: c.payout_methods.list(), models.PayoutMethod, "payout_method", True),
    "GET /payout-methods/{id}/{id}": (
        lambda c: c.payout_methods.get("USDT", "tron"),
        models.PayoutMethodDetails,
        "payout_method_details",
        False,
    ),
    "GET /exchange-rates": (lambda c: c.exchange_rates.list(), models.ExchangeRate, "exchange_rate", True),
    "GET /events": (lambda c: c.events.list(), models.EventRecord, "event_record", True),
    "GET /events/{id}": (lambda c: c.events.get(ID), models.EventRecord, "event_record", False),
    "POST /events/{id}/redeliver": (
        lambda c: c.events.redeliver(ID),
        models.EventRecord,
        "event_record",
        False,
    ),
    "POST /events/redeliver": (
        lambda c: c.events.redeliver_bulk(created_from=SINCE),
        models.BulkRedeliveryResult,
        "bulk_redelivery_result",
        False,
    ),
    "POST /webhooks/test": (lambda c: c.webhooks.send_test(), models.EventRecord, "event_record", False),
}


def test_every_operation_of_the_document_is_in_the_sdk() -> None:
    assert len(OPERATIONS) == 34
    assert sorted(SDK) == sorted(OPERATIONS)


@pytest.mark.parametrize("operation", sorted(OPERATIONS))
def test_operation_is_called_with_its_method_and_path(operation: str) -> None:
    call, model, fixture, is_list = SDK[operation]
    data: Any = [response(fixture)] if is_list else response(fixture)
    transport = FakeTransport(ok(data, {"cursor": {"has_more": False}}))
    result = call(make_client(transport))

    sent = transport.last
    path = sent.url[len(BASE_URL) :].split("?")[0].removeprefix("/api/v4")
    path = re.sub(r"/(?:[0-9a-f-]{36}|USDT|tron)(?=/|$)", "/{id}", path)
    assert f"{sent.method} {path}" == operation

    items = list(result) if is_list else [result]  # type: ignore[call-overload]
    assert all(isinstance(item, model) for item in items)


@pytest.mark.parametrize("operation", sorted(OPERATIONS))
def test_every_response_field_is_in_the_model(operation: str) -> None:
    model = SDK[operation][1]
    fields = {f.name.removesuffix("_") for f in dataclasses.fields(model)}
    missing = [name for name in OPERATIONS[operation] if name not in fields]
    assert missing == [], f"{model.__name__} lacks {missing}"
