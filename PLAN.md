# q-trad data foundation implementation plan

**Status:** DATA FOUNDATION QUALIFIED
**Phase boundary:** data ingestion, audit, normalisation, replay and health visibility  
**Explicitly excluded:** strategies, allocation, risk, paper execution, P&L, live orders and IBKR

## Objective

Deliver a deterministic, inspectable data path:

> IG demo data → raw audit record → canonical quote event → derived one-minute bars → PostgreSQL → Parquet → replay → read-only operator console.

The fixed universe is AUD/USD, EUR/USD, USD/JPY, GBP/USD, Australia 200, US 500 and FTSE 100.

## Future-facing research record

`RESEARCH-INTRADAY-STRATEGY.md` records a public-literature survey and prioritised
research backlog for a later strategy phase. It does not admit strategy, allocation,
risk, paper-execution or order implementation into the current data-only phase. This is a preliminary
investigation and should not be treated as comprehensive.

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
| WP4 — IG demo adapter | DONE | reconnect/refresh/backoff and failure handling verified |
| WP5 — backfill and research data | DONE | live backfill, quota, export and replay verified |
| WP6 — API and operator console | DONE | API and rendered console returned HTTP 200 |
| WP7 — failure testing and soak | DONE | remediation implemented; preliminary lifecycle gates and fresh 24-hour seven-instrument soak passed |
| WP8 — capture operations release | IN PROGRESS | versioned capture-v1 configuration, collector deployment assets and cloud qualification pending |

## WP8 — capture operations release

- Keep collection data-only and start the OCI host on the qualified seven-instrument
  `capture-v1` universe.
- Make universe identity part of run, listing-validation, backfill and export evidence.
- Persist bounded listing economics and event-backed effective listing validation.
- Provide planned historical coverage/backfill, a machine-readable collector readiness
  contract, immutable multi-architecture releases, backup/restore and OCI monitoring
  runbooks.
- Require 72-hour cloud qualification before a separately validated 20-instrument universe
  is admitted.

Implementation status:

- Complete: hashed `capture-v1`, ADR 0009 listing events/economics, planned historical
  coverage/backfill, readiness contract, immutable Compose deployment and initial ARM64
  bounded-cloud qualification.
- Complete and staged on the disabled collector: daily manifest/checksum backups, weekly
  isolated restore verification, backup/restore/disk/readiness OCI metrics, and deterministic
  operations-script tests.
- Complete: GitHub push/PR CI and manual commit-tagged `linux/amd64`/`linux/arm64`
  OCIR publication workflows. Registry credentials and the protected release environment
  require operator configuration before the first workflow dispatch.
- Complete: the private backup bucket, instance-principal policy and both non-secret operations
  environment files are installed. Bucket lifecycle, upload, isolated restore and custom-metric
  publication pass. OCI/Beszel alarm thresholds remain an operator tuning activity rather than a
  collection gate.
- Complete: the dedicated GitHub publisher has capture-compartment `manage repos`; the
  workflow published and the ARM host pulled the attested dual-architecture OCI index by
  immutable digest. The prior digest is retained as the application rollback target.
- Complete: the pinned ARM release applied migrations, reached all-seven readiness with an
  exactly caught-up projection, published a fully healthy OCI metric set, and stopped
  cleanly under systemd/Compose supervision.
- Complete: digest rollback reached all-seven readiness on both the saved prior image and
  the restored current image without rolling back schema or canonical data. Clean host
  reboot recovered Tailscale, Docker, Beszel, the release and XFS database mount; the
  post-reboot current image again reached caught-up all-seven readiness and stopped cleanly.
- In progress: the operations timers and unattended `capture-v1` collector are enabled for
  the 72-hour qualification ending no earlier than `2026-07-17T03:05:33Z`. Direct SSH is
  restricted at OCI to the operator's IPv6 range and Tailscale is the backup route; Bastion
  enablement and final alarm tuning are tracked improvements rather than release gates.
    The corrected candidate has passed deliberate container restart and host reboot recovery.
    `capture-v2` remains excluded.
- Complete locally and undeployed: ADR 0018 replaces repeated merged Lightstreamer raw payloads
  with changed-field deltas, including explicit-null semantics, while canonical quotes continue
  from bounded per-generation state. Hash-verified `storage snapshot` and offline `storage
  compare` commands measure database/relation/index growth per raw message. This candidate is not
  introduced into the running 72-hour qualification. GitHub CI run `29322668979` passed all static,
  migration and PostgreSQL 18 gates with 211 tests at branch head `e24ccc7`.
- Complete locally and undeployed: storage snapshot schema version 2 remains backward-readable and
  adds JSON-text comparison plus per-index byte/scan deltas. The storage audit preserves raw and
  canonical facts and all correctness indexes, and makes secondary-index, payload-representation
  and hash-width changes conditional on representative measurements rather than estimates. GitHub
  CI run `29325936926` passed all static, migration and PostgreSQL 18 gates with 218 tests at branch
  head `1aefd62`.
- Complete locally and undeployed: offline storage comparison attributes raw, canonical and combined
  growth to heap, indexes and auxiliary PostgreSQL allocation, normalised both per raw message and
  per new relation row. It reports the canonical/raw row ratio so the observed headline growth is
  not incorrectly assumed to represent exactly one canonical event per provider update. GitHub CI
  run `29326440612` passed all static, migration and PostgreSQL 18 gates with 219 tests at branch
  head `246855e`.
- Complete locally and undeployed: comparison now rejects release/source/database identity drift and
  emits an automated evidence gate requiring both six elapsed hours and 100,000 new raw messages.
  Index-scan evidence additionally requires unchanged PostgreSQL statistics; representative
  active-market conditions remain an explicit operator review. GitHub CI run `29326917893` passed
  all static, migration and PostgreSQL 18 gates with 224 tests at branch head `9189f21`.
- Complete locally and undeployed: comparison converts the observed interval into raw/canonical and
  retained-relation rates plus explicitly mechanical combined-storage scenarios for one, 30 and 365
  days. These remain labelled interval extrapolations rather than forecasts.
- In progress locally without collector deployment: ADR 0014 and the bounded zero-copy
  canonical-event feed prepare the later isolated paper/research boundary. This remains a
  read-only data-foundation interface and makes no IG call.
- Complete locally: the provider-neutral feed consumer contract strictly decodes canonical event
  pages, pins source/universe/configuration/schema identity and validates exact cursor continuation
  and monotonic high-water evidence. The offline `feed verify` command accepts saved pages only.
  Serving-universe changes require an explicit caught-up rebind on the same source; source or feed
  schema changes cannot reuse the cursor.
- Complete locally: ADR 0015 and `feed probe` add a bounded async client for one page through an
  operator-established literal-loopback tunnel. Redirects, ambient proxies, credentials,
  unexpected status/content, response growth, total duration, cursor mismatch and page overrun
  fail closed. The probe explicitly reports that its candidate cursor was not persisted; there is
  still no cursor database, derived writer or paper behaviour.
- Complete locally: ADR 0016, migration `0004` and the loopback-only PostgreSQL binding provide
  independently authenticated direct-read access without sharing collector credentials. The
  privilege role can select approved canonical/reference/read-model/operations tables, but cannot
  access raw capture or write; PostgreSQL CI proves the grants.
- Complete locally: the 20-instrument `capture-v2` candidate list is now a deterministic,
  hashable offline catalogue that deliberately contains no provider epics and cannot be
  loaded as an ingestion universe.
- Complete locally without provider or collector access: a bounded `instruments review` workflow
  enumerates relevant IG demo candidates, classifies fail-closed rejection reasons and emits a
  deterministic, non-overwriting manifest with `selection_authority=false`. It never chooses an
  epic, writes PostgreSQL or starts a stream. Fixture coverage retains multiple eligible listings
  for explicit operator review and excludes volatile snapshots and credentials.
- Complete locally: review discovery has global search/detail request budgets, and approved
  discovery rejects missing, zero or negative minimum-size economics instead of substituting a
  value. Collector readiness now requires a running ingestion record with the API's exact
  capture-universe configuration hash.
- Deferred qualification gate: do not invoke the review command against IG demo until the active
  `capture-v1` 72-hour window closes. Its eventual output is evidence for manual mapping review,
  not an approved `capture-v2` universe.
- Complete locally: explicit universe promotion verifies the manifest's canonical hash, exact
  catalogue identity and one manually selected eligible IG demo listing per instrument. It rejects
  stale, tampered, omitted, duplicate, unseen, reused or ineligible selections and deterministically
  renders a non-overwriting, undeployed TOML release bound to review and selection hashes.
- Complete locally without IG or collector access: historical backfill is now an explicit
  plan/review/register/execute state machine. A canonical plan binds its exact UTC range,
  universe hash, configured listing and effective version, resolution, chunks and quota evidence;
  registration requires the operator-confirmed SHA-256. Plan-scoped BID/ASK/MID coverage attempts
  remain separate from live gaps, repeated plans preserve independent evidence, and changed
    provider bars append corrections. The bounded read-only API exposes this projection.
- Complete locally without collector access: ADR 0017 and migration `0006` replace mutable
  content-prefix research manifests with schema-version-2 canonical manifest identity. Exports bind
  an explicit UTC range, the exact universe/configuration, application image/version, grouped
  coverage, provenance, live gaps and historical-coverage attempts; replay verifies the manifest,
  every file hash and decoded semantic content. Per-instrument/day content identity reuses unchanged
  partitions as the store grows. `bars-v2/` isolates new partitions from a rolled-back legacy
  exporter, while nullable forward columns preserve the prior application's INSERT contract.
- Complete on the feature branch: isolated GitHub CI passed formatting, linting, typing,
  shell validation, PostgreSQL 18 migration and the full feed/catalogue test suite. The
  draft PR remains unmerged and cannot deploy the collector.
- Complete locally and undeployed: ADR 0019 adds a versioned backup-v2 identity and a
  non-overwriting snapshot-to-research importer. It verifies source, universe, images, migration,
  archive and restored counts before producing hash-verified import evidence. Research export can
  require that evidence and binds it into the immutable manifest; no collector or OCI access was
  used while implementing this path. GitHub CI run `29324650522` passed all static, shell,
  migration and PostgreSQL 18 gates with 216 tests at branch head `4f366d2`.

## WP0 — documentation and scaffold

- Maintain `AGENTS.md`, README, architecture, engineering and status documents.
- Record modular-monolith, Docker, event-storage and bar decisions.
- Use Python 3.13, PostgreSQL 18, `uv`, Ruff, Pyright and pytest.
- Supply a secret-free Docker-first workflow.
- Supply a VS Code Dev Container that isolates Codex and development tooling from WSL
  without exposing the host Docker socket.

## WP1 — canonical domain and ports

- Implement the seven canonical instrument IDs and effective provider listings.
- Implement immutable quotes, bars, modes, health, gaps, runs and event envelopes.
- Define `Clock`, `RawCapture`, `EventStore`, `MarketDataAdapter` and `ResearchStore` ports.
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

- Create a non-overwriting canonical plan for explicit instruments and an exact UTC range.
- Bind the plan to the selected universe/configuration, exact effective listing versions,
  one-minute resolution, request chunks and timestamped operator quota evidence.
- Reserve 20% of the reported allowance and reject a plan whose exact range exceeds the
  remaining usable points; do not turn an allowance into an implicit "last N minutes" range.
- Require review and explicit hash confirmation before registration, and execute only the
  atomically claimed persisted plan without listing substitution.
- Keep historical-bar provenance and plan-scoped coverage distinct from quote-derived bars
  and observed live-stream gaps. Append changed provider history as a new correction revision.
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
python -m qtrad ingest --environment ig-demo --max-seconds 90 --force-reconnect-after-seconds 20
python -m qtrad backfill plan --universe PATH --start UTC --end UTC --remaining-allowance N --output PATH INSTRUMENT...
python -m qtrad backfill register --plan PATH --confirm-plan-hash SHA256
python -m qtrad backfill execute --plan-hash SHA256
python -m qtrad research export --universe PATH --start UTC --end UTC
python -m qtrad replay --manifest PATH
python -m qtrad projections rebuild
python -m qtrad feed verify --source-id SOURCE --universe-name UNIVERSE --configuration-hash HASH PAGE...
python -m qtrad feed probe --endpoint http://127.0.0.1:PORT --source-id SOURCE --universe-name UNIVERSE --configuration-hash HASH
python -m qtrad api
```

## Verification evidence

- Current feature-branch local gates: Ruff formatting/lint, Pyright and `ty` pass;
  195 tests pass with eight PostgreSQL migration/integration tests deferred to isolated CI.
- GitHub CI run `29316896861` passed all 194 tests against PostgreSQL 18 after applying
  migrations through `0005`, including exact plan/coverage identity, repeated coverage attempts,
  append-only historical corrections, live-gap isolation and the bounded read-only API.
- GitHub CI run `29320193656` passed formatting, linting, both strict type checkers, ShellCheck,
  migration through `0006` and all 203 tests against PostgreSQL 18. It proves schema-version-2
  manifest persistence, exact duplicate acceptance, conflicting identity rejection and the prior
  application's legacy INSERT after the forward migration, alongside bounded range export,
  per-partition reuse and tamper-failing replay.
- Current Ruff check: passed.
- Current strict-core Pyright check: zero errors and warnings.
- Pyright now checks the IG adapter in strict mode through minimal local `trading-ig` and
  Lightstreamer stubs plus adapter-boundary protocols; the former directory-wide
  exclusion and consequential production `Any` types were removed.
- CLI orchestration now creates one `SystemClock` per command invocation and injects it
  through IG ingestion, backfill, export and replay instead of constructing clocks at
  individual timestamp sites.
- Current `ty` check: passed.
- Dev Container image rebuilt successfully with Codex CLI `0.142.2`.
- Dev Container Trixie image and isolated host-global Codex guidance copy verified.
- Dev Container configuration now provisions the local/OCI compiler, database, SSH and
  network-diagnostic toolchain through its multi-architecture Trixie image while retaining
  the repository-only host bind mount and no Docker-socket access. Package availability,
  JSON configuration and the uv-based bootstrap command were verified before rebuild.
- The initial Oracle Linux ARM64 collector host has restricted IPv6 SSH, a dedicated XFS
  PostgreSQL volume, Docker Engine and OCI CLI. Capture Compose binds PostgreSQL to the
  required host mount, and backup validation uses the pinned database container's client.
- Commit `dd9a2d0` produced and ARM-qualified one slim `linux/amd64`/`linux/arm64` OCI
  image with attestations. Sydney OCIR publication and pull-by-digest verification passed
  at `sha256:cebdde74e02240e2210985cbc927c7744f7b7007d36c7a20f501418710400633`
  using passwordless instance-principal authentication.
- Application image rebuilt successfully on the Python 3.13 Trixie base.
- Current isolated PostgreSQL-backed suite: 106 passed through migration `0003`.
- Capture bootstrap remediation suite: 107 passed, including currency-qualified IG pip
  economics at the adapter boundary; Ruff, Pyright and `ty` also passed.
- Listing identity has deterministic coverage proving that volatile IG snapshots do not
  change the effective product version.
- Capture readiness has regression coverage for the persisted `ig-market-data` adapter
  identity. The current suite contains 109 tests; Ruff, Pyright and `ty` pass.
- Corrected dual-architecture release `d12217f` passed ARM cloud migration, idempotent
  seven-listing validation, live readiness, bounded ingestion and clean-stop gates at OCI
  index digest `sha256:c5e6a42f242c1cec38948c1798ad602878d3af1487bd4349aa11b457dac51828`.
- PostgreSQL-backed branch coverage: 70% overall; replay is 100%, bars 95%, gaps 89%,
  ingestion 82%, the IG adapter 61% and CLI orchestration 40%.
- Architecture tests enforce the declared dependency direction; PostgreSQL read-model
  queries reside in the PostgreSQL adapter rather than the application layer.
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
- Bounded exponential REST retries, fresh-session stream reconnect, stale-stream
  detection, terminal disconnect handling and queue-saturation recovery passed
  deterministic tests.
- Live backfill returned five one-minute points for each instrument and persisted 105
  bid/ask/midpoint bars with `IG_HISTORICAL` provenance; an overlapping rerun wrote
  zero duplicate events.
- A minimal follow-up persisted IG's provider-reported remaining historical allowance.
- Parquet manifest `b2b9d83c91a0fb97fc1e245e` replayed 222 rows to SHA-256
  `b2b9d83c91a0fb97fc1e245e108afa67128d72e58a3243d62b6f02a350158ee8`.
- A forced live reconnect refreshed the REST session, retained one Lightstreamer
  connection, resumed all seven subscriptions and reported zero dropped records.
- A fresh-process restart then received 229 updates across all seven subscriptions and
  terminated cleanly.
- CLI dispatch, argument forwarding and invalid ingestion timing bounds are covered for
  every public command without contacting IG.
- Authentication exhaustion, forced-reconnect preconditions and exhaustion, subscription
  degradation and callback lifecycle failure have focused deterministic coverage.
- The seven-instrument soak runbook and evidence record define preflight, objective
  pass/fail conditions, observation points, reconnect/restart procedure, redaction review,
  export/replay verification and the runtime freeze boundary.
- The pre-soak rehearsal passed formatting, Ruff, Pyright, `ty`, all 86 PostgreSQL-backed
  tests, migration application and read-only health/system/instrument API checks.
- The future-facing intraday-strategy research dossier records a recency-weighted public
  evidence survey, validation standard, market-state assessment, risk review, source
  ledger and prioritised experiment backlog without changing the current phase boundary.
- The first 24-hour soak attempt failed after about 77 minutes: the forced reconnect did
  not restore all seven subscriptions, subsequent stale reconnects exhausted, the run was
  incorrectly recorded as `COMPLETED`, and ingestion processes remained resident. WP7
  requires remediation and a fresh frozen-candidate soak.
- ADR 0010 lifecycle remediation is implemented. Deterministic coverage now distinguishes
  transport from all-subscription data readiness, rejects superseded-generation callbacks,
  escalates stalled SDK retries, classifies fatal provider errors, propagates terminal
  recovery failure and verifies disconnect completion. A 126-second live candidate smoke
  recovered after one retryable reconnect error, restored all seven healthy quotes,
  recorded one reconnect and zero drops, stopped cleanly and left no ingestion process.
  The subsequent repeated-reconnect sequence passed its first stage but exposed an
  executor and `trading-ig` rate-limiter thread leak while shutting down its second stage.
- ADR 0011 contains synchronous provider calls behind named deadlines, treats unresolved
  calls as terminal, retains stream ownership until disconnect is confirmed, stops local
  rate-limiter resources independently of remote logout, bounds reconnect cycles and
  records forced-reconnect request and completion separately. Deterministic coverage
  includes all Lightstreamer degraded states, fresh post-recovery readiness, failed
  disconnect ownership and subprocess exit after a timed-out provider call.
- Preliminary live qualification for the remediated candidate passed before the fresh
  soak, including forced reconnect and fresh-process restart evidence, repeated-reconnect
  lifecycle coverage, static gates and deterministic failure coverage.
- The fresh 24-hour seven-instrument soak passed: run
  `0d9cf6de-421f-493b-bd69-014b4845d00a` started
  `2026-07-07T01:05:07.463801+00:00`, finished
  `2026-07-08T01:05:29.494570+00:00`, finalised as `STOPPED`, recorded one reconnect,
  zero dropped records and zero provider operations, and left no ingestion process
  resident.
- Post-soak verification passed: formatting check, Ruff, Pyright, `ty`, focused IG
  lifecycle suite with 32 tests and the full 104-test suite against an isolated migrated
  PostgreSQL database. A direct post-soak run against the accumulated soak database also
  completed, but projection rebuild volume made it unsuitable as the final automated
  gate.
