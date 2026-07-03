# Current status

**Updated:** 2026-07-02  
**Current milestone:** credential-dependent IG validation and operational soak  
**State:** BLOCKED ON EXTERNAL INPUT

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

## Verification evidence

- Ruff: passed on current source.
- Pyright: `0 errors, 0 warnings, 0 informations` on current source.
- Current framework-independent tests: 25 passed, including Hypothesis OHLC invariants.
- Earlier Docker Python 3.13/PostgreSQL 18 full suite: 25 passed, including three database/API integration tests.
- Migration `0001`: applied successfully to PostgreSQL 18.
- Research export: 3 integration bars, manifest `85570db93ecb060eb3da6105`.
- Replay hash: `85570db93ecb060eb3da61054b56b28a1befe8e4ee5f6aef53ddbfa9270f9ef1`.

## Pending verification

- Rerun the full Docker/PostgreSQL suite after the latest gap, run-tracking and redaction additions.
- Start the API on the new configurable host port (default `8080`) and verify the rendered console.
- Run credential-gated IG listing discovery, streaming and historical backfill.
- Confirm all seven account-specific IG mappings.

## External gates

- `.env` is absent. IG demo credentials are required for live integration tests.
- Run a 24-hour seven-instrument soak covering active Australia 200, FTSE 100 and US 500 sessions.
- Force one Lightstreamer reconnect and one process restart during the soak.
