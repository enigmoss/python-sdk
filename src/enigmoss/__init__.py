"""Official Python client of the Enigmoss Project API v4.

Quick start::

    import enigmoss
    from uuid import uuid4

    client = enigmoss.Enigmoss.from_env()  # ENIGMOSS_BASE_URL, ENIGMOSS_PROJECT_ID, ENIGMOSS_PAYMENT_KEY
    payment = client.payments.create(amount="10.00", currency_code="USDT", order_id=f"test-{uuid4().hex[:8]}")
    print(payment.payment_url)

Import map: the client, options and errors from ``enigmoss``; response models from
``enigmoss.models``; webhook verification without a client from ``enigmoss.webhooks``. The full
rules for AI assistants are in ``llms.txt`` inside this package.
"""

from enigmoss import models, webhooks
from enigmoss._client import Enigmoss, RawResponse
from enigmoss._fallback import OriginChange
from enigmoss._options import RequestOptions
from enigmoss._page import Page
from enigmoss._secret import Secret
from enigmoss._transport import HttpRequest, HttpResponse, StdlibTransport, Transport
from enigmoss._version import __version__
from enigmoss.errors import (
    APIError,
    AuthenticationError,
    ConfigurationError,
    ConflictError,
    EnigmossError,
    ErrorCode,
    IdempotencyError,
    InvalidArgumentError,
    NotFoundError,
    PermissionDeniedError,
    RateLimitError,
    SendPhase,
    ServerError,
    TransportError,
    UnexpectedResponseError,
    Violation,
    WebhookFailureReason,
    WebhookVerificationError,
)
from enigmoss.models import PaymentMethodRef, WebhookEvent

__all__ = [
    "APIError",
    "AuthenticationError",
    "ConfigurationError",
    "ConflictError",
    "Enigmoss",
    "EnigmossError",
    "ErrorCode",
    "HttpRequest",
    "HttpResponse",
    "IdempotencyError",
    "InvalidArgumentError",
    "NotFoundError",
    "OriginChange",
    "Page",
    "PaymentMethodRef",
    "PermissionDeniedError",
    "RateLimitError",
    "RawResponse",
    "RequestOptions",
    "Secret",
    "SendPhase",
    "ServerError",
    "StdlibTransport",
    "Transport",
    "TransportError",
    "UnexpectedResponseError",
    "Violation",
    "WebhookEvent",
    "WebhookFailureReason",
    "WebhookVerificationError",
    "__version__",
    "models",
    "webhooks",
]
