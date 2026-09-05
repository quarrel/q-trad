# Engineering guidelines

## Design

- Keep the application a modular monolith.
- Keep domain code deterministic and independent of frameworks.
- Translate external data once, at the adapter boundary.
- Prefer explicit domain names and small protocols.
- Keep canonical facts immutable and make projections disposable.
- Treat completed verification of immutable evidence as reusable evidence. Downstream boundaries independently verify their own claims rather than recursively replaying unchanged ancestors unless a confirmatory or revocation policy requires it.
- Scope cache and verification identities to semantics capable of changing the protected claim. Logging, documentation and presentation changes must not invalidate data-scale work.

## Performance and resource use

Correctness includes practical execution at the intended workload. Apply this section to substantial
ingestion, transformation, verification, feature, fitting, evaluation and export changes, including
their immediate producers and consumers. Ordinary small changes need engineering judgement; when
there is no material processing impact, a short statement suffices without a benchmark or checklist.

### Planning

Include a compact performance section in the existing implementation plan or execution brief:

- **Workload and acceptance:** expected rows, bytes, partitions, instruments, folds/models and target
  hardware; the baseline and practical runtime, peak RAM/VRAM and temporary/output disk budgets.
  Distinguish measured facts, projections and proposed targets. Use existing requirements or baseline
  evidence; absence of a fixed numerical budget alone is not a reason to stop for operator input.
- **Data flow and work counts:** what each stage reads, computes, retains and writes; how expensive
  scans, decoding, hashing, feature preparation and fits scale across the affected end-to-end path.
  Identify shared work, its owner and reuse lifetime, plus work that must remain separate.
- **Resources and execution:** bound batch sizes, simultaneous representations, queued work, worker
  copies and intermediate storage. Explain material concurrency and CPU/GPU choices, including data
  preparation, serialisation and transfers, rather than assuming more workers or GPU use is faster.
- **Evidence and stopping:** name the representative validation workload, result-equivalence checks,
  useful work counts and resource measurements. State sufficient acceptance and the limit of tuning;
  expose a material feasibility gap early rather than repeatedly launching expensive runs.

Keep mechanisms advisory unless an existing authority requires them. Do not invent numerical limits,
additional approval gates or a line-by-line implementation recipe to fill this section.

### Implementation and device choice

Remove avoidable repeated work before adding acceleration. Reuse unchanged expensive preparation
across consumers where semantics permit; avoid full-data copies, repeated format conversion and
intermediate materialisation without a current consumer or recovery need. Bound reuse to the
working set rather than introducing an unbounded cache. Preserve independent verification claims
and causal fold/training boundaries: shared preparation must not leak future information or replace
required fold-specific fits. Evidence reuse follows `docs/EVIDENCE_GOVERNANCE.md`.

Choose the simplest execution that meets the workload's needs. When choosing or changing a backend,
compare a credible CPU path on the operator's target CPU (currently an i9 285K) with GPU execution
where a benefit is plausible. Include preparation, transfer, synchronisation and output costs in
elapsed time; GPU utilisation or kernel time alone does not establish acceleration. Use bounded,
appropriately shaped batches and reuse device-resident data where useful. Do not build a second
backend merely to benchmark it when existing evidence settles the choice. Keep numerical equivalence
within the experiment's declared tolerances and choose the device before freezing its runtime policy;
performance work does not authorise a silent fallback or a change to a frozen scientific release.

### Validation and restraint

Use focused deterministic work-count checks for structural regressions, such as repeated ancestor
replay, full scans or feature construction. Pair them with representative end-to-end elapsed time and
peak resource evidence where costs materially change. A tiny fixture proves only the dimensions it
exercises; use an additional scale point or a justified projection when scaling remains uncertain.
Record workload, hardware, concurrency and cache state so comparisons are interpretable. Reuse valid
measurements when the affected path and assumptions are unchanged.

Fix obvious structural waste and stop when correctness and practical acceptance criteria are met.
If they are missed, inspect the dominant cost and make a targeted correction; revise the plan when
meeting them requires material redesign. Do not expand into unrelated tuning, generic benchmarking
infrastructure, new caches, execution frameworks or speculative abstractions. A review finding must
identify a concrete material cost, violated budget or missing required evidence; the existence of a
potentially faster alternative alone is not a blocker. This guidance adds no automatic full-suite
run, scientific execution, consequential-execution classification or independent approval gate.

## Python

- Python 3.13, frozen dataclasses and strict typing in core packages.
- UTC-aware `datetime`; never call the wall clock from domain code.
- `Decimal` for prices, sizes and financial values.
- Pydantic for settings and HTTP models, not as the domain model.
- Async functions are for I/O or orchestration, not ordinary calculations.
- Structured logs use stable event names and exclude secrets.
- Construct one clock at the runtime composition root for each command invocation and
  inject that instance through the operation. Call `now()` at each event boundary; sharing
  a clock source does not mean freezing one timestamp for the whole operation.
- Contain untyped third-party libraries behind small local protocols and committed,
  version-matched `.pyi` stubs for only the APIs used. Do not exclude first-party adapter
  directories from static analysis or let provider-facing `Any` values cross the adapter
  boundary.

## Reuse

- Search before adding code.
- Extract shared code only after two concrete callers share an invariant.
- Do not create generic helpers, base repositories or service locators.
- Keep adapter policy in adapters and business invariants in the domain.
- A third-party dependency must remove more complexity than it introduces.

## Sprawl controls

An ADR is required before adding:

- a top-level package;
- another deployable process or image;
- another datastore, cache or message queue;
- a web framework or large runtime library;
- a second implementation of an existing port.

Do not scaffold future strategy/execution packages until their phase begins.

## Testing

- Unit tests protect domain invariants.
- Property tests cover ordering, idempotency and OHLC invariants.
- Contract tests apply the same cases to fixture and IG adapters.
- PostgreSQL integration tests use the real supported major version.
- Replay tests compare stable hashes, not incidental row order.
- Credential-gated and soak tests are never reported as passing when skipped.
- Measure branch coverage against the PostgreSQL-backed suite; use it to find untested
  operational paths rather than as a substitute for scenario quality.

## External connection lifecycle

- Model authentication, transport connection, subscription, data readiness, degradation,
  retry, failure and shutdown as separate states.
- Define readiness from observable application evidence. For a seven-instrument stream,
  every subscription must acknowledge and deliver a fresh valid update.
- Attach a generation to clients, callbacks and queued work so late callbacks cannot
  revive or contaminate a replacement connection.
- Apply deadlines independently to authentication, connection, subscription, first data,
  library-managed retry and shutdown.
- Keep one retry budget and rate limiter outside disposable provider client instances.
  Use classified failures, exponential full jitter, a cap and circuit-breaker cooldown.
- Emit bounded structured state transitions and provider error codes. Logs must establish
  why recovery was attempted without exposing response bodies, account data or tokens.
- Treat cleanup as behaviour to test: all callbacks quiesce, transports close, background
  tasks/threads end and the process exits.
- Stage operational validation: deterministic lifecycle tests, repeated credential-gated
  reconnects, a shorter endurance run, then the full soak.

## Static quality gates

- `uv run ruff format --check src tests`
- `uv run ruff check src tests`
- `uv run pyright`
- `uv run ty check`

Run focused checks during iteration and the complete clean database/static/test gate at a milestone
or release boundary. Credential-gated and endurance tests are required only when their behaviour is
under change; never report a skipped gate as passing.

## Documentation budget

- Keep active documentation about current intent, facts, risks and next decisions.
- Archive completed chronology, hashes and incident narratives outside the routine reading path.
- Update only documents whose claims changed; an ordinary code change does not require edits to
  every governance file.
- Use an ADR for a durable, costly-to-reverse decision, not a reversible experiment detail.
