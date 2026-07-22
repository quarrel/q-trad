# q-trad agent instructions

## Purpose and authority

q-trad exists to determine, as efficiently and honestly as possible, whether short-horizon
multi-asset forecasts can produce useful paper portfolio outcomes after realistic costs and joint
risk constraints. Negative results are useful evidence.

Read the minimum authority needed for the task:

1. Always read `AGENTS.md` and the relevant active section of `PLAN.md`.
2. Read `docs/STATUS.md` when current operational or research state affects the work.
3. Read `docs/TRADING_RESEARCH.md` only for target, feature, forecast, evaluation, risk, portfolio or
   paper work.
4. Read the relevant part of `docs/ARCHITECTURE.md` only when changing a system boundary or
   implemented/intended flow.
5. Read accepted ADRs and task-specific runbooks only when touching their decision or operation.

`docs/archive/` is historical evidence, not routine context. Consult it only to reconstruct an
incident or decision, verify retained evidence or handle a compatibility boundary that still
affects current work. Keep active documents concise and archive completed chronology.

## Current phase

The current milestone is **R0 — alignment, coverage and data readiness**:

> current native capture → reviewed APAC coverage and historical inputs → aligned causal datasets →
> multi-horizon forecasts → explicit cost/risk states → constrained offline paper portfolio → later
> continuous shadow paper.

The reviewed 23-market `capture-v4` collector is running on OCI. Its 22 non-VIX markets remain
potentially tradable subject to experiment role and paper eligibility; the AUD-denominated VIX is
context-only. Korea 200 has no eligible demo listing, and the reviewed Bitcoin listings were
unavailable; both remain quarantined. Any later publication or activation remains a separately
authorised operation.

The old single-instrument strategy-ranking report is retained framework-proof evidence. It is not
the intended model/portfolio architecture and its negative cost-aware result is not an effectiveness
claim.

The phase may implement offline target generation, walk-forward evaluation, local and pooled models,
a residual structural GNN-LSTM experiment, cost/risk models, horizon positions, portfolio
optimisation and internal paper accounting. Continuous live shadow integration begins only after the
offline MVP. It must not add an IG order operation, production IG endpoint, broker-order port or
real-capital path.

When experimental learning and operational hardening compete, prefer the shortest trustworthy
experiment unless missing hardening could make a result materially false, irreproducible, unsafe or
cause loss of required evidence.

## Product model

- A **target** defines one asset's realised return over a declared decision time and horizon, its
  availability time and dependency interval.
- A **point forecast** records expected return, return unit, feature/training cut-offs and immutable
  model/experiment/fold identity.
- An **out-of-fold forecast artefact** contains forecasts made without training on their targets and
  can be consumed without loading model code.
- A **cost estimate** describes expected physical trading costs separately from gross alpha.
- A **portfolio risk state** describes ordered joint covariance and configured group/currency
  exposures independently of forecast uncertainty.
- A **horizon sleeve** preserves asset/horizon intent, virtual position and model attribution.
- A **target portfolio** nets horizon intents, applies global constraints and records adjustment
  reason codes.
- A **paper evaluator** pairs decisions with subsequent executable-side evidence and independently
  reconciles gross performance, costs and net P&L.
- An **experiment evaluator** compares forecast, economic and portfolio outcomes in locked time
  order and retains failed/rejected configurations.

The offline MVP has no runtime strategy selector, learned regime gate or automatic promotion
lifecycle. Models and configurations remain experiments until held-out evidence supports retention.

## Research validity

Correctness is required where an error could create false confidence:

- no look-ahead in features, transformations, training, residuals, warm-up, calibration, risk,
  selection, target windows or outcome pairing;
- explicit source, receive, feature cut-off, decision, training cut-off and target-availability times
  in UTC;
- dependency-derived purging/embargo for overlapping target, feature and update windows;
- immutable dataset, experiment, fold, model and configuration identity;
- executable bid/ask evidence rather than midpoint fills;
- explicit spread, latency, adverse slippage, commission, financing and unsupported assumptions;
- `Decimal` for prices, quantities, currency conversion and money; explicit tested conversion to
  numerical model/risk arrays;
- known product economics, sessions and conversion for paper eligibility;
- visible gaps, dropped callbacks, stale inputs, unavailable assets and excluded intervals;
- no forward-filled executable prices and no silent coercion of missing markets into aligned panels;
- external history retains venue/product provenance and never masquerades as native IG fill evidence;
- top-of-book size changes are not called trade volume or CVD without a validated volume source;
- retained forecasts and outcomes for every compared configuration;
- independent P&L arithmetic, simple controls and component-aware ablations; and
- locked time-ordered out-of-sample evaluation before any effectiveness claim.

Rank correlation is one forecast input, not profitability. Forecast uncertainty, expected return,
portfolio covariance and operational validity remain separate concepts.

## Architecture and implementation

- Keep one modular Python application and image. Add a process only for demonstrated lifecycle or
  failure isolation.
- Dependency direction is `domain ← ports ← application ← adapters/runtime/API`.
- Domain code must not import frameworks, model libraries, provider libraries, environment
  configuration or filesystem code.
- Convert provider values at adapter boundaries and prevent provider identifiers from becoming
  canonical identity.
- Use frozen domain values, injected clocks and synchronous deterministic transformations.
- Keep `asyncio` at I/O/orchestration boundaries.
- Prefer functions and composition over pass-through service layers. Allow local duplication until
  two concrete uses demonstrate a stable shared invariant.
- Do not adopt the archived source plan's package/class tree. Extend existing boundaries only when
  the active milestone exercises them.
- Do not add Redis, Kafka, Celery, TimescaleDB, React, Kubernetes, another datastore or another
  top-level runtime product without measured need and an ADR.
- Do not scaffold probabilistic forecasts, conformal calibration, session experts, dynamic graphs,
  context nodes, ensembles or multi-period optimisation before an active experiment uses them.
- Choose solver and numerical/model dependencies at milestone entry using version-specific
  documentation; no research suggestion alone makes a library an architectural requirement.

## Experimental compatibility and evidence

Before the first decision-grade result, internal research schemas, events and APIs may change
incompatibly. Prefer a documented one-time migration, re-export or clean rebuild over dual readers,
dual writers and indefinite legacy compatibility.

Classify state before changing it:

- development databases, projections and failed local experiments are disposable unless retained by
  an experiment record;
- market data, manifests, forecasts and configurations cited by a retained result are research
  evidence;
- material collector failures may be retained as incident evidence; and
- the collector's raw and canonical history is operational evidence until a reviewed retention or
  replacement decision says otherwise.

Never rewrite or selectively delete the running collector's raw or canonical history. This does not
require future research schemas to read every experimental epoch.

## External I/O and collector safety

- The collector is IG demo data-only. No production endpoint or order submission may exist.
- Treat broker, stream and database connections as explicit lifecycles with bounded readiness,
  recovery and shutdown.
- Readiness requires per-channel evidence; a connected socket or aggregate activity is insufficient.
  Quiet prices alone are not transport failure.
- Tag callbacks with a connection generation and ignore superseded work.
- Bound queues and make loss, lag and recovery exhaustion visible. An unbounded collector ends only
  as explicit `STOPPED` or truthful `FAILED`.
- Collector observation is read-only by default. Publication, deployment, provider experiments,
  evidence writes and cloud changes require the current runbook and explicit authority.
- Keep the active `capture-v4` collector running. Never infer a Korea mapping, promote quarantined
  Bitcoin or activate a candidate catalogue directly.
- Never expose credentials, account identifiers, rendered secret-bearing configuration or session
  tokens in logs, fixtures, events, tools or version control.

## Working method

Before changing code, read the active `PLAN.md` milestone, relevant implemented contracts and tests,
plus `docs/TRADING_RESEARCH.md` for research/paper work. Confirm the change advances the active
research programme or protects a required correctness boundary.

Run checks in proportion to the change:

- focused tests and static checks while iterating;
- the complete clean PostgreSQL, formatting, lint, typing and test gate at a milestone, schema or
  release boundary; and
- credential-gated or endurance checks only when their behaviour is under review.

Update active documents only when their current claims change. Add or supersede an ADR for a durable
architectural decision, not ordinary implementation detail or a reversible model experiment.

Stop rather than guess when an instrument mapping, timestamp, price basis, product economics,
currency conversion or historical-product equivalence is ambiguous; when a change could reach a
live broker endpoint; or when credentials/evidence could be exposed.

Use Python 3.13, `uv`, en-GB text, Ruff and strict typing in domain, ports and application code.
Unexpected required-field and computation failures propagate with context rather than becoming
plausible defaults.

## GitHub workflow verification

The GitHub MCP fine-grained PAT cannot be granted Checks API access. This is an expected platform
limitation, not a missing permission or merge blocker. Verify CI through GitHub Actions workflow
runs for the exact commit instead of repeatedly raising unavailable check-run access.
