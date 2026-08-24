# ADR 0030: two-stage sleeves and one physical cost boundary

- **Status:** Accepted
- **Date:** 2026-08-24
- **Supersedes:** none

## Context

R3 keeps asset/horizon intent visible while constructing one constrained physical paper
target.  Charging each virtual sleeve independently would charge opposing intents
twice and would make later global repair impossible to reconcile.  This boundary is
durable across R3--R6 because it defines optimisation input, cost semantics,
accounting and future attribution.

## Decision

Adopt two stages:

1. Each asset/horizon sleeve carries a gross forecast, requested virtual target and
   model lineage.  Sleeve optimisation does not book an external trade or physical
   transaction cost.
2. One physical stage consumes the ordered sleeve intents and the current physical
   state.  Opposing deltas are matched in stable sleeve-key order as internal
   transfers.  The remaining net request is solved and repaired into one physical
   target per asset.
3. Transaction components (spread, latency movement, adverse slippage, commission and
   supported impact) are charged exactly once on the final physical quantity delta
   (target - current).  Financing is charged exactly once on the resulting physical
   holding and elapsed holding interval.  Internal crosses have zero external cost.
4. A solver or constraint repair may retain, reduce or close requested exposure, but
   may not invent a new alpha direction.  A zero forecast cannot open, increase
   or reverse alpha-driven exposure.
5. Final physical movement, component costs and P&L are allocated back to sleeves in
   canonical sleeve-key order.  Decimal largest-remainder allocation resolves
   quantity and money residuals; ties are broken by the stable sleeve key.  Repair
   attribution is explicit and cannot be labelled as alpha intent.
6. Forecasts remain gross.  Expected physical cost is a separate versioned state.
   Expected net return/contribution is derived as gross contribution less the complete
   once-calculated physical cost.  Evaluators recompute net fields and reject
   caller-supplied totals that do not reconcile.

## Contracts and fail-closed semantics

The provider-neutral R3 economics module freezes immutable Decimal contracts for
product economics, causal FX, schedules and six expected-cost components.  Missing,
stale, ambiguous, dimensionally invalid or unsupported values deny paper eligibility.
`DOCUMENTED_ZERO` is the only zero-valued declaration for commission/financing; an
absent or unsupported value is never silently converted to zero.  Impact uses one of
`SUPPORTED_MODEL`, `CAPPED_NO_IMPACT_RANGE` or `UNSUPPORTED_BLOCKING`; the last one
blocks source-aligned economics and the capped case requires a proposed quantity
within its reviewed range.

Product economics binds canonical asset and exact source/product identity, currencies,
contract/value-per-price-unit, minimum and increment quantities, tick size/value,
commission and financing schedules, impact disposition, session profile/version,
effective/observed times, economics version and provenance.  FX binds a causal observed
rate, health, max-age, source and version.  All times are timezone-aware UTC and all
prices,
quantities, conversion rates and money use `Decimal`.

## SolverPolicy

R3.A freezes `DEFAULT_SOLVER_POLICY` for the first 15-minute convex slice:

| Field | Decision |
|---|---|
| Python runtime | 3.13 |
| Modelling library | CVXPY 1.7.3 |
| Convex backend | CLARABEL 0.11.1 |
| Licence | Apache-2.0 |
| Warm starts | disabled |
| Accepted status | exactly `optimal` plus independent feasibility recomputation |
| Tolerances | absolute/relative `1e-8`; max 1000 iterations |
| Variable order | canonical tuple beginning with `physical_target` |

The decision was reproduced on Python 3.13 with `uvx --from cvxpy==1.7.3`:
a two-variable quadratic objective with hard box constraints returned `optimal`
using CLARABEL with warm-start disabled.  The command resolved Python-3.13
wheels and reported CVXPY's Apache-2.0 licence and CLARABEL's Apache-2.0 licence.
The dependency is intentionally not added to `pyproject.toml` in R3.A; R3.C owns
the first runtime dependency change.

## Identity classification

Identity-bearing fields remain separate:

- **Semantic:** contract/version, canonical asset/source/product identity,
  currencies, schedules, impact and session policies, Decimal economics values,
  FX policy/rate state, cost component values, solver policy and tolerances.
- **Closure/physical:** canonical asset/sleeve order, physical quantities/deltas,
  component ordering, exact Decimal serialisation and any declared child bytes.
- **Provenance:** source/product observation references, effective/observed times,
  source/configuration and code/runtime identifiers used to reproduce a transformation.
- **Verifier:** verifier contract/version, check-set, exact artefact and receipt identity.
- **Promotion/authority:** explicit acceptance, readiness class and operator authority;
  these are not implied by a valid economics or solver value.

No receipt, promotion or generic evidence framework is introduced by this ADR. A
target must bind the immediate parent decision-input authority; descendants must
authenticate that parent proof without replaying ancestry.

## Reconciliation invariants

The independent evaluator must prove:

- every transaction component is computed once from final physical delta;
- financing is computed once from physical holding and interval;
- internal crossings produce no external movement or cost;
- gross forecast, expected cost and expected net remain distinct;
- Decimal component sums equal total costs exactly;
- net contribution equals gross contribution minus total expected cost;
- accepted quantities satisfy minimums, increments, tick and configured constraints;
- no repair or fallback creates a new alpha direction.

## Consequences

R3.C and later consumers depend on this contract. Missing economics, FX, cost or
risk state fails closed and exposes no partial target. This ADR does not create a
provider call, order operation, account path or production trading endpoint.
