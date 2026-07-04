# Current status

**Updated:** 2026-07-04
**Current milestone:** 24-hour seven-instrument operational soak
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
- The adapter retries REST authentication with bounded exponential backoff and refreshes
  the REST session before rebuilding a terminally disconnected or stale stream.
- Queue saturation drops and counts the newest update without blocking the Lightstreamer
  callback thread, then recovers health after the queue drains.
- A conservative live backfill produced bid, ask and midpoint bars for all seven
  instruments with `IG_HISTORICAL` provenance and correct one-minute bounds.
- An overlapping live backfill appended zero duplicate canonical events.
- IG's provider-reported remaining historical allowance is persisted separately from the
  operator-supplied allowance.
- A forced reconnect resumed all seven subscriptions with one connection and no dropped
  records; a subsequent fresh-process restart also restored all seven subscriptions.
- The final 222-row research export replayed deterministically.
- The pre-soak architecture review moved PostgreSQL read-model SQL out of the application
  layer, added dependency-direction tests and removed an unused projection port.
- Ruff formatting was applied consistently across source and tests; Ruff, Pyright and
  `ty` are reproducible development gates.
- A VS Code Dev Container now provides Python 3.13, `uv`, Codex and the PostgreSQL 18
  sidecar without mounting the host Docker socket or sharing its virtual environment
  with WSL.
- The Dev Container provisions pinned Tilth and remote Context7 MCP servers in its
  persistent container-local Codex configuration.
- The application and Dev Container use Debian Trixie base images. The Dev Container
  copies the host's global Codex guidance without copying credentials or other Codex
  state.
- A future-facing intraday-strategy research dossier now surveys public FX and
  equity-index evidence, validation quality, market-state/regime research, adaptive risk,
  reported success and candidate experiments. It changes no current implementation scope.

## Verification evidence

- Ruff: passed on current source.
- Pyright: `0 errors, 0 warnings, 0 informations` on current source.
- `ty`: passed on current source.
- Dev Container image: built successfully; Codex CLI `0.142.2` available.
- Dev Container MCP configuration: Tilth `0.9.0` and Context7 enabled.
- Dev Container global guidance: host `~/.codex/AGENTS.md` copied into isolated Codex state.
- Dev Container PostgreSQL-backed suite: 68 passed.
- Current PostgreSQL-backed suite: 86 passed, including dependency-direction checks,
  Hypothesis OHLC invariants, lifecycle/failure cases, deterministic replay cases and
  four database/API integration tests.
- PostgreSQL-backed branch coverage: 70% overall. Core deterministic workflows range
  from 82% to 100%; CLI orchestration is 40% and the IG adapter is 61%.
- Migrations `0001` and `0002`: applied successfully to PostgreSQL 18.
- The API health endpoint and rendered console returned HTTP 200.
- Pre-soak API rehearsal: health, system and instrument endpoints returned HTTP 200 and
  the server shut down cleanly.
- Pre-soak runbook and evidence template: prepared with objective pass/fail criteria,
  candidate freeze rules and reconnect/restart/export/replay evidence fields.
- Intraday strategy research: initial recency-weighted survey completed with 40-source
  ledger and explicit future research priorities.
- Research export: 222 bars, manifest `b2b9d83c91a0fb97fc1e245e`.
- Replay hash: `b2b9d83c91a0fb97fc1e245e108afa67128d72e58a3243d62b6f02a350158ee8`.

## Pending verification

- Continue adding provider error-branch coverage after the soak where operational
  evidence identifies risk; the focused pre-soak suite now covers CLI dispatch and the
  highest-value deterministic authentication, reconnect, subscription and callback
  failures.
- Run AUD/USD, EUR/USD, USD/JPY, GBP/USD, Australia 200, US 500 and FTSE 100
  continuously for at least 24 hours.
- Ensure that run covers active Australia 200, FTSE 100 and US 500 sessions.
- Use one Lightstreamer connection carrying all seven subscriptions; do not run
  concurrent ingestion connections for the same API key.
- Force one Lightstreamer reconnect and one process restart during the soak.
