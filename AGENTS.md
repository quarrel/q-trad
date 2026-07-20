# q-trad agent instructions

## Purpose and authority

q-trad exists to find out, as efficiently and honestly as possible, whether short-horizon
factor-style strategies can produce useful results after realistic costs. The system must support
many strategies running continuously against live data, mostly in shadow paper mode, so their
forecasts and hypothetical outcomes can be compared, ranked, selected for the current market state
and eventually retired when they no longer justify attention.

Read only the documents needed for the work. The normal critical path is:

1. `AGENTS.md`
2. `PLAN.md`
3. `docs/STATUS.md`
4. the relevant part of `docs/ARCHITECTURE.md`
5. accepted ADRs and task-specific runbooks only when the change touches their decision or operation
6. `PREPLAN.md` for product vocabulary and longer-term intent

`docs/archive/` is historical evidence, not required context. Do not read it unless reconstructing a
past decision or incident. Progress history belongs in the archive, not in active planning documents.

Keep active documentation concise. Update a document only when its current truth changes. Do not
make every agent repeatedly ingest completed work, command transcripts, hashes or dated evidence.
Archive material that remains useful for reconstruction but no longer guides the next decision.

## Current phase

The current phase is the **research-framework proof**:

> reliable IG demo data → reviewed research universe → comparable strategy forecasts → causal
> shadow paper outcomes → simple effectiveness ranking → reproducible experiment report.

The existing seven-market `capture-v1` collector is an ingestion proof and is currently running on
OCI. Treat it as evidence-bearing operational state. The next intended universe is approximately 20
liquid IG demo markets, beginning with `config/capture-v2-candidates.toml`; that catalogue has no
selection or deployment authority.

This phase may implement historical replay, signal strategies, shadow paper execution, fixed sizing
and limits, virtual positions/P&L, strategy evaluation, simple market-state observations and
deterministic ranking. It must not add an IG order operation, production IG endpoint, broker-order
port, automatic real-capital promotion or live execution.

When experimental learning and operational hardening compete, prefer experimental learning unless
the missing hardening could make a strategy conclusion materially false, irreproducible, unsafe or
cause loss of the research data needed to support it.

## Product model

- A **signal strategy** emits a timestamped forecast or target for a declared instrument set,
  horizon and return definition.
- A **strategy evaluator** pairs forecasts with later outcomes and calculates comparable,
  time-ordered measures such as Rank IC, cost-aware paper P&L and stability.
- A **market-state model** describes contemporaneously observable conditions; it does not claim an
  objective regime or select a strategy by itself.
- A **strategy selector** ranks strategies using only information available at the decision time and
  records eligibility or selection.
- An **allocation engine** applies capital/risk budgets to selected strategies. Selection and sizing
  are different decisions.
- Unselected strategies normally remain `SHADOW` so their forecasts and hypothetical outcomes are
  retained. A bounded lifecycle may later move strategies through `CANDIDATE`, `SHADOW`, `ELIGIBLE`,
  `SELECTED`, `PAUSED` and `RETIRED`.

The first implementation proves these contracts with a few simple strategies and a transparent
ranking rule. It does not need sophisticated regime inference, automated pruning or a claim of
profitability.

## Research validity

Correctness is required where an error could create false confidence:

- no look-ahead, including warm-up, regime labels, selection and outcome windows;
- explicit source, receive and decision time in UTC;
- executable bid/ask evidence rather than midpoint fills;
- explicit spread, latency, adverse slippage and unsupported cost assumptions;
- `Decimal` for prices, quantities and money;
- known product economics, sessions and currency conversion for paper eligibility;
- visible gaps, dropped callbacks, stale inputs and excluded intervals;
- retained forecasts for selected and unselected strategies;
- deterministic datasets, configuration, replay, scoring and reports;
- independent P&L arithmetic and simple benchmarks;
- time-ordered out-of-sample evaluation before an effectiveness claim.

Rank IC is an evaluation input, not profitability. Its forecast unit, horizon, cross-sectional or
time-series basis, rolling window, overlapping observations, minimum sample and regime conditioning
must be versioned before results are compared.

## Architecture and implementation

- Keep one modular Python application and image. Add a process only for demonstrated lifecycle or
  failure isolation.
- Dependency direction is `domain ← ports ← application ← adapters/runtime/API`.
- Domain code must not import frameworks, provider libraries, environment configuration or
  filesystem code.
- Convert provider values at adapter boundaries and prevent provider identifiers from becoming
  canonical identity.
- Use frozen domain values, injected clocks and synchronous deterministic transformations.
- Keep `asyncio` at I/O/orchestration boundaries.
- Prefer functions and composition over pass-through service layers. Allow local duplication until
  two concrete uses demonstrate a stable shared invariant.
- Do not add Redis, Kafka, Celery, TimescaleDB, React, Kubernetes or another top-level runtime
  product without measured need and an ADR.
- Do not build a market-state, selector, sleeve, allocation or execution abstraction beyond what a
  current experiment exercises.

## Experimental compatibility and data retention

Before the first decision-grade strategy result, internal schemas, events and APIs may change
incompatibly. Prefer a documented one-time migration, re-export or clean rebuild over dual readers,
dual writers and indefinite legacy compatibility.

Classify state before changing it:

- development databases, projections and failed local experiments are disposable;
- market data or manifests cited by a retained result are research evidence;
- material collector failures may be retained as incident evidence;
- the live collector's raw and canonical history is operational evidence until a reviewed snapshot,
  retention or replacement decision says otherwise.

Never rewrite or selectively delete the running collector's raw or canonical history. This rule
protects current evidence; it does not require every future schema to read every experimental epoch.

## External I/O and collector safety

- The collector is IG demo data-only. No production endpoint or order submission may exist.
- Treat broker, stream and database connections as explicit lifecycles with bounded readiness,
  recovery and shutdown.
- Readiness requires relevant channel evidence; a connected socket or aggregate activity is not
  sufficient. Quiet prices alone are not transport failure.
- Tag callbacks with a connection generation and ignore superseded work.
- Bound queues and make loss, lag and recovery exhaustion visible. An unbounded collector ends only
  as explicit `STOPPED` or truthful `FAILED`.
- Do not deploy, restart, migrate, reconcile or maintain the collector during an active measurement
  interval unless its reviewed protocol requires it.
- Collector observation is read-only by default. Deployment, provider experiments, evidence writes
  and cloud changes require their task-specific runbook and explicit authority.
- Never expose credentials, account identifiers, rendered secret-bearing configuration or session
  tokens in logs, fixtures, events, tools or version control.

## Working method

Before changing code, read the active `PLAN.md` milestone, `docs/STATUS.md`, existing contracts and
relevant tests. Confirm the change advances the research-framework proof or a necessary correctness
boundary.

Run checks in proportion to the change:

- focused tests and static checks while iterating;
- the complete clean PostgreSQL, formatting, lint, typing and test gate at a milestone or release
  boundary;
- credential-gated or endurance checks only when their behaviour is the subject of the change.

Update `PLAN.md`, `docs/STATUS.md` or `docs/ARCHITECTURE.md` only when their claims changed. Add or
supersede an ADR only for a durable architectural decision, not ordinary implementation detail.

Stop rather than guess when an instrument mapping, timestamp, price basis, product economics or
currency conversion is ambiguous; when a change could reach a live broker endpoint; or when
credentials or evidence could be exposed.

Use Python 3.13, `uv`, en-GB text, Ruff and strict typing in domain, ports and application code.
Unexpected required-field and computation failures must propagate with context rather than becoming
plausible defaults.
