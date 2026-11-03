"""The stdlib transport against a local echo server: bytes on the wire are the bytes signed."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import socket
import threading
import time
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

import enigmoss
from enigmoss import HttpRequest, SendPhase, StdlibTransport, TransportError
from tests.support import PAYMENT_KEY, PROJECT_ID, contract, response


class EchoHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, format: str, *args: object) -> None:
        pass

    def _handle(self) -> None:
        length = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(length) if length else b""
        if self.path.endswith("/drop"):
            self.close_connection = True
            self.connection.shutdown(socket.SHUT_RDWR)
            return
        if self.path.endswith("/slow"):
            time.sleep(1.0)
        timestamp = self.headers.get("X-Timestamp", "")
        project = self.headers.get("X-Project-Uuid", "")
        signed = f"{timestamp}\n{project}\n{self.command}\n{self.path}\n".encode() + body
        expected = hmac.new(PAYMENT_KEY.encode(), signed, hashlib.sha256).hexdigest()
        echo = {
            "method": self.command,
            "path": self.path,
            "body_b64": base64.b64encode(body).decode(),
            "signature_valid": hmac.compare_digest(expected, self.headers.get("X-Signature", "")),
            "client_port": self.client_address[1],
            "headers": {k.lower(): v for k, v in self.headers.items()},
        }
        data = response("payment") | {"metadata": echo}
        payload = json.dumps({"data": data, "meta": {"request_id": "echo-1"}}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("X-Request-Id", "echo-1")
        self.end_headers()
        self.wfile.write(payload)

    do_GET = _handle
    do_POST = _handle
    do_PATCH = _handle


@pytest.fixture(scope="module")
def server() -> Iterator[str]:
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), EchoHandler)
    httpd.daemon_threads = True
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{httpd.server_address[1]}"
    httpd.shutdown()
    httpd.server_close()


def client_for(base_url: str, **overrides: object) -> enigmoss.Enigmoss:
    options: dict[str, object] = {
        "base_url": base_url,
        "project_id": PROJECT_ID,
        "payment_key": PAYMENT_KEY,
        "allow_insecure_http": True,
        "max_retries": 0,
    }
    options.update(overrides)
    return enigmoss.Enigmoss(**options)  # type: ignore[arg-type]


def test_query_with_reserved_characters_is_sent_as_signed(server: str) -> None:
    client = client_for(server)
    result = client.request("GET", "/payments", query={"q": "a+b/c=d&e", "name": "Ünï code", "flag": True})
    echo = result.data["metadata"]
    assert echo["path"] == "/api/v4/payments?q=a%2Bb%2Fc%3Dd%26e&name=%C3%9Cn%C3%AF%20code&flag=true"
    assert echo["signature_valid"] is True
    assert result.request_id == "echo-1"


def test_non_ascii_body_is_sent_as_signed(server: str) -> None:
    client = client_for(server)
    payment = client.payments.create(
        amount="10.50",
        currency_code="USDT",
        order_id="order-echo-1",
        description="Оплата заказа №42 — café\r\n😀  ",
    )
    echo = payment.metadata
    body = base64.b64decode(echo["body_b64"])
    assert body.decode() == (
        '{"amount":"10.50","currency_code":"USDT","order_id":"order-echo-1",'
        '"description":"Оплата заказа №42 — café\\r\\n😀 \\u2028"}'
    )
    assert echo["signature_valid"] is True
    assert echo["headers"]["content-type"] == "application/json"
    assert echo["headers"]["idempotency-key"].startswith("sdk1-")
    assert echo["headers"]["user-agent"].startswith("enigmoss-python-sdk/1.0.0-rc1 Python/")


def test_raw_crlf_body_of_the_signature_vector(server: str) -> None:
    vector = next(
        v
        for v in contract("signature-vectors.json")["request"]["vectors"]
        if v["name"] == "non-ascii-body-with-crlf"
    )
    body = vector["body"].encode()
    timestamp = int(time.time())
    from enigmoss._signing import sign_request

    request = HttpRequest(
        method="POST",
        url=server + "/api/v4/payouts",
        headers={
            "X-Project-Uuid": PROJECT_ID,
            "X-Timestamp": str(timestamp),
            "X-Signature": sign_request(PAYMENT_KEY, timestamp, PROJECT_ID, "POST", "/api/v4/payouts", body),
            "Content-Type": "application/json",
        },
        body=body,
        timeout=5,
        keep_alive=False,
    )
    reply = StdlibTransport().send(request)
    echo = json.loads(reply.body)["data"]["metadata"]
    assert base64.b64decode(echo["body_b64"]) == body
    assert len(body) == vector["body_bytes"]
    assert echo["signature_valid"] is True


def test_reads_reuse_a_connection_and_money_calls_do_not(server: str) -> None:
    client = client_for(server)
    first = client.request("GET", "/payments").data["metadata"]
    second = client.request("GET", "/payments").data["metadata"]
    assert first["client_port"] == second["client_port"]

    created = client.payments.create(amount="1", currency_code="USDT", order_id="order-echo-2").metadata
    assert created["headers"]["connection"] == "close"
    assert created["client_port"] != second["client_port"]


def test_refused_connection_is_not_sent() -> None:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    client = client_for(f"http://127.0.0.1:{port}")
    with pytest.raises(TransportError) as caught:
        client.payments.create(amount="1", currency_code="USDT", order_id="order-echo-3")
    assert caught.value.phase is SendPhase.NOT_SENT
    assert caught.value.outcome_unknown is False
    assert caught.value.idempotency_key is not None


def test_connection_dropped_after_sending_is_sent(server: str) -> None:
    client = client_for(server)
    with pytest.raises(TransportError) as caught:
        client.request("POST", "/drop", body={"a": 1})
    assert caught.value.phase is SendPhase.SENT
    assert caught.value.outcome_unknown is True


def test_timeout_after_sending_is_sent(server: str) -> None:
    client = client_for(server, timeout=0.2)
    with pytest.raises(TransportError) as caught:
        client.request("POST", "/slow", body={})
    assert caught.value.phase is SendPhase.SENT
    assert caught.value.timed_out is True
    assert caught.value.outcome_unknown is True
