# R3 execution plan — cost and portfolio baseline

- Status: ACTIVE execution authority for R3 after merge
- Planning job: R3-P0
- Scope: offline paper research only
- Implementation status at plan creation: NOT STARTED
- Predecessor: terminal R2-IBKR-HISTORICAL scientific result and its independent approval
- Safety boundary: no broker-order port, external order, live-account connection, production endpoint, provider acquisition, or real-capital path

## 1. Authority, purpose and conclusion boundary

This plan executes the R3 milestone selected by PLAN.md. It is governed by AGENTS.md,
docs/TRADING_RESEARCH.md, docs/ARCHITECTURE.md, accepted ADRs 0006, 0007, 0009 and 0027, and
the final-authenticated terminal R2 scientific report supplied outside Git. If these authorities
conflict, the scientific, source-provenance, evidence-preservation, no-live-order and fail-closed
boundaries prevail.

R3 asks whether retained forecasts can be converted into deterministic offline paper positions under
explicit physical costs, ordered joint risk and global constraints, with independently reconciling
position, cost and P&L arithmetic. It establishes contracts and implementation evidence for that
question. It may also produce clearly labelled historical exploratory sensitivity evidence.

R3 does not establish forecast effectiveness, executable alpha, post-cost profitability, portfolio
effectiveness, native-source validity, continuous paper readiness or production readiness. Those
conclusions require a later locked, source-aligned native experiment with executable-side evidence.
R3 does not reopen, reinterpret or replace the terminal R2 result.

The current POOLED_LOCAL_RIDGE is a retained negative/control baseline, not an established alpha
stream. R2 found LOCAL_RIDGE negative versus ZERO_RETURN, POOLED_LOCAL_RIDGE positive only versus
LOCAL_RIDGE, pooled beyond zero inconclusive because that question was not frozen, and
POOLED_CROSS_ASSET_RIDGE rejected at OOF. The small pooled-versus-local improvement must not be
treated as executable or post-cost alpha.

R3 succeeds if the mechanics and evidence boundaries below work honestly. A zero-position or negative
economic result is a valid R3 outcome.

## 2. Evidence classes and allowed conclusions

R3 must label every artefact and report with both MarketDataSourceClass and evidence purpose. The three
purposes are not interchangeable.

| Evidence purpose | Permitted inputs | Permitted conclusion | Prohibited conclusion |
| --- | --- | --- | --- |
| Implementation evidence | bounded synthetic or fixture data through the exact production CLI, persistence and independent verification paths | contracts, state transitions, failure modes and reconciliation work as implemented | forecast, cost, execution or portfolio effectiveness |
| Historical exploratory evidence | authenticated R2-IBKR-HISTORICAL forecasts/outcomes plus explicit hypothetical or externally reviewed economics and cost assumptions | sensitivity and mechanism behaviour for the named historical MIDPOINT source and assumptions | native spread/fill/slippage, executable performance, decision-grade post-cost alpha or another provider’s result |
| Future native decision-grade evidence | one separately qualified native-source foundation, causal bid/ask observations, reviewed product economics, frozen pre-holdout cost/risk/solver policy and an untouched native holdout | only the questions frozen for that exact native experiment after its complete irreversible protocol | inheritance from historical R2, cross-provider fill claims, post-hoc questions or production readiness |

Historical MIDPOINT bars cannot instantiate observed native spread, latency movement, fill or slippage.
Historical exploratory reports must show unsupported components and assumption sensitivities rather
than manufacture executable observations. Future native execution is input-gated and is not required
to call R3 implementation complete.

## 3. Architectural decision: sleeves first, one physical cost boundary

### Recommendation

Adopt option B: two-stage sleeve intents followed by a physical target optimisation and deterministic
attribution back to sleeves.

The first stage maintains and updates persistent asset/horizon virtual intents. Each horizon optimises
its gross-return/risk intent without booking a sleeve-level external trade or physical transaction
cost. It keeps the forecast, gross expected return, risk contribution, intended virtual position,
review/expiry time and model lineage visible. It does not pretend that each sleeve independently
trades the market.

The second stage consumes all current sleeve intents, the current physical portfolio, one ordered
risk state and one physical economics/cost state. It constructs one constrained physical target per
asset. Transaction costs are evaluated exactly once on the net physical position delta. Financing is
evaluated exactly once on the physical position and holding interval. The accepted physical target and
delta are then attributed deterministically back to sleeves.

This implements the existing PLAN.md and docs/ARCHITECTURE.md direction: horizon-specific targets,
then global physical netting, constraint repair and stable attribution. A single joint optimiser over
all sleeve variables could also avoid transaction-cost double counting, but it would couple horizon
intent generation, netting, physical constraints and attribution into one opaque solve. That would
make the one-horizon vertical slice harder to prove, make horizon intent less persistent, and make
solver failures harder to classify at the owning boundary.

### Double-counting failures to prevent

The design and regressions must prevent all of these paths:

1. subtracting standalone turnover cost from each sleeve and then subtracting physical target cost
   again;
2. charging both sides of opposing sleeve changes even though they cross internally and create no
   physical delta;
3. applying minimum commission, spread or nonlinear impact once per sleeve instead of once per
   physical asset delta;
4. charging financing on gross opposing sleeve positions instead of the once-netted physical
   position and elapsed holding interval;
5. using a net-return forecast as optimiser input and then subtracting the same cost state again in
   the physical objective or report; and
6. calculating expected cost on a pre-constraint target while reporting the post-repair position.

Forecasts therefore remain gross. Expected physical costs remain separate. Expected net return is a
derived report field, never a replacement forecast.

### Attribution failures to prevent

The attribution policy must also prevent:

1. allocating external costs by gross sleeve turnover when only a smaller net physical delta traded;
2. attributing post-cap or post-rounding positions using stale pre-repair sleeve weights;
3. turning internal sleeve crossings into paper fills, external costs or portfolio P&L;
4. silently assigning global de-risking or solver repair to a model as alpha-driven intent;
5. nondeterministic residual allocation caused by unordered maps, floating-point ties or solver
   iteration order; and
6. allowing sleeve attribution totals to differ from the physical position, physical delta, component
   cost or total P&L.

At each decision, opposing requested sleeve deltas are matched in stable sleeve-key order as internal
transfers with zero external cost. Any remaining same-direction requested movement forms the physical
request. Global optimisation may retain, reduce or cancel that request but may not invent an
alpha-driven direction. Accepted external movement is allocated back in deterministic proportional
order, with Decimal largest-remainder handling at the quantity increment and stable sleeve-key
tie-breaking. Constraint-driven de-risking carries its own repair attribution rather than being
mislabelled as model intent. Component cost allocation must sum exactly to the once-calculated physical
component cost.

### ADR requirement

An ADR is warranted because this is a durable R3–R6 boundary affecting optimisation, semantic
identity, accounting and future continuous-paper attribution, and because option A is a credible
alternative with materially different failure modes.

R3.A must add docs/adr/0030-two-stage-sleeve-and-physical-cost-boundary.md before dependent kernel code
lands. The ADR must freeze:

- cost once at the physical transaction boundary;
- financing once on the physical holding interval;
- internal-cross semantics;
- the no-new-direction rule;
- deterministic post-netting attribution and rounding residual policy;
- separation of forecast, expected cost and expected net return;
- independent reconciliation invariants; and
- the fields that are semantic, closure, provenance, verifier or promotion authority.

The solver library is a versioned implementation choice, not itself a durable ADR unless its selection
changes this architecture.

## 4. First vertical slice and sequencing rule

The first complete path is one 15-minute horizon. The schema may carry a configured horizon key from
the start, but no multi-horizon branching, generic sleeve framework or multi-period optimisation may
land before the 15-minute path passes all of these together:

1. authenticated forecast input;
2. complete product economics and currency conversion;
3. versioned expected-cost state;
4. causally estimated ordered risk state;
5. persistent virtual position transition;
6. continuous physical target and delta;
7. constraints, quantity rounding and deterministic repair;
8. immutable decision and attribution persistence;
9. subsequent executable-side fixture evaluation; and
10. independent position, cost and P&L reconciliation.

This production-CLI micro-run is implementation evidence only. It must use correctly shaped inputs and
exercise the actual persistence, authentication and verification boundaries. It must include opposing
sleeves even though the configured horizon is the same, so internal crossing and physical-cost
invariants are proved before multi-horizon generalisation.

Only after this gate passes may R3.F add the 5, 30 and 60-minute horizons and conflicting-horizon
lifecycle behaviour.

## 5. Contract model

All domain values are frozen, deterministic and provider-neutral. I/O and solver libraries stop at
adapter/application boundaries. Times are timezone-aware UTC. Decimal is used for price, quantity,
notional, currency conversion, cost and money; any float conversion for numerical arrays is explicit,
versioned and independently checked.

### 5.1 Decision identity and chronology

Every R3 decision binds:

- source class and evidence purpose;
- dataset/foundation and immediate-parent verification or promotion authority;
- forecast artefact, experiment, model, fold and configuration identities;
- asset order and horizon order;
- feature, training, risk and cost cut-offs;
- decision time and target review/expiry times;
- economics, FX, cost, risk, constraint and solver-policy versions;
- current physical and virtual-position state identity; and
- reporting currency, which remains AUD under retained ADR 0007.

No holdout outcome may influence cost calibration, risk estimation, solver selection, constraints,
rounding or repair for a future decision-grade run.

### 5.2 Product economics and expected-cost state

A paper-eligible asset requires an effective, reviewed economics state covering:

- canonical asset and exact source/product identity;
- price currency, settlement/exposure currency and reporting currency;
- contract or lot size and value per one price unit;
- minimum quantity and quantity increment;
- tick size, tick value and rounding policy;
- commission schedule, minimums and currency;
- financing basis, rate source, accrual clock and charge schedule;
- contemporaneous healthy FX conversion path and staleness policy;
- session and paper-eligibility state; and
- effective-from time, observation time, version and provenance.

Missing, stale, ambiguous, dimensionally inconsistent or inapplicable economics makes the asset
ineligible. A documented zero commission or zero financing charge is data, not a default. If impact
cannot be supported for the proposed quantity, the target must be capped to a documented no-impact
validity range or fail closed; the system must not invent an impact estimate.

Expected cost is a versioned pre-decision state with separate components:

- spread: direction-aware movement from the decision reference to the executable side;
- latency movement: direction-aware movement expected between the decision executable reference and
  the first healthy side after the configured delay;
- adverse slippage: additional configured or estimated movement beyond that latency-side quote;
- commission: the reviewed physical schedule including minimums;
- financing: the reviewed cost over the physical holding interval;
- supported impact: only a measured or explicitly justified quantity-dependent model; and
- unsupported assumptions: explicit states that can deny eligibility but never become silent zeros.

For asset i, the transaction-cost components apply to physical delta:

Δq_i = q_i,target − q_i,current.

Financing applies to the resulting physical holding and elapsed interval, not to each virtual sleeve.
Every component is available in native money, AUD and an objective-compatible return/notional unit,
with explicit Decimal conversion. The conversion policy must round only at declared currency and
quantity boundaries.

### 5.3 Gross forecast, expected cost and expected net return

The following remain distinct persisted/reportable fields:

- gross forecast return and its horizon/unit;
- gross expected monetary contribution at the accepted physical exposure;
- expected transaction cost by component;
- expected financing cost;
- expected net return and expected net monetary contribution; and
- realised gross midpoint P&L, realised executable-side adjustments, realised costs and realised net
  P&L.

Expected net equals gross expected contribution minus the once-calculated physical expected costs.
The evaluator recomputes it; it does not trust a caller-supplied net field.

A zero forecast, including a complete ZERO_RETURN control, creates no new alpha-driven exposure. It
may retain a currently valid position when the frozen policy permits, reduce risk, honour expiry or
close exposure. It may not open a position, increase absolute exposure or reverse direction because
of numerical noise, a cost sign, a constraint repair or a fallback.

### 5.4 Ordered portfolio risk state

A risk state binds one exact ordered asset tuple and contains:

- as-of, observation cut-off, lookback and availability policy;
- return definition and horizon;
- estimator and shrinkage method/version;
- ordered covariance matrix with unit and numerical-boundary policy;
- sample counts, missingness/exclusion disposition and effective observations;
- positive-semidefinite and symmetry checks with frozen tolerances;
- ordered group keys, asset-to-group exposure matrix and configured group caps;
- ordered currency keys, asset-to-currency exposure matrix and configured currency caps;
- per-asset, gross, net, concentration and portfolio-risk caps used by the kernel; and
- semantic, closure and provenance identities.

Risk is independent of forecast uncertainty and forecast scaling. No unordered set or provider
identifier defines matrix position. A stale, incomplete, non-finite, non-symmetric, non-PSD,
order-mismatched or otherwise invalid required risk state fails closed. It must never be replaced by a
diagonal, zero covariance or default cap merely to obtain a target.

The first estimator is a horizon-specific shrinkage covariance selected and fitted only on information
available at the decision time. Estimator and cap selection for any future decision-grade experiment
must be frozen without its holdout.

### 5.5 Persistent virtual asset/horizon positions

A virtual position is keyed by source, experiment/configuration, canonical asset and horizon. Its
immutable transition records:

- prior virtual quantity and state identity;
- gross forecast and model attribution;
- requested continuous target and delta;
- review time, expiry time and lifecycle disposition;
- risk/cost/constraint policy identities used;
- accepted attributed physical quantity and external-delta share;
- internal-cross quantity;
- adjustment and repair reason codes; and
- successor state identity.

Offline persistence must reconstruct the same current virtual state by ordered replay. Later R6
checkpoint/process design is explicitly out of scope. R3 adds no continuous runtime or restart
orchestrator.

### 5.6 Physical target construction

The one-horizon kernel produces horizon intents first. The physical stage then:

1. authenticates the complete decision-input state;
2. aggregates current sleeve positions and requested deltas in canonical asset/horizon order;
3. matches internal opposing changes deterministically;
4. forms the net requested physical delta;
5. optimises one continuous physical target against gross sleeve value, physical cost and ordered
   risk;
6. independently checks solver status and continuous feasibility;
7. converts to Decimal quantities;
8. rounds to valid quantity/tick economics;
9. repairs global constraints deterministically;
10. calculates the final physical delta from the persisted current physical position; and
11. attributes final positions, external movement, internal crossings, cost and repair back to
    sleeves.

The target artefact contains both current and target physical quantities. It never emits an order or
broker instruction. A target is publishable only if all post-rounding constraints and reconciliation
invariants pass.

### 5.7 Global constraints, rounding and repair

Hard constraints are applied to physical positions, not independently duplicated per sleeve:

- asset eligibility and per-asset position/notional cap;
- minimum and increment-valid quantity;
- gross and net exposure caps;
- concentration cap;
- ordered group exposure caps;
- ordered currency exposure caps;
- configured portfolio-risk bound;
- no-new-direction/no-new-alpha rule when applicable; and
- session, staleness and operational eligibility vetoes for evaluation inputs.

Continuous optimisation does not prove discrete feasibility. Rounding is directionally conservative
at valid quantity increments, followed by a deterministic repair pass. Repair may only retain,
reduce, close or redistribute already requested exposure within the frozen policy; it may not create
new alpha direction.

Reason codes are versioned enum values with stable priority and payload schemas. The initial required
families are:

- INPUT_ECONOMICS_MISSING, INPUT_FX_MISSING, INPUT_COST_INVALID and INPUT_RISK_INVALID;
- ZERO_FORECAST_NEW_EXPOSURE_BLOCKED and ASSET_PAPER_INELIGIBLE;
- SOLVER_ERROR, SOLVER_NON_OPTIMAL, SOLVER_INFEASIBLE and SOLVER_RESULT_INVALID;
- QUANTITY_ROUNDED and MINIMUM_QUANTITY_NOT_MET;
- ASSET_CAP_REPAIR, GROSS_CAP_REPAIR, NET_CAP_REPAIR and CONCENTRATION_CAP_REPAIR;
- GROUP_CAP_REPAIR, CURRENCY_CAP_REPAIR and PORTFOLIO_RISK_REPAIR;
- CURRENT_POSITION_PROJECTED and NEW_ALPHA_EXPOSURE_BLOCKED; and
- ATTRIBUTION_RESIDUAL_REPAIRED and DECISION_BLOCKED.

The implementation may refine names in R3.D, but the categories, ordering, semantic versioning and
fail-closed effects are mandatory. Free text may explain a code but cannot replace it.

### 5.8 Solver selection and failure semantics

R3.A must freeze a SolverPolicy before R3.C kernel code depends on a solver. Selection must use
version-specific evidence for Python 3.13 and the exact convex problem required by the 15-minute
slice. Compare the smallest maintained candidates that support:

- convex quadratic risk;
- linear or supported convex physical-turnover cost;
- hard linear asset/group/currency/gross/net constraints;
- any required convex portfolio-risk constraint;
- explicit status, residual and iteration reporting;
- deterministic single-process tests with warm-start disabled; and
- a licence and binary distribution compatible with the repository.

CVXPY is a candidate, not a requirement. Do not write a custom optimiser merely to avoid a dependency.
Record the chosen library, backend, versions, objective scaling, tolerances, iteration limits,
canonical variable order and accepted status in the R3.A PR and semantic SolverPolicy.

Only an exact accepted optimal status plus an independent feasibility recomputation may publish the
solved target. Inaccurate, iteration-limited, infeasible, unbounded, numerical-error, exception,
non-finite, order-mismatched or tolerance-violating output is failure. No partial solution or
last-iterate target is exposed.

On failure:

1. retain the current physical position only if it remains valid and current;
2. otherwise project it deterministically toward zero into the valid set without opening, increasing
   or reversing alpha-driven exposure;
3. record solver and repair reason codes; and
4. if a valid projection cannot be proved, publish a blocked decision with no target transition.

Missing economics, FX, cost or required risk state follows the same fail-closed no-new-exposure rule;
it is not a solver fallback.

## 6. Independent reconciliation

The independent evaluator must not call the target constructor, reuse its aggregate result or trust
its derived totals. It consumes immutable inputs, decisions, attributions and subsequent evidence and
recomputes using a separately structured path.

### Position reconciliation

For every decision and asset it proves:

- prior physical position plus final physical delta equals target physical position;
- sum of attributed sleeve physical positions plus explicit non-alpha repair attribution equals the
  physical target;
- sum of external sleeve delta shares equals the physical delta;
- internal-cross shares cancel exactly and create no physical delta;
- replayed sleeve transitions reproduce the next virtual state; and
- quantities satisfy product increments and all final constraints.

### Cost reconciliation

It recomputes every physical component from the final physical delta or holding interval and proves:

- transaction cost was calculated once on physical movement;
- financing was calculated once on the physical holding interval;
- internal crosses have zero external cost;
- sleeve component allocations sum exactly to the physical component;
- native-currency amounts convert to AUD using the bound FX evidence; and
- total expected and realised costs equal their component sums without a hidden residual.

### P&L reconciliation

It reports and proves separately:

- gross midpoint price P&L;
- bid/ask spread effect;
- latency movement;
- adverse slippage;
- commission;
- financing;
- supported impact;
- FX translation effect where applicable; and
- net AUD P&L.

Executable evaluation pairs each decision with the first qualifying healthy side after the configured
latency under ADR 0006. No executable side is forward-filled. Missing, stale, closed-session or
incomplete evidence produces an explicit unavailable/excluded outcome, never a midpoint fill.

The report also reconciles ordered group/currency exposures, portfolio risk and marginal risk before
and after repair. Exact Decimal ledgers must reconcile exactly; numerical risk/objective values must
meet frozen tolerances and report the residual.

## 7. Evidence handoffs

Before the first durable R3 artefact, the implementing PR must classify every identity-bearing field
as semantic, closure, provenance, verifier or promotion authority and name obsolete paths to delete.
Do not build a generic evidence framework.

The durable handoff is stage-specific:

1. R3 decision-input bundle authenticates the exact retained forecast authority, source/evidence
   class, current position state, economics/cost receipt and risk receipt.
2. Target construction authenticates that immediate-parent bundle, consumes only required children,
   transforms once and publishes an immutable target closure.
3. Independent target verification replays the R3 transformation once and creates a create-only
   target-verification receipt.
4. Paper evaluation authenticates the target receipt plus exact subsequent outcome evidence, consumes
   required children and publishes one immutable report closure.
5. Independent report verification recomputes reconciliation once and creates a create-only report
   receipt.
6. Any later consequential native promotion authenticates the exact accepted receipt, readiness and
   explicit operator authority without replaying ancestry.

Each child binds its immediate parent’s semantic, closure and verification or promotion identities.
Ordinary descendants authenticate the receipt and hash/decode only children they consume. They do not
replay R2, cost, risk or target ancestry. Deep audit is exceptional after a relevant defect, verifier
revocation or explicit operator request.

The terminal historical R2 report and approval remain immutable predecessor evidence. R3 creates new
artefacts; it never edits R2 evidence in place. A historical exploratory R3 report binds that exact
R2 source/result boundary and cannot be promoted into native decision-grade authority.

## 8. PR tranches and DAG

Each PR has one bounded owner, an independent exact-head review, focused tests/static checks and the
validation appropriate to any schema or retained-evidence boundary. Any changed head invalidates its
review and identity-sensitive validation.

### R3.A — cost/economics contracts and architectural ADR

Scope:

- add ADR 0030;
- define product-economics, FX and component cost contracts;
- define gross/expected-cost/expected-net separation;
- define cost validity and fail-closed eligibility;
- freeze SolverPolicy using version-specific evidence; and
- cover zero/unsupported versus missing semantics without provider access.

Exit gate: component units and timing are explicit; transaction cost is physical-delta-only;
financing is physical-holding-only; missing economics/FX/cost fails closed; ADR 0030 is accepted; the
solver dependency decision is reproducible.

### R3.B — ordered risk state

Scope:

- implement ordered horizon-specific shrinkage covariance;
- implement group and currency exposure mappings/caps;
- bind causal cut-offs, estimator/version, units and identities; and
- reject missing, stale, order-mismatched, non-finite or invalid covariance/risk.

Exit gate: permutation and replay tests prove stable ordering; causal tests prove no post-decision
input; independent calculations reproduce covariance/exposures within frozen tolerance; invalid risk
blocks new exposure.

R3.A and R3.B may proceed in parallel after R3-P0 because their ownership does not overlap.

### R3.C — one-horizon virtual/physical target kernel

Scope:

- implement persistent 15-minute virtual positions;
- construct horizon intents and the continuous physical target;
- apply expected transaction cost once to physical delta and financing once to physical holdings;
- implement zero-forecast/no-new-exposure semantics; and
- persist target, delta, internal crossing and attribution inputs.

Exit gate: deterministic unit/property tests cover long, short, flat, opposing intent and current
position cases; solver outputs pass independent continuous-feasibility checks; repeated ordered replay
is byte/identity stable; no order or provider operation exists.

### R3.D — rounding, constraints and failure repair

Scope:

- convert numerical targets to Decimal product quantities;
- implement hard global caps, deterministic conservative rounding and repair;
- freeze reason-code version/order/payloads;
- implement solver/input failure semantics and current-position projection; and
- implement exact post-repair sleeve attribution.

Exit gate: adversarial boundary tests prove every final target satisfies economics and all caps;
permuted input order cannot change output; solver/inaccurate/infeasible/missing-input cases expose no
partial target; attribution totals equal physical totals exactly.

### R3.E — independently reconciled offline report

Scope:

- implement the separate position, cost and P&L evaluator;
- persist immutable decision/outcome/report closures and create-only receipts;
- expose gross forecast, expected cost and expected net separately;
- run the complete 15-minute fixture through the production CLI and persistence path; and
- produce an implementation-evidence report with component and reason-code drill-down.

Exit gate: the representative micro-run exercises all deliverables and state transitions; independent
position/cost/P&L reconciliation has no unexplained residual; injected double-counting, stale quote,
bad FX and attribution mutations fail; the full retained-scale shape/limit inventory is recorded
before any 10+ minute run.

### R3.F — multi-horizon extension

Scope:

- add 5, 30 and 60-minute sleeve lifecycles after the R3.E gate;
- preserve per-horizon review/expiry/model attribution;
- net conflicting horizons into the one physical target;
- extend risk/cost/report identities without duplicating physical cost; and
- remove any one-horizon-only compatibility path rather than retain dual writers.

Exit gate: all configured horizons pass deterministic replay, conflict, expiry, netting, rounding,
attribution and reconciliation tests; the 15-minute result remains unchanged under the equivalent
single-horizon configuration.

### R3.G — source-aligned executable evaluation

Scope:

- bind evaluation to exact source class, product economics, session, receive-time and executable-side
  evidence;
- classify fixture implementation, historical exploratory and future native decision-grade outputs;
- make MIDPOINT-only historical limitations machine-visible in reports;
- implement native bid/ask pairing, latency/slippage stress and unavailable-outcome semantics without
  performing a provider acquisition or holdout reveal; and
- define the separate authority/input gate for any later native decision-grade execution.

Exit gate: a source cannot borrow another source’s spreads, fills, FX, product economics or conclusion;
MIDPOINT-only input cannot emit an executable result; the exact CLI path passes with source-aligned
fixtures; any real native run remains blocked until its separately reviewed inputs and authority exist.

### DAG

    R3-P0
      ├── R3.A ──┐
      └── R3.B ──┴── R3.C ── R3.D ── R3.E ── R3.F ── R3.G

R3.F is blocked until the complete one-horizon vertical slice in R3.E passes. R3.G may prepare
source-aligned contracts, but no real native decision-grade execution is authorised by this DAG.

## 9. Validation and review gates

For this R3-P0 documentation job only:

- git diff --check;
- consistency review against PLAN.md, docs/STATUS.md, docs/TRADING_RESEARCH.md and
  docs/ARCHITECTURE.md; and
- no complete test gate.

For implementation PRs:

- focused deterministic, mutation/property and static checks while iterating;
- TMPDIR=/workspace/tmp ops/dev/verify.sh at schema, durable evidence and milestone boundaries, and at
  least at the exact R3.E, R3.F and R3.G candidate heads;
- exact production-CLI micro-run before any retained-scale or 10+ minute path;
- retained-scale shape/resource projections and the AGENTS.md rerun-escalation gate when applicable;
- independent exact-head review of identity, causality, physical-cost and reconciliation boundaries;
  and
- GitHub static workflow reported as static evidence only, never as a test pass.

No validation authorises provider access, promotion, a native holdout, external orders or any other
special-state action.

## 10. Exact R3 milestone exit gates

R3 may be marked complete only when all of the following are true:

1. R3.A through R3.G implementation contracts are merged and the exact heads passed their required
   review/validation.
2. ADR 0030 is accepted and code conforms to the two-stage, one-physical-cost boundary.
3. The complete 15-minute production-CLI vertical slice passed before multi-horizon code was
   generalised.
4. Product economics, FX, all expected-cost components and ordered risk are versioned, causal and
   fail closed when required information is missing or invalid.
5. Zero forecast cannot create or increase alpha-driven exposure.
6. Persistent virtual positions, physical targets and deltas replay deterministically.
7. Every post-rounding physical target satisfies asset, gross, net, concentration, group, currency and
   configured risk caps.
8. Solver failure, inaccurate output and infeasibility expose no partial target; valid current-position
   retention or deterministic exposure-reducing projection is explicit.
9. Transaction costs are calculated once on physical delta; financing once on physical holdings;
   internal crossings have zero external cost.
10. Sleeve attribution sums exactly to final physical position, delta, component costs and P&L, with
    stable reason codes for every adjustment.
11. The independent evaluator reproduces positions, cost components, gross P&L and net AUD P&L from
    immutable inputs, with exact Decimal reconciliation and frozen numerical tolerances.
12. Durable target and report boundaries have create-only independent verification receipts and
    ordinary descendants authenticate immediate-parent proof without ancestry replay.
13. The fixture implementation report, any historical exploratory report and the future native
    decision-grade gate are visibly and machine-readably distinct.
14. Historical MIDPOINT evidence cannot produce an executable/native conclusion and no source inherits
    another source’s economics or evidence.
15. The full 5/15/30/60-minute configuration passes conflict, expiry, netting, repair and attribution
    tests without changing the equivalent 15-minute-only result.
16. No broker-order port, external submission, live-account/production endpoint or real-capital path
    exists.
17. The final active documents state the actual result, including negative or blocked outcomes, and
    continue to identify pooled Ridge as a retained baseline rather than established alpha.

A positive portfolio or P&L metric is not an exit criterion. A future native decision-grade result is
not an R3 implementation exit criterion.

## 11. Unresolved input dependencies and stop conditions

These inputs are deliberately not guessed by R3-P0:

- exact paper-eligible source/product set for each later evidence run;
- effective contract, quantity/tick, commission and financing schedules for those products;
- causal FX paths and staleness limits, although AUD remains the reporting currency;
- supported impact validity range or an explicit size cap where impact is unavailable;
- spread, latency and adverse-slippage calibration data/policy for each source;
- shrinkage estimator parameters, lookback, group/currency mappings and numerical cap values;
- exact solver library/backend/version and tolerances, to be resolved by the R3.A selection gate; and
- a qualified native evidence interval, frozen native experiment questions and untouched holdout
  authority for any future decision-grade run.

Missing product economics, FX, required cost or risk stops new exposure for the affected asset.
Ambiguous product identity, price basis, currency conversion or source equivalence stops the evidence
run. Lack of native inputs does not block fixture implementation or clearly labelled historical
exploration, but it blocks every native executable or decision-grade conclusion. No item above grants
provider access, reacquisition, holdout access, promotion or external trading authority.
