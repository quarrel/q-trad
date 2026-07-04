# Engineering guidelines

## Design

- Keep the application a modular monolith.
- Keep domain code deterministic and independent of frameworks.
- Translate external data once, at the adapter boundary.
- Prefer explicit domain names and small protocols.
- Keep canonical facts immutable and make projections disposable.

## Python

- Python 3.13, frozen dataclasses and strict typing in core packages.
- UTC-aware `datetime`; never call the wall clock from domain code.
- `Decimal` for prices, sizes and financial values.
- Pydantic for settings and HTTP models, not as the domain model.
- Async functions are for I/O or orchestration, not ordinary calculations.
- Structured logs use stable event names and exclude secrets.
- Construct one clock at the runtime composition root for each command invocation and
  inject that instance through the operation. Call `now()` at each event boundary; sharing
  a clock source does not mean freezing one timestamp for the whole operation.
- Contain untyped third-party libraries behind small local protocols and committed,
  version-matched `.pyi` stubs for only the APIs used. Do not exclude first-party adapter
  directories from static analysis or let provider-facing `Any` values cross the adapter
  boundary.

## Reuse

- Search before adding code.
- Extract shared code only after two concrete callers share an invariant.
- Do not create generic helpers, base repositories or service locators.
- Keep adapter policy in adapters and business invariants in the domain.
- A third-party dependency must remove more complexity than it introduces.

## Sprawl controls

An ADR is required before adding:

- a top-level package;
- another deployable process or image;
- another datastore, cache or message queue;
- a web framework or large runtime library;
- a second implementation of an existing port.

Do not scaffold future strategy/execution packages until their phase begins.

## Testing

- Unit tests protect domain invariants.
- Property tests cover ordering, idempotency and OHLC invariants.
- Contract tests apply the same cases to fixture and IG adapters.
- PostgreSQL integration tests use the real supported major version.
- Replay tests compare stable hashes, not incidental row order.
- Credential-gated and soak tests are never reported as passing when skipped.
- Measure branch coverage against the PostgreSQL-backed suite; use it to find untested
  operational paths rather than as a substitute for scenario quality.

## Static quality gates

- `uv run ruff format --check src tests`
- `uv run ruff check src tests`
- `uv run pyright`
- `uv run ty check`
