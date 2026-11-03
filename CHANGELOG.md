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
