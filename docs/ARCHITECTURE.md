# q-trad architecture

## Purpose and status

q-trad is a modular Python application for trustworthy short-horizon strategy experiments. The
implemented data foundation feeds a planned research loop in which many strategies can be evaluated
continuously without receiving external capital.

Implemented today:

> IG demo/fixtures → raw capture → canonical quote and bar events → PostgreSQL projections → Parquet
> datasets → deterministic replay → read-only API/console.

Current intended extension:

> canonical data → strategy forecasts → shadow paper fills and ledgers → realised outcomes → strategy
> scores → market-state-aware ranking and experiment reports.

No broker-order port, IG order operation or production IG endpoint exists.

## Shape

One codebase and application image contains separately testable modules and command roles. PostgreSQL
is the canonical/operational store; Parquet is the research dataset format. Process roles may be
separated for lifecycle isolation, but they remain one modular monolith.

Dependency direction:

```text
domain ← ports ← application ← adapters/runtime/API
```

- `domain`: immutable values and deterministic transformations;
- `ports`: narrow provider-neutral I/O contracts;
- `application`: use cases and orchestration;
- `adapters`: IG, PostgreSQL, Parquet and fixtures;
- `runtime/API`: settings, composition, commands and presentation.

Provider identifiers and library types stop at adapter boundaries. Domain code has no framework,
database, environment, filesystem or provider-library dependency.

## Data foundation

PostgreSQL retains redacted raw inputs, immutable canonical events, reference data, rebuildable read
models and operational checkpoints. Accepted raw input and its canonical or quarantine outcome are
committed atomically. Canonical streams use stable identities and optimistic versions.

Market-data conventions:

- UTC half-open one-minute intervals;
- distinct bid, ask and midpoint bars;
- midpoint samples require sufficiently contemporaneous sides;
- transport receive-time progress, not processing wall time, advances the lateness watermark;
- later observations create revisions rather than rewriting events;
- missing executable prices are never forward-filled;
- source, receive and persistence times remain distinct;
- observed gaps and internal drops remain visible and can exclude an interval from research.

The running OCI collector is capture-only. Research, development and paper processes must not write
to its database. Snapshot/import and release operations use the checked-in capture runbook. Routine
development does not require collector access.

## Research loop

### Signal strategies

A strategy consumes ordered eligible observations and emits a comparable forecast or target with
strategy/configuration identity, applicability, decision time, horizon, strength and rationale.
Warm-up may update state but cannot emit an executable instruction.

### Shadow paper accounting

Unselected strategies normally remain `SHADOW`. Fixed sizing and limits translate targets into
hypothetical paper orders. Only later healthy bid/ask evidence can fill them. Each strategy retains
an isolated virtual ledger and explicit spread/slippage/cost assumptions.

### Evaluation

The evaluator joins forecasts to their defined later outcomes without look-ahead. Metric contracts
are versioned and include their horizon, window, sampling and exclusion rules. Rank IC, paper P&L,
drawdown, turnover, coverage and sensitivity serve different purposes and remain separately visible.

### Market state and selection

The market-state model records contemporaneously observable features and may return `UNKNOWN`.
The selector ranks or admits strategies using only prior evaluation evidence and the state known at
that time. It records every decision. The allocation engine separately applies capital/risk limits.

The framework proof may implement these as simple functions and reports. Separate services,
hierarchical sleeves and automated lifecycle transitions are introduced only when a concrete
experiment needs differing policies.

## Universe

`capture-v1` contains seven ingestion-proof markets. `config/capture-v2-candidates.toml` proposes a
research universe of eight FX pairs, eight equity indices, spot gold, spot silver, Bitcoin/USD and
US crude. It is deliberately non-authoritative:
provider review and explicit operator selection must precede a capture release.

A strategy declares whether it applies to one instrument, a subset, a cross-section or a basket.
Capture eligibility and paper eligibility are separate; an instrument may be captured while missing
economics or session evidence keeps it out of paper execution.

## Correctness boundary

Research claims require causal timestamps, explicit eligible intervals, executable-side fills,
cost assumptions, deterministic replay, retained unselected forecasts, independent accounting and
time-ordered held-out evaluation. These protections are not optional simplifications.

Production-style release evidence, broad compatibility, highly available operation and exhaustive
operator tooling are not architectural goals in this phase. Add them only when they protect current
research evidence or a measured operational risk.

## Compatibility and evidence

Disposable development state and pre-result experimental schemas may be rebuilt or migrated once.
Datasets and configurations cited by a retained result must remain reproducible. The running
collector's raw/canonical history is not rewritten or selectively deleted; later code need not
support every earlier experimental writer indefinitely.

Operational history and superseded architectural detail live under `docs/archive/`. Durable accepted
decisions remain in `docs/adr/`; task-specific procedures remain in runbooks and are loaded only when
needed.
