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

No broker-order port, IG order operation or production IG endpoint exists.

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
- `adapters`: IG, PostgreSQL, Parquet and fixture implementations;
- `runtime/API`: settings, composition, commands and read-only presentation.

The source planning document's package tree is not adopted. New research components follow these
existing boundaries and are added only when their milestone begins. Provider identifiers and
library types stop at adapter boundaries; domain code imports no model framework, database,
environment, filesystem or provider library.

## Collector and research boundary

The OCI collector is capture-only. Research, model training and paper processes do not write to its
database. A verified snapshot is restored into an isolated `qtrad_research_*` database, then exported
to immutable manifested datasets. Routine development requires neither collector credentials nor
collector access.

The current 20-market `capture-v3` release remains active. The established catalogue →
non-authoritative provider review → operator selection flow accepted China A50, Taiwan and a
context-only AUD VIX, quarantined Korea 200 and Bitcoin, and produced an undeployed immutable
23-market `capture-v4`. Publication and activation remain separate authorised boundaries.

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

Forecast, economic and portfolio gates remain separate. Reports bind dataset, code, configuration,
folds and outputs and retain failed/rejected experiments. A negative result is valid; Rank IC alone
is never described as profitability.

## Compatibility and evidence

Before the first decision-grade result, experimental schemas and APIs may change incompatibly.
Prefer one-time migration, re-export or clean rebuild to indefinite dual compatibility. Retained
datasets and configurations cited by a result remain reproducible.

The running collector's raw and canonical history is never rewritten or selectively deleted.
Historical plans, qualification protocols and superseded architecture live under `docs/archive/`;
durable current decisions remain in `docs/adr/`.
