# q-trad architecture

## Purpose and status

q-trad is a modular Python application for trustworthy short-horizon multi-asset research and
internal paper portfolio evaluation.

Implemented data foundation:

> IG demo/fixtures → raw capture → canonical quote and bar events → PostgreSQL projections →
> versioned Parquet datasets → deterministic replay → read-only health views.

Implemented retained framework proof:

> completed midpoint bars → simple single-horizon strategy forecasts → exact-horizon outcomes;
> subsequent healthy bid/ask quotes → latency/slippage round trips → isolated ledgers → hash-bound
> score and ranking report.

That proof remains reproducible evidence for timing, fills and accounting. Its strategy-ranking and
per-forecast round-trip shapes are not compatibility requirements for the intended system.

Intended offline research flow:

> immutable research dataset → aligned multi-asset panels and targets → chronological folds →
> out-of-fold local/pooled/residual forecasts → cost and ordered risk states → horizon sleeve
> targets → global netting and constrained physical paper targets → causal paper outcomes →
> component-aware experiment reports.

Later continuous shadow flow:

> live canonical data → stable forecast stack → persistent virtual horizon positions → net paper
> position changes → subsequent executable-side fills → attributed positions/P&L and operational
> vetoes.

No broker-order port, provider order operation, production broker endpoint or live-account connection
exists.

## Shape

One codebase and application image contains separately testable modules and command roles.
PostgreSQL is the canonical/operational store; Parquet plus immutable manifests is the existing
research artefact boundary. Add a new process only for demonstrated lifecycle or failure isolation.

Dependency direction remains:

```text
domain ← ports ← application ← adapters/runtime/API
```

- `domain`: frozen values and synchronous deterministic transformations;
- `ports`: narrow provider-neutral I/O contracts;
- `application`: chronological use cases and orchestration;
- `adapters`: implemented IG/PostgreSQL/Parquet/fixture adapters and the planned isolated IBKR adapter;
- `runtime/API`: settings, provider composition, commands and read-only presentation.

The source planning document's package tree is not adopted. New research components follow these
existing boundaries and are added only when their milestone begins. Provider identifiers and
library types stop at adapter boundaries; domain code imports no model framework, database,
environment, filesystem or provider library.

## Collector and research boundary

The live IG OCI collector is capture-only. Research, model training and paper processes do not write
to its database. A verified snapshot is restored into an isolated `qtrad_research_*` database, then
exported to immutable manifested datasets. Routine development requires neither collector credentials
nor collector access.

ADR 0028 defines the intended IBKR paper source as a second independent capture runtime and
PostgreSQL event store using the same application image. Its Gateway, ingest, API, universe, backup
and restore lifecycles remain separate from IG, and it shares no stream-version history. Gateway
credentials and 2FA remain outside q-trad. The adapter exposes market data only; account access,
acquisition, deployment and publication remain separately authorised operations.

The current 23-market `capture-v4` release is active. The established catalogue → non-authoritative
provider review → operator selection flow accepted China A50, Taiwan and a context-only AUD VIX and
quarantined Korea 200 and Bitcoin. Publication and activation remain separate authorised boundaries
for any later release.

The IG adapter normally discovers listings by bounded aliases. A candidate catalogue may also bind
an exact epic as a non-authoritative review hint when provider search omits a known listing. A
reviewed active preference may use the same direct-detail fallback during discovery; explicit
selection, identity/economics checks and release hashes remain mandatory.

Canonical market-data conventions remain:

- UTC half-open one-minute intervals;
- distinct source, receive, persistence, feature, decision, training and target-availability times;
- bid, ask and midpoint facts with sufficiently contemporaneous sides;
- later corrections as revisions rather than rewritten events;
- visible gaps, dropped callbacks, stale inputs and exclusions;
- no forward-filled executable prices; and
- stable internal identities independent of provider listing identifiers.

Provider-history research uses a separate manifested observation contract with explicit request,
contract-mapping, session, correction and availability policies plus an authenticated `available_at`.
A versioned foundation availability selector consumes that field without writing assumed values into
native `received_at` or `persisted_at`; the native selector continues to use measured lineage.

The durable IBKR historical foundation handoff is:

> retained `foundation build` → one independent `foundation verify` with a create-only receipt →
> ordinary `foundation authenticate`.

The receipt binds the exact foundation, provider and child closure, configuration, readiness and
claim-scoped verifier semantics. Authentication rechecks that closure without Stage 7 or Stage 8
semantic replay. Its authority is `IMPLEMENTATION_EVIDENCE_ONLY`. For a qualifying foundation,
real IBKR F2 instead requires one operator-authorised `foundation promote-confirmatory` cumulative
replay from a clean detached immutable runtime, followed by cheap `authenticate-promotion` checks.
The create-only promotion binds the exact Stage 6–8 roots, accepted verifiers, qualifying readiness,
runtime and authorization; confirmatory OOF construction and replay must carry that attestation.

Each foundation binds one `MarketDataSourceClass`; historical, IBKR-native and IG-native observations
remain separate through feature construction, modelling and reporting. That source dimension is
orthogonal to R2 `EvidenceClass`. Foundation, experiment, feature, fit, forecast and report identities
bind both independently.

## Research contracts

### Targets and forecast lineage

Fixed-horizon targets are keyed by asset, decision time and configured horizon. The initial grid is
5, 15, 30 and 60 minutes, with a 15-minute first vertical path. Each target records its availability
time, dependency/overlap interval and deterministic gap/session disposition.

Every forecast records feature and training cut-offs, model/configuration identity,
experiment/fold identity, expected return and return unit. Out-of-fold status is proven from fold
lineage rather than trusted as a caller label. Later outcomes never mutate the earlier forecast.

### Forecast implementations

The required simple controls are per-asset Ridge and a pooled non-graph model. The committed graph
experiment predicts only residuals derived from out-of-fold local forecasts and compares fixed,
learned structural and shuffled graphs. The first temporal graph baseline is an LSTM. No graph
implementation receives architectural privilege merely because it is complex.

### Costs and portfolio risk

Cost estimation is separate from alpha and reports observed/modelled spread, adverse slippage,
commission, financing and supported impact in units compatible with the portfolio objective.

Portfolio risk is an ordered, versioned covariance/group state independent of forecast uncertainty.
The first portfolio implementation uses horizon-specific targets followed by global physical
netting, constraint repair and stable reason codes. Conflicting horizons remain visible.

Numerical model/risk arrays may use an appropriate floating-point representation. Prices,
quantities, conversion and money use `Decimal`; conversions at the numerical boundary are explicit
and tested.

### Paper outcomes and accounting

Model labels use completed-bar midpoint returns. Economic evaluation separately uses subsequent
healthy bid/ask observations after configured latency, with adverse slippage and explicit costs.
Persistent horizon attribution survives physical netting. Fill and ledger arithmetic remains
independently reproducible in the reporting currency.

The checkpoint/process architecture for continuous paper operation is intentionally decided at R6,
after offline interfaces and lifecycle requirements are demonstrated.

## Evaluation boundary

Walk-forward folds fit transformations and models only on permitted history. Purging and embargo are
derived from target, feature and update dependency windows. A locked final holdout is unavailable to
feature, model, calibration, risk and solver selection.

Confirmatory preparation and reveal are capability-gated. A persisted G1 selection is independently replayed
from verified F2 before it can authorise final fitting. The resulting G2 preparation derives every scientific
choice and R2.B feature row from that authority, fits the exact selected-plus-retained dependency set, and
seals forecasts and explicit opportunity coverage as `OWNED_UNOPENED`. Independent verification recomputes
the complete feature, fit, forecast and coverage closure. Reveal first publishes create-only base and
confirmatory OPENED markers, then issues a runtime-only opened capability; only that capability may decode
the exact authenticated target child. Evaluation inherits every scientific policy from G1, successful output
is irreversibly CONSUMED, and independent R2.H replay classifies valid consumed, opened-incomplete and invalid
terminal states without reset or reopen operations.

Forecast, economic and portfolio gates remain separate. Reports bind dataset, code, configuration,
folds and outputs and retain failed/rejected experiments. A negative result is valid; Rank IC alone
is never described as profitability.

## Compatibility and evidence

Before the first decision-grade result, experimental schemas and APIs may change incompatibly.
Prefer one-time migration, re-export or clean rebuild to indefinite dual compatibility. Retained
datasets and configurations cited by a result remain reproducible.

The running IG collector's raw and canonical history and any later IBKR collector history are never
rewritten or selectively deleted. Historical proposals, qualification protocols and superseded
architecture live under `docs/archive/`; durable current decisions remain in `docs/adr/`.
