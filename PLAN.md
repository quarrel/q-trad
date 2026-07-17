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

  - Complete locally: ADR 0024 separates the persistent development database from a pinned,
    tmpfs-backed PostgreSQL test service. The Dev Container advances only interactive `db` to head;
    the local verification helper creates and drops a guarded `qtrad_test_*` database while
    reproducing CI's `0003` compatibility test, head migration and full suite. Integration tests no
    longer fall back to the application database URL, and the host Docker socket remains unmounted.
    The clean local gate passes all 298 tests. A new `qtrad_dev` database is at migration `0009`;
    the ambiguous pre-separation `qtrad` database remains untouched and unselected.

  - Complete: hashed `capture-v1`, ADR 0009 listing events/economics, planned historical
    coverage/backfill, readiness contract, immutable Compose deployment and initial ARM64
    bounded-cloud qualification.
  - Complete locally and undeployed: ADR 0009 listing events now project atomically and supersede
    effective listings at provider/environment/instrument scope, including an epic change. Migration
    `0008` enforces one open-ended selection and rebuild replays event-backed listing history while
    preserving legacy rows. Migration preflight fails closed on existing ambiguity. GitHub CI run
    `29338222506` passed migration through `0008` and all 245 tests against PostgreSQL 18 at branch
    head `5467017`.
  - Complete locally and undeployed: migration `0009` validates lower-case SHA-256 identity for run
    configurations, research manifests, backfill plans and non-null provider-listing universes. Run
    and listing application boundaries reject malformed identity before database access; nullable
    legacy listing rows remain distinguishable. A read-only audit of the frozen collector found no
    malformed identities in its 11 runs or seven listings. GitHub CI run `29339570739` applied migration
    `0009`, exercised direct database rejection and passed all 251 tests against PostgreSQL 18 at branch
    head `7437258`.
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
    restricted at OCI to the operator's IPv6 range from the WSL host; the Dev Container uses
    Tailscale exclusively because direct Docker/WSL IPv6 forwarding was retired as unreliable.
    Bastion enablement and final alarm tuning are tracked improvements rather than release gates.
    Future host-hardening work should extend healthwatch with clock-synchronisation evidence from
    Chrony: OCI's `169.254.169.254` source online, system clock synchronised, normal leap status and a
    bounded absolute offset. Alert thresholds should be qualified against normal host observations;
    an initial sustained 100 ms offset threshold is conservative for capture receive timestamps.
      The corrected candidate has passed deliberate container restart and host reboot recovery.
      `capture-v2` remains excluded.
        - The belated 48-hour checkpoint at `2026-07-16T13:30:16Z` was 58.4 hours into the
          candidate. Host services, all three containers, timers, daily backup, restore evidence,
        projection catch-up and database capacity remained available, but Lightstreamer queue
        saturation had begun at `2026-07-16T12:57:15Z`. Readiness returned HTTP 503 with no fresh
        required quotes; the adapter had recorded 17,985 dropped records by
        `2026-07-16T13:30:57Z`. The window remains frozen for honest closure evidence, but the
          candidate cannot satisfy the no-loss gate; no restart or other collector mutation was
          performed.
        - The boundary checkpoint at `2026-07-17T03:05:54Z` found HTTP 200 readiness, seven fresh
          subscriptions, exact projection catch-up and healthy backup/restore/capacity evidence, but
          the run retained 22,029 dropped records, one reconnect and 70 observed gaps. Formal
          qualification closure remains evidence-gated, but the internal loss already prevents a
          `PASS`.
          - Root-cause analysis is complete locally: accepted input rose from roughly 9 to 32 records
          per second; persistence latency then grew from milliseconds to more than eight minutes.
          Queue loss ran from `2026-07-16T12:57:15Z` to `13:36:11Z` and the backlog cleared around
          `14:20Z`. Ingestion incorrectly advanced bar closure with processing wall time, converting
            delayed-but-ordered records into 339,336 durable `MarketBarCorrected` events and amplifying
            the backlog. A local undeployed correction uses transport receive time, makes drop health
            sticky, rate-limits overflow logs, records queue occupancy and limits health persistence to
            state changes or one write per second. First/last drop receive times remain in bounded health
            evidence even though per-drop logging is removed. Formatting, Ruff, Pyright, `ty` and all 283
            database-independent tests pass; isolated
              migrated-PostgreSQL validation remains the release gate.
          - Complete: the failed 72-hour candidate is hash-bound as `FAIL`; its 22,029 drops and 70
            unexplained gaps remain immutable. The overload correction is deployed to `capture-v1`
            at migration `0009` and has begun a new seven-instrument measurement with all-seven
            caught-up readiness and zero initial drops. Historical corroboration remains pending a
            sparse 267-point plan set; the rejected 20,741-point rectangle made no provider request.
          - None of the 70 projected market-data gaps overlaps the overload interval. Gap projections
            describe observed quote silence, not callbacks discarded by an internal queue; the
            independent qualification loss gate still requires `dropped_records=0`. Historical API
            corroboration may classify the retained gaps after closure, but must not be used to erase
            or reclassify the queue-loss failure.
            - The retained gaps are short and strongly clustered: all last 121–385 seconds; 69 begin
            between `20:00Z` and `21:59Z` across three consecutive days, with one isolated FTSE 100
            interval at `04:25Z`. A provider/session-cycle cause is only a hypothesis. After formal
            closure, derive the quota-bounded reviewed historical plan for all 70 intervals and record
              whether IG historical bars exist before assigning any upstream or operational class.
            - Complete: merged sparse plan-set execution queried all 70 retained gaps through 56 exact
              IG demo plans and 267 historical points in the isolated verified-snapshot database. The
              first unpaced run hit a separate request-rate allowance after 27 requests while reporting
              9,865 weekly points remaining; append-only usage evidence retained that failed attempt.
              A reviewed three-second provider-boundary pacing correction passed CI and the exact same
              set resumed to completion with 9,733 points remaining. The v2 offline artifact found
                historical data for all 70 gaps and complete coverage for all 210 basis results (834/834
                expected basis-minute intervals). This warrants deeper streaming/session-path analysis
                but does not prove what IG's streaming endpoint emitted or repair the failed qualification.
              - Streaming-continuity analysis of the verified snapshot found zero raw callbacks for
                the affected instrument inside all 70 gaps, while every interval retained 35–723
                canonical quotes from two to six other subscriptions on the same connection. The bounded
                lifecycle log contains no disconnect, watchdog, subscription-error or reconnect event
                during those gaps. Historical MID bars move in all 70 intervals, with no flat gap and
                three to eight minute bars per interval. This rules out persistence, queue loss, a
                whole-connection outage and ordinary price inactivity, but cannot yet separate IG demo
                per-item stream suppression from SDK/subscription delivery failure.
                - Complete locally and undeployed: the adapter captures bounded subscription
                establishment/end, server-error, real-frequency and Lightstreamer lost-update evidence.
                Subscription renewal invalidates prior merged item state and requires a fresh healthy
                update; SDK-reported loss remains sticky degraded health. The focused lifecycle suite
                  passes all 46 focused tests. Idempotent REST reads now serialise one v2 invalid-token
                reauthentication and one replay. Each login now fails closed unless IG's client-app
                response contains exactly one entry for the configured API key; numeric published
                rates and the pinned library's published-minus-two effective rates are retained without
                API-key material. Authoritative allowance failures are not retried. The complete isolated
                  PostgreSQL gate now passes all 347 tests, formatting, Ruff, Pyright, `ty` and ShellCheck.
                The experiment and exit gates are in
                  `docs/STREAMING_CONTINUITY_INVESTIGATION.md`; the corrected collector remains untouched.
                - In progress locally and undeployed: proposed ADR 0025 adds IG's documented
                  application heartbeat as separate whole-connection evidence and requires it for
                  readiness without treating it as token renewal or per-item proof. Stateful delta
                  normalisation now shares event-loop ordering with subscription renewal. The maintained
                  Lightstreamer 2.2.2 used API is compatible locally and is selected through a reviewed uv
                  override because `trading-ig` pins superseded 1.0.3; IG demo qualification remains a
                  release gate. Library-managed transport recovery now separately invalidates heartbeat
                  readiness as well as PRICE readiness, so pre-stall evidence cannot restore health.
                - The reproducible isolated load probe passes 2,000 callbacks at 200/s over 40 synthetic
                  subscriptions with zero drops. With a 5 ms injected persistence stall it absorbs a
                  queue high-water of 799/10,000 and drains completely with 6.58-second p95 and 6.91-second
                  maximum lag. The five-minute 200/s profile also passes all 60,000 callbacks with zero
                  loss, queue high-water 51/10,000 and 0.257-second maximum lag. A separate all-item
                  renewal at load passes 2,000 callbacks and re-establishes complete state for all 40
                  subscriptions. Provider-backed connection/recovery faults remain pending.
                - The provider-backed discriminator is now a single-connection PRICE-versus-CHART:TICK
                  contrast for the same seven epics. Fifteen subscriptions including heartbeat remain below IG's published
                40-subscription limit and avoid its explicit prohibition on multiple concurrent
                  connections. This experiment runs only after the current measurement in a separate
                  evidence store; it is not an undeclared collector sidecar or release change. A
                  guarded local harness is now implemented: it requires an exact collector-stopped
                  acknowledgement, all 15 channels data-ready, non-overwriting hash-bound callback
                  evidence, zero queue/SDK loss, verified unsubscribe/disconnect, REST logout and
                    HTTP-session/provider-worker termination. It remains unexecuted against IG while
                    the corrected collector measurement is active.
                    Threshold-exceeding heartbeat silence is now an explicit discrepancy, and the
                    terminal gate requires every channel genuinely fresh plus a connected transport
                    immediately before deliberate shutdown; historical readiness cannot pass.
                - A separate guarded recovery harness now drives the production adapter without a
                  database. It requires fresh initial data, terminates the underlying Lightstreamer
                  client, verifies automatic recovery, injects a fixed invalid local REST token and
                  verifies one bounded reauthentication/replay plus another complete stream
                  generation. Exact reconnect/reauthentication counts, zero loss and full process
                  cleanup are release gates. Every ready phase now requires current-key-validated
                  published and effective demo trading/non-trading rates, and sticky abandoned-provider-operation state
                  fails shutdown. It also remains unexecuted while the collector owns the API key.
                - An independent offline verifier now recomputes either experiment manifest hash and,
                  for the contrast, confines and streams the gzip JSON-lines artifact to verify every
                  record, increasing sequence, count and uncompressed SHA-256. Integrity verification
                  also requires the exact schema-v1 experiment check set, preserves a truthful FAIL
                  and is required before evidence review.
                - The release sequence is now explicit: finish the untouched old-lock corrected run
                  through its recurrent windows and weekend; stop it with operator approval; execute
                  both provider probes during active markets; accept ADR 0025 only on PASS; then
                  publish/deploy the exact ARM candidate and give that image its own fresh 72-hour
                  `capture-v1` endurance. Earlier stages cannot qualify the later dependency/image.
          - Post-window reconciliation planning exposed that the first deployed environment omitted
            `QTRAD_CAPTURE_SOURCE_ID` and therefore established the validated default
            `local-development` as the effective identity of this canonical store. The first plan was
            not executed. Evidence and snapshot documentation now bind the truthful existing identity;
            it must not be renamed merely to improve its label.
  - Complete locally and undeployed: a bounded qualification-closure helper will write one
  self-hashed, non-overwriting automatic evidence snapshot and cannot pass before the candidate
  boundary. It reads loopback APIs, systemd/Compose state, backup/restore status, migration and disk
  capacity; it leaves gap, log, monitoring and active-market review explicitly pending for the
    operator. GitHub CI run `29334657157` passed all static, shell, migration and PostgreSQL 18 gates
    with 237 tests at branch head `3415f70`.
    - Complete locally and undeployed: qualification finalisation is a separate offline, self-hashed
      record. It verifies the automatic snapshot, exact operator-review binding, full-window log and
      monitoring coverage and one classification per candidate gap. ADR 0021 and review/final schema
      v2 add evidence-bound `EXPECTED_MARKET_INACTIVITY` without treating quote silence as proof of a
      transport failure: every gap requires bounded evidence references, while absent or ambiguous
      continuity remains `UNEXPLAINED` and cannot pass. Valid failed reviews are preserved; malformed,
      omitted, mismatched or tampered input cannot emit a decision. Active-market representativeness
      is explicitly separate from the later physical-storage comparison. The earlier v1 implementation
      passed GitHub CI run `29335826682` with 243 tests at branch head `335f089`; v2 remains local and
      undeployed. Formatting, Ruff, both type checkers, ShellCheck, skill validation, focused script
      tests and all 279 tests pass locally against a disposable database migrated through `0009`.
      - Pending after the frozen window: run one reviewed, plan-bound IG demo historical query for
        every candidate gap against an isolated writable database. Compare exact listing/UTC interval
        bars with immutable live evidence as a diagnostic only; historical presence prompts deeper
        stream-path investigation, while absence supports but cannot prove upstream inactivity.
        Historical results neither repair gaps nor determine ADR 0021 classification by themselves.
        - Complete locally and undeployed: ADR 0022 and `qualification gap-history` turn that future
          query into bounded evidence. The offline command binds the automatic snapshot, exact plan,
          verified post-evidence database snapshot, completed three-basis coverage and immutable
          research manifest/Parquet content, then writes a non-overwriting self-hashed per-gap result
          without a causal classification. Formatting, Ruff, both type checkers, ShellCheck and all
          288 tests pass locally against a disposable database migrated through `0009`. The real
          provider query remains pending the window end.
            - Complete locally and undeployed: `qualification gap-plan` now derives the common
              minute-aligned range and sorted distinct instruments directly from the automatic evidence.
              It requires the exact verified post-evidence snapshot target/source/universe and the
              repository's single current Alembic head before delegating to the existing listing-bound
              plan writer. Operator allowance entry, review, registration and hash confirmation remain.
            - Complete locally and undeployed: a post-window log-evidence helper verifies and binds the
              automatic qualification snapshot, derives its exact candidate interval and streams bounded
              Docker/systemd history into a root-only, non-overwriting bundle. Filtered inspection records
              bind immutable image, restart and effective logging identity without exposing container
              environment. An independent offline verifier rejects binding, schema, exact-file-set,
              ownership/mode, hash, count, timestamp or image drift before returning the manifest hash.
              Neither tool performs semantic review or can qualify the release.
            - Complete locally and undeployed: ADR 0023 closes two hour-24 qualification-tool
              incompatibilities without changing the collector. Compose array and newline-delimited
              output now normalise to one checked representation. The exact frozen digest may bind its
              pre-hash-field readiness response only through one total running ingestion row carrying the
              expected configuration hash after reconciliation; every later image requires endpoint
              identity and fails closed if it is absent. Formatting, Ruff, both type checkers, ShellCheck,
              45 focused qualification tests and all 282 database-independent tests pass locally; the 13
              PostgreSQL integration tests remain delegated to the isolated pull-request CI service.
              - Complete on `main`: release CI proves the newer reconciliation command against a fresh database
                stopped at the collector's exact migration `0003` before advancing that same database to head
                for the full suite. PR 1 merged exact reviewed head
                `bd053b48d5e15c6ff5455387838c83bed3c001d8` as merge commit
                `08c0e5b99a83cdafe1a53bfc8e2afb8c01850fc6`; the resulting main-branch CI passed. This
                closes the previously implicit old-schema compatibility assumption. Publishing the immutable
                utility image remains a separate operator-gated action.
  - Complete locally and undeployed: pre-candidate run reconciliation is now an explicit
    hash-confirmed two-step operation. Its read-only plan binds capture/database/universe and
    immutable tool-image identity, the strict candidate cutoff and every eligible stale run.
    Execution locks and rechecks the
    complete set atomically, marks only those rows `FAILED` with an asserted cutoff upper-bound basis,
    and cannot touch the current run or raw/canonical data. A guarded immutable one-shot helper is
    prepared for use only after the candidate window. GitHub CI run `29346869695` passed every static
    and shell gate, migration through `0009` and all 275 tests against PostgreSQL 18 at branch head
      `5ba0b07`; the collector remains unchanged.
  - Complete locally without collector access: repository operational guidance now treats the
    collector and qualification artifacts as evidence-bearing state, protects immutable raw and
    canonical history, and requires independent read-back after control-plane writes. The
    repo-scoped `qtrad-capture-ops` skill classifies observation, evidence, publication, deployment
    and control-plane work before acting, and fresh read-only Codex invocations correctly refused a
    deployment during qualification while permitting bounded health observation. Dev Container MCP
      registration is sequential and verified; Context7 uses Codex's `env_vars` allow-list to forward
      its inherited credential rather than persisting it in a renderable process argument. A fresh
      `codex exec` process successfully resolved Context7 documentation. Skill validation, JSON
      validation, formatting,
    Ruff, Pyright, `ty` and all 275 tests pass against a disposable database migrated through
    `0009`. GitHub CI run `29383000650` passed the repository's full pull-request gate at
    implementation head `cb68265`.
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
- Complete locally and undeployed: storage snapshot schema version 3 remains backward-readable with
  versions 1 and 2, records pre-marker versus coded raw-representation schema state and binds exact
  per-code row counts. Offline comparison derives interval representation deltas, proves an
  all-`CHANGED_FIELDS` interval or exposes newly added `LEGACY_UNCLASSIFIED` rollback-compatible rows,
  and rejects a representation-schema transition. GitHub CI run `29341678396` passed every static and
  shell gate, the real PostgreSQL representation probe and all 255 tests at branch head `6a84319`.
- Complete locally and undeployed: `storage compare` now writes a bounded, non-overwriting,
  self-hashed release artifact retaining its snapshot and image identity. Offline `storage contrast`
  accepts only verified same-source/configuration artifacts from distinct immutable images, requires
  a merged-state baseline, all-`CHANGED_FIELDS` candidate and passed automated thresholds, and reports
  mechanical per-message change without claiming the required active-market reviews or accepting a
  storage decision. GitHub CI run `29343411122` passed all static and shell gates, migration through
  `0009` and all 258 tests against PostgreSQL 18 at branch head `8ff48cd`.
- Complete locally and undeployed: bounded `storage review` inputs now become self-hashed operator
  assertions bound to one exact comparison, release and interval. Offline `storage qualify` verifies
  both reviews against their contrast, emits `PASS` only when both are representative, preserves an
  honest negative `FAIL`, and cannot accept a storage decision. The running collector remains
  unchanged. GitHub CI run `29344877350` passed every static and shell gate, migration through `0009`
  and all 264 tests against PostgreSQL 18 at branch head `961d9d5`.
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
  days. These remain labelled interval extrapolations rather than forecasts. GitHub CI run
  `29327187828` passed all static, migration and PostgreSQL 18 gates with 224 tests at branch head
  `448250e`.
- Complete locally and undeployed: a guarded operator helper runs storage snapshots only from an
  already-local immutable inspector digest using `--no-deps --pull never`, refuses unsafe labels and
  overwrites, and returns evidence written by the non-root image to root-only ownership without
  restarting collector services. GitHub CI run `29327683523` passed all static, shell, migration and
  PostgreSQL 18 gates with 228 tests at branch head `fad5211`.
- Complete locally and undeployed: ADR 0020 and migration `0007` add a compact first-class raw payload
  representation code without rewriting legacy payloads. The current IG candidate writes
  `CHANGED_FIELDS`, fixtures write `FIXTURE`, and pre-marker/rollback writes remain conservatively
  `LEGACY_UNCLASSIFIED`; downgrade refuses to erase changed-field identity. GitHub CI run
  `29332161781` passed all static and shell gates, migration through `0007`, old-writer/default and
  bounded-code integration checks, and all 232 tests against PostgreSQL 18 at branch head `dfe45ea`.
- Next gated sequence: after the frozen qualification closes successfully, retain the current image
  long enough to collect a representative before/after storage interval, then qualify the
  changed-field image separately and collect its own representative interval. Each comparison uses
  one immutable image identity; it is invalid to compare snapshots across the release boundary.
  Only those results can open an index, JSON representation or whole-legacy-epoch archive decision.
- Complete locally and undeployed: ADR 0014 and the bounded zero-copy
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
  - Complete locally without provider access: `instruments sync --universe PATH` validates a
    reviewed, epic-pinned non-streaming universe into the selected writable database without
    changing ingestion's configured universe or starting a stream. This supplies the explicit
    listing-validation step before candidate historical planning on an isolated database. GitHub CI
    run `29336513376` passed every static, shell, migration and PostgreSQL 18 gate with all 244 tests
    at branch head `640caf7`.
- Complete locally without collector access: ADR 0017 and migration `0006` replace mutable
  content-prefix research manifests with schema-version-2 canonical manifest identity. Exports bind
  an explicit UTC range, the exact universe/configuration, application image/version, grouped
  coverage, provenance, live gaps and historical-coverage attempts; replay verifies the manifest,
  every file hash and decoded semantic content. Per-instrument/day content identity reuses unchanged
  partitions as the store grows. `bars-v2/` isolates new partitions from a rolled-back legacy
  exporter, while nullable forward columns preserve the prior application's INSERT contract.
    - Complete on `main`: isolated GitHub CI passed formatting, linting, typing, shell validation,
      PostgreSQL 18 migration and the full feed/catalogue test suite. PR 1 merged exact reviewed head
      `bd053b48d5e15c6ff5455387838c83bed3c001d8` as merge commit
      `08c0e5b99a83cdafe1a53bfc8e2afb8c01850fc6`; the resulting main-branch CI passed.
    - Release-boundary audit complete: the image workflow publishes only from `main`, but merge and
      green CI do not themselves authorise publication or deployment. The new immutable utility image
      remains unpublished and the collector remains frozen on its qualification release.
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
python -m qtrad runs reconcile-plan [--universe PATH] --cutoff UTC --output PATH
python -m qtrad runs reconcile --plan PATH --confirm-plan-hash SHA256
python -m qtrad instruments sync [--universe PATH]
python -m qtrad ingest --environment ig-demo
python -m qtrad ingest --environment ig-demo --max-seconds 60
python -m qtrad ingest --environment ig-demo --max-seconds 90 --force-reconnect-after-seconds 20
python -m qtrad backfill plan --universe PATH --start UTC --end UTC --remaining-allowance N --output PATH INSTRUMENT...
python -m qtrad backfill register --plan PATH --confirm-plan-hash SHA256
python -m qtrad backfill execute --plan-hash SHA256
python -m qtrad research export --universe PATH --start UTC --end UTC
python -m qtrad replay --manifest PATH
python -m qtrad projections rebuild
python -m qtrad storage snapshot --universe PATH --output PATH
python -m qtrad storage compare --output PATH BEFORE AFTER
python -m qtrad storage contrast --output PATH BASELINE_COMPARISON CANDIDATE_COMPARISON
python -m qtrad storage review --output PATH COMPARISON REVIEW_INPUT
python -m qtrad storage qualify --output PATH CONTRAST BASELINE_REVIEW CANDIDATE_REVIEW
python -m qtrad feed verify --source-id SOURCE --universe-name UNIVERSE --configuration-hash HASH PAGE...
python -m qtrad feed probe --endpoint http://127.0.0.1:PORT --source-id SOURCE --universe-name UNIVERSE --configuration-hash HASH
python -m qtrad api
```

## Verification evidence

- Current feature-branch local gates: Ruff formatting/lint, Pyright, `ty` and both ShellCheck sets
  pass; 262 tests pass with 13 PostgreSQL migration/integration tests deferred to isolated CI.
- GitHub CI run `29335826682` passed formatting, Ruff, Pyright, `ty`, both ShellCheck sets,
  migration through `0007` and all 243 tests against isolated PostgreSQL 18 at branch head
  `335f089`.
- GitHub CI run `29336513376` passed the same full gate set and all 244 tests at branch head
  `640caf7`, including explicit non-streaming universe validation without runtime-universe use.
  - GitHub CI run `29338222506` passed formatting, Ruff, Pyright, `ty`, both ShellCheck sets,
    migration through `0008` and all 245 tests against isolated PostgreSQL 18 at branch head
    `5467017`. It proves alternate-epic supersession, atomic failure rollback, the one-effective-listing
    index and canonical projection rebuild.
  - GitHub CI run `29339570739` passed every static and shell gate, migration through `0009` and all
    251 tests against PostgreSQL 18 at branch head `7437258`. It proves validated SHA-256 constraints
    reject malformed new run, manifest, backfill-plan and non-null listing identities.
  - GitHub CI run `29341678396` passed every static and shell gate, migration through `0009` and all
    255 tests against PostgreSQL 18 at branch head `6a84319`. It proves snapshot-v3 representation
    counts reconcile with the actual raw table and preserves deterministic pre-marker, changed-field,
    rollback-compatible and legacy snapshot behaviour.
- GitHub CI run `29343411122` passed every static and shell gate, migration through `0009` and all
  258 tests against PostgreSQL 18 at branch head `8ff48cd`. It proves comparison artifacts are
  non-overwriting and hash/semantics verified, while contrast rejects identity drift, sub-threshold
  evidence and rollback-compatible candidate rows without accepting an operator decision.
- GitHub CI run `29344877350` passed every static and shell gate, migration through `0009` and all
  264 tests against PostgreSQL 18 at branch head `961d9d5`. It proves exact review/contrast binding,
  valid negative-review preservation, semantic tamper rejection and the no-storage-decision boundary.
- GitHub CI run `29346869695` passed every static and shell gate, migration through `0009` and all
  275 tests against PostgreSQL 18 at branch head `5ba0b07`. It proves exact stale-run set locking,
  omitted-target rollback, current-run preservation and terminal-record immutability.
- GitHub CI run `29332962174` passed every static and shell gate, migration through `0007` and all
  232 tests against PostgreSQL 18 at branch head `65f7037`.
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
  network-diagnostic toolchain plus the Docker CLI and Compose plugin through its
  multi-architecture Trixie image while retaining the repository-only host bind mount and
  no Docker-socket access. Package availability and merged offline Compose validation
  were verified in the running container; persistence in a fresh container requires the
  normal Dev Container rebuild.
- Dev Container networking deliberately uses an IPv4-only Docker bridge under WSL
  mirrored networking. Collector access follows the WSL host's Tailscale routes and an
  unprivileged non-interactive Tailscale SSH gate must pass before Dev Container startup;
  Docker contains no Tailscale state or network capability. Compose validation,
  authorised SSH gate health, IPv4 internet, collector reachability and PostgreSQL service
  discovery passed.
- Dev Container startup now clears any stale PID-managed Codex Remote Control daemon state
  before starting a fresh daemon after the interactive database migration. The existing
  Codex named volume preserves the host identity and device pairing across ordinary
  container rebuilds.
- The image keeps one npm-installed latest Codex bootstrap while Remote Control owns its
  persistent standalone runtime. The pnpm tool dependency tree now contains Tilth only,
  removing its unused duplicate Codex installation.
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
