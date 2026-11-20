"""Configuration, from_env, the escape hatch and secrets."""

from __future__ import annotations

import pickle

import pytest

import enigmoss
from enigmoss import ConfigurationError, Enigmoss, Secret
from tests.support import BASE_URL, PAYMENT_KEY, PROJECT_ID, FakeTransport, make_client, ok, response


def test_base_url_is_normalized_and_validated() -> None:
    assert make_client(base_url="https://API.example.com/api/v4/").base_url == "https://api.example.com"
    assert make_client(base_url="https://api.example.com:8443").base_url == "https://api.example.com:8443"
    for bad in (
        "",
        "api.example.com",
        "http://api.example.com",
        "https://api.example.com/v1",
        "https://u:p@api.example.com",
        "https://api.example.com?x=1",
    ):
        with pytest.raises(ConfigurationError):
            make_client(base_url=bad)
    assert (
        make_client(base_url="http://localhost:8000", allow_insecure_http=True).base_url
        == "http://localhost:8000"
    )


def test_project_id_and_keys() -> None:
    assert make_client(project_id=PROJECT_ID.upper()).project_id == PROJECT_ID
    with pytest.raises(ConfigurationError, match="UUID"):
        make_client(project_id="42")
    with pytest.raises(ConfigurationError, match="at least one"):
        make_client(payment_key=None, payout_key=None)
    with pytest.raises(ConfigurationError):
        make_client(max_retries=11)
    with pytest.raises(ConfigurationError):
        make_client(idempotency="always")
    with pytest.raises(ConfigurationError):
        make_client(timeout=0)


def test_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in ("ENIGMOSS_BASE_URL", "ENIGMOSS_PROJECT_ID", "ENIGMOSS_PAYMENT_KEY", "ENIGMOSS_PAYOUT_KEY"):
        monkeypatch.delenv(name, raising=False)
    with pytest.raises(ConfigurationError, match="ENIGMOSS_BASE_URL"):
        Enigmoss.from_env()
    monkeypatch.setenv("ENIGMOSS_BASE_URL", BASE_URL)
    monkeypatch.setenv("ENIGMOSS_PROJECT_ID", PROJECT_ID)
    with pytest.raises(ConfigurationError, match="ENIGMOSS_PAYMENT_KEY"):
        Enigmoss.from_env()
    monkeypatch.setenv("ENIGMOSS_PAYMENT_KEY", PAYMENT_KEY)
    transport = FakeTransport(ok(response("project")))
    client = Enigmoss.from_env(transport=transport, max_retries=0)
    assert client.project.get().name == "Example Shop"
    assert transport.last.headers["X-Project-Uuid"] == PROJECT_ID


def test_secrets_do_not_leak() -> None:
    client = make_client()
    secret = Secret("super-secret-value")
    assert "super-secret-value" not in repr(secret) + str(secret) + repr(client)
    assert PAYMENT_KEY not in repr(client.__dict__)
    with pytest.raises(TypeError, match="pickled"):
        pickle.dumps(secret)
    with pytest.raises(enigmoss.InvalidArgumentError):
        Secret("")


def test_user_agent_and_app_info() -> None:
    transport = FakeTransport(ok(response("project")))
    make_client(transport, app_info=("my-shop", "2.1.0")).project.get()
    agent = transport.last.headers["User-Agent"]
    assert agent.startswith("enigmoss-python-sdk/1.0.0 Python/")
    assert agent.endswith(" my-shop/2.1.0")
    with pytest.raises(ConfigurationError):
        make_client(app_info=("bad name", None))


def test_request_escape_hatch() -> None:
    payout_id = "9b2d4f6a-1c3e-4a5b-8d7f-0e1a2b3c4d5e"
    transport = FakeTransport(ok(response("payout")))
    client = make_client(transport)
    result = client.request(
        "POST",
        f"/payouts/{payout_id}/cancel",
        key="payout",
        options=enigmoss.RequestOptions(idempotency_key="cancel-1"),
    )
    assert result.data["id"] == payout_id
    assert result.request_id == "req-1"
    sent = transport.last
    assert sent.url == f"{BASE_URL}/api/v4/payouts/{payout_id}/cancel"
    assert sent.body == b"{}"
    assert sent.headers["Idempotency-Key"] == "cancel-1"

    client.request("GET", "/api/v4/project", key="any", query={"a": None, "b": "1"})
    assert transport.last.url == f"{BASE_URL}/api/v4/project?b=1"
    assert transport.last.body is None
    assert "Idempotency-Key" not in transport.last.headers
    for bad in ("payments", "/payments?limit=1", "/payments/{id}"):
        with pytest.raises(enigmoss.InvalidArgumentError):
            client.request("GET", bad)


def test_path_segments_are_validated() -> None:
    transport = FakeTransport(ok(response("payment_method")))
    client = make_client(transport)
    for bad in ("..", ".", "", "USDT/tron", "a?b"):
        with pytest.raises(enigmoss.InvalidArgumentError):
            client.payment_methods.update_discount(bad, "tron", discount_percent=0)
        with pytest.raises(enigmoss.InvalidArgumentError):
            client.payout_methods.get("USDT", bad)
    assert transport.requests == []


def test_extra_query_and_body() -> None:
    transport = FakeTransport(ok(response("payment")))
    client = make_client(transport)
    client.payments.create(
        amount="1",
        currency_code="USDT",
        order_id="order-1",
        options=enigmoss.RequestOptions(extra_body={"new_field": True}, extra_query={"expand": "payer"}),
    )
    assert (
        transport.last.body == b'{"amount":"1","currency_code":"USDT","order_id":"order-1","new_field":true}'
    )
    assert transport.last.url.endswith("/api/v4/payments?expand=payer")


def test_amount_inputs() -> None:
    from decimal import Decimal

    transport = FakeTransport(ok(response("payment")))
    client = make_client(transport)
    for value, wire in ((Decimal("10.50"), "10.50"), (Decimal("1E+2"), "100"), (7, "7"), ("0.10", "0.10")):
        client.payments.create(amount=value, currency_code="USDT", order_id="order-1")
        assert transport.last.body is not None and f'"amount":"{wire}"'.encode() in transport.last.body
    for bad in (True, -1, Decimal("-1"), "1e5", "10,50"):
        with pytest.raises(enigmoss.InvalidArgumentError):
            client.payments.create(amount=bad, currency_code="USDT", order_id="order-1")
