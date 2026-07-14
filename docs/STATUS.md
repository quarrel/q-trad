# Current status

**Updated:** 2026-07-14
**Current milestone:** capture operations release
**State:** IN PROGRESS — `capture-v1` cloud qualification is running

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
- Dev Container setup now registers Tilth, remote Context7 and repository-scoped GitHub
  MCP servers through the Codex CLI. The private GitHub repository is configured as
  `origin` and is the regular synchronisation target for reviewed commits.
- The application and Dev Container use Debian Trixie base images. The Dev Container
  copies the host's global Codex guidance without copying credentials or other Codex
  state.
- The Dev Container includes the compiler, database client, SSH, IPv6, DNS, socket,
  packet, file and shell diagnostics needed for local and OCI operations. Its only host
  bind mount is the q-trad repository; it does not mount the host Docker socket, home
  directory, SSH agent or WSL/cloud filesystem.
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
- The IG adapter now tracks received-time freshness per required subscription so a quiet
  but active instrument cannot be masked by other instruments, while low-volatility
  overnight sessions have a five-minute staleness window before degradation and a
  thirty-minute prolonged-staleness reconnect threshold.
- `trading-ig` REST calls made through the adapter session now have bounded default HTTP
  connect/read timeouts while preserving explicit call-level overrides.
- The fresh 24-hour seven-instrument soak completed as `STOPPED` after about 24 hours and
  22 seconds. The run carried all seven subscriptions on one Lightstreamer connection,
  recovered from one application-level reconnect cycle, recorded zero dropped records and
  left no ingestion process resident.

## Verification evidence

- Ruff: passed on current source.
- Pyright: `0 errors, 0 warnings, 0 informations` on current source.
- IG adapter focused static check: included in strict Pyright with no diagnostics.
- `ty`: passed on current source.
- Dev Container image: rebuilt without cache; Codex CLI `0.144.1` matches npm's
  `@openai/codex` `latest` dist-tag.
- Dev Container MCP configuration: Tilth `0.9.0` and Context7 enabled after the rebuild.
- Dev Container static gates after the rebuild: Ruff, Pyright and `ty` passed.
- Dev Container global guidance: host `~/.codex/AGENTS.md` copied into isolated Codex state.
- Dev Container PostgreSQL-backed suite: 68 passed.
- Current isolated PostgreSQL-backed suite: 106 passed through migration `0003`, including dependency-direction checks,
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
- Post-remediation focused lifecycle suite: `uv run pytest tests/test_ig_lifecycle.py`
  passed with 32 tests.
- Post-remediation formatting/lint/type gates passed: Ruff format check, Ruff lint,
  Pyright and `ty`.
- Fresh isolated-database final suite: `uv run pytest` passed with 104 tests after
  migrating and seeding a temporary PostgreSQL database for the gate. The earlier direct
  run against the soak database completed but took 15 minutes because a projection
  rebuild intentionally scanned the accumulated soak events; it is not used as the final
  automated gate.
- Fresh 24-hour soak run: `0d9cf6de-421f-493b-bd69-014b4845d00a`, started
  `2026-07-07T01:05:07.463801+00:00`, finished
  `2026-07-08T01:05:29.494570+00:00`, final status `STOPPED`, one reconnect, zero
  dropped records, zero provider operations and no resident ingestion process.

## Pending verification

- Continue adding provider error-branch coverage after the soak where operational
  evidence identifies risk; the focused pre-soak suite now covers CLI dispatch and the
  highest-value deterministic authentication, reconnect, subscription and callback
  failures.
- Keep future PostgreSQL integration gates isolated from the long-lived soak database
  unless the objective is explicitly a projection-volume exercise.

## Next-phase preparation

Preparation may proceed after the WP7 `PASS`, but paper runtime implementation remains
outside the current data-only phase until explicitly admitted by a later plan update.

- ADR 0005 accepts the no-live-order paper vertical-slice boundary.
- ADRs 0006 and 0007 define causal top-of-book paper fills, session handling, fixed
  allocation, shadow isolation and AUD weighted-average virtual accounting.
- ADR 0010 requires evidence-based external connection readiness, generation isolation,
  watchdogs, shared retry policy and verified shutdown before another full soak.
- `docs/PAPER_SLICE_DECISIONS.md` resolves or defers the blocking PREPLAN questions.
- `docs/PAPER_SLICE_ACCEPTANCE.md` defines the executable causal, safety, accounting,
  determinism and operator scenarios.
- `docs/PAPER_SLICE_RESEARCH.md` records bounded bar, session, fill, product-economics and
  historical-data outcomes. The complete-soak analysis can now use the passed WP7
  evidence.

## Capture operations preparation

- `capture-v1` is a versioned TOML configuration for the qualified seven-instrument
  collector; the candidate 20-instrument expansion is not admitted until IG mappings and
  a separate 72-hour qualification pass.
- The repository now contains immutable-image Compose, systemd, backup, healthwatch and
  OCI operator-runbook assets. The required OCI resources, credentials and registry image
  are configured; the runtime qualification is in progress.
- The initial OCI ARM64 collector host is provisioned and hardened with its dedicated
  PostgreSQL block volume, restricted IPv6 SSH, Docker Engine and OCI tooling. The capture
  deployment now binds PostgreSQL to that required mount and validates backups with the
  pinned PostgreSQL container's client version.
- The slim application image for commit `dd9a2d0` is published privately to Sydney OCIR
  for `linux/amd64` and `linux/arm64` with SBOM/provenance attestations at OCI index digest
  `sha256:cebdde74e02240e2210985cbc927c7744f7b7007d36c7a20f501418710400633`.
  The collector authenticates without stored credentials through its instance principal,
  and an ARM64 pull-by-digest round trip ran successfully as UID 10001.
- Initial collector bootstrap exposed and corrected two release-path defects before any
  unattended ingestion: Compose now receives the root-owned deployment environment for
  model interpolation, and the read-only application services initialise uv's cache in
  their existing temporary filesystem. IG's currency-qualified `onePipMeans` product
  label is preserved as bounded provider semantics rather than coerced to a decimal.
- Collector bootstrap also proved that the unbounded IG detail response contains volatile
  snapshot data. Listing-version identity now hashes only bounded, stable selection and
  product-economics facts, preventing price changes from creating false superseding
  listing versions.
- The first cloud bounded ingest persisted 1,215 raw messages and fresh quotes for all
  seven instruments, reached the projection checkpoint, and stopped cleanly. Its
  readiness probe exposed a mismatched persisted adapter identity; readiness now queries
  the canonical `ig-market-data` identity written by ingestion.
- The corrected `d12217f` release is published for `linux/amd64` and `linux/arm64` at OCI
  index digest `sha256:c5e6a42f242c1cec38948c1798ad602878d3af1487bd4349aa11b457dac51828`.
  A final bounded cloud run returned ready with seven fresh instruments and an exactly
  caught-up projection, then stopped cleanly with 1,569 accumulated raw messages. The
  capture stack remains stopped and disabled pending backup, monitoring and reboot gates.
- Tailscale MagicDNS now provides direct Dev Container SSH access to
  `opc@q-trad-capture` under a TCP/22-only peer policy, replacing the temporary WSL socket
  proxy. A separately constrained Beszel agent reports clean collector telemetry to the
  operator's local hub; application readiness and backup/restore monitoring remain
  separate qualification gates.
- Daily backup tooling now writes a custom-format archive, checksum and manifest binding
  the universe plus application/database image digests before bucket upload. Weekly restore
  verification downloads the latest daily set, validates it and restores into a disposable,
  networkless PostgreSQL container using the manifest-pinned image.
- The host-local health watcher now publishes readiness, fresh-subscription count,
  projection lag, backup age, restore evidence/age and database-volume free space as OCI
  custom metrics, and fails closed when any required operational evidence is stale.
- GitHub workflow definitions now provide push/pull-request static and isolated-PostgreSQL
  gates plus manual commit-SHA-tagged dual-architecture publication to Sydney OCIR. The
  protected repository release environment and dedicated publisher credentials are configured.
- Five deterministic operations tests cover the collector stop contract, successful backup
  evidence, upload object sets, manifest-pinned restore verification, metric publication and
  unhealthy readiness.
- GitHub CI passed all static checks, migration and 113 tests against an isolated PostgreSQL
  18 service. The `capture-release` environment has a main-only deployment policy. Host metric
  publication targets OCI's required regional ingestion endpoint and passes with the scoped
  instance-principal permission.
- A repository-scoped, read-only GitHub deploy key now backs the host checkout at
  `/home/opc/q-trad-source`; `git pull --ff-only` succeeds without an operator PAT. The
  reviewed release archive is staged at the current full commit and `/opt/qtrad-capture`
  selects it atomically.
- The private `qtrad-capture-backups` bucket is versioned with active 14-day daily,
  56-day weekly and 7-day previous-version lifecycle rules. Instance-principal access
  uploaded the first real archive/checksum/manifest set successfully.
- The first isolated restore verification exposed and corrected the OCI CLI's direct
  `data` array response shape. The retry verified the checksum, restored migration `0003`
  into a networkless temporary PostgreSQL container and read 1,642 canonical events.
  Backup and restore status evidence is fresh; their timers are enabled with the collector.
- OCI custom-metric ingestion now accepts the collector's instance-principal publication.
  With capture deliberately stopped, healthwatch correctly publishes the available fresh
  backup/restore/disk evidence and exits unhealthy because readiness is unavailable.
- The dedicated GitHub publisher authenticates to Sydney OCIR, and Buildx completes both
  architecture builds. OCIR denied manifest publication under `use repos`, including after
  identity-domain and propagation checks; the OCI-specific correction is capture-compartment
  `manage repos` for the dedicated publisher group.
- GitHub Actions subsequently published commit `839cd49` as an attested OCI index at digest
  `sha256:3ca07eaee8cf1500546c1779bb0732d9260b085e8a179e3514a507da4ee77d80`.
  Registry inspection proves native `linux/amd64` and `linux/arm64` manifests with separate
  per-platform attestation manifests. The collector pulled the index by digest, selected
  ARM64, and verified the image runs as non-root `qtrad` on `aarch64`.
- `/etc/qtrad/capture.env` now pins that index digest and the prior application digest is
  retained root-only for rollback. The active reviewed release descriptor is selected through
  `/opt/qtrad-capture`.
- The pinned ARM release then passed the deployment-path smoke under systemd and Compose:
  migration completed, readiness returned HTTP 200 with all seven expected instruments,
  and global/projection positions were exactly caught up at 1,678. Healthwatch accepted the
  complete readiness, lag, fresh backup/restore and disk evidence and published its healthy
  OCI metric set. The stack stopped cleanly afterward; capture and all timers remain
  disabled.
- Application rollback was exercised without changing schema or canonical data: the saved
  prior digest reached seven-of-seven caught-up readiness, then the restored current digest
  did the same. The current digest remains pinned and the rollback reference remains
  root-only.
- A clean host reboot recovered Tailscale SSH, Docker, the Beszel agent, the reviewed release
  symlink and the dedicated XFS PostgreSQL mount with no failed units. The post-reboot current
  image returned seven fresh instruments with global/projection positions exactly caught up
  at 1,756, then stopped cleanly.
- The unattended `capture-v1` qualification began on 2026-07-14. Its first deliberate
  container restart exposed that Compose's default `SIGTERM` bypassed Python's clean
  cancellation path and left the interrupted run non-terminal. Release descriptor
  `89c7553` now sends `SIGINT` and allows 90 seconds for verified disconnect.
- With that correction deployed, both a clean host reboot and a deliberate ingestion
  container restart finalised their preceding runs as `STOPPED`, started replacement runs,
  restored seven-of-seven fresh readiness and caught projections up exactly. Capture,
  healthwatch, backup and restore-verification timers remain enabled. The candidate window
  ends no earlier than `2026-07-17T03:05:33Z`.
- Restricted direct IPv6 SSH remains the primary recovery route and policy-constrained
  Tailscale SSH is the backup. Bastion availability and ongoing OCI/Beszel threshold tuning
  no longer gate data collection, but remain operator hardening follow-ups.
- Local branch preparation has started without changing the frozen collector. ADR 0014 defines
  a zero-copy, loopback-only canonical-event feed with bounded cursor pages, source/universe
  identity and no raw-record exposure. Its local implementation adds no IG call or downstream
  paper behaviour.
- The comment-only `capture-v2` list is now a structured, hashable 20-instrument offline
  catalogue. It contains no preferred provider epics, fails if read as an approved ingestion
  universe and therefore cannot expand the running collector.
- Draft PR 1 validates this local preparation in an isolated PostgreSQL 18 CI environment.
  Formatting, linting, typing, shell checks, migration and the full test job passed; no image
  was published and the frozen OCI release was not changed.
- The future feed-capable Compose descriptor now fails configuration unless the operator sets
  a stable, non-secret capture-source ID. This branch is not deployed; the running collector's
  environment and release descriptor remain unchanged during qualification.
