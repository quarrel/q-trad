# Fixture daily universe laboratory

This namespace implements the approved U1/U2 vertical slice using wholly invented prices,
products, source dates, reserve dates and UTC sessions. It is IMPLEMENTATION_ONLY.
Empirical inputs are NONE_APPROVED. There is no empirical adapter or access-isolation claim.

Run from the checkout:

```sh
uv run -m experiments.universe_discovery \
  --output-root tmp/agents/u-lane-20260909/u12/fixtures \
  --run-id fixture-001 --families 24
```

Use a new run ID for each attempt. The ordinary append-only run register records starts,
completions and failures. The experiment specification and entire selection register are saved
before outcome scoring. Source-file hashes supplement the Git commit for uncommitted runs.
The synthetic panel uses Parquet, with exact Decimal values serialised as strings.

The batch prepares each market once and calculates each market/vintage/probe position ledger
once. The 100 fixed broad and matched draws, original synthetic anchor cohort and size-matched
anchor views reuse those outcomes, as do base and doubled variable-friction scenarios.
The CLI writes the panel, catalogue, specification, past-only atlas, complete cohort/assignment
register, physical position ledger, market tail/coverage diagnostics, reconciled reducers and
measured CPU performance. A valid zero signal contributes a flat zero. Missing or delayed signals
and missing or too-small entry scales make that market/vintage unavailable, with session-level
reasons and separate raw-data/payoff coverage. Existing positions retain their entry scale.
A smaller selected cohort is explicitly flagged when frozen group/cluster constraints prevent
the requested size.

The primary reducer requires every predeclared comparison. Its matched-complete sensitivity is
separate. Signed selected-slot contributions reconcile to the equal-vintage matched delta before
positive concentration clipping. Whole-quarter bootstrap intervals are descriptive.

Each market catalogue here is a synthetic, frozen schedule, not a bitemporal empirical instrument
master. Contract dates are the authorised last tradable dates, including delivery avoidance.
Real exchange sessions, historical membership, contract economics, cost and carry schedules,
timestamps and release lags require separate qualification. The fixture calendar has weekdays and
no holidays. No real mapping, period comparison, reserve access, provider call, FX/capital return,
intraday stop/depth/limit exit or empirical alpha conclusion is supported. A delayed-close fill
cannot be inferred from daily OHLC and is explicitly unavailable. The loader's predecode rejection
is defence in depth; it does not establish actual filesystem, shell or connector isolation.

Focused checks:

```sh
uv run pytest -q tests/experiments/universe_discovery
uv run ruff check experiments/universe_discovery tests/experiments/universe_discovery
uv run ruff format --check experiments/universe_discovery tests/experiments/universe_discovery
uv run pyright experiments/universe_discovery tests/experiments/universe_discovery
```

Strict Pyright directives apply throughout this namespace. The application and shared configuration
are unchanged; these fixtures do not require the full application test suite.
