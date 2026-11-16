"""The HTTP transport: the protocol, its request/response types and the stdlib implementation."""

from __future__ import annotations

import http.client
import select
import socket
import ssl
import threading
import urllib.request
from base64 import b64encode
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable
from urllib.parse import unquote, urlsplit

from enigmoss.errors import SendPhase, TransportError

__all__ = ["HttpRequest", "HttpResponse", "StdlibTransport", "Transport"]


@dataclass(frozen=True, slots=True)
class HttpRequest:
    """One HTTP attempt, fully built and signed by the SDK.

    Attributes:
        method: ``"GET"``, ``"POST"`` or ``"PATCH"``.
        url: Absolute URL. Send it exactly as is: the path and query are signed.
        headers: Headers to send, including the signature.
        body: Exact body bytes, or ``None`` for requests without a body.
        timeout: Time limit of the attempt, in seconds.
        keep_alive: ``False`` for calls that create objects or move money: the transport should
            use a fresh connection, because a reused keep-alive connection that the server
            already closed turns a safe call into an unknown outcome.
    """

    method: str
    url: str
    headers: Mapping[str, str]
    body: bytes | None
    timeout: float
    keep_alive: bool = True


@dataclass(frozen=True, slots=True)
class HttpResponse:
    """A received HTTP response. ``headers`` keys are lower case."""

    status: int
    headers: Mapping[str, str] = field(default_factory=dict)
    body: bytes = b""

    def header(self, name: str) -> str | None:
        """Returns a header value by case-insensitive name."""
        return self.headers.get(name.lower())


@runtime_checkable
class Transport(Protocol):
    """Sends one HTTP request. Use it to plug in your own HTTP client or a mock in tests.

    Implementations must not follow redirects or modify the URL, headers or body, and must raise
    :class:`~enigmoss.errors.TransportError` with an honest ``phase`` when no response arrives.
    Pass ``host_unreachable=True`` to that error only when the connection to the API host itself
    was certainly never established (DNS, connect, TLS handshake): it lets the client ask
    ``fallback_base_url`` for the current API origin. Leave it out when unsure, and for a
    failing proxy.
    """

    def send(self, request: HttpRequest) -> HttpResponse:
        """Sends the request and returns the response, whatever its status code."""
        ...


_PoolKey = tuple[str, str, int]


class StdlibTransport:
    """The default transport, built on :mod:`http.client`.

    Reads reuse a small pool of keep-alive connections; requests with ``keep_alive=False`` always
    use a fresh connection that is closed afterwards. Redirects are never followed. TLS peers
    are verified with the system trust store. ``HTTPS_PROXY`` (and ``NO_PROXY``) are honored for
    https URLs.
    """

    def __init__(self, *, max_idle_connections: int = 4, ssl_context: ssl.SSLContext | None = None) -> None:
        self._max_idle = max_idle_connections
        self._ssl_context = ssl_context or ssl.create_default_context()
        self._idle: dict[_PoolKey, list[http.client.HTTPConnection]] = {}
        self._lock = threading.Lock()

    def send(self, request: HttpRequest) -> HttpResponse:
        """Sends the request; see :class:`Transport`."""
        parts = urlsplit(request.url)
        scheme = parts.scheme.lower()
        host = parts.hostname or ""
        port = parts.port or (443 if scheme == "https" else 80)
        target = parts.path + (f"?{parts.query}" if parts.query else "")
        key: _PoolKey = (scheme, host, port)

        conn = self._checkout(key) if request.keep_alive else None
        reused = conn is not None
        if conn is None:
            conn = self._connect(scheme, host, port, request.timeout)
        else:
            conn.timeout = request.timeout
            if conn.sock is not None:
                conn.sock.settimeout(request.timeout)

        try:
            # skip_accept_encoding: send only the headers the SDK built (plus Host).
            conn.putrequest(request.method, target, skip_accept_encoding=True)
            for name, value in request.headers.items():
                conn.putheader(name, value)
            if request.body is not None:
                conn.putheader("Content-Length", str(len(request.body)))
            if not request.keep_alive:
                conn.putheader("Connection", "close")
            conn.endheaders(request.body)
        except (OSError, http.client.HTTPException) as exc:
            conn.close()
            raise TransportError(
                f"Sending the request failed: {exc!r}",
                phase=SendPhase.UNKNOWN,
                timed_out=isinstance(exc, TimeoutError),
            ) from exc

        try:
            response = conn.getresponse()
            body = response.read()
        except (OSError, http.client.HTTPException) as exc:
            conn.close()
            # A reused connection closed by the server fails here without a single byte of
            # response; the server may or may not have seen the request.
            phase = SendPhase.UNKNOWN if reused else SendPhase.SENT
            raise TransportError(
                f"No response was received: {exc!r}",
                phase=phase,
                timed_out=isinstance(exc, TimeoutError),
            ) from exc

        headers: dict[str, str] = {}
        for name, value in response.getheaders():
            headers.setdefault(name.lower(), value)

        if request.keep_alive and not response.will_close:
            self._checkin(key, conn)
        else:
            conn.close()

        return HttpResponse(status=response.status, headers=headers, body=body)

    def close(self) -> None:
        """Closes all idle connections."""
        with self._lock:
            pools, self._idle = self._idle, {}
        for conns in pools.values():
            for conn in conns:
                conn.close()

    def _connect(self, scheme: str, host: str, port: int, timeout: float) -> http.client.HTTPConnection:
        conn: http.client.HTTPConnection
        proxy = None
        if scheme == "https":
            proxy = None if urllib.request.proxy_bypass(host) else urllib.request.getproxies().get("https")
            if proxy:
                proxy_parts = urlsplit(proxy if "://" in proxy else f"http://{proxy}")
                conn = http.client.HTTPSConnection(
                    proxy_parts.hostname or "",
                    proxy_parts.port or 8080,
                    timeout=timeout,
                    context=self._ssl_context,
                )
                tunnel_headers = {}
                if proxy_parts.username is not None:
                    credentials = f"{unquote(proxy_parts.username)}:{unquote(proxy_parts.password or '')}"
                    tunnel_headers["Proxy-Authorization"] = (
                        "Basic " + b64encode(credentials.encode()).decode()
                    )
                conn.set_tunnel(host, port, headers=tunnel_headers)
            else:
                conn = http.client.HTTPSConnection(host, port, timeout=timeout, context=self._ssl_context)
        else:
            conn = http.client.HTTPConnection(host, port, timeout=timeout)

        try:
            conn.connect()
        except (OSError, http.client.HTTPException) as exc:
            conn.close()
            # DNS, refused connection, TLS handshake, proxy CONNECT: nothing of the request left
            # the client. Behind a proxy only a TLS error is known to come from the API host: it
            # happens after the tunnel is open; anything else may be the proxy failing.
            raise TransportError(
                f"Could not connect to {host}:{port}: {exc!r}",
                phase=SendPhase.NOT_SENT,
                timed_out=isinstance(exc, TimeoutError),
                host_unreachable=not proxy or isinstance(exc, ssl.SSLError),
            ) from exc
        return conn

    def _checkout(self, key: _PoolKey) -> http.client.HTTPConnection | None:
        while True:
            with self._lock:
                conns = self._idle.get(key)
                if not conns:
                    return None
                conn = conns.pop()
            if _is_alive(conn):
                return conn
            conn.close()

    def _checkin(self, key: _PoolKey, conn: http.client.HTTPConnection) -> None:
        with self._lock:
            conns = self._idle.setdefault(key, [])
            if len(conns) < self._max_idle:
                conns.append(conn)
                return
        conn.close()


def _is_alive(conn: http.client.HTTPConnection) -> bool:
    """An idle keep-alive socket must have nothing to read; readable means closed by the server."""
    sock = conn.sock
    if sock is None:
        return False
    try:
        if isinstance(sock, ssl.SSLSocket) and sock.pending():
            return False
        readable, _, _ = select.select([sock], [], [], 0)
    except (OSError, ValueError):
        return False
    if not readable:
        return True
    try:
        return sock.recv(1, socket.MSG_PEEK) != b""
    except (OSError, ValueError):
        return False
