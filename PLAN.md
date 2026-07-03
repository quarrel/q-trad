# q-trad data foundation implementation plan

**Status:** IN PROGRESS  
**Phase boundary:** data ingestion, audit, normalisation, replay and health visibility  
**Explicitly excluded:** strategies, allocation, risk, paper execution, P&L, live orders and IBKR

## Objective

Deliver a deterministic, inspectable data path:

> IG demo data → raw audit record → canonical quote event → derived one-minute bars → PostgreSQL → Parquet → replay → read-only operator console.

The fixed universe is AUD/USD, EUR/USD, USD/JPY, GBP/USD, Australia 200, US 500 and FTSE 100.

## Completion rules

Statuses are `NOT STARTED`, `IN PROGRESS`, `BLOCKED`, or `DONE`.

A work package is `DONE` only when:

- its automated checks pass;
- verification evidence is recorded below;
- `docs/STATUS.md` reflects reality;
- `docs/ARCHITECTURE.md` reflects implemented structure;
- architectural changes have an accepted ADR.

## Work packages

| Work package | Status | Exit evidence |
|---|---|---|
| WP0 — documentation and repository scaffold | DONE | image built; migration and static checks passed |
| WP1 — canonical domain and ports | DONE | core tests and strict type checks passed |
| WP2 — PostgreSQL audit spine | DONE | migration 0002 applied; full PostgreSQL suite passed |
| WP3 — fixture adapter, bars and replay | DONE | deterministic Parquet/replay hash verified |
| WP4 — IG demo adapter | IN PROGRESS | PRICE smoke passed; reconnect/backoff hardening pending |
| WP5 — backfill and research data | IN PROGRESS | export/replay verified; live backfill pending |
| WP6 — API and operator console | DONE | API and rendered console returned HTTP 200 |
| WP7 — failure testing and soak | IN PROGRESS | credentials verified; resilience work and 24-hour soak pending |

## WP0 — documentation and scaffold

- Maintain `AGENTS.md`, README, architecture, engineering and status documents.
- Record modular-monolith, Docker, event-storage and bar decisions.
- Use Python 3.13, PostgreSQL 18, `uv`, Ruff, Pyright and pytest.
- Supply a secret-free Docker-first workflow.

## WP1 — canonical domain and ports

- Implement the seven canonical instrument IDs and effective provider listings.
- Implement immutable quotes, bars, modes, health, gaps, runs and event envelopes.
- Define `Clock`, `RawCapture`, `EventStore`, `MarketDataAdapter`, `ResearchStore` and projection ports.
- Enforce UTC, `Decimal`, immutability and strict dependency direction.

## WP2 — PostgreSQL audit spine

- Separate raw, canonical, reference, read-model and operations schemas.
- Commit a redacted raw input and its canonical or quarantine result atomically.
- Enforce idempotent raw hashes and optimistic stream versions.
- Build effective listing, latest quote/bar, health, gap, run, quota and checkpoint projections.
- Rebuild all projections from canonical events.

## WP3 — fixture adapter, bars and replay

- Process fixtures before connecting to IG.
- Build `[start, end)` one-minute bid, ask and midpoint bars.
- Require contemporaneous bid and ask within five seconds for midpoint samples.
- Close at a five-second watermark; represent late changes as revisions.
- Never forward-fill missing executable prices.
- Replay by event time, receive time and global position using an injected clock.

## WP4 — IG demo adapter

- Wrap `trading-ig` behind canonical ports.
- Support demo authentication, metadata/history and Lightstreamer prices only.
- Bound queues and implement reconnect, refresh, backoff, quota and staleness state.
- Use one Lightstreamer connection for all seven subscriptions; never run concurrent
  streaming connections for the same API key.
- Discover provider listings from configured aliases and validate all metadata.
- Require the canonical quote currency and select the validated standard contract
  preference for each instrument.
- Fail closed on ambiguity.
- Reject production URLs and expose no order API.

## WP5 — backfill and research data

Use:

```text
per_instrument_points =
    min(1000, floor(0.8 × remaining_weekly_allowance / 7))
```

- Keep historical-bar provenance distinct from quote-derived bars.
- Export Parquet by data type, instrument and UTC date.
- Include schema, coverage, gaps, provenance, code/configuration versions and hashes in manifests.

## WP6 — API and operator console

- Expose health, runs, instruments, listings, quotes, bars, gaps, checkpoints and manifests.
- Use FastAPI, Jinja and HTMX polling.
- Display `as_of`, projection checkpoint, broker environment and data-quality state.
- Keep the console read-only.

## WP7 — hardening and soak

- Test duplicates, reordering, malformed/partial updates, precision, DST, quotas, disconnects, database interruption, queue saturation, restarts, secret redaction and production-route rejection.
- Run all seven starting instruments continuously for at least 24 hours:
  AUD/USD, EUR/USD, USD/JPY, GBP/USD, Australia 200, US 500 and FTSE 100.
- Keep all seven subscriptions on one Lightstreamer connection.
- Ensure the soak includes an active session for Australia 200, FTSE 100 and US 500.
- Force one reconnect and one application restart.

## Public commands

```text
python -m qtrad db upgrade
python -m qtrad instruments sync
python -m qtrad ingest --environment ig-demo
python -m qtrad ingest --environment ig-demo --max-seconds 60
python -m qtrad backfill --max-points 1000
python -m qtrad research export
python -m qtrad replay --manifest PATH
python -m qtrad projections rebuild
python -m qtrad api
```

## Verification evidence

- Current Ruff check: passed.
- Current strict-core Pyright check: zero errors and warnings.
- Current PostgreSQL-backed suite: 35 passed.
- PostgreSQL 18 migrations 0001 and 0002 applied successfully.
- Atomic ingestion, duplicate suppression, stream conflicts, projection rebuild and read-only API were exercised against PostgreSQL.
- IG demo authentication passed.
- IG discovery now filters irrelevant search results before detail requests.
- Initial discovery stopped fail-closed on ambiguous USD/JPY and FTSE 100 variants.
- The operator selected standard rather than mini or alternate-currency contracts;
  all seven preferred listings were then validated and persisted.
- The API health endpoint and rendered operator console returned HTTP 200 on a configurable
  local port.
- A bounded all-seven PRICE streaming smoke persisted 537 raw updates and seven healthy
  latest-quote projections, then finalised its run as `STOPPED`.
- Parquet manifest `85570db93ecb060eb3da6105` replayed to SHA-256 `85570db93ecb060eb3da61054b56b28a1befe8e4ee5f6aef53ddbfa9270f9ef1`.
- Historical backfill, reconnect/backoff hardening and the 24-hour soak remain pending
  and must not be reported as passed.
