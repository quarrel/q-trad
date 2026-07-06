# Current status

**Updated:** 2026-07-06
**Current milestone:** 24-hour seven-instrument operational soak
**State:** BLOCKED — remediation implemented; endurance qualification pending

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
- The IG adapter is now included in strict Pyright checking. Minimal local stubs describe
  the consumed `trading-ig` and Lightstreamer APIs, boundary protocols type the injected
  clients and callbacks, and production IG adapter code contains no explicit `Any` or
  type-ignore suppression.
- CLI composition now injects one shared clock per command invocation into the IG adapter
  and all ingestion, backfill, export and replay timestamp paths.
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
- Reconnect lifecycle remediation now requires generation-scoped all-seven data
  readiness, ignores superseded callbacks, escalates prolonged library-managed retries,
  applies shared capped full-jitter recovery with a cooldown, and propagates terminal
  failure instead of allowing natural iterator completion.
- The pinned IG-compatible Lightstreamer client has a narrow disposal repair for its
  un-awaited WebSocket close callback. Shutdown now waits for confirmed disconnect and
  invalidates the old callback generation.
- A bounded live remediation smoke recovered after one retryable session-refresh
  rejection, restored healthy quotes for all seven instruments, reported one reconnect
  and zero dropped records, finalised as `STOPPED`, and left no ingestion process resident.
- The repeated-reconnect qualification exposed a second shutdown defect: cancellation of
  a synchronous provider call left asyncio executor and `trading-ig` rate-limiter threads
  resident after the run had been recorded `STOPPED`.
- Provider calls now have named bounded operation lifecycles outside asyncio's default
  executor. Unresolved calls fail the adapter, client ownership is retained until
  confirmed disconnect, local rate-limiter cleanup is unconditional, and forced
  reconnect completion is recorded separately from the request.
- Lightstreamer `STALLED`, `DISCONNECTED:WILL-RETRY` and
  `DISCONNECTED:TRYING-RECOVERY` states now require fresh healthy updates from all seven
  instruments before readiness can recover.

## Verification evidence

- Ruff: passed on current source.
- Pyright: `0 errors, 0 warnings, 0 informations` on current source.
- IG adapter focused static check: included in strict Pyright with no diagnostics.
- `ty`: passed on current source.
- Dev Container image: built successfully; Codex CLI `0.142.2` available.
- Dev Container MCP configuration: Tilth `0.9.0` and Context7 enabled.
- Dev Container global guidance: host `~/.codex/AGENTS.md` copied into isolated Codex state.
- Dev Container PostgreSQL-backed suite: 68 passed.
- Current PostgreSQL-backed suite: 101 passed, including dependency-direction checks,
  Hypothesis OHLC invariants, lifecycle/failure cases, deterministic replay cases and
  four database/API integration tests. A subprocess regression also proves that a timed
  out provider operation does not keep the command resident.
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

- Complete the repeated-reconnect and two-hour credential-gated qualification for the
  newly remediated candidate. The prior sequence passed `reconnect-2`, then stalled while
  shutting down `reconnect-3`; it is failed diagnostic evidence, not an endurance gate or
  soak result.
- Repeat the complete frozen-candidate rehearsal and 24-hour soak after remediation.
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

## Next-phase preparation

Preparation may proceed while the frozen data-foundation candidate soaks, but paper
runtime implementation remains gated on a conclusive WP7 `PASS`.

- ADR 0005 accepts the no-live-order paper vertical-slice boundary.
- ADRs 0006 and 0007 define causal top-of-book paper fills, session handling, fixed
  allocation, shadow isolation and AUD weighted-average virtual accounting.
- ADR 0010 requires evidence-based external connection readiness, generation isolation,
  watchdogs, shared retry policy and verified shutdown before another full soak.
- `docs/PAPER_SLICE_DECISIONS.md` resolves or defers the blocking PREPLAN questions.
- `docs/PAPER_SLICE_ACCEPTANCE.md` defines the executable causal, safety, accounting,
  determinism and operator scenarios.
- `docs/PAPER_SLICE_RESEARCH.md` records bounded bar, session, fill, product-economics and
  historical-data outcomes. The complete-soak analysis remains pending.
