# R3 native experiment protocol

- **Status:** Frozen future authority — not instantiated and not authorised to run
- **Protocol version:** 1
- **Evidence purpose:** `FUTURE_NATIVE_DECISION_GRADE`
- **Source class/environment:** `IG_NATIVE_CAPTURE`, reviewed IG **demo** `capture-v4` only
- **Reporting currency:** AUD

## 1. Authority, scope and non-authorisation

This is the reviewed authority for one future source-specific native paper experiment. It implements R3.I in `docs/R3_EXECUTION_PLAN.md`; `PLAN.md`, `docs/TRADING_RESEARCH.md`, `docs/ARCHITECTURE.md`, ADR 0006, ADR 0007 and ADR 0030 remain governing constraints. Source separation, causal availability, holdout protection, no-order and fail-closed rules prevail if wording conflicts.

This document neither authorises nor performs provider acquisition, qualification, deployment, evidence writing, protocol instantiation, readiness review, feature/model construction, a scientific run, holdout access or outcome access. It does not authorise broker orders, account operations, external submission, a production endpoint or real capital. Each is a later separately authorised operation.

The terminal `IBKR_HISTORICAL_RESEARCH` MIDPOINT result and R3.H historical exploration are not inputs to this experiment. They cannot establish IG-native spread, executable-side prices, fills, slippage, economics or conclusions. A later valid result is limited to the frozen IG-demo source/products/period and questions below; it is not production readiness or a claim about live trading or another provider.

## 2. Frozen experiment and comparator register

The experiment ID is `R3_IG_NATIVE_PORTFOLIO_V1`. It uses all configured 5/15/30/60-minute horizons and the ADR 0030 two-stage design: virtual asset/horizon sleeves, then one constrained physical portfolio. Forecasts remain gross returns; expected and realised costs remain distinct; net P&L is derived and independently reconciled.

The register is closed and exhaustive:

| ID | Family | Role |
| --- | --- | --- |
| `ZERO_RETURN` | exact zero gross-return forecast for every eligible asset/horizon | required negative control |
| `LOCAL_RIDGE` | per-asset Ridge on the frozen eligible local features | required simple baseline |
| `POOLED_LOCAL_RIDGE` | pooled Ridge on the same local features with `EQUAL_INSTRUMENT_TOTAL_WEIGHT_MEAN_ONE` | candidate |

`POOLED_CROSS_ASSET_RIDGE`, graph/residual models, ensembles, regime gates, alternative features and post-hoc comparators are excluded. A configuration that cannot be built, verified or cover the required rows remains a failed member of the register; it is not replaced.

All comparators use the same eligible products, decisions, target grid, initial flat virtual/physical state, economics, risk, solver, constraints, outcome rules and exact common-support denominator. `ZERO_RETURN` has no fit, tuning or fallback and cannot open, increase or reverse alpha-driven exposure.

The model policy is frozen to the R2 contract's local-feature baseline: `LOCAL_RETURNS`, `LOCAL_VOLATILITY_RANGE` and `TIME_AVAILABILITY` only. Both Ridge families use that same feature set; `POOLED_CROSS_ASSET` is not an input to either comparator. Feature windows are exactly 1 and 5 minutes; every required family has pre-holdout coverage `>= 0.90`. Continuous values use training-only median imputation and standardisation; binary indicators remain unscaled. Ridge uses deterministic `lsqr`, tolerance `1e-8`, at most 10,000 iterations and alpha grid `(0.01, 0.1, 1.0, 10.0)`; the larger alpha wins an equal validation loss. The minimum rows are exactly 100 training, 20 inner-validation and 20 outer-validation rows in every required fold/horizon cell. The only model-selection criterion is lower instrument-balanced MSE on frozen validation support. Economics, portfolio and holdout outcomes cannot participate. The authenticated configuration must exactly encode these values; a mismatch stops instantiation.

## 3. Questions, endpoints, multiplicity and decision rules

Post-`OPENED` evaluation uses complete paired support: a row is eligible only if all three comparators have an independently reconciled native executable outcome for the same asset, horizon and decision time. Unavailable, stale, missing, closed-session or invalid-FX evidence is explicitly unavailable, never a zero fill, return or cost.

The primary endpoint is paired **net AUD P&L per eligible decision**, including gross midpoint P&L, executable-side spread effect, latency movement, adverse slippage, commission, financing, supported impact and FX translation. Positive means greater post-cost P&L for the left-hand comparator.

| Question | One-sided null versus alternative | Role |
| --- | --- | --- |
| Q1 | `POOLED_LOCAL_RIDGE - ZERO_RETURN <= 0` versus `> 0` | primary |
| Q2 | `LOCAL_RIDGE - ZERO_RETURN <= 0` versus `> 0` | secondary |
| Q3 | `POOLED_LOCAL_RIDGE - LOCAL_RIDGE <= 0` versus `> 0` | secondary incremental |

Descriptive-only outputs are paired mean/median and total net AUD P&L, gross midpoint P&L, every cost component, coverage and exclusions, MSE, rank correlation, direction accuracy, forecast-bucket monotonicity and stability by asset/horizon/UTC week/group. Forecast diagnostics cannot rescue a failed economic question; rank IC is never a profitability claim.

For Q1–Q3, use a paired moving-block bootstrap over contiguous complete UTC calendar-day blocks of the paired rows: 10,000 resamples; PCG64 seed from the first 16 hexadecimal digits of the instantiation-record semantic ID; one-sided p-value is the resampled-mean fraction `<= 0`; two-sided percentile 95% interval comes from the same resamples. Apply Holm step-down to all three at family-wise alpha 0.05. A question is positive only if its Holm-adjusted one-sided p-value is `< 0.05` and paired mean difference is `> 0`; it is negative if its two-sided 95% interval is wholly below zero; otherwise inconclusive. Q1 alone controls the primary conclusion. No annualisation, objective, subgroup, magnitude or unregistered metric changes this rule.

`VALID_CONSUMED_RESULT` requires every mandatory receipt/reconciliation, complete paired support and a classification for every question. A valid negative or inconclusive result is final for this ID. Missing support/receipt, a failed lifecycle or invalid input is `INVALID` or `OPENED_INCOMPLETE`, never a negative result and never a rerun right.

## 4. Source and deterministic product resolution

The sole source is the IG-demo `capture-v4` canonical native quote history (`MarketDataSourceClass.IG_NATIVE_CAPTURE`), never IBKR native, IBKR historical or external history. The immutable candidate catalogue is current `capture-v4` universe identity `eca6649cfd2477204d9a6d5970596657ad0d94b0a25916f8b26b9c5f0c606078`. `index:volatility`, `index:korea-200` and `crypto:bitcoin-usd` are permanently excluded. The release's remaining 22 canonical IDs are the complete candidate set; provider listing identifiers remain evidence attributes, not canonical identities.

At instantiation select **all and only** candidate IDs that pass every qualification in section 5 over the resolved pre-holdout interval, sorted lexicographically. The selected ID tuple and count are permitted future parameters, but selection cannot use an outcome, forecast, cost, model or portfolio result. Retain every candidate's pass/fail disposition and reason code.

Stop without an experiment if fewer than six products qualify or if the tuple lacks at least one `fx:`, `index:` and `commodity:` target. A rejected candidate is not substituted. A changed source class/environment, catalogue hash or canonical ID requires a newly reviewed protocol, not run-time amendment.

## 5. Qualification, chronology and causal availability

Before `OPENED`, the readiness builder may consume authenticated pre-holdout native metadata and quote/bar evidence plus only the sealed holdout's outcome-blind opportunity/schedule and target-maturity timestamps. It cannot decode a holdout target, executable price, outcome, realised coverage, cost, forecast, portfolio or evaluator result. It retains source, receive, persisted, feature-cutoff, decision, training-cutoff, target-availability and dependency times in UTC. It never substitutes persistence time for measured receive time.

For every candidate product and qualifying pre-holdout block, conditions 1–5 must hold. In the sealed holdout, pre-`OPENED` work is limited to the outcome-blind capacity check in condition 3 and the calendar rule below:

1. Exact source/product mapping, price basis, quote/settlement/exposure currencies, reviewed economics, session calendar and causal AUD FX paths are unambiguous and valid.
2. In the pre-holdout evidence, native bid/ask sides are sufficiently contemporaneous under ADR 0006 for every decision and outcome side. No executable price is forward-filled. A gap, stale side, recovery/drop disposition, closed session, revision ambiguity or invalid FX produces an unavailable row.
3. In every qualifying pre-holdout block, valid target and executable-side eligibility coverage is at least 90% for **each** configured 5/15/30/60-minute horizon. Native bid/ask pairing has at least 90% 15-minute base-grid pairwise common support for every selected pair; that paired base-grid gate applies before construction of every horizon's outcome, not only the 15-minute horizon. Before `OPENED`, the holdout check is limited to an authenticated outcome-blind opportunity/schedule capacity under these same fixed thresholds; it cannot inspect whether an outcome actually qualifies.
4. Each product has a source-active eligible opportunity in every one of 16 anchored complete UTC Monday–Sunday weeks ending before proposed holdout start. First/last timestamps and aggregate coverage do not substitute.
5. Every training/inner-validation/outer-validation fold-horizon cell meets the fixed 100/20/20 row minimum. The instantiation record freezes the post-`OPENED` validity gate: the holdout must have at least 20 valid native target rows and at least 20 complete paired executable outcome rows for every selected asset/horizon, and at least 20 complete UTC calendar-day bootstrap blocks across the complete paired set. Before `OPENED`, only its schedule/opportunity capacity is recorded. Any failed pre-holdout qualification stops before opening; any later failed frozen holdout validity gate yields `INVALID` or `OPENED_INCOMPLETE` and cannot alter selection, calendar or this protocol.

Resolve the future calendar outcome-blindly: start at the first Monday 00:00 UTC after the first usable timestamp, partition complete weeks into consecutive `6 + 2 + 2 + 2 + 4` blocks (initial training, inner validation, outer A, outer B, holdout), and use the earliest block for which every selected product passes the pre-holdout rules and the four sealed holdout weeks have the required deterministic opportunity schedule and target-maturity timestamps. The last four weeks are the holdout. If no such block exists by the immutable source cutoff supplied in the later operator capsule, stop `INSUFFICIENT_HISTORY_FOR_MODEL_CONCLUSION`. Do not decode holdout values to resolve this calendar, and do not slide dates after any forecast, cost, portfolio or outcome result.

Chronological folds use the two pre-holdout outer blocks in time order. At each boundary, training rows must predate it, have target availability no later than it, and have target/feature/update dependency intervals ending before it. Embargo is the maximum relevant target horizon, feature window and update dependency duration. Overlapping or unavailable rows are purged. Persist and independently replay exact memberships, derived embargo/purge durations and counts; hand-written date gaps are invalid.

## 6. Frozen economics, risk and portfolio policy

All values here are selected before holdout outcome access and bound in the instantiation record. Product-specific values are taken from exact qualifying pre-holdout receipts; an operator cannot select alternatives after readiness or convert unsupported values to zero.

- Economics/FX bind exact source/product identity, contract or lot/value-per-price-unit, quantity/tick rules, currencies, calendar and causal AUD conversion with staleness policy. Prices, quantities, conversion and money use `Decimal`.
- Spread is the direction-aware decision-reference to executable-side movement. Latency movement is the direction-aware movement to the first qualifying healthy side after the reviewed, versioned paper delay; it is not a broker-fill latency claim. Adverse slippage uses the reviewed pre-holdout policy and parameters.
- Commission and financing use exact reviewed schedules. A zero is valid only when documented in the receipt. Impact must be `SUPPORTED_MODEL` within its evidenced range or `CAPPED_NO_IMPACT_RANGE` within its cap; `UNSUPPORTED_BLOCKING` makes the product ineligible.
- ADR 0030 `DEFAULT_SOLVER_POLICY` is fixed: CVXPY 1.7.3, CLARABEL 0.11.1, warm starts disabled, only `optimal` plus independent feasibility recomputation, abs/rel tolerances `1e-8`, maximum 1000 iterations and canonical physical-target order. The causally fitted horizon-specific shrinkage risk state binds the selected asset tuple, estimator/lookback/caps/group/currency mappings before `OPENED`.
- Physical hard constraints are eligibility/increments, asset/notional, gross, net, concentration, group, currency and portfolio-risk caps, plus session/staleness and no-new-alpha vetoes. Directionally conservative rounding and deterministic repair may retain, reduce, close or redistribute requested exposure but cannot invent an alpha direction. Solver/integrity failures publish only the named blocked/current-position-projection result, never a partial target.

Transaction components (spread, latency, adverse slippage, commission, impact) are charged exactly once on final physical delta. Financing is charged exactly once on final physical holdings and interval. Internal sleeve crossings have no external movement/cost. The independent evaluator recomputes position, component costs, gross P&L, net AUD P&L and attribution without using constructor aggregates.

## 7. Instantiation record and independent readiness review

The only uninstantiated values are calendar endpoints, qualifying canonical IDs/count and exact retained input/receipt identities. The later create-only `R3_IG_NATIVE_PORTFOLIO_V1` record must include:

- protocol version/Git identity, source/environment/catalogue identity, selected products and every candidate disposition;
- exact source/foundation/configuration/observation/panel/target/fold/economics/FX/risk/cost/solver/position identities with immediate-parent receipt bindings and semantic/closure/provenance classification;
- calendar boundaries, dependency memberships/purge/embargo, pre-holdout support/coverage matrices and row counts, sealed holdout opportunity/schedule capacity, exclusions and exact outcome-blind comparator configuration IDs;
- all economics/calibration/impact/risk/constraint/tolerance values, Q1–Q3 register, bootstrap/multiplicity policy, code/image identity and create-only destinations; and
- `holdout_outcomes_accessed=false`, `state=PREPARED_UNOPENED`, sealed forecast/coverage/opportunity children and every protocol stop condition.

The builder authenticates immediate-parent receipts and only required children; it does no recursive semantic replay and decodes no outcome. It must fail before a write for an existing destination, absent/mismatched identity, unsupported/ambiguous field, cross-source input or non-deterministic resolution.

An independent reviewer who did not construct the record/children replays product selection, calendar/fold derivation, pre-holdout qualification matrices, sealed outcome-blind capacity, configuration register, economics/risk bindings and outcome-blind feature/forecast/coverage closure. Only then may it create an independent `PREPARED_UNOPENED` readiness receipt. A failed review stops; it does not allow a revised selection from the same proposed holdout.

## 8. Opening, evaluation, consumption and invalidation

This document alone authorises none of these actions. After separate explicit operator authority, order is fixed:

1. Authenticate exact instantiation/readiness receipts, `PREPARED_UNOPENED`, clean destinations and untouched holdout; record the operator acknowledgement.
2. Write and verify the create-only base `OPENED` marker **before** decoding a holdout target/outcome, then the create-only confirmatory `OPENED` marker. Only the resulting runtime-only capability may decode the exact authenticated target child.
3. Materialise sealed forecasts/coverage for holdout opportunities and decode only required authenticated native outcomes. Before evaluating Q1–Q3, apply the already frozen 20-target-row, 20-paired-outcome-row and 20-complete-UTC-day-block validity gate to the already fixed calendar/product set. A failure is `INVALID` or `OPENED_INCOMPLETE`; it cannot slide the calendar, reselect products, lower a threshold, replace evidence or authorise a rerun. Otherwise run the frozen evaluator, persist immutable report children and independently verify Q1–Q3 plus reconciliation.
4. Only after successful independent verification write one create-only `CONSUMED` record binding `OPENED`, instantiation/readiness, outcome/report/verification identities and classifications. `CONSUMED` is terminal: no reset, reopen, overwrite, re-evaluation or replacement execution uses this holdout.

If `OPENED` exists and a later operation fails, retain truthful failed/invalid terminal evidence as separately authorised; never delete/reset/conceal it. Invalidating conditions include changed/missing identities; source/product/currency/price-basis ambiguity; cross-source evidence; unavailable economics/FX/risk; changed calendar/comparator/metric/bootstrap/solver policy; closure/receipt mismatch; outcome access before marker verification; incomplete coverage; failed replay; or a defect plausibly affecting the claim. In particular, a post-`OPENED` support failure remains `INVALID` or `OPENED_INCOMPLETE`, with no calendar slide, product reselection, threshold change or rerun. An invalidated/consumed experiment needs a new ID and future untouched holdout under a new reviewed authority; it cannot be repaired in place.

## 9. Machine-readable evidence boundary and go/no-go list

Every artefact/report carries both source class and evidence purpose:

| Purpose | Permitted input | Permitted conclusion |
| --- | --- | --- |
| `IMPLEMENTATION_EVIDENCE_ONLY` | synthetic fixtures through the exact path | implementation contracts only |
| `HISTORICAL_EXPLORATORY` | `IBKR_HISTORICAL_RESEARCH` MIDPOINT plus labelled assumptions | historical mechanism/sensitivity only |
| `FUTURE_NATIVE_DECISION_GRADE` | this qualified `IG_NATIVE_CAPTURE` foundation and untouched native holdout | Q1–Q3 only |

`MIDPOINT_ONLY` is non-executable/unavailable for the native endpoint. It cannot borrow IG spreads, executable sides, economics, FX, fills or conclusions; native evidence cannot inherit a historical result. Mixed-source evidence is a hard failure.

Before any later invocation, operator and independent reviewer must establish exact source/environment, deterministic selected products, causal qualified **pre-holdout** coverage and sealed outcome-blind holdout schedule capacity, frozen calendar/folds, authenticated pre-holdout economics/risk/solver, sealed outcome-blind children, clean create-only destinations, independent readiness receipt and explicit opening authority. Any failure stops before holdout access. None of these checks grants provider acquisition, qualification, a release, an order or production action.
