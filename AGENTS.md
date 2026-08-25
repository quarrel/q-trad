# q-trad agent instructions

## Purpose and authority

q-trad asks whether short-horizon multi-asset forecasts can produce useful paper portfolio outcomes
after realistic costs and joint risk constraints. Negative results are useful evidence. It is an
experimental research system for one operator, not a public API, production trading product or
real-capital system.

Internal research artefacts, CLIs and Python APIs have no compatibility obligation unless an active
authority names retained evidence or an operation that requires one. Design for the system that
exists.

Read the minimum authority needed:

1. Always read this file and the relevant active section of `PLAN.md`.
2. Read `docs/STATUS.md` when current operational or research state affects the work.
3. Read `docs/TRADING_RESEARCH.md` for targets, features, forecasts, models, evaluation, risk,
   portfolios or paper outcomes.
4. Read the relevant part of `docs/ARCHITECTURE.md` when changing an implemented or intended system
   boundary or flow.
5. Read `docs/EVIDENCE_GOVERNANCE.md` for identity-bearing contracts, immutable evidence,
   compatibility/migration, retained-scale execution, verification, promotion or consequential
   research authority.
6. Read `docs/DEVELOPMENT.md` for verification, PostgreSQL test and GitHub CI semantics.
7. Read accepted ADRs and the task-specific runbook only when touching their decision or operation.
8. When the operator invokes MAP, read `.codex/map/MAP_Orchestrator.md`; MAP owns generic
   multi-agent procedure, candidate search, monitoring, convergence and context rotation.

`docs/archive/` is historical evidence, not routine context. Use it only to reconstruct an incident
or decision, verify retained evidence, or handle a named surviving compatibility boundary.

An active implementation plan supplies the execution authority. File lists, function names and
mechanisms are normally soft guidance; scientific, evidence, safety and authority boundaries are hard
constraints.

## Always-on boundaries

Apply rigour where an error could invalidate the current experiment, create false confidence, corrupt
retained evidence or cross a safety boundary. Do not import public-API, multi-tenant, production or
real-capital requirements unless their absence would make the current result dishonest or unusable.

Keep these distinctions explicit:

- artefact validity is not evidential sufficiency;
- implementation evidence is not a decision-grade result;
- paper correctness is not production readiness;
- execution provenance is not semantic identity;
- verification is not promotion; and
- a later consumer owns requirements affecting only its interpretation.

Never:

- add a broker-order port, order submission operation, production broker endpoint, live-account path
  or real-capital path;
- access holdout outcomes, call a provider, reacquire data, publish/promote evidence, or perform an
  irreversible or special-state operation without its exact current authority;
- overwrite or selectively edit immutable research evidence or running collectors' raw/canonical
  history;
- allow historical or external-source evidence to masquerade as native executable evidence;
- expose credentials, account identifiers, tokens or rendered secret-bearing configuration; or
- guess an instrument mapping, timestamp, price basis, product economics, currency conversion,
  historical-product equivalence or evidence identity.

Stop and request the missing decision when any of those values is materially ambiguous or when a
change would weaken a holdout/causal boundary, discard retained evidence without an authorised
migration, reach a live endpoint, create irreversible external state, or materially enlarge the
agreed boundary.

## Research and implementation baseline

No look-ahead is permitted in features, transformations, training, calibration, risk, selection,
target windows, outcome pairing or executable evidence. Preserve explicit UTC source, receive,
feature-cutoff, decision, training-cutoff and target-availability times; dependency-derived
purging/embargo; immutable experiment/fold/model/configuration identity; source-class separation; and
exact evidence authority.

Use `Decimal` for prices, quantities, currency conversion and money. Numerical model/risk arrays may
use explicitly tested floating-point conversions. Missing markets, required fields and failed
computations do not become plausible empty/default values.

Keep one modular Python application and image. Dependency direction is
`domain ← ports ← application ← adapters/runtime/API`. Domain code imports no framework, provider,
environment, filesystem or model library. Provider identifiers stop at adapter boundaries. Use frozen
domain values, injected clocks and deterministic synchronous transformations; keep `asyncio` at I/O
boundaries.

Prefer the smallest trustworthy experiment and the smallest clean current contract. Delete obsolete
code when its replacement is authoritative. Add infrastructure, processes, dependencies,
compatibility or abstraction only for a demonstrated current need.

Use Python 3.13, `uv`, en-GB text, Ruff and strict typing in domain, ports and application code.
Unexpected required-field and computation failures propagate with context.

## Operational baseline

Collector observation is read-only by default. Deployment, provider experiments, evidence writes,
publication and cloud changes require explicit authority and the current runbook.

For authorised IG deployment use `ops/capture/deploy.sh`. For IBKR maintenance, capture,
qualification, backup or restore use the documented `ops/ibkr/` orchestration. Start one bounded
operator command and use its terminal evidence; do not decompose supported orchestration into ad-hoc
remote steps.

## Contradictions

Merged ADRs, scientific invariants, immutable evidence, holdout boundaries and explicitly retained
expensive work are hard constraints. Tactical file lists, locations, implementation mechanisms and PR
sequencing are soft unless expressly made binding.

Make the narrowest necessary exception to a soft tactic when it prevents the smallest correct
implementation, and record the reason. Never resolve a contradiction by silently weakening
correctness or by preserving obsolete complexity without a current requirement.
