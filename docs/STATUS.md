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
- A read-only checkpoint at `2026-07-14T10:48:34Z` found all four collector/timer units active,
  API and PostgreSQL healthy, ingestion up for eight hours, seven fresh instruments, no readiness
  reasons and exact projection catch-up at global/checkpoint position `352275`. Healthwatch,
  backup and restore-verification last results were successful.
- Restricted direct IPv6 SSH remains the primary recovery route and policy-constrained
  Tailscale SSH is the backup. Bastion availability and ongoing OCI/Beszel threshold tuning
  no longer gate data collection, but remain operator hardening follow-ups.
- Local branch preparation has started without changing the frozen collector. ADR 0014 defines
  a zero-copy, loopback-only canonical-event feed with bounded cursor pages, source/universe
  identity and no raw-record exposure. Its local implementation adds no IG call or downstream
  paper behaviour.
- Consumer-side feed validation is now implemented locally as a pure state transition plus strict
  saved-page decoder. It accepts legitimate sequence gaps and concurrent-append empty pages, but
  rejects source or universe drift, stale/replayed/skipped cursors, high-water regression,
  contradictory page metadata, malformed canonical events and raw-record identity. `feed verify`
  performs no HTTP request or persistence.
- ADR 0015 now adds the separately bounded transport probe without touching the collector. Its
  HTTPX client accepts only an explicit literal-loopback tunnel endpoint, disables redirects and
  ambient proxy configuration, bounds status, content type, request duration, decoded bytes and
  page size, then applies the same identity/cursor contract. `feed probe` fetches one page and
  reports `cursor_persisted=false`; it has no cursor database or downstream writer.
- The previously documented direct database inspection boundary is now implemented locally in ADR
  0016 and migration `0004`. A non-login reader privilege can select only canonical, reference,
  read-model and operations schemas; raw capture and writes remain denied. PostgreSQL is bound only
  to collector host loopback for an independently authenticated SSH-tunnel login. This migration
  and Compose change are not deployed during the frozen qualification.
- The feed contract distinguishes current serving-universe identity from per-event historical
  provenance. A universe/configuration transition can be acknowledged only through an explicit
  caught-up rebind on the same capture source; source or schema changes fail and require a new
  cursor or consumer contract respectively.
- The comment-only `capture-v2` list is now a structured, hashable 20-instrument offline
  catalogue. It contains no preferred provider epics, fails if read as an approved ingestion
  universe and therefore cannot expand the running collector.
- The feature branch now implements the next offline gate: `qtrad instruments review` enumerates
  bounded IG demo listing evidence into a hash-addressed JSON manifest without automatic
  minimum-size selection, database writes or streaming. Wrong-currency, non-rolling,
  unavailable, unknown and invalid-size evidence receives stable fail-closed reason codes;
  multiple eligible candidates remain visible for explicit operator selection.
- The review manifest excludes provider snapshots and credentials, cannot overwrite prior
  evidence and declares `selection_authority=false`. Only a later explicit-epic universe release
  can authorise sync or ingestion. Fixture tests exercise this path; no IG request was made while
  the `capture-v1` qualification is running.
- The following local promotion gate is also implemented without external I/O: a strict review
  parser verifies canonical content hashes and exact catalogue fields, then requires one explicit
  eligible IG demo listing per instrument from an operator-authored selection file. It rejects
  stale, tampered, omitted, duplicate, unseen, reused and ineligible selections before rendering
  an undeployed universe bound to review and selection hashes. It cannot sync or deploy that file.
- Draft PR 1 validates this local preparation in an isolated PostgreSQL 18 CI environment.
  Formatting, linting, typing, shell checks, migration and the full test job passed; no image
  was published and the frozen OCI release was not changed.
- A branch-wide release audit tightened two fail-closed boundaries without contacting IG or the
  collector: listing review now has global search/detail request budgets and discovery no longer
  invents missing minimum-size economics. API readiness additionally requires the running
  ingestion record to match the served capture-universe configuration hash.
- The future feed-capable Compose descriptor now fails configuration unless the operator sets
  a stable, non-secret capture-source ID. This branch is not deployed; the running collector's
  environment and release descriptor remain unchanged during qualification.
- The earlier schema-only claim for planned historical coverage has been replaced locally with
  an executable, fail-closed workflow. `backfill plan` records explicit instruments, an exact
  half-open UTC range, the selected universe hash, configured and effective IG demo listing
  identity, one-minute resolution, chunks and timestamped quota evidence in canonical JSON.
  It makes no IG call and refuses to overwrite evidence.
- `backfill register` requires the reviewed SHA-256 before it persists a plan and plan-scoped
  BID/ASK/MID coverage attempts. `backfill execute` atomically claims only that persisted hash,
  loads its exact listing versions and cannot rediscover or substitute them. Failed plans are
  explicit retries; completion requires observations for every basis.
- Migration `0005` completes historical coverage identity with effective listing version,
  provenance, resolution and detecting/covering plan hashes. Repeated reviewed plans retain
  independent coverage evidence; identical returned bars are idempotent and changed historical
  values append canonical corrections. Live `data_gaps` are neither updated nor closed.
- The read-only `/api/v1/historical-coverage` resource exposes bounded open or completed
  historical attempts separately from `/api/v1/gaps`. All implementation and tests so far are
  local/isolated; no IG request, OCI mutation or collector database access occurred.
- GitHub CI run `29316896861` passed formatting, linting, both strict type checkers, ShellCheck,
  migration through `0005` and all 194 tests against isolated PostgreSQL 18. The initial run
  exposed an untyped optional query bind; the corrected query and a regression for its unfiltered
  bounded form passed. This feature branch remains undeployed during collector qualification.
- ADR 0017 and migration `0006` now make local research exports independently verifiable. A
  schema-version-2 manifest hash binds the exact universe/configuration, application image/version,
  file hashes, semantic bars, coverage, provenance, observed live gaps and plan-scoped historical
  coverage. Metadata or Parquet tampering fails before replay is accepted.
- New partitions use `bars-v2/`, so the legacy exporter in a rolled-back application cannot
  overwrite files referenced by a current manifest. Legacy manifests remain readable, and an
  integration test executes the old INSERT shape after migration `0006` to prove forward-schema
  application rollback compatibility. Export remains confined to an isolated writable database
  copy and has not contacted IG, OCI or the collector.
- Research export now requires an exact half-open UTC range and uses independent semantic identity
  for each instrument/day partition. Adding another day therefore reuses prior immutable files
  instead of rewriting every historical partition under a new whole-export hash.
- GitHub CI run `29320193656` passed formatting, linting, Pyright, `ty`, ShellCheck, migration
  through `0006` and all 203 tests on isolated PostgreSQL 18 for branch head `2fdac45`. This includes
    schema-version-2 persistence/conflict checks and the prior application's legacy manifest INSERT
    after the forward migration. No image was published and the collector remains unchanged.
- ADR 0018 corrects the local Lightstreamer raw boundary: new candidate code persists only fields
  changed in each callback, retains explicit nulls in the audit record and removes null fields from
  per-generation canonical state. Existing raw and canonical history is not rewritten, and the
  pinned OCI qualification image remains unchanged.
- New read-only `storage snapshot` evidence binds capture source, universe/configuration and image
  identity to exact raw/canonical counts, observed PostgreSQL relation/index sizes and bounded
  payload samples. `storage compare` verifies two files offline and reports physical byte deltas
  per raw message, allowing the roughly 1,500-byte observation to be decomposed before retention
  or schema decisions are made.
- GitHub CI run `29322668979` passed formatting, Ruff, Pyright, `ty`, ShellCheck, migrations through
  `0006` and all 211 tests against PostgreSQL 18 for branch head `e24ccc7`. This includes the
  read-only storage inspector's actual catalogue/JSONB queries. No image was published and the
  collector remains unchanged.
- The local storage evidence has been extended without a schema migration: version-two snapshots
  compare physical JSONB payloads with PostgreSQL's JSON text rendering; offline comparison now
  decomposes each index's growth and scan-counter change per raw message. Saved version-one evidence
  remains verifiable.
- `docs/CAPTURE_STORAGE_AUDIT.md` records the current raw/canonical columns and query dependencies.
  It retains raw/canonical facts, deduplication, primary keys and event/stream uniqueness. Only the
  unused-by-current-code event-type/time index, payload representation and fixed-width hashes remain
  evidence-gated candidates; no collector schema or retention policy changed.
- GitHub CI run `29325936926` passed formatting, Ruff, Pyright, `ty`, ShellCheck, migrations through
  `0006` and all 218 tests against PostgreSQL 18 at branch head `1aefd62`, including the version-two
  statistics-reset and JSON-text catalogue queries.
- Offline storage comparison now decomposes combined retained growth into main heap, indexes and
  auxiliary PostgreSQL allocation, with raw/canonical relation breakdowns, bytes per raw message,
  bytes per new relation row and the observed canonical/raw row ratio. The active collector and its
  qualification image remain unchanged.
- GitHub CI run `29326440612` passed formatting, Ruff, Pyright, `ty`, ShellCheck, migrations through
  `0006` and all 219 tests against PostgreSQL 18 at branch head `246855e`.
- Storage comparison now fails closed across capture source, database, universe, configuration,
  application-version or immutable-image drift. Its machine-readable gate requires six elapsed
  hours and 100,000 new raw messages, marks index evidence unusable across a statistics reset and
  keeps active-market representativeness as an operator review rather than an inferred fact.
- GitHub CI run `29326917893` passed formatting, Ruff, Pyright, `ty`, ShellCheck, migrations through
  `0006` and all 224 tests against PostgreSQL 18 at branch head `9189f21`.
- Storage comparison now reports observed raw-message, canonical-event and raw/canonical relation
  byte rates, plus explicitly mechanical combined-relation scenarios over one, 30 and 365 days.
  The output binds its representative-threshold status and does not label those scenarios forecasts.
- ADR 0019 now closes the local snapshot-to-research gap without touching OCI. Future backup-v2
  manifests bind capture source, universe, images and migration in self-hashed identity, while the
  restore verifier remains compatible with qualification-era v1 bundles.
- `ops/research/import-capture-snapshot.sh` verifies a downloaded dump/checksum/manifest set and
  operator source/universe expectations, creates only a new `qtrad_research_*` database, restores
  without source grants and writes hash-verified import evidence. Existing targets fail closed and a
  failed invocation removes only the database it created.
- Research export can verify that import evidence against its configured database, capture source
  and universe, then includes the source archive/import identity in the content-authenticated
  Parquet manifest. This remains local and undeployed; no collector database, Object Storage object
  or IG endpoint was accessed.
- GitHub CI run `29324650522` passed formatting, Ruff, Pyright, `ty`, both capture and research
  ShellCheck sets, migrations through `0006` and all 216 tests against PostgreSQL 18 for branch head
  `4f366d2`. It covers backup v1/v2 restore compatibility, v1/v2 import, failed-import cleanup,
  evidence tampering and manifest provenance binding.
