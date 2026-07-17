# Current status

**Updated:** 2026-07-17
**Current milestone:** capture operations release
**State:** IN PROGRESS — `capture-v1` window complete; failed loss gate and closure evidence pending

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
- Dev Container startup now stops stale Codex Remote Control daemon state before starting
  a fresh daemon after the development database migration. Its host identity and device
  pairings remain in the persistent container-local Codex named volume across ordinary
  rebuilds.
- The Dev Container retains one npm-installed latest Codex CLI as its image bootstrap;
  Remote Control owns the standalone runtime in persistent Codex state, and the separate
  pnpm-managed Tilth dependency tree no longer installs a redundant Codex package.
- The application and Dev Container use Debian Trixie base images. The Dev Container
  copies the host's global Codex guidance without copying credentials or other Codex
  state.
- The Dev Container includes the compiler, database client, SSH, IPv6, DNS, socket,
  packet, file and shell diagnostics needed for local and OCI operations. It also includes
  the Docker CLI and Compose plugin for offline configuration validation. Its only host
  bind mount is the q-trad repository; it does not mount the host Docker socket, home
  directory, SSH agent or WSL/cloud filesystem.
- The Dev Container intentionally supports IPv4 rather than public IPv6 egress under WSL
  mirrored networking. Collector administration uses the WSL host's authenticated
  Tailscale client; an unprivileged gate must complete a non-interactive Tailscale SSH
  command as `opc` before Dev Container startup. Docker contains no Tailscale state, TUN
  device or network capability.
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
- Dev Container image: rebuilt; Codex CLI `0.144.5` matches npm's
  `@openai/codex` `latest` dist-tag.
- Dev Container Codex Remote Control: stable CLI `0.144.5` reproduced stale PID-managed
  state after container recreation; `stop` classified it as `notRunning`, cleared it and
  allowed a fresh daemon to bootstrap with the same persistent environment identity.
- Dev Container Codex installation boundary: package and Dockerfile regression coverage
  confirms one npm image bootstrap plus Remote Control's persistent managed runtime.
- Dev Container MCP configuration: Tilth `0.9.0` and Context7 enabled after the rebuild.
- Dev Container static gates after the rebuild: Ruff, Pyright and `ty` passed.
- Dev Container networking: the IPv4-only Compose configuration validates. The
  unprivileged gate resolves the collector's full MagicDNS name and completes an
  authorised Tailscale SSH command through the WSL host; IPv4 internet and PostgreSQL
  service discovery pass.
- Dev Container Docker tooling: Debian Trixie's Docker CLI and Compose plugin validate
  the merged Compose configuration without a Docker socket or daemon access. The running
  container has the packages installed; the Dockerfile change takes effect on the next
  Dev Container rebuild.
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

- Local database verification no longer shares the persistent interactive `db`. ADR 0024 adds a
  separate pinned PostgreSQL 18 `test-db` on tmpfs and a guarded complete-verification helper that
  mirrors the CI migration sequence in a fresh `qtrad_test_*` database. The Dev Container continues
  to keep the host Docker socket outside its isolation boundary. The helper passed the `0003`
  compatibility gate, migration through `0009` and all 298 tests, then removed its database. The new
  persistent `qtrad_dev` is at `0009`; the older ambiguous `qtrad` database was preserved but is no
  longer selected by development or tests.

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
- A read-only host audit at `2026-07-17T03:34:29Z` confirmed enabled, active Chrony synchronisation
  against OCI's link-local `169.254.169.254` service, normal leap state, complete recent reachability
  and sub-millisecond error. The current watcher does not publish clock health. A future hardening
  slice should add source-online, synchronised-state, leap-state and absolute-offset metrics/alarms;
  no running-host change is required for the present healthy configuration.
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
- PR 1 merged exact reviewed head `bd053b48d5e15c6ff5455387838c83bed3c001d8` as merge commit
  `08c0e5b99a83cdafe1a53bfc8e2afb8c01850fc6`. The operator confirmed the resulting main-branch CI
  passed; its only message was the standing non-blocking Node.js 20 deprecation warning. The
  publication job still enforces `refs/heads/main`, and publication remains a separate protected
  action. No image from the merged work has been published and the collector remains frozen.
- Operational evidence safety is now durable repository guidance: collector observation defaults
  to read-only, qualification mutations must be protocol-bound, legacy raw/canonical history cannot
  be selectively rewritten or deleted, guarded helpers require immutable images, secrets must not
  be rendered, and control-plane writes require independent read-back.
- The repo-scoped `qtrad-capture-ops` skill routes collector work through WP8, status, the relevant
  runbook heading and accepted ADR before classifying observation, evidence, publication,
  deployment or control-plane work. Fresh read-only Codex invocations discovered the skill,
  refused a changed-field deployment during active qualification and permitted a bounded health
  and message-rate observation without claiming unobserved evidence.
- Dev Container setup now configures Context7, GitHub and Tilth sequentially after the base Codex
  configuration, verifies their non-secret identities, and fails on missing credential environment.
    Context7 uses Codex's `env_vars` allow-list to forward its inherited environment variable instead
    of storing the key in a command argument that `codex mcp get/list` can render. The generated
    registrations passed identity checks, and a fresh `codex exec` process successfully resolved a
    Context7 library;
  skill validation, Dev Container JSON validation, formatting, Ruff, Pyright, `ty` and all 275 tests
  pass against a disposable PostgreSQL database migrated through `0009`. GitHub CI run
  `29383000650` passed the full pull-request gate at implementation head `cb68265`.
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
- A second read-only checkpoint at `2026-07-14T15:26:37Z` found seven fresh subscriptions,
  `READY` adapter state, exact global/checkpoint catch-up at `725684`, zero reconnects, drops,
  provider operations and gaps, healthy API/PostgreSQL containers, and successful backup and
  healthwatch results. Five pre-candidate database rows remain `RUNNING` beside the single actual
  candidate run; the host still has only one ingestion container.
- The first daily backup after hour 24 completed successfully at `2026-07-15T03:30:40Z` while
  readiness remained exactly caught up for all seven instruments. Independent Object Storage
  read-back found the expected dump, checksum and manifest; the dump was `195589108` bytes.
  The much larger host-interface transmit counter is iSCSI database traffic rather than backup or
  public-internet egress: a 15-second sample matched `4789149` VNIC transmit bytes with `4669440`
  writes to the dedicated block device. The runbook now requires VNIC and block-volume correlation
  before alerting on apparent egress.
- A local, undeployed reconciliation path now closes that known evidence issue after the frozen
  window. `runs reconcile-plan` writes a bounded self-hashed complete-set plan for one configuration
  strictly before the candidate cutoff. Hash-confirmed execution rechecks the capture source,
  database, universe, immutable tool image and every target under lock, then atomically records only
  those abandoned rows
  as `FAILED` with the cutoff labelled an operator-asserted upper bound. It refuses a partial or
  changed set and cannot include the current run or alter raw/canonical data. The guarded helper uses
  an immutable one-shot image without starting dependencies. GitHub CI run `29346869695` passed every
  static and shell gate, migration through `0009` and all 275 tests against PostgreSQL 18 at branch
  head `5ba0b07`; the collector remains unchanged.
- Restricted direct IPv6 SSH remains the primary recovery route and policy-constrained
  Tailscale SSH is the Dev Container's normal administration route. Direct IPv6 from the Dev
  Container has been retired because Docker/WSL forwarding repeatedly caused instability; the
  operator's WSL host retains working restricted IPv6 as an independently routed fallback.
  Bastion availability and ongoing OCI/Beszel threshold tuning no longer gate data collection,
  but remain operator hardening follow-ups.
- The operator has independently verified the OCI Service Gateway path and successful access to
  other Ksplice endpoints from the collector. The remaining repository-specific Ksplice failure is
  being escalated to Oracle support and remains a non-blocking host-hardening follow-up rather than
  a capture qualification failure.
- A belated 48-hour read-only checkpoint at `2026-07-16T13:30:16Z` occurred 58.4 hours after the
  candidate boundary, with 13 hours 35 minutes remaining. The capture service, ingestion/API/
  PostgreSQL containers and the actual `qtrad-backup`, `qtrad-healthwatch` and
  `qtrad-restore-verify` timers remained present; the daily backup completed successfully at
  `2026-07-16T03:31:07Z`, restore evidence remained valid at migration `0003`, and the database
  volume was 7% used.
- That checkpoint exposed a material candidate failure condition. Lightstreamer queue saturation
  began at `2026-07-16T12:57:15Z`; at `2026-07-16T13:30:17Z` the adapter was `DEGRADED`, readiness
  returned HTTP 503, all seven quotes were stale and 17,439 records had been dropped. At
  `2026-07-16T13:30:57Z` the adapter status had returned to `HEALTHY` and projections remained
  caught up, but readiness was still HTTP 503 with zero fresh quotes and the cumulative drop count
  had reached 17,985. Ingestion was using about 86% CPU but only 1.8 GiB of 10.6 GiB available
  memory; no container had restarted.
- The read model contained 39 observed gaps at the checkpoint, all retained for the required
  post-window review. Queue saturation and dropped records mean the candidate cannot currently
  satisfy the no-unexplained-loss acceptance gate. Collection remains untouched so automatic
  closure, log preservation and historical corroboration can describe the complete failure
  honestly; no restart, migration, reconciliation or other collector mutation was performed.
- At the exact post-boundary checkpoint (`2026-07-17T03:05:54Z`), the collector had recovered to
  HTTP 200 readiness with seven fresh instruments and exact projection catch-up. All containers and
  timers remained active, the database volume was 8% used and backup/restore evidence was valid.
  Recovery does not erase the run's 22,029 dropped records, one reconnect or 70 retained gaps; the
  no-unexplained-loss gate has failed even though formal hash-bound closure remains pending.
- Read-only database and log analysis isolated the overload interval. Accepted input climbed from
  roughly 9 records/s to a five-minute peak of 31.92 records/s. Canonical persistence p50 rose from
  about 5 ms to more than eight minutes; queue drops began at `2026-07-16T12:57:15Z`, ended at
  `13:36:11Z`, and latency returned to milliseconds around `14:20Z`. The interval produced 208,574
  quote events, 339,336 bar corrections and 3,780 bar closes.
- The implementation advanced the five-second bar watermark with processing wall time after every
  dequeued record. Once database work lagged transport, the bar builder closed queued records'
  minutes prematurely and each later-processed quote emitted further durable corrections. That
  feedback multiplied database transactions and physical writes until the 10,000-record queue
  overflowed. The same loop also persisted adapter health after every record and logged every drop.
  - A local undeployed corrective branch now advances bars from each record's transport receive time,
    keeps any run-level queue loss visibly `DEGRADED`, rate-limits overflow logs, reports queue current/
    high-water occupancy and first/last drop receive times, and persists health only on state changes or
    once per second. Formatting,
    Ruff, Pyright, `ty` and all 283 database-independent tests pass. The configured Dev Container
    database is intentionally not migrated in place, so the isolated PostgreSQL integration gate
    remains delegated to CI. The running collector has not changed.
  - None of the 70 retained gap intervals overlaps the `12:57:15Z`–`13:36:11Z` queue-loss interval.
    This is expected: the gap projection records observed quote silence, whereas a full internal queue
    discards callbacks before canonical processing. Qualification independently requires
    `dropped_records=0`, so later IG historical corroboration of genuine market gaps cannot hide or
    remediate this run's internal loss.
  - Read-only gap review found durations of 121–385 seconds. Sixty-nine of 70 intervals begin between
    `20:00Z` and `21:59Z` across 14–16 July; the sole exception is an FTSE 100 interval beginning at
    `2026-07-16T04:25:07.986Z`. This repeated shape is evidence for a provider/session-cycle hypothesis,
    not a classification. The post-closure reviewed historical plan must query all 70 intervals within
    evidenced quota and record whether provider bars exist.
  - The retained ingestion log still spans the candidate start and contains 22,159 bounded records,
    including exactly 22,029 `ig_queue_saturated` events. This independently corroborates the database
    health total and confirms that the formal non-overwriting log bundle can still preserve the complete
    loss sequence; it has not yet been written.
  - The first daily backup after the boundary ran on schedule from `2026-07-17T03:30:04Z` to
    `03:31:41Z` and exited successfully while collector readiness remained HTTP 200. This is useful
    continuing-operations evidence, but does not change the failed no-loss qualification result.
- The first post-window reconciliation plan correctly found the five expected stale runs but was
    not executed because its effective capture source was `local-development`, conflicting with the
    runbook's assumed `oci-sydney-capture-1`. The deployed environment had omitted the explicit source
    variable, so the validated application default is the truthful identity already used by this
  canonical store and its backups. Documentation now preserves that identity rather than relabelling
  existing history.
  - After that identity correction, hash-confirmed reconciliation atomically closed exactly the five
    reviewed rows as `FAILED`; read-back found one current ingestion run and no raw/canonical mutation.
    The first immutable automatic qualification snapshot at `2026-07-17T04:37:33Z` correctly failed
    the no-drop gate with 22,029 dropped records and retained all 70 candidate gaps.
  - That snapshot also exposed two non-substantive closure-check defects. The ledger had recorded the
    later checkout's descriptor hash rather than the active descriptor from frozen commit `89c7553`,
    and the mount assertion rejected the valid `/dev/sdb` XFS filesystem because systemd automount
    adds a same-target `autofs` row. The active descriptor exactly matches its frozen commit at SHA-256
    `a95e53c3f7bec61ebc11126484ad61ad71828f542727c72a3d9654d88541c57d`; a local correction now
    requires exactly one read/write XFS backing filesystem while allowing only same-target XFS/autofs
    records. The original snapshot is preserved rather than overwritten.
  - The non-overwriting log bundle was captured before any lifecycle operation and independently
    verified at manifest SHA-256
    `093f5267d9ae8a6acd838180d84a60fcb0c2a995d3f8436fa30c991bd3047b43`.
  - The corrected numbered snapshot now fails only `adapter_ok`, as intended, and its bound log bundle
    verifies independently. A subsequent online backup produced a checksum-verified post-evidence
    snapshot, which was restored into the isolated local `qtrad_research_capture_20260717` database at
    migration `0003`; its immutable import evidence binds 3,047,086 raw messages and 3,478,536
    canonical events. The collector remained running and was not migrated.
  - That first real local import exposed a psql integration bug hidden by the command mock: psql does
    not expand variables embedded in `--command`. The database-existence guard now feeds its
    parameterised query on stdin, and both focused tests and the real 575 MB snapshot import pass.
  - Formal closure then exposed that the finaliser could write an operator-review failure but rejected
    an automatic failure before writing any decision. The local correction verifies consistency of
    the individual/aggregate automatic checks, preserves their actual value and emits `PASS` only when
    both automatic and operator gates pass. Regression coverage proves a failed automatic snapshot is
    retained as a final `FAIL` artifact.
    - The original post-gap planner failed closed before IG access because its common rectangular range
      requests 20,741 points against IG's documented 10,000-point weekly allowance. A read-only union of
      the 70 minute-aligned gaps yields 56 instrument/range spans totalling only 267 points. The next
      local slice replaces the quota-wasteful rectangle with a hash-bound sparse plan set; quota evidence
      will not be fabricated and the collector remains untouched.
    - PR 9 merged the hash-bound sparse planner, exact set register/execute boundary, migration `0010`,
      zero-result request evidence and append-only per-request quota ledger after PostgreSQL 18 CI passed
      all 307 tests. The final set hash `20d8bd4a116499f2240b5b0e78a76c7a8f84fd0cb209dcdc1604507b14599474`
      binds 56 plans, all 70 unique gaps, 267 requested points, the verified snapshot import and a
      2,000-point reserve.
    - The first execution completed 27 requests/135 points before the 28th rapid request raised
      `ApiExceededException`; IG still reported 9,865 weekly historical points. The active plan and run
      failed closed, and the usage ledger retained 27 completed attempts plus one incomplete rate-limited
      attempt. Context7-backed `trading-ig` guidance confirmed that historical requests require separate
      pacing. PR 10 added a conservative three-second adapter-boundary interval and passed full CI.
    - The exact set then resumed without replacement or duplicate completion. All 56 plans completed,
      the ledger records 267 successful requested/returned points plus the retained incomplete attempt,
      and IG's final provider-reported weekly allowance is 9,733. Research manifest
      `5289530e6b5d946c626593f74eda8d14774d1454774fff666c9c313a9946565d` binds 62,175 exported bars.
      Offline v2 artifact `ce5c6e1c2fad69ba909067b67e5c4409a888379af3cb108204aa28df0c273d89`
      reports `HISTORICAL_DATA_PRESENT` for all 70 live gaps and complete coverage for all 210 basis
      results (834/834 expected basis-minute intervals). This is evidence for deeper streaming/session
      investigation, not proof of streaming emission and not remediation of the failed no-drop gate.
  - The failed candidate is now formally closed by final evidence
    `d7bcd88e3179aca9eda89673f14383d6525bcd92e602462c6c56815892fb5c3f`. It binds the corrected
    automatic snapshot, operator review, all 70 `UNEXPLAINED` gaps and the 22,029 dropped records;
    qualification decision is `FAIL` and no expansion is admitted.
  - The previously published overload-corrected ARM image was then deployed under reviewed descriptor
    commit `807a967` after the verified post-evidence backup. The collector stopped cleanly, applied
    expand-only migrations from `0003` through `0009`, and started a new seven-instrument run on digest
    `sha256:cb9d8efa9951daea91269e596c798c85fa262ab7100d93050025461eecb363ee`.
    Read-back at `2026-07-17T05:02Z` found HTTP 200 readiness, seven fresh subscriptions, exact
    projection catch-up, zero reconnects/drops/provider operations and queue high-water 7/10,000.
    This begins corrected `capture-v1` measurement; `capture-v2` and 40-instrument stress remain
    separate later stages.
  - A read-only analysis of the verified failed-candidate snapshot found no raw callback for the
    affected item inside any of the 70 gaps. Every exact interval nevertheless contained 35–723
    canonical quotes from two to six other subscriptions on the same Lightstreamer connection, and
    retained lifecycle logs contain no gap-time transport or subscription failure. Historical MID
    prices move in every interval across three to eight minute bars; none is flat. The remaining
    uncertainty is IG demo per-item stream suppression versus SDK/subscription delivery.
  - A local undeployed continuity-evidence slice records subscription establishment/end,
    server-applied real frequency, bounded server errors and SDK-reported lost updates. Renewal
    invalidates prior item state and loss is sticky degraded health. Idempotent REST reads additionally
    perform at most one serialised v2 invalid-token reauthentication/replay. Each session creation now
    validates exactly one current-key client-app entry and retains only its numeric published allowances
    plus the pinned library's published-minus-two effective rates; missing, duplicate, malformed or
    mismatched evidence fails closed without retaining API-key material. Authoritative allowance errors
    are retained without retrying them.
      The earlier 42 focused lifecycle tests and complete 317-test isolated PostgreSQL gate passed.
      Proposed ADR 0025 now adds the IG application heartbeat as distinct whole-connection evidence,
      requires it for readiness and moves changed-field state mutation onto the event-loop side of the
      provider callback boundary. Forty-six focused lifecycle tests pass. It does not treat heartbeat as
      session renewal or proof of a PRICE emission.
    - `trading-ig` 0.0.24 pins superseded Lightstreamer 1.0.3. The maintained 2.2.2 API passes q-trad's
      used-surface probe and 83 adapter/CLI tests under a local uv override; its official 2.1.0 changelog
      specifically records higher update-rate performance. Provider compatibility remains unproven and
      this lock is not deployed.
      - The new isolated load helper produced self-hashed PASS evidence for 2,000 callbacks at 200/s over
        40 subscriptions with zero loss. A 5 ms injected persistence stall reached queue high-water
        799/10,000, p95 lag 6.58 seconds and maximum lag 6.91 seconds before complete drain. The five-minute
        200/s profile passed all 60,000 callbacks with zero loss, queue high-water 51/10,000, p95 lag
        4.33 ms and maximum lag 257 ms. An all-item renewal at load separately passed 2,000 callbacks and
        recorded 40 renewal events with complete post-renewal state. Provider-backed connection/recovery
        faults remain outstanding.
      - Ingestion health persistence now runs on its own periodic task rather than only after a market
        record. Whole-stream silence can therefore persist heartbeat, lifecycle and failure evidence even
        when no PRICE callback arrives.
          The current complete gate passes formatting, Ruff, Pyright, `ty`, ShellCheck, frozen-schema
            compatibility, all migrations and all 345 tests.
    `docs/STREAMING_CONTINUITY_INVESTIGATION.md` defines the
    endurance and synthetic-stress gates plus a same-connection PRICE-versus-CHART:TICK contrast.
        The latter uses 15 of IG's published 40 subscriptions including heartbeat and avoids the prohibited
        second connection. Its guarded local harness now records compact hash-bound callback and lifecycle
        evidence, requires all channels data-ready, and fails on loss/discrepancy or incomplete stream,
        REST HTTP-session or bounded-worker teardown. It cannot run without an exact collector-stopped
        acknowledgement and remains unexecuted while the corrected collector measurement is active;
      no collector mutation occurred.
    - A second guarded provider-recovery harness now uses the production adapter without a database.
      It requires initial PRICE/heartbeat/frequency readiness, terminates the actual Lightstreamer
      client, verifies automatic recovery with fresh records from all instruments, then injects a
      fixed invalid local REST token and requires one bounded reauthentication/replay plus another
      complete stream generation. Exact reconnect and reauthentication counts, zero loss/errors and
      complete adapter/consumer/provider-thread cleanup fail closed. Each ready phase requires the
      positive effective trading/non-trading rates obtained from the current demo login, and sticky
      abandoned-provider-operation state fails shutdown. It remains unexecuted while the corrected
      collector owns the API key.
    - Provider experiment evidence now has an independent offline verifier. It recomputes either
      manifest self-hash and, for contrast evidence, confines the adjacent event path, parses every
      gzip JSON-lines record and recomputes increasing sequence, count and uncompressed SHA-256.
      Schema v1 requires the complete experiment-specific check set and exact PASS equivalence;
      verification preserves rather than masks a valid failure artifact.
    - The continuity protocol now prevents evidence substitution: the current old-lock run measures
      the overload correction through its recurrent windows/weekend; guarded provider probes run only
      after its approved stop and during active markets; ADR 0025 remains Proposed until both pass;
      and the resulting immutable ARM image then requires its own fresh 72-hour `capture-v1`
      endurance before `capture-v2` can be admitted.
    - A bounded read-only corrected-run checkpoint at `2026-07-17T12:01:46Z` found one current
      ingestion run, HTTP 200 readiness, 7/7 subscriptions, exact projection catch-up, zero drops or
      reconnects and queue high-water 10/10,000. The gap endpoint still contained exactly the 70
      failed-candidate gaps; none began after the corrected run started at `2026-07-17T05:02:02Z`.
      This run had not yet crossed the recurrent `20:00Z`–`22:00Z` window, so the checkpoint is not
      endurance or causal closure. Host NTP remained synchronised. No mutation occurred.
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
- `main` now contains the next offline gate: `qtrad instruments review` enumerates
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
- PR 1 merged this preparation after isolated PostgreSQL 18 CI passed formatting, linting, typing,
  shell checks, migration and the full test job. The resulting main-branch CI also passed; no image
  was published and the frozen OCI release was not changed.
- A branch-wide release audit tightened two fail-closed boundaries without contacting IG or the
  collector: listing review now has global search/detail request budgets and discovery no longer
  invents missing minimum-size economics. API readiness additionally requires the running
  ingestion record to match the served capture-universe configuration hash.
- The future feed-capable Compose descriptor now fails configuration unless the operator sets
  a stable, non-secret capture-source ID. This code is not deployed; the running collector's
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
  - Listing validation for a new non-streaming universe is now an explicit local command boundary.
    `instruments sync --universe PATH` supplies the reviewed universe to the adapter and persists its
    validation evidence without changing the environment-selected ingestion universe or starting a
    stream. The documented path uses the same isolated writable database as candidate backfill.
    GitHub CI run `29336513376` passed all static, shell, migration and PostgreSQL 18 gates with 244
    tests at branch head `640caf7`.
    - The subsequent identity audit found that ADR 0009's projection still treated the provider epic,
      rather than provider/environment/instrument, as effective-selection identity. Local candidate code
      now commits the validation event and projection atomically, closes a superseded epic at the new
      event time, and rebuilds event-backed listings from canonical history. Migration `0008` adds the
      one-open-selection constraint and intentionally fails if pre-existing ambiguity needs review.
      The first PostgreSQL run exposed an integration fixture that added an eighth operator-visible
      instrument; the isolated correction retained the canonical seven. GitHub CI run `29338222506`
      then passed migration through `0008` and all 245 tests at branch head `5467017`.
    - The persistence-identity audit is complete locally without changing the collector. Migration
      `0009` validates lower-case SHA-256 configuration/universe identity on runs, research manifests,
      backfill plans and non-null provider listings; it also validates each backfill plan hash. Run and
      listing boundaries now reject malformed values before database access. Nullable listing identity
      remains confined to legacy pre-event rows. A read-only collector query found zero malformed values
      across 11 runs and seven listings. GitHub CI run `29339570739` passed migration through `0009`,
      direct constraint rejection and all 251 tests against PostgreSQL 18 at branch head `7437258`.
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
  bounded form passed. This implementation remains undeployed during collector qualification.
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
- A local storage-evidence audit found that image identity alone could not prove which raw writer
  representations were added during a measured interval. Snapshot schema version 3 now remains
  backward-readable with versions 1 and 2 while binding pre-marker/coded schema state and exact
  per-code counts. Comparison reports whether all new rows are `CHANGED_FIELDS`, exposes new
  `LEGACY_UNCLASSIFIED` rollback-compatible rows and rejects a representation-schema transition. The
  active collector and its qualification image remain unchanged. GitHub CI run `29341678396` passed
  the real PostgreSQL representation probe, migration through `0009` and all 255 tests at branch head
  `6a84319`.
- Storage comparison evidence is now durable locally rather than unhashed stdout. The compare command
  writes one non-overwriting artifact binding both snapshot hashes and release identity; the new
  offline contrast command requires distinct digest-pinned images, matching source/configuration,
  passed automated thresholds and an all-`CHANGED_FIELDS` candidate before calculating mechanical
  per-message changes. Contrast explicitly leaves both active-market reviews required and records no
  accepted storage decision. The collector remains unchanged. GitHub CI run `29343411122` passed
  migration through `0009`, every artifact/contrast case and all 258 tests against PostgreSQL 18 at
  branch head `8ff48cd`.
- Active-market storage review is now durable local evidence rather than an unresolved prose gate.
  A bounded operator input targets one exact comparison hash; `storage review` produces a
  non-overwriting assertion carrying the comparison's source, configuration, image and interval.
  `storage qualify` binds both assertions to their exact contrast, preserves a valid negative
  `FAIL`, and leaves `storage_decision_accepted=false` even on `PASS`. Rehashed semantic tampering,
  mismatched comparison/release identity and premature reviews fail closed. The running collector
  is unchanged. GitHub CI run `29344877350` passed every static and shell gate, migration through
  `0009` and all 264 tests against PostgreSQL 18 at branch head `961d9d5`.
- GitHub CI run `29326917893` passed formatting, Ruff, Pyright, `ty`, ShellCheck, migrations through
  `0006` and all 224 tests against PostgreSQL 18 at branch head `9189f21`.
- Storage comparison now reports observed raw-message, canonical-event and raw/canonical relation
  byte rates, plus explicitly mechanical combined-relation scenarios over one, 30 and 365 days.
  The output binds its representative-threshold status and does not label those scenarios forecasts.
- GitHub CI run `29327187828` passed formatting, Ruff, Pyright, `ty`, ShellCheck, migrations through
  `0006` and all 224 tests against PostgreSQL 18 at branch head `448250e`.
- `ops/capture/storage-snapshot.sh` now makes the deferred baseline procedure reproducible. It
  accepts only an already-local immutable digest, runs one non-root inspector container with
  `--no-deps --pull never`, refuses unsafe labels or existing output and restores evidence to
  root-only ownership. It remains undeployed during the active qualification.
- GitHub CI run `29327683523` passed formatting, Ruff, Pyright, `ty`, all capture/research ShellCheck
  gates, migrations through `0006` and all 228 tests against PostgreSQL 18 at branch head `fad5211`.
- ADR 0020 and migration `0007` now distinguish raw payload representations with a compact stable
  code. Existing rows receive a constant fast default without an `UPDATE`; new IG changed-field and
  fixture records are explicit, while rollback writers remain conservatively unclassified. No raw
  payload, hash, deduplication key, canonical event or raw-record reference is rewritten.
- GitHub CI run `29332161781` passed formatting, Ruff, Pyright, `ty`, both ShellCheck sets, migration
  through `0007` and all 232 tests against PostgreSQL 18 at branch head `dfe45ea`. The database suite
  proves an old INSERT shape receives code zero and the check constraint rejects an unknown code.
- The local capture-feed foundation is complete and remains undeployed: the bounded API page,
  provider-neutral consumer, loopback-only probe and independently authenticated read-only database
  route all have deterministic or PostgreSQL-backed coverage. No cursor store or derived writer has
  been added during the data-only phase.
- The post-qualification storage comparison is now explicitly release-bound. The unchanged
  merged-state image and the later unchanged changed-field image each require their own accepted
  before/after interval; cross-image snapshots cannot be compared by weakening identity checks.
  Operational deletion or legacy-epoch archiving remains closed until both measurements exist.
  - Qualification closure is now reproducible locally without altering the frozen release. The new
  host helper refuses overwrite, binds the exact candidate/release/configuration and its own tool
  hash, and fails closed before 72 hours or on unhealthy readiness, lifecycle, backup/restore,
  migration, unit, container or disk evidence. Its output always remains pending the documented
    operator reviews; it has not been run against the collector. GitHub CI run `29334657157` passed
    ShellCheck, migration through `0007` and all 237 tests against PostgreSQL 18 at branch head
    `3415f70`.
    - A separate local qualification finaliser now verifies that automatic snapshot and binds bounded
      operator reviews of candidate gaps, full-window logs, monitoring and active-market
      representativeness. ADR 0021 and review/final schema v2 require explicit bounded evidence
      references per gap and add `EXPECTED_MARKET_INACTIVITY` only for same-generation continuity,
      spontaneous pre-recovery resumption and retained market/provider context; absent or ambiguous
      evidence remains `UNEXPLAINED` and cannot pass. The finaliser preserves a self-hashed `FAIL`,
      refuses overwrite and emits no decision for malformed, incomplete, mismatched or tampered input.
      It performs no external I/O and remains undeployed. The earlier v1 implementation passed GitHub
        CI run `29335826682` with all 243 tests at branch head `335f089`. The v2 implementation remains
        local and undeployed; formatting, Ruff, both type checkers, ShellCheck, focused script tests and
        all 279 tests pass against a disposable database migrated through `0009`.
      - Post-window gap diagnosis is now explicitly queued: every candidate gap will receive a
        reviewed, quota-bound IG demo historical query for its exact listing and UTC interval through
        an isolated writable database. Returned bars will be compared with immutable live evidence,
        but the separate historical path is corroboration only and cannot repair or conclusively
        classify a streaming gap.
        - The local ADR 0022 implementation makes that diagnosis reproducible without collector or IG
          access during the window. `qualification gap-history` verifies a post-evidence snapshot
          import, exact reviewed plan, completed BID/ASK/MID coverage, copied live-gap identity and
          research manifest/Parquet hashes before writing a self-hashed corroboration artifact. The
          actual historical request remains deferred until the frozen interval closes. Formatting,
          Ruff, both type checkers, ShellCheck and all 288 tests pass locally against a disposable
          database migrated through `0009`.
            - The local `qualification gap-plan` companion removes manual gap-range and instrument
              transcription. It verifies the automatic evidence and exact post-evidence snapshot
              database/source/universe, requires the repository's current Alembic head, then delegates
              the derived minute-aligned range and sorted instruments to the existing plan writer.
              Allowance entry and explicit reviewed hash confirmation remain operator gates.
            - A local post-window log-evidence helper now verifies the automatic snapshot's self-hash,
              derives its exact interval and writes a root-only, non-overwriting bundle of filtered
              container identities and byte-bounded Docker/systemd logs. The manifest binds every file,
              the helper and the automatic evidence; it excludes container environments and rendered
              Compose configuration and cannot perform the operator review. Its independent offline
              verifier fails on automatic-evidence, schema, exact-file-set, ownership/mode, content,
              timestamp or image drift and returns only the authenticated manifest hash. A read-only remote
              audit found the current retained logs far below their effective five-by-10-MiB rotation
              capacity, so the frozen collector was not changed.
            - The hour-24 audit exposed two local closure-tool assumptions before hour 72: OCI Compose
              returns newline-delimited `ps` JSON rather than one array, and the frozen image's readiness
              response predates the configuration-hash field. ADR 0023 normalises both supported Compose
              forms. For the exact frozen digest only, configuration identity is instead bound by exactly
                one total running ingestion row with the expected hash after reconciliation, plus the exact
                images/descriptor/source and all normal readiness gates. Later images still require endpoint
                identity. Formatting, Ruff, Pyright, `ty`, ShellCheck, 45 focused qualification tests and
                all 282 database-independent tests pass locally. The Dev Container has no Docker client, so
                the 13 PostgreSQL integration tests remain an explicit pull-request CI gate. No collector
                process or configuration changed.
              - The post-window artifact audit found that the newer reconciliation image had only been tested
                on migration head even though it must operate against the frozen collector at migration
                `0003`. Pull-request CI now stops a fresh PostgreSQL 18 database at `0003`, runs the exact
                reconciliation integration test, then upgrades that database to head for the full suite. The
                pull-request and main-branch gates passed before and after PR 1 merged exact reviewed head
                `bd053b48d5e15c6ff5455387838c83bed3c001d8` as
                `08c0e5b99a83cdafe1a53bfc8e2afb8c01850fc6`. The GitHub MCP's check-runs path remains
                unavailable to its fine-grained PAT, so the operator independently confirmed the green
                main-branch run. The utility image remains unpublished and the collector remains unchanged.
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
