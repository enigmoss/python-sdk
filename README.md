# Enigmoss Python SDK

The official Python client of the Enigmoss Project API v4: accept crypto payments, run static
wallets, send payouts and refunds, and verify signed webhooks.

- **Thin.** One client, one method per API operation, frozen dataclasses with `Decimal` amounts.
- **Safe by default.** Requests are signed, creates carry an `Idempotency-Key` derived from your
  `order_id`, retries never pay twice, payouts get a 180 s timeout.
- **Zero dependencies.** Only the standard library. Python 3.11+.

## Quick start

**1. Install**

```bash
pip install enigmoss
```

**2. Set three environment variables.** Open your project in the dashboard: it shows the API
host, the project ID and the API keys.

| Variable | Value |
|---|---|
| `ENIGMOSS_BASE_URL` | Your API host, e.g. `https://api.example.com`. The host is white-label and specific to your account: there is no default. |
| `ENIGMOSS_PROJECT_ID` | The project ID (a UUID). |
| `ENIGMOSS_PAYMENT_KEY` | The payment key. Add `ENIGMOSS_PAYOUT_KEY` only if you send payouts, refunds or transfers. |

`from_env()` takes the same keyword options as `Enigmoss(...)`, e.g. `from_env(timeout=10)`. The
base URL must be https; `allow_insecure_http=True` allows `http://` for a local stub only.

**3. Create an invoice**

```python
from uuid import uuid4

import enigmoss

client = enigmoss.Enigmoss.from_env()
payment = client.payments.create(amount="10.00", currency_code="USDT", order_id=f"test-{uuid4().hex[:8]}")
print(payment.payment_url)  # open it: this is the hosted payment page
```

An invoice on a live project is safe: no money moves until someone pays it. Cancel it with
`client.payments.cancel(payment.id)`. There is no separate test mode.

Amounts are **strings or `Decimal`** (`"10.00"`), never floats; they are sent exactly as given.

### `order_id` in a real checkout

One `order_id` is one invoice, forever. Repeating `create()` with the same `order_id` and the
same data within 24 hours returns the same invoice (a double click, a retried job); with other
data it fails with `IdempotencyError`; after 24 hours the `order_id` is simply taken. So give
each payment *attempt* its own `order_id`, and reuse it when you repeat that attempt:

```python
from decimal import Decimal

import enigmoss

client = enigmoss.Enigmoss.from_env()


def checkout_url(order_id: int, attempt: int, total: Decimal) -> str | None:
    payment = client.payments.create(
        amount=total,
        currency_code="USDT",
        order_id=f"order-{order_id}-{attempt}",  # a new attempt after expiry or cancel: attempt + 1
        callback_url="https://shop.example/webhooks/enigmoss",
        metadata={"order_id": order_id},
    )
    return payment.payment_url  # may be None: never redirect to an empty URL


print(checkout_url(42, 1, Decimal("10.50")))
```

`client` is thread-safe: create one per process and reuse it.

## Webhooks

The API sends `payment.*`, `payout.*` and `refund.*` events to your `callback_url`. Rules:

- verify the **raw body bytes** (never a re-parsed dict) with `construct_event()`; answer
  **400** when it raises `WebhookVerificationError`;
- answer **2xx within 15 seconds**; do heavy work in a queue. Until you answer 2xx the API
  repeats the delivery (up to about two days), and deliveries can arrive twice or out of order;
- **deduplicate by `event.id`** atomically;
- fulfil an order only when **`payment.is_successful`** (paid *and* final). A deposit to a static
  wallet is a `payment.*` event with `wallet_id` set and no `order_id`: check it first.

All webhooks, including `payout.*` and `refund.*`, are signed with the **payment key**.

The handler below claims `event.id` with `INSERT ... ON CONFLICT DO NOTHING` in the same
transaction as your work, so a failed handler releases the claim and the next delivery is
processed again. It uses `sqlite3` to stay runnable; the SQL is the same in PostgreSQL (with
`%s` placeholders). Keep the table: a processed `event.id` stays claimed (the API stops
redelivering after about two days, so rows older than a few days can be deleted).

<!-- readme-test: name=webhook-handler -->
```python
import sqlite3

import enigmoss
from enigmoss.models import Payment

client = enigmoss.Enigmoss.from_env()
db = sqlite3.connect("webhooks.db", check_same_thread=False)
db.execute("CREATE TABLE IF NOT EXISTS processed_webhooks (event_id TEXT PRIMARY KEY)")


def fulfil_order(order_id: str) -> None:
    """Your code: enqueue fulfilment of the order."""


def process_event(event: enigmoss.WebhookEvent) -> None:
    with db:  # one transaction: the claim is rolled back if handling fails
        claimed = db.execute(
            "INSERT INTO processed_webhooks (event_id) VALUES (?) ON CONFLICT DO NOTHING", (event.id,)
        ).rowcount
        if not claimed:
            return  # a repeated delivery
        payment = event.object
        if isinstance(payment, Payment):
            if payment.wallet_id is not None:
                pass  # a deposit to a static wallet: find the customer by payment.wallet_id
            elif payment.is_successful and payment.order_id is not None:
                fulfil_order(payment.order_id)
```

**FastAPI.** A `def` endpoint runs in a thread pool, so the sync client does not block the event
loop; the raw body comes from a small async dependency.

<!-- readme-test: uses=webhook-handler scenario=fastapi -->
```python
from typing import Annotated

from fastapi import Depends, FastAPI, HTTPException, Request

import enigmoss

app = FastAPI()


async def raw_body(request: Request) -> bytes:
    return await request.body()


@app.post("/webhooks/enigmoss")
def enigmoss_webhook(request: Request, body: Annotated[bytes, Depends(raw_body)]) -> dict[str, bool]:
    try:
        event = client.webhooks.construct_event(body, request.headers)
    except enigmoss.WebhookVerificationError:
        raise HTTPException(status_code=400) from None
    process_event(event)  # the handler above
    return {"ok": True}
```

**Django.**

<!-- readme-test: uses=webhook-handler scenario=django -->
```python
from django.http import HttpRequest, HttpResponse, HttpResponseBadRequest
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

import enigmoss


@csrf_exempt
@require_POST
def enigmoss_webhook(request: HttpRequest) -> HttpResponse:
    try:
        event = client.webhooks.construct_event(request.body, request.headers)
    except enigmoss.WebhookVerificationError:
        return HttpResponseBadRequest()
    process_event(event)  # the handler above
    return HttpResponse(status=200)
```

**Flask.**

<!-- readme-test: uses=webhook-handler scenario=flask -->
```python
from flask import Flask, abort, request

import enigmoss

app = Flask(__name__)


@app.post("/webhooks/enigmoss")
def enigmoss_webhook() -> tuple[str, int]:
    try:
        event = client.webhooks.construct_event(request.get_data(), request.headers)
    except enigmoss.WebhookVerificationError:
        abort(400)
    process_event(event)  # the handler above
    return "", 200
```

A separate webhook service without a client verifies with
`enigmoss.webhooks.construct_event(body, headers, project_id=..., payment_key=...)`. While you
rotate the payment key, pass both: `payment_key=[new_key, old_key]`.

`event.object` is a `Payment`, `Payout` or `Refund` (parsed on first access), or `None` for
other events; `event.data` is the raw JSON. Send yourself a test delivery with
`client.webhooks.send_test()`, and replay missed events with `client.events.list()` /
`client.events.redeliver()`.

## Payouts and refunds

Payouts, refunds and transfers move money and need the **payout key**; requests signed with it
must come from an IP address in the project's whitelist. One `order_id` is one payout, forever:
the SDK derives the `Idempotency-Key` from it, so repeating `create()` with the same `order_id`
returns the stored payout instead of paying twice.

Check **`outcome_unknown`** on every error. When it is `True` (the connection broke after
sending, a 5xx, a timeout), the payout may exist: do **not** create a new `order_id` and do not
repeat the call in the same request. Mark the withdrawal "to reconcile" and check it later.

```python
import enigmoss
from enigmoss.models import PayoutStatus

client = enigmoss.Enigmoss.from_env()  # with ENIGMOSS_PAYOUT_KEY set


def send_withdrawal(withdrawal_id: int, address: str) -> str:
    try:
        payout = client.payouts.create(
            order_id=f"withdrawal-{withdrawal_id}",  # one withdrawal, one order_id, forever
            amount="100.00",
            currency_code="USDT",
            network_code="tron",
            address=address,
        )
    except enigmoss.ConflictError as exc:
        if exc.is_order_id_taken:  # created earlier: look at that payout
            return "reconcile"
        raise
    except enigmoss.EnigmossError as exc:
        if exc.outcome_unknown:
            return "reconcile"  # may exist: check later, never with a new order_id
        raise  # definitely not created: fix the cause (exc.code, exc.message)
    return "done" if payout.status is PayoutStatus.COMPLETED else "pending"  # final status arrives by webhook


def reconcile(withdrawal_id: int) -> enigmoss.models.Payout | None:
    """A periodic job: None means it was never created, so create() with the same order_id is safe."""
    return client.payouts.find_by_order_id(f"withdrawal-{withdrawal_id}")


print(send_withdrawal(42, "TRecipientabcdefghijklmnopqrstuvwx"))
print(reconcile(42))
```

Creating a payout can take up to about 160 seconds; the SDK waits at least 180 seconds on
`payouts.create()` and `refunds.create_*()`. Refunds follow the same pattern:
`client.refunds.create_for_payment(payment_id, order_id=..., address=...)` and, for a blocked
static wallet, `client.refunds.create_for_wallet(wallet_id, order_id=..., address=...)`.
`client.payouts.quote(...)` calculates a payout without creating it.

## Errors

Every exception derives from `enigmoss.EnigmossError` and has `outcome_unknown` and `request_id`
(quote it to support). `str(exc)` reads `"<server message> [<code>, request <id>]"`; hints such as
"which key" or "check the clock" are attached as exception notes.

| Exception | When |
|---|---|
| `APIError` | Any error response: `.status`, `.code`, `.type`, `.message`, `.details`, `.violations`, `.retry_after`. Validation and business rules (422) are plain `APIError`. |
| `AuthenticationError` | 401: wrong key or key type, unknown project, clock off. |
| `PermissionDeniedError` | 403: API permission disabled, IP not whitelisted (`auth.ip_forbidden`). |
| `NotFoundError` | 404. |
| `ConflictError` | 409: `.is_order_id_taken` and `.existing_resource_id`, or a state that forbids the action. |
| `IdempotencyError` | 409 `idempotency.*`. **Not** a `ConflictError`: catching conflicts never swallows "still in progress". |
| `RateLimitError` | 429; `.retry_after` seconds. |
| `ServerError` | 5xx; `outcome_unknown` is always `True`. |
| `TransportError` | No response; `outcome_unknown` unless it was certainly not sent (`.phase`). |
| `UnexpectedResponseError` | Unreadable response; `outcome_unknown` on a 2xx. |
| `WebhookVerificationError` | Webhook rejected; `.reason`. |
| `InvalidArgumentError` | A bad argument (also a `ValueError`); nothing was sent. |
| `ConfigurationError` | Missing base URL, project or the key an endpoint needs. |

Compare `exc.code` with `enigmoss.ErrorCode` constants and keep a default branch: the server may
add codes.

## Retries and idempotency

On by default, and safe for money:

- reads are retried after network errors, 429 and 502-504;
- creates and actions (`cancel`, `mark_paid`, `block`) are retried only when it cannot cause a
  second effect: nothing was sent, or an `Idempotency-Key` protects the call, or the server says
  the request was not executed. Business errors are never retried;
- redeliveries and test webhooks are retried only when nothing was sent;
- backoff is exponential with jitter (0.5 s to 8 s); a `Retry-After` up to 10 s is waited out,
  a longer one raises `RateLimitError` at once.

```python
import enigmoss

client = enigmoss.Enigmoss.from_env(max_retries=0)  # no automatic retries
client = enigmoss.Enigmoss.from_env(idempotency="random")  # a new Idempotency-Key per call
client = enigmoss.Enigmoss.from_env(idempotency=None)  # no key where the server does not require one

client = enigmoss.Enigmoss.from_env()
client.payments.cancel(
    "7c9e6679-7425-40de-944b-e07fc1f90ae7",
    options=enigmoss.RequestOptions(max_retries=0, timeout=10),  # this call only
)
```

| `idempotency=` | Creates (payments, wallets, payouts, refunds, transfers) | Actions |
|---|---|---|
| `"order_id"` (default) | `sdk1-` + hash of the request path and `order_id`: a repeat returns the stored object for 24 h | a random UUID per call |
| `"random"` | a random UUID per call; retries inside the call reuse it | a random UUID per call |
| `None` | no key for payments and wallets; payouts, refunds and transfers still send the `order_id` key (the server requires one). Nothing that was sent is retried without a key | no key |

`RequestOptions(idempotency_key="...")` sends your own key.

## Payment statuses

The one fulfilment rule is `payment.is_successful`: `status` is `PAID` or `OVERPAID` **and**
`is_final` is `True`. `is_final` comes from the server; trust it over this table.

| `PaymentStatus` | `is_final` | What to do |
|---|---|---|
| `PENDING`, `PROCESSING`, `CONFIRMING` | never | Wait. |
| `PAID`, `OVERPAID` | may be `False` while a conversion settles | Fulfil when `is_successful`. |
| `UNDERPAID` | may be `False` | Do not fulfil; ask for the rest or accept with `payments.mark_paid()`. |
| `UNDERPAID_WAITING` | never | Wait for the missing part. |
| `EXPIRED`, `CANCELLED`, `FAILED` | typically `True` | Not paid. A new attempt needs a new `order_id`. |
| `LOCKED` | never | An AML check holds the funds: `payments.create_aml_verification_links()`. |
| `REFUNDING` | never | A refund is in progress. |
| `REFUNDED`, `REFUND_FAILED` | typically `True` | Mark refunded, or handle the failed refund manually. |
| `UNKNOWN` | read `is_final` | A status newer than this SDK: do not fulfil, log it. |

Every enum has `UNKNOWN` for values added later; keep a default branch when you match on them.

## Which key for what

| Key | Resources |
|---|---|
| **payment** | `payments`, `wallets`, `balances`, `transactions`, `events`, `webhooks` (including verifying incoming webhooks), `payment_methods.update_discount()` |
| **payout** | `payouts`, `refunds`, `transfers` (IP whitelist applies) |
| either | `project.get()`, `payment_methods.list()`, `payout_methods`, `exchange_rates` |

A call whose key is not configured raises `ConfigurationError` before anything is sent. Some
scenarios need both keys: blocking a wallet (`wallets.block()`, payment) and refunding it
(`refunds.create_for_wallet()`, payout); receiving `payout.*` webhooks (payment) for payouts you
create (payout). `client.project.get()` shows the key type and its permissions.

## Async frameworks

The client is synchronous. In an `async def` endpoint, do not call it directly: that blocks the
event loop. Use a `def` endpoint (FastAPI runs it in a thread pool, as above) or
`await asyncio.to_thread(client.payments.get, payment_id)`.

## Everything else

- **All methods** mirror the API, with keyword arguments named like the wire fields:
  `payments.create/get/find_by_order_id/list/iterate/cancel/mark_paid/create_aml_verification_links`,
  `wallets.create/get/find_by_order_id/list/iterate/block`,
  `payouts.quote/create/get/find_by_order_id/list/iterate`,
  `refunds.create_for_payment/create_for_wallet/get/find_by_order_id/list/iterate`,
  `transfers.create/get/find_by_order_id/list/iterate`, `balances.list`,
  `transactions.list/iterate`, `payment_methods.list/update_discount`,
  `payout_methods.list/get`, `exchange_rates.list(from_=...)`,
  `events.list/iterate/get/redeliver/redeliver_bulk`, `webhooks.construct_event/send_test`,
  `project.get`.
- **Pagination.** `list()` returns a `Page` (`items`, `next_cursor`, `has_more`); `iterate()`
  walks every page lazily; `find_by_order_id()` returns the object or `None`.
- **Models** are frozen dataclasses in `enigmoss.models`; unknown fields are kept in
  `model.raw`. Datetimes are timezone-aware UTC.
- **Other endpoints.** `client.request("GET", "/some/path", key="payment")` signs and sends any
  request and returns a `RawResponse` with the decoded `data`.
- **Your own HTTP client or a test double.** Pass `transport=`: any object with
  `send(request: enigmoss.HttpRequest) -> enigmoss.HttpResponse`. It must send the URL, headers
  and body unchanged and raise `enigmoss.TransportError(phase=...)` when no response arrives.

```python
import json

import enigmoss


class FakeTransport:
    """Answers every request with one canned response; keeps what was sent."""

    def __init__(self, data: object) -> None:
        self.data = data
        self.sent: list[enigmoss.HttpRequest] = []

    def send(self, request: enigmoss.HttpRequest) -> enigmoss.HttpResponse:
        self.sent.append(request)
        return enigmoss.HttpResponse(200, {}, json.dumps({"data": self.data}).encode())


fake = FakeTransport({"id": "3f2b8c1e-5d4a-4e7b-9c6f-1a2b3c4d5e6f", "name": "Shop", "key_type": "payment",
                      "permissions": [], "server_time": 1767225600})
client = enigmoss.Enigmoss(base_url="https://api.example.com", project_id="3f2b8c1e-5d4a-4e7b-9c6f-1a2b3c4d5e6f",
                           payment_key="test-key", transport=fake)
assert client.project.get().name == "Shop"
assert fake.sent[0].headers["X-Project-Uuid"] == "3f2b8c1e-5d4a-4e7b-9c6f-1a2b3c4d5e6f"
```

To test your webhook endpoint, sign a body the way the API does:
`hmac.new(payment_key.encode(), f"{timestamp}\n{project_id}\n".encode() + body, "sha256").hexdigest()`
goes into `X-Signature`, with `X-Timestamp` and `X-Project-Uuid` next to it.

[`llms.txt`](llms.txt) is a compact summary of this SDK for AI coding assistants; it also ships
inside the package.

## Versioning

Semantic versioning. A Python version is dropped only in a major release. See the
[changelog](CHANGELOG.md).

## License

MIT
