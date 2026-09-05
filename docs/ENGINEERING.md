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

q-trad is a single-operator research system: useful results and fast iteration both matter.
Processing paths must be practical at their intended scale in runtime, memory and disk use.
Choose the simplest approach that meets the task, and address obvious structural waste.

Before implementing substantial processing changes, give a brief performance argument in the
existing plan or task notes and update it with the result for review:
the relevant workload and baseline, dominant costs across stages, reuse boundaries, resource bounds
and evidence sufficient to establish practical acceptance. Cover only dimensions that could change
the decision. Use existing budgets or propose reasonable targets from evidence; distinguish estimates
from measurements. Routine changes need no separate performance section or benchmark.

Inspect how expensive work scales across rows, partitions, folds, models and consumers. Avoid
unnecessary scans, decoding, hashing, feature construction, copies and intermediate storage. Bound
working sets and concurrency, including worker copies and queued work. Reuse unchanged preparation
where semantics permit, preserving causal fold-specific fits and independent evidence claims.
Evidence handoffs remain governed by `docs/EVIDENCE_GOVERNANCE.md`.

Choose CPU or GPU for meaningful end-to-end benefit on the target hardware. When that choice is
uncertain and material, use a bounded comparison against a credible CPU path, including preparation,
transfers, synchronisation and output handling. Existing applicable evidence may settle it; do not
build another backend just to complete a comparison. GPU utilisation alone is not evidence of speed.
Preserve numerical requirements and any frozen scientific runtime policy.

Select evidence to resolve the actual risk: existing measurements, deterministic work counts,
focused timings, resource projections or a representative end-to-end benchmark. A small sample
supports only the dimensions it exercises; test or project remaining scale risks with stated
assumptions. Use elapsed-time and peak-resource measurements when proxies cannot settle feasibility.
Identify the workload and relevant runtime conditions so evidence can be interpreted and reused.

Stop when correctness and practical acceptance are established. Investigate the dominant cost if
they are not; revise the approach when necessary instead of repeatedly launching expensive runs.
Further optimisation, infrastructure or abstraction needs a concrete current benefit. Review findings
must identify material waste, a violated requirement or an unresolved risk, rather than a merely
possible faster alternative. This guidance creates no additional scientific execution or approval gate.

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

Add infrastructure or abstraction only for a demonstrated current need. A new package, deployable
process, datastore, framework or port implementation deserves scrutiny for the complexity it adds.
Use an ADR when the decision is durable and costly to reverse or an existing authority requires one;
a reversible local implementation choice does not need an ADR merely because it fits that list.

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

### Testing proportionality

Protect meaningful behaviour with the smallest discriminating test. Preserve the real integration,
persistence, causal or scale boundary being tested; choose fixtures, parameter sets and test doubles
accordingly. Reversible low-impact edits need no new test that merely mirrors the implementation.

Treat test runtime, including setup/teardown and cumulative reruns, as an engineering cost. Use existing
duration evidence or focused measurement to assess materially expensive additions. A test that adds
30 minutes to a four-minute suite needs redesign or a justified separate invocation before acceptance.
Genuinely necessary scale/endurance checks belong at a documented run boundary, with actual selection
keeping them out of routine iteration. Preserve required claims and gates; never hide a slowdown by
silently dropping coverage or report an interrupted/skipped check as passing.

Choose focused checks during iteration and the complete gate at the boundaries in
`docs/DEVELOPMENT.md`. Once sufficient evidence passes, repeat or broaden checks only for a change,
failure or unresolved concern that affects it. Carry forward unaffected evidence with its provenance;
required final-candidate checks still apply. Reviewers assess evidence rather than routinely rerunning
it. Stop when required behaviour and practical validation cost are established.

Owners account for validation cost and reassess unexpected slowdowns before equivalent reruns.
MAP's `Validation cost and elapsed progress` section owns programme-level intervention.

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

Follow `docs/DEVELOPMENT.md` for complete-gate boundaries and invocation. Run credential-gated and
endurance checks when required by the changed behaviour or active plan; report skipped checks accurately.

## Documentation budget

- Keep active documentation about current intent, facts, risks and next decisions.
- Archive completed chronology, hashes and incident narratives outside the routine reading path.
- Update only documents whose claims changed; an ordinary code change does not require edits to
  every governance file.
- Use an ADR for a durable, costly-to-reverse decision, not a reversible experiment detail.
- Maintain one home for each engineering principle; role instructions should reference it and state
  responsibility. After an incident, first determine whether an existing rule, a code regression
  test or clearer project context addresses the cause. Consolidate overlapping guidance instead of
  accumulating mandatory procedures. Keep model-specific tuning separate from durable project rules.
