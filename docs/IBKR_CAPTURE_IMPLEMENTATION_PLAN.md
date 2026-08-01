# IBKR capture and historical-data implementation plan

**Status:** ACTIVE
**Approved track:** independent IBKR paper-market-data source
**Research relationship:** parallel data-acquisition track for R2; no broker orders

## 1. Purpose and authority

This document is the normative implementation plan for adding Interactive Brokers (IBKR) paper
market data to q-trad. It covers exact contract qualification, immutable historical acquisition,
live top-of-book capture, provider-history research foundations and source-specific R2 experiments.

`PLAN.md` owns programme sequencing. `docs/TRADING_RESEARCH.md` owns stable research policy.
`docs/ARCHITECTURE.md` owns implemented and intended boundaries. ADR 0028 fixes the durable
independent-source decision. `docs/STATUS.md` alone records demonstrated current state.

This work accelerates chronological R2 research without weakening the pending IG-native experiment.
An IBKR result is limited to its declared IBKR product and evidence class. It cannot substantiate IG
quotes, spreads, slippage, fills, product economics or paper outcomes.

## 2. Governance

Substantive changes to fixed decisions, evidence meaning, source separation, availability policy,
no-order scope or completion gates require an explicit amendment recording the original and revised
requirements, rationale, evidence impact, approval and date. Code must not silently revise this plan.

Stage completion requires focused automated evidence. Schema, artefact, milestone and release
boundaries require the complete clean PostgreSQL, static-analysis and test gate. Account access,
external acquisition, host deployment and publication remain separately authorised operations.

## 3. Outcomes

The track has four independent outcomes:

1. **Capability qualified:** exact account-visible contracts and entitlements are retained or rejected.
2. **Historical bootstrap complete:** an immutable verified IBKR historical foundation can drive the
   source-specific R2 experiment.
3. **IBKR capture complete:** a reviewed universe streams into an independent canonical store with
   truthful health, recovery, backup and restore evidence.
4. **Research objective complete:** R2 reports a positive, negative or inconclusive IBKR historical
   result and, where justified, an IBKR-native comparison.

Software completion never implies that an account, dataset, host or research result exists.

## 4. Fixed decisions

### 4.1 Independent source, runtime and store

Use one q-trad codebase and application image, but a distinct source and operational lifecycle:

```text
capture-ibkr-v1
capture_source_id = "ibkr-paper-v1"
provider = "ibkr"
environment = "paper"
```

The IBKR deployment has its own IB Gateway, q-trad ingest role, PostgreSQL event store, read-only
capture API, backups, restore verification, universe and deployment descriptor. It shares no stream
version history with IG. IB Gateway authentication, restart and market-data session lifecycle justify
process and host isolation.

Research never writes to either collector store. It consumes immutable exports from an isolated
research copy. Existing collector history is never rewritten or selectively deleted.

### 4.2 Canonical and provider identity

Stable q-trad instrument IDs remain provider-independent. Each accepted IBKR listing binds an exact
`conId`, exchange and reviewed contract evidence. Symbol searches and marketing names are
non-authoritative candidates and cannot establish equivalence.

Provider identifiers and TWS API types stop at `adapters/ibkr`. Domain and application code retain
explicit provider, environment, listing and source identities through provider-neutral contracts.
Ambiguous mappings, overlapping eligible listings or changed contract identity fail closed.

### 4.3 Source class, R2 evidence class and experiment separation

Define a provider-source dimension independent of R2 research status:

```text
MarketDataSourceClass
    IBKR_HISTORICAL_RESEARCH
    IBKR_NATIVE_CAPTURE
    IG_NATIVE_CAPTURE
```

R2 `EvidenceClass` retains its existing orthogonal values:

```text
IMPLEMENTATION_EVIDENCE_ONLY
CONFIRMATORY
```

An `R2-IBKR-HISTORICAL` artefact may therefore bind
`market_data_source_class = IBKR_HISTORICAL_RESEARCH` and
`evidence_class = CONFIRMATORY`. Provider-history observations and every foundation, experiment,
feature, fit, forecast, evaluation report and evidence bundle bind both dimensions independently into
their semantic identities. Verification rejects a missing, substituted or inconsistent dimension.

Use distinct experiment identities:

```text
R2-IBKR-HISTORICAL
R2-IBKR-NATIVE
R2-IG-NATIVE
```

One foundation bundle contains one `MarketDataSourceClass`. Historical, IBKR-native and IG-native
rows are not silently combined. Any later augmentation experiment has a separate identity, compares
native-only and augmented controls and retains an untouched native holdout. Historical bars may
support development, training or a conclusion about the named IBKR product, but are not native
delivery evidence and cannot prove contemporaneous quotes or executable fills.

### 4.4 No order capability

The IBKR adapter exposes market-data operations only. It has no order methods, imports no q-trad
order port and creates no order settings, command or endpoint. q-trad receives only Gateway host,
port, dedicated client ID, environment and required market-data type. Usernames, passwords and 2FA
secrets remain at the operator-controlled Gateway login boundary and never enter q-trad settings,
logs, fixtures or evidence.

### 4.5 Historical availability and revisions

IBKR historical rows retain how and when they were requested. Their research availability policy is
an explicit source assumption, initially `BAR_END_PLUS_DECLARED_PROVIDER_DELAY`; later persistence
must not masquerade as original market-time availability. Provider-history rows expose an
authenticated `available_at` selected through a versioned `ProviderHistoricalAvailabilityPolicy`;
they do not populate native `received_at` or `persisted_at` with assumed times.

The policy name, declared delay, derived `available_at`, request/response time and correction/revision
assumptions participate in observation, foundation and downstream semantic identities. Historical
responses are frozen exactly as returned. Repeated requests never overwrite evidence. Changed rows
become a new immutable dataset or explicit revisions with request lineage. Historical BID and ASK
extrema are not assumed contemporaneous and remain ineligible for spread features until validated
against observed IBKR top-of-book capture.

### 4.6 Acquisition priorities

Acquire one-minute history first. Collect live top-of-book updates as the preferred high-frequency
path. Use one-second historical bars only for bounded declared investigations; a full-universe week
of one-second bars is not an R2 prerequisite.

## 5. Initial candidate universe

Review canonical concepts comparable to the useful core of the IG universe:

- FX: AUD/USD, EUR/USD, USD/JPY, GBP/USD, USD/CHF, USD/CAD, NZD/USD and EUR/JPY.
- Equity indices: Australia 200, US 500, US 30, US Tech 100, FTSE 100, Germany 40,
  Japan 225, EU Stocks 50 and Hong Kong HS50.
- Commodities: Gold, Silver and US Crude.

China A50, Taiwan and VIX are excluded from the first candidate release until exact account-visible
contracts, semantics and entitlements are proven. Korea 200 and Bitcoin remain quarantined. No
instrument count is a success criterion: a smaller reviewed universe is acceptable when every
rejection is explicit.

## 6. Contracts

### 6.1 Contract-review evidence

For every candidate retain `conId`, symbol, local symbol, security type, exchange, currency, trading
class, multiplier, minimum tick, market-rule IDs, valid exchanges, long name, underlier `conId`,
timezone, trading hours and liquid hours. Also retain account-visible data type, entitlement errors,
historical capabilities, earliest returned timestamp, timezone/RTH behaviour, latency and row count.

### 6.2 Historical request plan

Add immutable contract `qtrad-ibkr-historical-plan-v1`. Each request binds:

- plan, capture-source, universe and contract-evidence identities;
- canonical instrument, `conId` and exchange;
- exact half-open UTC range, bar size, `whatToShow` and `useRTH`;
- IBKR duration, expected chunk count, pacing class and timezone policy;
- application, image and planner identity.

Execution records request ID, attempt and completion times, response disposition, rows, bounds,
hashes, pacing state and errors. Restarting execution resumes from retained successful chunks without
silently duplicating or replacing them. Planned request coverage is contiguous and non-overlapping
after clipping to the exact requested half-open range; this requirement does not imply that returned
market bars form a continuous grid.

### 6.3 Provider-history observations and availability selection

Add a separate manifested observation contract, initially
`qtrad-provider-historical-observations-v1`, with a frozen
`ProviderHistoricalObservation.available_at` and a versioned
`ProviderHistoricalAvailabilityPolicy`. It declares `MarketDataSourceClass`, provider, environment,
provenance, request/completion times, historical availability and correction policies, request
manifests, contract mappings, session evidence and included bar bases.

Add a versioned foundation availability-selector protocol. Its native implementation continues to
select measured `received_at` or `persisted_at` from `qtrad-research-observations-v1`. Its
provider-history implementation authenticates the stored `available_at` against interval end, the
declared policy and delay, request lineage and correction assumptions. It never writes an assumed
historical availability into either native timestamp field. Unknown selector versions or mismatched
recomputation fail closed.

The existing deterministic panel, target, fold and thin foundation-bundle transformations may be
reused only through that selector after adapters translate rows into the explicit provider-history
contract. Every child remains independently verifiable. One foundation binds one
`MarketDataSourceClass`; the foundation and downstream R2 identities independently bind both source
class and R2 `EvidenceClass`.

### 6.4 Live records, callback chronology and health

Persist actual Level 1 adapter callbacks in arrival order. Every callback carries its connection
generation and a local monotonic arrival sequence in addition to provider sequence where available,
provider/event time, local receive time, request ID, exact contract identity and update fields. Two
payload-equal callbacks with different callback identities remain separate evidence.

Normalisation may emit one-sided quotes and retain separate side times, but never invents a side or
manufactures periodic quotes by carrying state forward. A derived snapshot may be order-independent
only at an explicitly defined coalescing boundary where that property is mathematically valid; it
does not replace or reorder native callback/event history. Replaying the same identity-bearing
callback sequence is deterministic and idempotent. Crossed or invalid states produce bounded visible
dispositions.

Healthy live capture requires socket connection, next-valid-ID and server-time evidence, healthy
market-data farms, available historical farm, exact subscriptions, required `LIVE` data type, first
and recent bid/ask evidence, bounded queues, zero callback drops, zero pacing violations and a current
connection generation. Connectivity codes 1100, 1101, 1102 and 1300 have explicit tested handling.

## 7. Dependency graph and parallel work

```text
Stage 0 decision and plan
        |
        v
Stage 1 capability/product probe
        |
        +-----------------------------+
        |                             |
        v                             v
Stage 2 contracts and fixtures    R2.C -> R2.D/R2.E -> R2.F1
        |
        v
Stage 3 historical bootstrap and provider-history foundation
        |                             |
        +-----------------------------+
        v
Stage 4 R2 software verification and historical integration
        |
        v
Stage 5 R2-IBKR-HISTORICAL

Stage 2 live adapter -> Stage 6 capture host/qualification -> Stage 7 R2-IBKR-NATIVE
                                                           |
                                                           v
                                            Stage 8 optional transfer experiment
```

R2.C is software-complete. R2.D through R2.F1 and corresponding R2.H software verification may
continue using synthetic and representative bundles. R2-IG-NATIVE remains pending until a qualifying
frozen IG-native bundle passes its unchanged gates.

## 8. Staged implementation

### Stage 0 — decision and programme alignment

Deliver:

- accepted ADR 0028;
- this normative plan;
- active-plan, status, research-policy and architecture reconciliation;
- explicit R1 provider-history extension and R2 source-specific amendment;
- candidate-universe ownership and evidence terminology.

Exit evidence:

- all active documents agree that IBKR is independent, read-only and provenance-distinct;
- R2.B remains software-complete implementation evidence;
- no unimplemented IBKR capability is reported as working;
- no IG-native readiness or conclusion is implied;
- documentation checks and conflict searches pass.

### Stage 1 — account, entitlement and capability probe

Add a bounded read-only command shaped as:

```text
qtrad instruments review
    --provider ibkr
    --environment paper
    --catalogue config/capture-ibkr-v1-candidates.toml
    --output <review-evidence>
```

Probe without ingestion: exact contracts, live/delayed/unavailable data, bid/ask/size fields,
one-minute MIDPOINT/BID/ASK, bounded one-second support, earliest history, timezone/RTH behaviour,
entitlement errors and completion latency.

Exit gate: at least twenty exact mappings, or an explicitly accepted smaller universe with every
unmatched or ambiguous concept quarantined. No mapping, price basis, product equivalence, session or
currency is inferred.

#### Current partial implementation and next boundary

The repository implements the local, non-secret preflight plus a fixture-tested, market-data-only
capability-probe boundary. The explicit `--execute-account-probe` path requires an operator-authored,
non-authoritative query specification, a create-only evidence output and the pinned official API wheel;
without that wheel it fails before any socket I/O. The preflight retains the canonical candidate
catalogue, validated Gateway host/port/client ID and deterministic
`OPERATOR_AUTHENTICATION_REQUIRED` output. No account-qualified evidence exists yet, and neither path
has mapping-selection authority or is Stage 1 exit evidence.

The approved next boundary uses an **IBC-managed paper IB Gateway** and a **pinned wheel built from an
official IBKR TWS API distribution**. Before account-gated implementation begins:

- select and record the official TWS API release, download origin, licence and SHA-256; build the wheel
  reproducibly and make it available as a controlled local/private dependency rather than using the
  stale public PyPI `ibapi` release;
- install and configure IB Gateway and IBC outside q-trad; IBC owns login, restart and 2FA handling,
  and its credentials or rendered secret-bearing configuration never enter q-trad settings, logs,
  fixtures, evidence or version control;
- authenticate a paper session, expose its read-only socket only to the intended host, and confirm the
  configured port and positive non-zero client ID without enabling an order path;
- qualify the weekly reauthentication lifecycle separately; until a complete boundary has been
  observed, retain `GATEWAY_WEEKLY_LIFECYCLE_UNQUALIFIED`; and
- supply the pinned official wheel and fixture-test it against the direct API transport's bounded
  callback/request lifecycle, sanitised errors and exact-contract evidence before an explicitly
  authorised live capability probe.

q-trad receives only the Gateway endpoint and client ID. Operator credentials remain outside the
application. A reachable or authenticated Gateway alone does not qualify contracts, entitlements,
historical availability, restart recovery or Stage 1 completion.

### Stage 2 — contracts, adapter and fixture qualification

Implement the official direct TWS Python API inside `adapters/ibkr`, bridging callbacks to a bounded
async queue. Add exact-contract review, pacing, historical planning, live normalisation and
provider-neutral runtime composition. Generalise only the existing hard-coded IG composition seam;
do not create a second ingestion framework.

Fixture exit evidence proves:

- contract ambiguity fails closed and mapping identity is deterministic;
- no order capability is reachable;
- raw callbacks preserve generation-authenticated local arrival order;
- replay of the same identity-bearing callback sequence is deterministic and idempotent, while
  payload-equal callbacks with different identities remain distinct;
- any order-independent derived coalescing is confined to an explicit valid boundary;
- one-sided, crossed, delayed and frozen states behave explicitly;
- timestamps normalise to UTC;
- planned historical request coverage is contiguous and non-overlapping after clipping;
- returned interval keys are ordered and unique, with deterministic reconciliation of overlapping
  responses;
- absent expected active intervals receive explicit gap dispositions, inactive intervals remain absent
  without being called gaps, and no interval is forward-filled;
- one-second planning honours the provider limit;
- pacing state survives restart;
- reconnect generations reconstruct subscriptions exactly; and
- refetches cannot overwrite prior evidence.

### Stage 3 — bounded historical bootstrap

Acquire in order:

1. 16–26 weeks of one-minute MIDPOINT, all-hours history for accepted contracts.
2. Bounded BID and ASK history for the six confirmatory candidates, expanding only after semantics
   and coverage are useful.
3. One active-day, one-second MIDPOINT investigation for those candidates only when tied to a declared
   feature or microstructure question.

Build and independently verify request/result manifests, provider-history observations with
authenticated `available_at`, the versioned availability selector, panels, targets, folds and a thin
source-specific foundation bundle. Verify contiguous/non-overlapping planned coverage separately from
returned market intervals: returned keys are ordered and unique, overlaps reconcile deterministically,
expected active absences have explicit gap dispositions, inactive intervals remain absent and nothing
is forward-filled.

Exit gate: an immutable deterministic dataset has explicit active-session evidence, at least six
qualifying targets across three declared groups if a confirmatory R2 run is intended, per-block
coverage, request lineage and successful availability/panel replay. Failure to meet those thresholds
is retained as `INSUFFICIENT_HISTORY_FOR_MODEL_CONCLUSION`, not repaired by weakening folds or
coverage gates.

### Stage 4 — R2 software continuation and historical integration

Use the completed R2.C fold-local preprocessing and alpha-selection contract, then continue R2.D local
Ridge, R2.E pooled controls, R2.F1 evaluation/selection machinery and the corresponding R2.H software
verification. Exercise the historical foundation as a provenance-distinct integration input once it
verifies.

Exit gate: synthetic and representative integration evidence independently replays features, fits,
forecasts, metrics, selection and disposable holdout mechanics. Until Stage 5, outputs remain
`IMPLEMENTATION_EVIDENCE_ONLY` and make no model-selection claim.

### Stage 5 — R2-IBKR-HISTORICAL

Freeze the qualifying provider-history bundle, eligible target subset, market groups, feature
families, experiment set, fold ranges, thresholds and locked holdout before execution. Run the full
local, pooled-local, pooled-context and zero-return comparison with the R2 chronological OOF,
selection and holdout workflow.

Exit gate: independently verified positive, negative or inconclusive evidence limited to the named
IBKR products and historical availability/revision assumptions. A credible negative result stops
unnecessary one-second expansion unless another bounded question is predeclared.

### Stage 6 — isolated live capture host and qualification

Deploy IB Gateway, q-trad ingest, PostgreSQL, read-only API, backup, restore verification and private
operator GUI/tunnel on an isolated host. Version one uses operator-assisted login, Gateway daily
auto-restart, authentication alerts, bounded fail-closed recovery, pinned installer/image identities
and explicit upgrades. It does not automate credentials or 2FA.

Qualify progressively with two instruments, six confirmatory instruments and then the accepted full
universe. Require exact source/universe/image identity, recovery testing, backup/restore verification,
no unexplained gaps or drops and at least one complete multi-region trading cycle. Capture completion
also requires observation across at least one complete weekly reauthentication boundary: expiry is
detected, capture fails closed while authentication is unavailable, the operator alert is delivered,
manual login recovery creates a new connection generation, subscriptions are reconstructed exactly
and any unavailable interval is accounted for without hidden loss. Until that boundary is observed,
report `GATEWAY_WEEKLY_LIFECYCLE_UNQUALIFIED`; the host and capture track are not complete.

### Stage 7 — R2-IBKR-NATIVE

After sufficient live quote-derived history accumulates, build and verify a distinct IBKR-native
foundation and run the source-specific R2 workflow. Compare with the historical result as a transfer
and assumption check. Do not retroactively label historical availability as measured or silently
reuse a consumed historical holdout.

### Stage 8 — optional cross-provider augmentation

Consider only after verified IBKR historical, IBKR-native and sufficient untouched IG-native evidence
exist. Register a distinct augmentation experiment comparing IG-native-only training, augmented
training and required controls against a newly frozen untouched IG-native holdout. External rows
never enter that holdout. Any conclusion is limited to the registered transfer question and cannot
substitute for IG executable-price, spread, slippage or paper evidence.

## 9. CLI shape

Expected bounded commands are:

```text
qtrad instruments review --provider ibkr --environment paper
qtrad historical ibkr plan
qtrad historical ibkr register
qtrad historical ibkr execute
qtrad historical ibkr verify
qtrad research observations build-provider-history
qtrad research observations verify-provider-history
qtrad research foundation build
qtrad research foundation verify
qtrad ingest --provider ibkr --environment paper
```

Exact parser structure may follow existing conventions, but command roles remain separate: planning
is pure, registration records immutable intent, execution performs account I/O, verification performs
no provider or database I/O, and research construction consumes only verified manifested evidence.

## 10. Verification

Every code-bearing stage runs its complete affected test modules plus:

```text
uv run ruff format --check .
uv run ruff check .
uv run pyright
uv run ty check
```

Schema, artefact, collector/runtime, milestone and release boundaries run `ops/dev/verify.sh`, which
uses a disposable local PostgreSQL database and the complete project suite.

Required evidence categories include:

- mapping ambiguity, entitlement and market-data-type failures;
- no-order reachability and credential redaction;
- generation-authenticated callback arrival order, identity-specific replay/idempotency, distinct
  payload-equal callbacks, one-sided/crossed states and reconnect generations;
- UTC, session, RTH and half-open interval semantics;
- contiguous/non-overlapping request plans, ordered unique returned keys, deterministic overlap
  reconciliation, active-interval gap dispositions, absent inactive intervals and no forward filling;
- pacing limits, resumability, chunk continuity and restart behaviour;
- immutable refetch, file/hash, manifest and semantic identity verification;
- source-separated observations and rejection of mixed or substituted children;
- independent `MarketDataSourceClass` and R2 `EvidenceClass` bindings through foundation, feature,
  forecast and report identities;
- provider-history `available_at` and versioned availability-selector replay without fabricated native
  receive/persistence lineage;
- deterministic rebuild under reversed input iteration where ordering is not itself market evidence;
- R1 causal target/fold/holdout invariants and R2 feature/fit/forecast/metric replay;
- weekly Gateway reauthentication expiry, fail-closed state, alert, new generation, exact subscription
  recovery and interval accounting; and
- progressive account-gated and live qualification scenarios.

Tests and verification assets encode the observable contract and are never weakened to accommodate
provider data. Credential-gated, deployment and external-write checks run only under separate current
authority.

## 11. Pull-request sequence

1. Stage 0 documentation, ADR and governance.
2. Capability-probe contracts, direct client boundary and exact mapping review.
3. Historical planner, pacing, immutable persistence and bounded acquisition.
4. Provider-history observation/foundation path and independent verification.
5. Live adapter, health, reconnect and provider-neutral runtime composition.
6. Isolated host, Gateway lifecycle, deployment descriptor, backups and runbook.
7. Historical R2 integration and experiment registration.
8. Native qualification and first IBKR-native foundation.

Historical work may precede the live-host PR after Stage 1 proves the account path. R2.C–R2.F1 may
proceed concurrently under the existing R2 plan.

## 12. Completion definitions

**Stage 0 complete:** the decision, normative plan and active-document reconciliation are accepted.

**Software complete:** adapter, planner, live ingestion composition, provider-history foundation,
independent verification and host lifecycle pass fixture evidence without claiming a dataset or host.

**Historical bootstrap complete:** an immutable verified 16+ week one-minute MIDPOINT foundation can
enter the frozen source-specific R2 workflow.

**Capture complete:** the accepted universe streams into its independent canonical store with
truthful health, reconnect recovery, backup and restore evidence, and the operator-authenticated
Gateway lifecycle has passed a complete weekly reauthentication boundary. A host observed for less
than that boundary remains `GATEWAY_WEEKLY_LIFECYCLE_UNQUALIFIED` and is not capture-complete.

**Research objective complete:** R2 reports a verified positive, negative or inconclusive IBKR
historical result and, where justified, an IBKR-native comparison, without exceeding source evidence.

None of these states authorises broker orders, real capital, production trading or an IG conclusion.

## 13. Deferred register

Defer automated Gateway credential/2FA injection, production or live-account connectivity, order
submission, public GUI exposure, full-universe one-second backfill, source-mixed foundations,
continuous contract synthesis, inferred product mappings and automatic experiment promotion. Revisit
only through explicit evidence and, for durable boundary changes, a superseding ADR.

## 14. Robustness implementation status (2026-08-01)

The shared IBKR session layer is now the required boundary for capability and future continuous
capture. It models socket generation, handshake, server time, farm capability, desired
subscriptions and bounded gateway escalation. Documented system codes are centralised, unknown
request errors are isolated, and unknown global codes become visible degraded state. `1101`
requests one exact resubscription epoch; repeated notifications do not duplicate it. Pacing and
capability evidence can use the PostgreSQL ledger and identity-bound atomic checkpoints.

Runtime settings default to matched API/Gateway 10.49, retain 10.45 as rollback, expose the
documented timeouts and reject mismatched versions. The API health model persists reason codes,
recovery action and sanitised attributes; `/api/v1/system` exposes them for the host health timer.
The `ops/ibkr` templates describe q-trad-2 mount, localhost socket, immutable-image, systemd and
operator-intervention assertions. Licensed archives, host wrappers and credentials remain a
separate deployment operation.
