"""The client."""

from __future__ import annotations

import os
import platform
import re
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Literal
from urllib.parse import urlsplit

from enigmoss import webhooks
from enigmoss._fallback import OriginChange
from enigmoss._http import API_PREFIX, Config, Endpoint, IdempotencyMode, KeyScope, KeySupport, Requester
from enigmoss._options import RequestOptions
from enigmoss._retry import RetryClass
from enigmoss._secret import Secret
from enigmoss._transport import StdlibTransport, Transport
from enigmoss._version import __version__
from enigmoss.errors import ConfigurationError, InvalidArgumentError
from enigmoss.models.events import WebhookEvent
from enigmoss.resources.catalog import ExchangeRatesResource, PaymentMethodsResource, PayoutMethodsResource
from enigmoss.resources.events import EventsResource, WebhooksResource
from enigmoss.resources.payments import PaymentsResource
from enigmoss.resources.payouts import PayoutsResource
from enigmoss.resources.project import ProjectResource
from enigmoss.resources.refunds import RefundsResource
from enigmoss.resources.transfers import BalancesResource, TransactionsResource, TransfersResource
from enigmoss.resources.wallets import WalletsResource

__all__ = ["Enigmoss", "RawResponse"]

_UUID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")
_APP_TOKEN = re.compile(r"[A-Za-z0-9._-]{1,64}")
_APP_VERSION = re.compile(r"[A-Za-z0-9.+_-]{1,32}")
_PRE_RELEASE = re.compile(r"^(\d+\.\d+\.\d+)(?:\.?(a|b|rc|dev)\.?(\d+))?$")
_IDEMPOTENCY_MODES = ("order_id", "random", None)


@dataclass(frozen=True, slots=True)
class RawResponse:
    """The result of :meth:`Enigmoss.request`.

    Attributes:
        data: The decoded ``data`` member of the response.
        meta: The decoded ``meta`` member (``request_id``, ``cursor``...), ``{}`` when absent.
        status: HTTP status code.
        request_id: ``X-Request-Id`` of the response.
    """

    data: Any
    meta: Mapping[str, Any]
    status: int
    request_id: str | None


class Enigmoss:
    """The client of the Enigmoss Project API v4. Thread-safe; create one and reuse it.

    Example::

        client = enigmoss.Enigmoss(
            base_url="https://api.example.com",   # your API host from the dashboard
            project_id="3f2b8c1e-5d4a-4e7b-9c6f-1a2b3c4d5e6f",
            payment_key="...",
        )

    Args:
        base_url: Your API host from the dashboard, e.g. ``"https://api.example.com"``.
            Required: the API is white-label. A trailing ``/api/v4`` is accepted.
        project_id: The project UUID from the dashboard.
        payment_key: The payment key: payments, wallets, balances, transactions, events, test
            webhooks and **verifying incoming webhooks**.
        payout_key: The payout key: payouts, quotes, refunds and transfers. Pass it only if you
            send money out.
        timeout: Time limit of one attempt in seconds. Payouts and refunds get at least 180 s.
        max_retries: Automatic retries after the first attempt; ``0`` disables retries.
        idempotency: ``"order_id"`` (default) derives the ``Idempotency-Key`` of a create from
            its ``order_id``, so repeating the same create returns the stored object.
            ``"random"`` sends a new UUID on every call (retries inside the call reuse it).
            ``None`` sends no key where the server does not require one; payouts, refunds and
            transfers always send the ``order_id`` key, and nothing that was sent is retried
            without a key.
        transport: Any object with ``send(HttpRequest) -> HttpResponse``; default: stdlib
            :mod:`http.client`.
        app_info: ``(name, version)`` of your application, appended to the User-Agent.
        allow_insecure_http: Allow ``http://`` base URLs (local development only).
        fallback_base_url: A second host that tells where the API is when ``base_url`` cannot be
            reached, e.g. ``"https://api.example.org"``. After the retries, a call that still
            could not connect asks this host for the current API origin, checks the signature of
            the answer, remembers the origin and repeats the request there. Not set: no fallback.
        on_origin_change: Called with an :class:`OriginChange` when the client starts to use
            another API origin: log it, alert on it.

    Raises:
        ConfigurationError: A required value is missing or malformed.
    """

    def __init__(
        self,
        *,
        base_url: str,
        project_id: str,
        payment_key: str | Secret | None = None,
        payout_key: str | Secret | None = None,
        timeout: float = 30.0,
        max_retries: int = 2,
        idempotency: IdempotencyMode = "order_id",
        transport: Transport | None = None,
        app_info: tuple[str, str | None] | None = None,
        allow_insecure_http: bool = False,
        fallback_base_url: str | None = None,
        on_origin_change: Callable[[OriginChange], None] | None = None,
    ) -> None:
        if payment_key is None and payout_key is None:
            raise ConfigurationError("Pass at least one API key: payment_key and/or payout_key.")
        if not (isinstance(timeout, int | float) and not isinstance(timeout, bool) and timeout > 0):
            raise ConfigurationError("timeout must be a positive number of seconds.")
        if isinstance(max_retries, bool) or not isinstance(max_retries, int) or not 0 <= max_retries <= 10:
            raise ConfigurationError("max_retries must be an integer between 0 and 10 (0 disables retries).")
        if idempotency not in _IDEMPOTENCY_MODES:
            raise ConfigurationError('idempotency must be "order_id", "random" or None.')
        if transport is not None and not callable(getattr(transport, "send", None)):
            raise ConfigurationError("transport must have a send(HttpRequest) -> HttpResponse method.")
        if on_origin_change is not None and not callable(on_origin_change):
            raise ConfigurationError("on_origin_change must be a callable taking an OriginChange.")

        self._config = Config(
            origin=_origin(base_url, allow_insecure_http),
            project_id=_project_id(project_id),
            payment_key=_secret(payment_key, "payment_key"),
            payout_key=_secret(payout_key, "payout_key"),
            timeout=float(timeout),
            max_retries=max_retries,
            idempotency=idempotency,
            user_agent=_user_agent(app_info),
            fallback_origin=(
                None
                if fallback_base_url is None
                else _origin(fallback_base_url, allow_insecure_http, "fallback_base_url")
            ),
            allow_insecure_http=bool(allow_insecure_http),
            on_origin_change=on_origin_change,
        )
        self._requester = Requester(self._config, transport if transport is not None else StdlibTransport())

        self.project = ProjectResource(self._requester)
        """The project: ``get()``. Either key."""
        self.payments = PaymentsResource(self._requester)
        """Invoices. Payment key."""
        self.wallets = WalletsResource(self._requester)
        """Static wallets. Payment key."""
        self.payouts = PayoutsResource(self._requester)
        """Payouts. Payout key."""
        self.refunds = RefundsResource(self._requester)
        """Refunds. Payout key."""
        self.transfers = TransfersResource(self._requester)
        """Transfers to the personal account. Payout key."""
        self.balances = BalancesResource(self._requester)
        """Balances. Payment key."""
        self.transactions = TransactionsResource(self._requester)
        """Balance transactions. Payment key."""
        self.payment_methods = PaymentMethodsResource(self._requester)
        """Payment methods. Either key; ``update_discount()`` needs the payment key."""
        self.payout_methods = PayoutMethodsResource(self._requester)
        """Payout methods. Either key."""
        self.exchange_rates = ExchangeRatesResource(self._requester)
        """Exchange rates. Either key."""
        self.events = EventsResource(self._requester)
        """The webhook events journal. Payment key."""
        self.webhooks = WebhooksResource(self._requester, self._construct_event)
        """Webhook verification and test deliveries. Payment key."""

    @classmethod
    def from_env(cls, **overrides: Any) -> Enigmoss:
        """Builds a client from the ``ENIGMOSS_*`` environment variables.

        Reads ``ENIGMOSS_BASE_URL``, ``ENIGMOSS_PROJECT_ID``, ``ENIGMOSS_PAYMENT_KEY`` and
        ``ENIGMOSS_PAYOUT_KEY`` (set at least one of the keys), and the optional
        ``ENIGMOSS_FALLBACK_BASE_URL``.

        Keyword arguments are passed to the constructor and win over the environment.

        Raises:
            ConfigurationError: A required variable is missing.
        """
        values: dict[str, Any] = {}
        for argument, variable in (
            ("base_url", "ENIGMOSS_BASE_URL"),
            ("project_id", "ENIGMOSS_PROJECT_ID"),
            ("payment_key", "ENIGMOSS_PAYMENT_KEY"),
            ("payout_key", "ENIGMOSS_PAYOUT_KEY"),
            ("fallback_base_url", "ENIGMOSS_FALLBACK_BASE_URL"),
        ):
            value = os.environ.get(variable, "").strip()
            if value:
                values[argument] = value
        values.update(overrides)
        for argument, variable in (("base_url", "ENIGMOSS_BASE_URL"), ("project_id", "ENIGMOSS_PROJECT_ID")):
            if argument not in values:
                raise ConfigurationError(f"Set {variable} (or pass {argument}=); find it in the dashboard.")
        if values.get("payment_key") is None and values.get("payout_key") is None:
            raise ConfigurationError(
                "Set ENIGMOSS_PAYMENT_KEY and/or ENIGMOSS_PAYOUT_KEY; find them in the dashboard."
            )
        return cls(**values)

    @property
    def project_id(self) -> str:
        """The project UUID, in lower case."""
        return self._config.project_id

    @property
    def base_url(self) -> str:
        """The API origin in use now, without ``/api/v4``.

        The configured one, or the origin learned from ``fallback_base_url`` while it is kept.
        """
        return self._requester.active_origin()

    def request(
        self,
        method: Literal["GET", "POST", "PATCH", "PUT", "DELETE"],
        path: str,
        *,
        body: Mapping[str, Any] | None = None,
        query: Mapping[str, str | int | bool | None] | None = None,
        key: KeyScope = "payment",
        options: RequestOptions | None = None,
    ) -> RawResponse:
        """Calls an endpoint the SDK does not model, with the same signing and typed errors.

        ``GET`` is retried like any read. Other methods are retried only when nothing was sent,
        unless you pass ``options=RequestOptions(idempotency_key=...)``: then the key is sent and
        the call is retried like a money call. For example the feature-gated payout cancel::

            client.request("POST", f"/payouts/{payout_id}/cancel", key="payout",
                           options=enigmoss.RequestOptions(idempotency_key=str(uuid.uuid4())))

        Args:
            method: HTTP method.
            path: Path relative to ``/api/v4``, e.g. ``"/payments"``. Encode path segments
                yourself; pass the query string as ``query``.
            body: JSON body; ``{}`` is sent for a body-less POST.
            query: Query parameters; ``None`` values are dropped.
            key: Which key signs the request: ``"payment"``, ``"payout"`` or ``"any"``.
            options: Per-call overrides.

        Raises:
            InvalidArgumentError: The path is malformed.
            EnigmossError: The request failed.
        """
        method_upper = method.upper()
        if method_upper not in ("GET", "POST", "PATCH", "PUT", "DELETE"):
            raise InvalidArgumentError(f"Unsupported method {method!r}.")
        if path.startswith(API_PREFIX + "/"):
            path = path[len(API_PREFIX) :]
        if not path.startswith("/") or "?" in path or "#" in path or "{" in path:
            raise InvalidArgumentError(
                'path must start with "/" and contain no "?", "#" or "{"; pass the query as query=.'
            )
        if key not in ("payment", "payout", "any"):
            raise InvalidArgumentError('key must be "payment", "payout" or "any".')

        if method_upper == "GET":
            endpoint = Endpoint("GET", path, key, RetryClass.READ)
        elif options is not None and options.idempotency_key is not None:
            endpoint = Endpoint(method_upper, path, key, RetryClass.MONEY, KeySupport.SUPPORTED)
        else:
            endpoint = Endpoint(method_upper, path, key, RetryClass.NON_IDEMPOTENT)
        result = self._requester.execute(endpoint, query=query, body=body, options=options)
        return RawResponse(
            data=result.data, meta=result.meta, status=result.status, request_id=result.request_id
        )

    def _construct_event(self, payload: bytes, headers: Any) -> WebhookEvent:
        key = self._config.payment_key
        if key is None:
            raise ConfigurationError(
                "Verifying webhooks needs the payment key; pass payment_key to Enigmoss()."
            )
        return webhooks.construct_event(payload, headers, project_id=self._config.project_id, payment_key=key)

    def __repr__(self) -> str:
        return f"Enigmoss(base_url={self.base_url!r}, project_id={self._config.project_id!r})"


def _origin(base_url: str, allow_insecure_http: bool, name: str = "base_url") -> str:
    """Normalizes ``base_url`` (and, by the same rules, ``fallback_base_url``) to an origin."""
    if not isinstance(base_url, str) or not base_url.strip():
        if name != "base_url":
            raise ConfigurationError(f'{name} must be an absolute URL such as "https://api.example.org".')
        raise ConfigurationError(
            'base_url is required: your API host from the dashboard, e.g. "https://api.example.com".'
        )
    try:
        parts = urlsplit(base_url.strip())
        port = parts.port
    except ValueError as exc:
        raise ConfigurationError(f"{name} is not a valid URL: {exc}") from exc
    scheme = parts.scheme.lower()
    if not parts.hostname or scheme not in ("http", "https"):
        raise ConfigurationError(f'{name} must be an absolute URL such as "https://api.example.com".')
    if scheme == "http" and not allow_insecure_http:
        raise ConfigurationError(
            f"{name} must use https (set allow_insecure_http=True only for local development)."
        )
    if parts.username is not None or parts.password is not None or parts.query or parts.fragment:
        raise ConfigurationError(f"{name} must not contain credentials, a query string or a fragment.")
    path = parts.path.rstrip("/")
    if path not in ("", API_PREFIX):
        raise ConfigurationError(
            f'{name} must point at the API host ("https://api.example.com"), '
            f'optionally followed by "{API_PREFIX}"; got path "{path}".'
        )
    host = parts.hostname.lower()
    if ":" in host:
        host = f"[{host}]"
    return f"{scheme}://{host}" + (f":{port}" if port is not None else "")


def _project_id(project_id: str) -> str:
    normalized = project_id.strip().lower() if isinstance(project_id, str) else ""
    if not _UUID.fullmatch(normalized):
        raise ConfigurationError(
            'project_id must be the project UUID, e.g. "3f2b8c1e-5d4a-4e7b-9c6f-1a2b3c4d5e6f".'
        )
    return normalized


def _secret(value: str | Secret | None, name: str) -> Secret | None:
    if value is None or isinstance(value, Secret):
        return value
    if not isinstance(value, str) or not value.strip():
        raise ConfigurationError(f"{name} must be a non-empty string.")
    return Secret(value.strip())


def _semver(version: str) -> str:
    """PEP 440 pre-releases as semver: the server reads only semver (``1.0.0rc1`` -> ``1.0.0-rc1``)."""
    match = _PRE_RELEASE.match(version)
    if match is None:
        return version
    base, kind, number = match.groups()
    return base if kind is None else f"{base}-{kind}{number}"


def _user_agent(app_info: Sequence[str | None] | None) -> str:
    agent = f"enigmoss-python-sdk/{_semver(__version__)} Python/{platform.python_version()}"
    if app_info is None:
        return agent
    if not isinstance(app_info, tuple) or len(app_info) != 2:
        raise ConfigurationError('app_info must be a (name, version) tuple, e.g. ("my-shop", "2.1.0").')
    name, version = app_info
    if not isinstance(name, str) or not _APP_TOKEN.fullmatch(name):
        raise ConfigurationError('app_info name must be 1-64 letters, digits, ".", "_" or "-".')
    if version is not None and not (isinstance(version, str) and _APP_VERSION.fullmatch(version)):
        raise ConfigurationError('app_info version must be 1-32 letters, digits, ".", "+", "_" or "-".')
    # Appended, never prepended: the server recognizes the SDK by the start of the header.
    return f"{agent} {name}" + ("" if version is None else f"/{version}")
