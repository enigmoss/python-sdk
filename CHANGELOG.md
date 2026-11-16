# Changelog

All notable changes are documented here. The project follows semantic versioning;
a Python version is dropped only in a major release.

## 1.0.0rc1 - unreleased

First release candidate.

- `Enigmoss` client and `Enigmoss.from_env()` for every operation of the Project API v4:
  project, payments, wallets, payouts, refunds, transfers, balances, transactions, payment
  methods, payout methods, exchange rates, events and webhooks; `client.request()` for the rest.
- Signed requests; `Idempotency-Key` derived from `order_id` on creates; safe retries
  (`max_retries=0` turns them off, `idempotency=` chooses the key mode); 180 s timeout on payouts
  and refunds; clock drift correction.
- `outcome_unknown` on every error; `IdempotencyError` is separate from `ConflictError`.
- `construct_event()` for webhooks, with key rotation.
- Frozen dataclass models with `Decimal` amounts, UTC datetimes, `UNKNOWN` enum values and `raw`.
- Zero runtime dependencies; Python 3.11+.
- API host fallback, off unless `fallback_base_url` (or `ENIGMOSS_FALLBACK_BASE_URL` for
  `from_env()`) is set. After the usual retries, a call that still could not connect to the API
  host asks the fallback host for the current API origin with a signed
  `GET /api/v4/project/platform-brand` (3 s, a one-time nonce in the signed URI, so a recorded
  answer fits no other request), verifies the signature of the answer, and repeats
  the request there with the same body and `Idempotency-Key`. The origin is kept in the client
  object for one hour to seven days; `client.base_url` shows it and `on_origin_change` reports
  the switch as an `OriginChange`. An error or an untrusted answer of the fallback host is
  raised with the network error as its cause and remembers nothing; a fallback that did not help
  is not repeated for 5 minutes; concurrent calls share one request to the fallback host.
- `TransportError.host_unreachable`: the transport is sure that no connection to the API host
  itself was made. Only such a failure starts the fallback. `StdlibTransport` sets it for direct
  connections, and behind a proxy only for a failed TLS handshake with the API host; a custom
  transport has to set it itself.
