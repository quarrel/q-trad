# Current status

**Updated:** 2026-07-03
**Current milestone:** IG historical backfill, resilience and operational soak
**State:** IN PROGRESS

## Completed

- Architecture preplan created.
- Initial universe decided:
  - AUD/USD
  - EUR/USD
  - USD/JPY
  - GBP/USD
  - Australia 200
  - US 500
  - FTSE 100
- Data-foundation implementation scope and anti-sprawl rules agreed.
- Governance, architecture, engineering and ADR documents created.
- Python 3.13/`uv` lock and Docker Compose scaffold created.
- PostgreSQL 18 migration applied successfully.
- Canonical instruments, events, quotes, bars, modes, gaps, runs and ports implemented.
- Atomic raw/canonical persistence, stream version checks and projections implemented.
- Fixture adapter, one-minute bar builder and deterministic replay implemented.
- Parquet export and manifest replay verified with identical SHA-256.
- IG demo-only adapter and fail-closed listing selection implemented.
- Read-only FastAPI/Jinja/HTMX operator console implemented.
- IG demo credentials authenticated successfully.
- PostgreSQL migration `0002` corrected market-bar projection identity so distinct
  source listings and provenance remain independently rebuildable.
- IG discovery prefilters irrelevant search results before requesting full market details.
- IG listing selection requires the canonical quote currency and uses the validated
  standard-contract preference for each of the seven instruments.
- All seven standard-contract IG demo mappings were discovered, validated and persisted.
- The adapter uses the supported `PRICE:{account identifier}:{epic}` subscription on one
  Lightstreamer connection; deprecated `MARKET`/`L1` subscriptions are not used.
- A 60-second all-seven streaming smoke persisted 537 raw updates, produced healthy
  latest quotes for every instrument and terminated cleanly.
- Persisted subscription labels exclude the account identifier.

## Verification evidence

- Ruff: passed on current source.
- Pyright: `0 errors, 0 warnings, 0 informations` on current source.
- Current PostgreSQL-backed suite: 35 passed, including Hypothesis OHLC invariants
  and three database/API integration tests.
- Migrations `0001` and `0002`: applied successfully to PostgreSQL 18.
- The API health endpoint and rendered console returned HTTP 200.
- Research export: 3 integration bars, manifest `85570db93ecb060eb3da6105`.
- Replay hash: `85570db93ecb060eb3da61054b56b28a1befe8e4ee5f6aef53ddbfa9270f9ef1`.

## Pending verification

- Run credential-gated IG historical backfill.
- Implement and verify reconnect, refresh and backoff behaviour before the soak.

## External gates

- Run AUD/USD, EUR/USD, USD/JPY, GBP/USD, Australia 200, US 500 and FTSE 100
  continuously for at least 24 hours.
- Ensure that run covers active Australia 200, FTSE 100 and US 500 sessions.
- Use one Lightstreamer connection carrying all seven subscriptions; do not run
  concurrent ingestion connections for the same API key.
- Force one Lightstreamer reconnect and one process restart during the soak.
