# R2 Implementation Plan — Local and Pooled Baselines

**Status:** Proposed  
**Milestone:** R2 — local and pooled baselines  
**Depends on:** A verified R1 causal foundation implementation  
**Primary vertical path:** 15-minute midpoint log return  
**Supported horizons:** 5, 15, 30 and 60 minutes  
**Execution mode:** Offline research only

---

## 1. Purpose

R2 determines whether simple linear models extract stable, chronological return information from the R1 foundation.

It implements and compares:

1. a per-instrument local Ridge baseline;
2. a pooled Ridge model using the same local feature representation;
3. a pooled non-graph model with explicit cross-asset context; and
4. the existing zero-return R1 probe.

The local and pooled models are retained controls for later graph experiments regardless of whether their predictive results are positive.

R2 has two distinct completion states:

1. **Software implementation complete:** the contracts, feature builders, models, evaluation, verification and holdout machinery are implemented and proven using synthetic fixtures and representative real data.
2. **Research milestone complete:** the implemented system has been run against a qualifying frozen dataset, including chronological out-of-fold selection and a locked holdout, and has produced a decision-grade positive, negative or inconclusive result.

The qualifying research bundle is not a prerequisite for beginning or completing most R2 software development.

A negative forecasting result is a successful research outcome. Insufficient data, failed chronology checks or an inability to produce a decision-grade conclusion do not constitute a forecasting result.

R2 does not require profitability, a positive Sharpe ratio, production readiness or progression to broker-facing paper execution.

---

## 2. Document governance

This document is the normative implementation plan for R2.

Implementation work must not silently revise the requirements to match completed code. A substantive change requires an explicit amendment recording:

- the original requirement;
- the revised requirement;
- why the change is necessary;
- whether prior evidence is invalidated;
- the approving review or pull request; and
- the amendment date.

Status reporting and descriptions of completed implementation belong in `PLAN.md` and `docs/STATUS.md`. They do not retroactively alter this plan.

Minor spelling, formatting and unambiguous CLI corrections do not require an amendment, but must remain visible in review.

### Amendment 1 — explicit eligibility subsets and staged readiness evidence

- **Original requirement:** the experiment bound the exact R1 universe and roles, named one target
  set and reported the four high-level software, representative, confirmatory and holdout readiness
  states. Feature-family evidence was represented by quote-state eligibility identifiers.
- **Revised requirement:** the experiment retains the full R1 target universe while authenticating
  separate model-eligibility decisions and a frozen confirmatory subset; feature eligibility is
  generic and identity-bearing; readiness separately reports contract, representative integration,
  confirmatory foundation data, inner-validation row evidence, overall confirmatory OOF and locked
  holdout states. Foundation-data readiness includes source-active per-instrument/block coverage,
  configured row minima, active-source duration and 16 anchored weekly buckets in which every
  confirmatory instrument has a qualifying target opportunity.
- **Rationale:** a smaller qualifying core must not require rebuilding R1 or deleting wider evidence,
  optional features must not weaken core controls, and an R1-only preflight must not imply that later
  R2.C inner-split or representative-integration evidence already exists. Weekly opportunity presence
  distinguishes 16 usable weeks from isolated activity with only a 16-week outer span without
  requiring continuous activity through genuine market closures.
- **Evidence impact:** no decision-grade R2 evidence exists, so none is invalidated. Synthetic R2.A
  readiness evidence produced before this amendment is superseded and cannot support a readiness
  claim under the revised contract.
- **Approval:** approved and merged by PR #22.
- **Amendment date:** 2026-07-26.

### Amendment 2 — source-specific provider-history experiments

- **Original requirement:** every R2 experiment consumed one verified native R1 foundation, external
  data was permitted for development or later augmentation, and the first confirmatory conclusion was
  defined only against a qualifying native foundation.
- **Revised requirement:** the existing `R2-IG-NATIVE` path and gates remain unchanged. R2 may also run
  `R2-IBKR-HISTORICAL` and later `R2-IBKR-NATIVE` as separate source-specific experiments. Each consumes
  one independently verified foundation and binds an independent `MarketDataSourceClass`, exact product
  mappings, availability/revision policy, experiment identity and conclusion boundary. Existing R2
  `EvidenceClass` remains orthogonal with values `IMPLEMENTATION_EVIDENCE_ONLY` and `CONFIRMATORY`.
  Foundation, experiment, feature, fit, forecast and report identities bind both dimensions. Provider
  history enters through a separately versioned observation contract with authenticated `available_at`
  and a versioned availability selector; it never shares a foundation with native rows or fabricates
  native receive/persistence lineage. A later cross-provider augmentation experiment remains distinct
  and requires native-only controls and an untouched native holdout.
- **Rationale:** independently governed IBKR history can accelerate chronological model research while
  native quote history accumulates, without masquerading as IG evidence or weakening causal, fold,
  coverage, selection or holdout rules.
- **Evidence impact:** R2.A and R2.B implementation evidence remains valid because their identity,
  current-cutoff, source-lineage and independent-verification requirements are unchanged. No
  decision-grade R2 evidence exists. Provider-history integration requires source-class/evidence-class
  independence, availability-selector and source-separation tests before it supports even a
  source-specific conclusion.
- **Approving review:** Stage 0 documentation review; implementation remains subject to subsequent
  code and account-capability review.
- **Amendment date:** 2026-07-28.

### Amendment 3 — discriminated local and pooled preprocessing selections

- **Original requirement:** `qtrad-r2-preprocessing-selection-v1` represented one outer fold and
  exactly one eligible target, accepted only `LOCAL_RIDGE`, and did not claim pooled-family selection.
- **Revised requirement:** v1 is a `model_family`-discriminated local/pooled union. Local selections
  retain exactly one target. Pooled selections retain the complete ordered eligible-target universe
  and bind `FULL_ONE_HOT_V1`, `NO_GLOBAL_INTERCEPT_V1` and
  `FIXED_UNIVERSE_OUTER_INNER_FIT_VALIDATION_V1` as first-class semantic policies. Every pooled
  instrument must be represented in outer training, purged inner fit and inner validation; configured
  row minima remain aggregate for the pooled fold. Stronger per-instrument minima require separate
  explicit configuration fields.
- **Rationale:** local and pooled Ridge share the same fold-local preprocessing, chronological selection
  and replay state. The existing `model_family` discriminator preserves the local singleton invariant,
  older readers fail closed on pooled values, and a second structurally duplicate contract would add no
  useful boundary. Fixed-universe representation prevents an untrained identity coefficient from being
  treated as learned without silently multiplying aggregate thresholds by the instrument count.
- **Evidence impact:** no durable pooled-selection or decision-grade R2 evidence exists. Existing local
  selections remain structurally local, but pre-amendment synthetic selection evidence is superseded and
  must be regenerated under the policy-bound semantic identity.
- **Approval:** approved and merged by PR #29.
- **Amendment date:** 2026-07-29.

---

## 3. Research questions

The primary R2 question is:

> Do local or pooled Ridge models produce stable out-of-sample information about configured future midpoint returns beyond the zero-return baseline?

Secondary questions are:

- Does sharing parameters across instruments improve generalisation?
- Does explicit cross-asset context improve on pooled local features?
- Which feature families contribute information?
- Is any apparent improvement stable across folds, instruments, periods and declared market-state buckets?
- Is the result produced by broad evidence or concentrated in a small number of observations?
- Does model coverage differ enough to explain apparent performance differences?

R2 does not answer whether the forecasts remain profitable after complete costs, portfolio constraints or physical position netting. Those questions begin in R3.

---

## 4. Scope

### 4.1 In scope

R2 includes:

- a strict experiment configuration;
- causal feature materialisation from the R1 bundle;
- bounded, ablatable feature families;
- fold-local preprocessing;
- chronological Ridge hyperparameter selection;
- per-instrument local Ridge models;
- pooled local-feature Ridge;
- pooled non-graph cross-asset Ridge;
- immutable model and preprocessing artefacts;
- out-of-fold forecasts and forecast-availability evidence;
- global, bucketed and stability evaluation;
- a frozen model-selection artefact;
- one locked-holdout evaluation;
- independent replay and verification;
- a thin R2 evidence bundle over independently manifested children;
- synthetic chronological fixtures sufficient to prove every model and leakage invariant;
- integration runs against representative but non-qualifying native data;
- a continuously updated R2 data-readiness report;
- explicit evidence classifications distinguishing implementation tests from research conclusions;
- provenance-distinct support for approved development datasets; and
- the ability to defer confirmatory OOF and holdout execution until sufficient data exist without blocking software implementation.

### 4.2 Not in scope

R2 does not implement:

- graph construction or message passing;
- nonlinear model searches;
- neural networks;
- probabilistic or distributional forecasts;
- conformal calibration;
- dynamic regime experts;
- automatic feature discovery;
- a general-purpose hyperparameter optimisation framework;
- cost or slippage simulation beyond clearly labelled diagnostics;
- portfolio optimisation;
- target positions;
- physical position netting;
- shadow-live inference;
- automatic model promotion;
- broker order operations; or
- production IG endpoints.

R2 also does not:

- lower chronological fold, coverage or holdout requirements merely to obtain an early result;
- describe a representative integration run as model-selection evidence;
- treat synthetic or purchased data as native IG execution evidence;
- silently combine native, IG historical-candle and external-market observations;
- reserve the eventual native holdout while repeatedly inspecting its outcomes; or
- require the collector to stop while software development proceeds.

The contracts should not scaffold deferred model families before an experiment needs them.

---

## 5. Fixed decisions

### 5.1 Source authority

Every R2 experiment consumes one verified R1 foundation bundle.

The experiment configuration binds:

- the R1 bundle ID;
- the observation dataset ID;
- the foundation configuration ID;
- the panel, target and fold dataset IDs;
- the ordered instrument universe;
- instrument roles;
- configured horizons;
- the locked holdout range; and
- the R1 application and image identity.

R2 does not rebuild alternative folds, targets or feature cut-offs inside model code.

### 5.2 Prediction target

The prediction target is the R1 completed-bar cumulative midpoint log return for the declared horizon.

The primary vertical path is 15 minutes. Schemas and algorithms must accept every configured R1 horizon from the outset.

Forecast values use `float64` log-return units. Prices and money are not model inputs at a monetary boundary in R2.

### 5.3 Instrument roles

- `TARGET` instruments may receive forecasts.
- `CONTEXT` instruments may contribute features but receive no tradable forecast target.
- VIX remains context-only.
- The exact ordered universe and roles come from the verified R1 bundle.
- An R2 experiment may exclude a target instrument only through an explicit, identity-bearing eligibility decision.

No model may silently infer instrument roles from whether data happen to be present.

### 5.4 Numerical implementation

Use a mature, pinned Ridge implementation rather than a custom optimiser.

The initial implementation should use scikit-learn Ridge with:

- `float64` matrices;
- an explicitly configured deterministic solver;
- explicit tolerance and iteration limits;
- no solver selected through `auto`;
- a pinned package version and lockfile;
- NumPy, scikit-learn and runtime image identities in build evidence; and
- declared numerical replay tolerances.

The first implementation should use one solver consistently across local and pooled models unless measured scale makes that impractical. A solver change is an experiment configuration change.

No executable pickle is the sole model authority. Canonical linear-model evidence consists of the feature schema, preprocessing state, intercepts, coefficients and fit configuration.

### 5.5 Holdout policy

The R1 holdout remains completely unavailable to:

- feature-family eligibility decisions;
- feature definitions;
- missingness thresholds;
- preprocessing choices;
- alpha selection;
- model-family selection;
- evaluation bucket thresholds;
- acceptance thresholds; and
- diagnostic interpretation used to modify the experiment.

OOF work excludes holdout decision rows entirely. Holdout features and forecasts are materialised only after the OOF selection manifest has been frozen.

The first holdout reveal consumes that holdout for the selected experiment family. Any model or feature change made after observing holdout outcomes is exploratory and requires a newly accumulated future holdout before it can make another confirmatory claim.

---

## 6. Readiness states and evidence gates

R2 work is governed by four separate readiness states.

A later readiness state depends on the earlier software state, but software development does not depend on having the final research dataset.

### 6.1 Implementation readiness

R2 implementation is ready to begin when the repository has:

- a verified R1 foundation implementation;
- synthetic fixtures exercising R1-compatible contracts; and
- at least one representative verified R1 bundle for integration testing.

The representative bundle does not need to satisfy the decision-grade history threshold.

Implementation-ready work includes:

- experiment contracts and semantic identities;
- causal feature materialisation;
- fold-local preprocessing;
- chronological alpha selection;
- local Ridge;
- pooled local-feature Ridge;
- pooled cross-asset Ridge;
- forecast and coverage artefacts;
- evaluation code;
- independent prediction and metric replay;
- selection-manifest mechanics;
- holdout-isolation mechanics;
- CLI workflows; and
- verification.

Evidence produced at this state is labelled:

```text
IMPLEMENTATION_EVIDENCE_ONLY
```

It may prove software correctness, causal behaviour and reproducibility. It cannot support model selection or an effectiveness claim.

### 6.2 Representative-data integration readiness

Representative-data integration is ready when an available native R1 bundle contains enough genuine variation to exercise:

- more than one instrument;
- gaps and missingness;
- revisions;
- uneven listing start times;
- real feature distributions;
- real fold membership;
- model fitting;
- persistence;
- numerical replay; and
- evaluation reconciliation.

The bundle may have insufficient duration, common coverage or instrument breadth for a scientific conclusion.

Integration runs should be used to identify:

- assumptions not exercised by synthetic fixtures;
- unexpectedly sparse features;
- unsuitable matrix shapes;
- memory and runtime constraints;
- numerical instability;
- coverage dispositions;
- real-world manifest size;
- incorrect source or listing assumptions; and
- failure modes requiring additional tests.

Outputs are labelled:

```text
IMPLEMENTATION_EVIDENCE_ONLY
INSUFFICIENT_HISTORY_FOR_MODEL_CONCLUSION
```

Configured training or validation durations must not be shortened simply to force the real bundle through a nominal confirmatory workflow.

### 6.3 Confirmatory OOF readiness

The first decision-grade R2 experiment becomes ready only when a verified R1 bundle contains at least:

- **16 calendar weeks** of common causal evidence;
- **6 eligible target instruments**;
- **3 declared market groups**, with at least two eligible targets in each;
- **90% valid 15-minute target coverage** for every eligible instrument in every configured research block;
- **6 weeks** of initial training history;
- **3 chronological OOF validation periods of 2 weeks each**; and
- a **4-week locked holdout**.

Coverage is measured only during intervals in which the instrument source is causally known to be active. Genuine closures and pre-listing periods do not count as missing opportunities.

The full captured observation universe remains in the R1 bundle. Instruments that do not qualify may remain context inputs or be explicitly R2-ineligible; they are not silently deleted from the evidence.

The 15-minute horizon is the only horizon required for the first confirmatory experiment. Support for 5, 30 and 60 minutes must remain structurally correct, but insufficient evidence at those horizons does not block the initial R2 result.

These are experiment-entry thresholds, not success metrics. Instrument count or row count alone does not establish sufficient evidence.

A confirmatory OOF run may begin only after:

- the qualifying instrument subset is fixed;
- market-group assignments are fixed;
- feature-family eligibility is fixed;
- the exact training, validation and holdout ranges are fixed;
- acceptance and concentration thresholds are fixed;
- every intended model comparison is registered; and
- the qualifying R1 bundle is frozen.

### 6.4 Locked-holdout readiness

The holdout becomes available only after:

- the confirmatory OOF run is complete;
- all OOF artefacts have been independently verified;
- the experiment count is recorded;
- the selected and required-control configurations are fixed;
- the final pre-holdout fitting procedure is fixed;
- metric and bucket definitions are fixed; and
- an immutable OOF selection manifest has been written.

Holdout features must not be materialised before this gate.

Holdout machinery may be developed and tested earlier using synthetic fixtures or explicitly disposable integration ranges. Those ranges must not be confused with the eventual confirmatory holdout.

### 6.5 Readiness status output

The readiness command should report the states independently:

```text
R2 software contract: READY
R2 representative integration: NOT_READY
R2 confirmatory foundation data: NOT_READY
R2 inner-validation row evidence: PARTIALLY_READY
R2 confirmatory OOF experiment: NOT_READY
R2 locked holdout evaluation: NOT_READY
```

This example is the expected R2.A state before representative feature/fit/replay evidence, a
qualifying frozen native foundation and the R2.C inner split exist. Those pending dependencies keep
overall confirmatory OOF readiness unavailable independently of contract readiness.

A single `READY` or `NOT_READY` value is insufficient because it would either block useful implementation work or imply scientific readiness prematurely.

---

## 7. Dependency graph

```text
Verified R1 contracts and synthetic fixtures
        |
        v
R2.A  Experiment contract and software readiness
        |
        +-----------------------------------------------+
        |                                               |
        v                                               v
R2.B  Causal raw features                       Parallel data track
        |                                        - live native capture
        |                                        - readiness reporting
        |                                        - purchased-data review
        v                                        - provenance decisions
R2.C  Fold-local preprocessing                          |
      and alpha selection                               |
        |                                               |
        +-------------------+---------------------------+
                            |
             +--------------+--------------+
             |                             |
             v                             v
R2.D  Local Ridge baseline         R2.E  Pooled controls
             |                             |
             +--------------+--------------+
                            |
                            v
R2.F1 Evaluation and selection machinery
     using fixtures and representative data
                            |
                            v
          Software implementation can complete here
                            |
                            | requires qualifying frozen bundle
                            v
R2.F2 Confirmatory chronological OOF experiment
                            |
                            v
R2.G1 Immutable selection freeze
                            |
                            v
R2.G2 Locked holdout evaluation
                            |
                            v
R2.H  Final research evidence bundle
```

Verifier support is implemented alongside each software stage.

Live collection and approved historical-data investigation proceed in parallel. They do not block R2.A through R2.F1.

---

## 8. Parallel data-readiness track

The collector continues uninterrupted while R2 software is implemented.

A read-only readiness process should regularly evaluate the available native history without selecting a model or consuming a holdout.

For each instrument and candidate common subset, report:

- first and last usable decision time;
- total active-source duration;
- valid feature coverage;
- valid 15-minute target coverage;
- late or unavailable observations;
- gaps and ambiguous-source intervals;
- valid opportunities by proposed research block;
- usable common weeks;
- declared market group;
- target or context eligibility;
- earliest possible qualifying bundle cutoff; and
- the specific unmet readiness conditions.

The readiness process may evaluate possible date boundaries, but it must not use model outcomes to choose them.

Readiness reports are mutable operational observations. They are not themselves frozen research bundles.

### 8.1 Live native data

Native capture remains the required source for:

- IG-specific feature and target conclusions;
- native coverage conclusions;
- IG spread and quote-size evidence;
- eventual IG paper fills;
- IG slippage evidence; and
- any later claim tied to executable IG products.

Once sufficient native evidence exists, a precise source cutoff is selected and exported as an immutable R1 bundle.

Collector data arriving after that cutoff belongs to a later experiment or future holdout. It is not appended silently to a frozen experiment.

### 8.2 Purchased or externally sourced data

A purchased dataset requires an explicit decision covering:

- vendor and licence;
- retention and redistribution restrictions;
- venue;
- exact product mapping;
- timestamp definitions;
- bar or tick construction;
- bid, ask, midpoint and trade semantics;
- timezone and session handling;
- corrections and revisions;
- corporate or contract adjustments;
- missing-data behaviour; and
- correspondence with the intended IG CFD market.

External data may have one of three declared roles.

#### Development data

Used to exercise:

- feature computation;
- larger matrices;
- long chronological folds;
- missingness behaviour;
- model fitting;
- performance; and
- evaluation.

It supports implementation evidence only.

#### External-market research data

Used to determine whether the forecasting concept has evidence on the named external venue or product.

Conclusions are limited to that external market. They are not described as IG CFD findings.

#### Training-augmentation data

Used as an input to a separately identified augmented model configuration.

The experiment must compare:

- native-only training;
- augmented training; and
- required simpler controls.

A native IG holdout remains necessary for an IG-native conclusion. External observations must not appear in the native holdout or masquerade as native quote history.

All source types remain provenance-distinct throughout feature construction, modelling and reporting.

---

## 9. Artefact contracts

R2 should introduce only the contracts necessary to make the experiment reproducible and independently reviewable.

### 9.1 R2 experiment configuration

Suggested contract:

```text
qtrad-r2-experiment-config-v1
```

It contains at least:

```text
name
schema_version

r1_bundle_id
observation_dataset_id
foundation_configuration_id
panel_dataset_id
target_dataset_id
fold_dataset_id

ordered_instruments
instrument_roles
target_instrument_eligibility
target_instruments
confirmatory_target_instruments
market_groups
horizons
primary_horizon

feature_sets
feature_windows
feature_coverage_thresholds
feature_eligibility

preprocessing_policy
alpha_grid
inner_validation_policy
ridge_solver
ridge_tolerance
ridge_max_iterations
pooled_weighting_policy

minimum_training_rows
minimum_inner_validation_rows
minimum_outer_validation_rows

metric_policy
forecast_bucket_policy
state_bucket_policy
model_selection_policy
acceptance_thresholds

holdout_range
numeric_replay_tolerances
market_data_source_class
evidence_class
```

Target and feature-family eligibility decisions contain an authenticated semantic evidence ID,
subject, state, reason and bounded evidence interval. Their evidence interval ends before the locked
holdout. The full R1 target universe remains present even when an instrument is explicitly
`NOT_ELIGIBLE` or `PENDING`; `target_instruments` contains the model-eligible set and
`confirmatory_target_instruments` freezes the subset used by the confirmatory readiness gates.

Unknown fields fail closed.

The configuration has a semantic ID calculated from canonical JSON. All model, feature, fit, forecast
and evaluation artefacts bind that ID. `market_data_source_class` identifies provider origin and
`evidence_class` independently identifies implementation-only or confirmatory research status; both
must match the verified foundation and every downstream artefact.

### 9.2 Raw feature dataset

Suggested contract:

```text
qtrad-r2-features-v1
```

Each row is keyed by:

```text
target_instrument_id
decision_time
feature_data_asof
latest_feature_bar_end
feature_set_id
```

The row contains:

- ordered raw feature values;
- explicit nulls;
- feature-availability indicators;
- source-observation lineage sufficient to reproduce each feature family;
- raw-feature schema ID;
- observation and panel dataset IDs; and
- the R2 experiment configuration ID.

Raw features are not imputed or standardised.

The OOF raw-feature child contains no holdout decision rows. A separate holdout feature child is created only after selection freeze.

### 9.3 Preprocessing-selection and fold-fit artefacts

R2.C owns a separate preprocessing feature-semantics contract, derived in exact raw-feature order without
changing the R2.B v1 schema:

```text
qtrad-r2-preprocessing-schema-v1
```

It classifies each feature as `CONTINUOUS` or `BINARY_INDICATOR`; the preprocessing-selection semantic
identity binds both its schema ID and full schema. R2.C also owns the selection contract:

```text
qtrad-r2-preprocessing-selection-v1
```

One artefact is stored for each outer fold and model scope. `model_family` discriminates the union:
`LOCAL_RIDGE` binds exactly one eligible target, while either pooled family binds the complete ordered
eligible-target universe. The semantic identity binds the instrument-identity, intercept and membership
policies in addition to `model_family` and `horizon`.

The R2.C artefact owns:

- exact outer, inner-fit, inner-validation and purged target membership;
- fold-local preprocessing state, including active and explicitly dropped features;
- imputation medians, scaling means and scales, and sample weights;
- the complete alpha candidate grid and candidate losses;
- the exactly selected alpha and deterministic larger-alpha tie-break;
- the split, preprocessing, loss, weighting and Ridge solver policies; and
- application image and scikit-learn library replay identities for the selection computation.

R2.D and R2.E own the final fold-fit contract:

```text
qtrad-r2-fold-fit-v1
```

That artefact is stored for each model family, horizon, outer fold and target instrument or pool. It owns
the authoritative model-family and horizon fit identity, final intercept and ordered coefficients, fit row
and exclusion counts, fit warnings or failure status, final-fit library and image identities, and numerical
diagnostics. R2.D supplies local fits; R2.E supplies pooled fits. No R2.C preprocessing-selection artefact
is evidence that a pooled model was selected or fitted.

### 9.4 Forecast and coverage artefacts

Valid forecasts continue to use an immutable forecast dataset with complete target, fold, feature-cutoff, model and experiment lineage.

A separate coverage artefact records every expected forecast opportunity, including failures:

```text
FORECASTED
FEATURES_UNAVAILABLE
INSUFFICIENT_TRAINING
INSUFFICIENT_INNER_VALIDATION
DEGENERATE_TARGET
DEGENERATE_FEATURE_MATRIX
NUMERICAL_FAILURE
MODEL_NOT_ELIGIBLE
```

A missing forecast is never silently replaced with zero.

Coverage denominators come from expected target membership, not from successfully emitted forecasts.

### 9.5 Evaluation report

Suggested contract:

```text
qtrad-r2-evaluation-v1
```

It binds:

- forecast, coverage and target dataset IDs;
- evidence classification;
- comparison support;
- all metric definitions;
- all bucket definitions and thresholds;
- global metrics;
- fold, instrument, horizon and period metrics;
- feature and coefficient stability;
- concentration diagnostics;
- configuration count;
- rejected configurations; and
- any unavailable diagnostic with a reason.

### 9.6 OOF selection manifest

Suggested contract:

```text
qtrad-r2-selection-v1
```

The selection manifest freezes:

- all configurations evaluated;
- the predeclared comparator set;
- the primary and secondary selection metrics;
- the OOF evidence used;
- selected feature sets;
- selected model configurations;
- selected holdout comparator set;
- final pre-holdout fitting procedure;
- holdout range;
- code and image identity; and
- the time and actor that froze the selection.

It is immutable and contains no holdout outcomes.

A confirmatory selection manifest may be created only from a confirmatory OOF run. Disposable fixture manifests must have a distinct evidence classification.

### 9.7 Holdout report

Suggested contract:

```text
qtrad-r2-holdout-evaluation-v1
```

It references the frozen selection manifest and contains:

- separately materialised holdout features;
- final pre-holdout model fits;
- holdout forecasts and coverage;
- the same metric and bucket definitions used in OOF evaluation;
- no newly selected threshold or model;
- a clear pass, fail or inconclusive conclusion for each declared question; and
- a statement that the holdout has been consumed.

### 9.8 R2 evidence bundles

The top-level R2 bundles remain thin. They reference independently manifested children rather than duplicating rows.

A software verification bundle may reference:

- configuration;
- synthetic fixtures;
- representative R1 inputs;
- implementation feature datasets;
- fold fits;
- forecasts;
- coverage;
- evaluations;
- replay evidence;
- selection-mechanics fixtures; and
- holdout-isolation fixtures.

A confirmatory research bundle adds:

- the qualifying frozen R1 bundle;
- confirmatory OOF features;
- confirmatory fold fits;
- confirmatory OOF forecasts and coverage;
- confirmatory OOF evaluation;
- immutable selection manifest;
- holdout features;
- final fits;
- holdout forecasts and coverage;
- holdout evaluation; and
- consumed-holdout evidence.

---

## 10. Causal feature construction

### 10.1 Current-cutoff selection

A feature calculated for decision time `D` uses the row's R1 `feature_data_asof` as its single availability cutoff.

All bars needed for a rolling feature must be selected as of that cutoff.

Do not construct a current rolling window by joining historical panel rows that were selected using their own earlier cut-offs. That would give the feature a different revision policy from “latest information known at `D`”.

For every required historical interval:

```text
bar interval end <= latest_feature_bar_end
selected availability time <= feature_data_asof
```

The highest eligible revision from at most one unambiguous source listing is used.

Revisions persisted after the current cutoff are unavailable. Missing intervals remain missing.

### 10.2 Exact windows

Return features use exact completed-bar endpoints.

For a declared lookback `L`:

```text
return_L =
    log(close at latest_feature_bar_end)
    - log(close at latest_feature_bar_end - L)
```

If either endpoint is unavailable, `return_L` is null. There is no interpolation and no substitution of a nearby interval.

Rolling statistics declare:

- their window;
- expected observation count;
- minimum valid count or coverage;
- treatment of gaps;
- whether consecutive observations are required; and
- an explicit coverage feature.

These values are part of feature-set identity.

### 10.3 Feature family A — local returns

The initial local return family should remain small and auditable:

- one-minute log return;
- 5-minute log return;
- 15-minute log return;
- 30-minute log return;
- 60-minute log return;
- short-minus-long return contrasts; and
- exact endpoint-availability flags.

Only windows covered by the observation bounds are emitted.

### 10.4 Feature family B — local volatility and range

Initial features may include:

- realised standard deviation of one-minute log returns over configured windows;
- mean absolute one-minute return;
- completed-bar log high-low range;
- rolling mean log range;
- return sign balance;
- available interval count; and
- window coverage ratio.

Do not introduce multiple specialised volatility estimators until the simple family has been evaluated.

### 10.5 Feature family C — time and availability state

Initial time features are:

- sine and cosine of UTC minute-of-day;
- sine and cosine of UTC day-of-week;
- declared source-active state;
- target-feature missing fraction;
- cross-market availability count; and
- relevant R1 quality or gap dispositions.

The project does not currently have an authoritative exchange-session calendar. Therefore:

- fixed UTC time buckets may be used;
- they must be named `UTC_TIME_BUCKET`, not exchange sessions;
- exact boundaries are versioned; and
- named trading-session features remain ineligible until backed by a reviewed calendar artefact.

### 10.6 Feature family D — spread

Spread features require a separate eligibility record proving that BID, ASK and MID bars have compatible:

- instrument and listing identity;
- exact interval;
- as-of revision selection;
- availability;
- sample semantics; and
- coverage.

Eligible initial spread features may include:

```text
close_spread = ask_close - bid_close
spread_fraction = close_spread / mid_close
spread_bps = 10,000 * spread_fraction
rolling_spread_mean
rolling_spread_change
spread coverage
```

A spread is null when the three sides do not align exactly. It is not reconstructed from unrelated bars or forward-filled.

Spread-family eligibility is decided using pre-holdout evidence only.

### 10.7 Feature family E — validated quote imbalance

Quote imbalance is optional and cannot block the core R2 path.

It becomes eligible only after retained evidence establishes:

- bid-size and ask-size meaning;
- positive-value coverage;
- timestamp and update semantics;
- correction and missingness treatment;
- source provenance; and
- sufficient pre-holdout coverage.

A candidate definition may be:

```text
imbalance = (bid_size - ask_size) / (bid_size + ask_size)
```

The denominator must be positive. Missing or invalid size is null.

Top-of-book size change is not trade volume and is never called cumulative volume delta. If the eligibility gate fails, the feature family is recorded as `NOT_ELIGIBLE`; values are not replaced with zero.

### 10.8 Feature family F — pooled cross-asset context

The pooled non-graph context should be a fixed-width, order-independent summary rather than a hidden or dynamically changing peer set.

Initial features should include:

- leave-one-out mean target-universe return by lookback;
- leave-one-out median return;
- cross-sectional return dispersion;
- proportion of available instruments with positive return;
- available instrument count;
- leave-one-out asset-class mean and dispersion;
- VIX context returns and volatility where available; and
- cross-market missingness and source-active counts.

The target instrument is excluded from leave-one-out aggregates.

Aggregates record their eligible instrument count. An aggregate is null below its configured minimum count.

No economic graph, learned adjacency, pairwise message passing or target-specific peer selection enters R2.

### 10.9 Bounded ablation ladder

Avoid combinatorial feature searches.

The initial declared ladder is:

```text
L0  local returns + time/availability
L1  L0 + local volatility/range
L2  L1 + spread, when eligible
L3  L2 + quote imbalance, when eligible

P0  pooled model using the selected local feature set
P1  P0 + pooled cross-asset context
```

Feature families are cumulative unless a specifically declared leave-one-family-out diagnostic is run.

Every evaluated feature set receives an immutable identity and counts towards the recorded experiment total.

---

## 11. Missingness and preprocessing

### 11.1 Raw missingness

Raw nulls remain null in the feature dataset.

No price, return, spread or imbalance feature is forward-filled.

Every imputable numeric feature has an associated missingness indicator unless its semantics make the indicator redundant and that decision is declared in configuration.

### 11.2 Fold-local transformations

All learned transformations are fitted inside the outer training fold.

This includes:

- median imputation;
- centring;
- scaling;
- variance filtering;
- clipping or winsorisation, if enabled;
- categorical vocabulary;
- volatility-regime thresholds;
- spread-state thresholds;
- forecast-bucket thresholds used operationally; and
- instrument weighting.

Validation rows are transformed using parameters frozen from training data.

Holdout rows are transformed using parameters frozen from the final pre-holdout fit.

### 11.3 Initial preprocessing policy

The initial policy should be:

1. preserve explicit missingness indicators;
2. median-impute continuous features using training data;
3. remove features with no training variance;
4. standardise continuous features using training mean and standard deviation;
5. leave binary indicators unscaled unless configuration says otherwise; and
6. reject non-finite transformed values.

Avoid winsorisation in the first path unless unbounded outliers prevent stable fitting. If introduced, clipping bounds are training-only quantiles and part of experiment identity.

### 11.4 Insufficient evidence

Minimum row and feature-coverage thresholds are explicit configuration.

When a local instrument or pooled fold has insufficient evidence:

- do not weaken the threshold automatically;
- do not borrow validation rows;
- do not silently fall back to another alpha;
- do not emit a zero forecast; and
- record the appropriate forecast-coverage disposition.

---

## 12. Chronological alpha selection

### 12.1 Bounded alpha grid

Use a small, predeclared logarithmic alpha grid. The grid is part of experiment identity.

Do not launch an adaptive or open-ended search.

### 12.2 Inner chronological validation

Each outer training fold is divided into:

```text
inner fit interval
inner embargo/purge
inner validation tail
```

The inner validation interval must satisfy the same target-maturity and overlap rules as an outer fold.

For each alpha:

1. fit preprocessing on the inner fit rows;
2. fit Ridge on the transformed inner fit rows;
3. score only the inner validation rows;
4. retain coverage and failure evidence; and
5. use the configured primary tuning loss.

Select the alpha with the lowest valid inner loss. Deterministic ties favour:

1. the larger alpha;
2. then the lexically earlier canonical configuration ID.

After selection, refit preprocessing and Ridge on the complete outer training membership.

Outer validation data cannot affect alpha selection.

### 12.3 Final pre-holdout fit

The final pre-holdout model repeats the declared inner-tail procedure using only pre-holdout data.

It does not select alpha by inspecting the distribution of outer-fold winning alphas unless that aggregation policy was frozen in the original experiment configuration.

---

## 13. Model definitions

### 13.1 Zero-return comparator

The R1 zero-return probe remains the invariant reference:

```text
expected_return = 0
```

It is not counted as a fitted model and has full coverage wherever an eligible target exists.

### 13.2 Local Ridge

One local model is fitted for each:

```text
target instrument
horizon
outer fold
feature set
```

The local model uses:

- only target-relative local feature families;
- time and availability features;
- no other tradable instrument's price features;
- no pooled cross-asset aggregate; and
- no hidden state shared with another instrument.

All per-instrument preprocessors, coefficients and failures are manifested.

### 13.3 Pooled local-feature Ridge

The first pooled control uses the same target-relative local feature schema as the local model but trains one shared model across target instruments.

It includes an explicit instrument identity effect.

The initial encoding should use one fixed instrument indicator per target instrument with a declared intercept treatment. Do not rely on library defaults that create an unidentified global-intercept/full-one-hot combination.

Feature slopes remain shared. Instrument-by-feature interactions are not included in the first pooled control.

### 13.4 Pooled cross-asset Ridge

The second pooled control adds the declared cross-asset context family to the pooled local model.

This separates:

- benefit from greater pooled training volume; and
- benefit from cross-asset information.

Without the pooled-local ablation, those two effects would be confounded.

### 13.5 Pooled sample weighting

A heavily observed instrument must not dominate the pooled fit solely because it contributes more rows.

The default pooled policy gives each eligible target instrument equal total training weight within a fold, with weights normalised to mean one.

The weighting policy is explicit and manifested.

Evaluation still reports:

- observation-weighted results;
- instrument-balanced results; and
- per-instrument results.

### 13.6 Fit failures

A model fit fails closed on:

- insufficient rows;
- no target variance;
- no usable features;
- non-finite matrices;
- solver non-convergence;
- invalid coefficient shape;
- non-finite coefficients or intercept;
- prediction replay mismatch; or
- undeclared warnings designated as fatal.

Failure is retained as evidence and contributes to coverage reporting.

---

## 14. Out-of-fold evaluation

### 14.1 Comparison support

Every pairwise comparison reports both:

- **own support:** every row forecast by that model; and
- **common support:** only rows forecast by every model in the comparison.

Coverage is reported alongside both.

A model cannot appear superior merely by omitting difficult observations.

### 14.2 Primary predictive metrics

For each model, horizon and declared support, report:

- count of eligible targets;
- forecast count and coverage;
- MAE in log-return units;
- RMSE in log-return units;
- Pearson correlation;
- Spearman rank correlation;
- directional accuracy where meaningful;
- forecast mean and standard deviation;
- target mean and standard deviation; and
- mean forecast error.

Correlations require a configured minimum group size and non-zero variance. Otherwise they are `NOT_DEFINED`, not zero.

Directional accuracy declares treatment of exact-zero targets and forecasts and reports excluded counts.

### 14.3 Forecast calibration and buckets

Report:

- realised-return intercept against forecast;
- realised-return slope against forecast;
- training-defined forecast buckets;
- forecast and realised mean in each bucket;
- row and instrument counts per bucket;
- bucket-order Spearman relationship; and
- monotonicity failures.

Primary bucket thresholds are learned from training predictions and applied unchanged to outer validation.

Validation-local quantile buckets may be shown as a descriptive secondary diagnostic but cannot support a deployable calibration claim.

### 14.4 Required breakdowns

Metrics are broken down by:

- target instrument;
- asset class or declared market group;
- horizon;
- outer fold;
- chronological period;
- UTC time bucket;
- volatility regime;
- spread state, when eligible;
- quote-imbalance availability, when eligible;
- feature missingness band; and
- R1 data-quality or gap disposition.

Volatility and spread thresholds applied to validation are derived from the corresponding training fold.

### 14.5 Stability evidence

For every fitted model family, report:

- metric delta versus zero by fold;
- pooled-versus-local delta by fold;
- context-versus-pooled-local delta by fold;
- metric delta by instrument;
- median, range and dispersion of fold deltas;
- proportion of eligible folds with improvement;
- proportion of eligible instruments with improvement;
- contribution of the best instrument to aggregate improvement;
- contribution of the best period to aggregate improvement;
- forecast-scale stability;
- coefficient sign agreement across folds;
- coefficient magnitude distribution; and
- features repeatedly removed or unavailable.

Standardised coefficients are used for cross-fold comparisons.

No aggregate metric substitutes for stability evidence.

### 14.6 Cost-adjacent diagnostics

R2 may report, where compatible spread evidence exists:

- proportion of absolute forecasts exceeding the contemporaneous half-spread return;
- forecast magnitude divided by spread;
- changes in forecast sign;
- mean absolute change in standardised forecast; and
- forecast turnover proxy by instrument.

These are diagnostics, not net returns, simulated fills or profitability claims. Complete costs and portfolio turnover belong to R3.

---

## 15. OOF model selection

### 15.1 Predeclared primary criterion

The initial primary model-selection criterion should be instrument-balanced outer-OOF squared-error loss on common support.

Secondary evidence includes:

- MAE;
- Pearson and Spearman relationship;
- direction;
- forecast-bucket ordering;
- coverage;
- fold stability;
- instrument stability; and
- concentration.

The primary criterion, weighting and tie-breaks are frozen before fitting.

### 15.2 Selection hierarchy

The required comparisons are:

```text
local Ridge                versus zero return
pooled local-feature Ridge versus local Ridge
pooled cross-asset Ridge   versus pooled local-feature Ridge
```

A more complex feature set is not selected solely because it beats zero. It must improve on its immediately simpler comparable configuration.

### 15.3 Credibility gate

A configuration may enter the frozen holdout comparator set only when:

- it has valid evidence in more than one chronological period or asset subset;
- its primary metric is not worse than the required simpler comparator by more than the configured tolerance;
- any claimed improvement is not entirely attributable to one instrument or period;
- its coverage is sufficient and not materially lower without explanation;
- coefficients and forecasts are numerically stable;
- no correctness or replay check failed; and
- every evaluated configuration is counted and retained.

The gate thresholds are explicit configuration. They must not be invented after results are seen.

A weak model may still enter the holdout comparator set as a required control, but it is labelled as such rather than as an OOF-selected candidate.

### 15.4 Freeze

Before any holdout feature is materialised, write and verify the immutable selection manifest.

The frozen holdout comparator set should normally contain:

- zero return;
- the retained local Ridge configuration;
- pooled local-feature Ridge;
- pooled cross-asset Ridge; and
- only those optional spread or imbalance variants explicitly justified by OOF evidence.

---

## 16. Locked holdout evaluation

### 16.1 Holdout build sequence

The holdout command:

1. verifies the R1 bundle;
2. verifies the R2 selection manifest;
3. confirms that the selection manifest contains no holdout outcomes or features;
4. materialises holdout features from R1 observations;
5. repeats the frozen final pre-holdout fitting procedure;
6. emits forecasts and coverage for the frozen comparator set;
7. applies unchanged metric and bucket definitions;
8. writes the holdout report;
9. marks the holdout as consumed for the experiment family; and
10. independently verifies the completed evidence bundle.

### 16.2 Interpretation

The holdout report answers the predeclared questions.

It must not:

- choose a new alpha;
- revise a feature threshold;
- drop an instrument because its outcome is poor;
- redefine a bucket;
- substitute a different support silently;
- select a new model;
- conceal failed coverage; or
- describe post-hoc exploration as confirmatory evidence.

A holdout failure remains in the research record. Subsequent changes require a new experiment ID and future holdout period.

---

## 17. Independent verification

### 17.1 Exact verification

The verifier exactly checks:

- manifest hashes and file hashes;
- semantic dataset IDs;
- R1 child references;
- ordered universe and roles;
- target, fold and forecast membership;
- feature schemas and ordering;
- raw feature lineage;
- absence of holdout rows from OOF artefacts;
- preprocessing membership;
- inner and outer chronology;
- alpha-grid completeness;
- deterministic tie-breaks;
- coefficient dimensions;
- forecast coverage denominators;
- bucket definitions;
- evaluation membership;
- evidence classification;
- configuration count;
- selection-manifest immutability; and
- holdout dependence on the frozen selection.

### 17.2 Numerical replay

The verifier reconstructs predictions without loading a pickled model:

```text
raw features
→ manifested imputation
→ manifested scaling
→ manifested active-feature selection
→ manifested coefficients and intercept
→ forecast
```

Predictions must match stored forecasts within declared absolute and relative tolerances.

Within the pinned build image, the verifier also refits Ridge from the manifested training matrix and configuration and compares:

- selected alpha;
- intercept;
- coefficients;
- predictions; and
- solver convergence evidence.

Do not require cross-platform bit identity from floating-point linear algebra. Require exact semantic inputs and tightly bounded numerical equivalence.

### 17.3 Evaluation replay

Every metric, bucket and stability table is recomputed from:

- verified forecasts;
- verified coverage;
- verified R1 targets;
- verified bucket definitions; and
- the experiment configuration.

The report is not trusted merely because its JSON hash is valid.

---

## 18. Staged implementation

R2 implementation proceeds before the qualifying research bundle exists.

Stages R2.A through R2.F1 may be completed using synthetic fixtures and representative native data. R2.F2, R2.G1, R2.G2 and final research completion require a qualifying frozen bundle.

Each stage records both:

```text
software_status
research_evidence_status
```

A stage may be software-complete while its confirmatory research execution remains pending.

### R2.A — Experiment contract and software readiness

#### Dependencies

- merged and verified R1 foundation implementation;
- synthetic R1-compatible fixtures;
- at least one representative verified native R1 bundle;
- retained instrument roles and holdout contract.

#### Deliverables

- strict R2 experiment configuration;
- feature-set contracts;
- model-family identifiers;
- numerical dependency decision;
- minimum evidence thresholds;
- pre-holdout readiness report;
- CLI configuration loader; and
- semantic configuration identity.

#### Correctness requirements

- reject unknown configuration fields;
- require exact R1 dataset identities;
- require exact ordered universe and roles;
- require the R1 holdout to be final where a confirmatory experiment is declared;
- require configured horizons to exist in R1 targets and folds;
- prevent VIX from becoming a target;
- bind all feature eligibility decisions to pre-holdout evidence;
- record insufficient native history without weakening gates;
- distinguish implementation and confirmatory evidence classes.

#### Exit evidence

- configuration round-trip and tampering tests;
- software-readiness status independent of dataset readiness;
- readiness output against the current representative bundle;
- explicit `READY`, `NOT_READY` or `PARTIALLY_READY` state by feature family;
- no requirement that the current bundle satisfy confirmatory thresholds;
- no effectiveness claim.

### R2.B — Causal raw feature dataset

#### Dependencies

- R2.A;
- verified R1 observation, panel and configuration children.

#### Deliverables

- raw-feature registry;
- current-cutoff as-of historical selector;
- local return features;
- volatility/range features;
- time and availability features;
- pooled cross-asset aggregates;
- separately gated spread features;
- separately gated imbalance features;
- OOF feature manifest and verifier.

#### Correctness requirements

- select every source bar using the current row's cutoff;
- require exact feature endpoints;
- retain explicit nulls and coverage;
- never forward-fill prices;
- preserve fixed feature ordering;
- exclude holdout decision rows from confirmatory OOF features;
- produce identical semantic identity under reversed input iteration;
- fail on ambiguous source lineage;
- fail when a feature references observations available after its cutoff;
- keep external and native provenance distinct.

#### Exit evidence

- synthetic exact-window tests;
- revision-as-of tests;
- gap and closure tests;
- feature order determinism;
- holdout-exclusion tests;
- feature builds against both synthetic and current representative bundles;
- real-data coverage and failure-disposition reporting;
- no model-effectiveness interpretation.

### R2.C — Fold-local preprocessing and alpha selection

#### Dependencies

- R2.B;
- verified R1 fold membership.

#### Deliverables

- preprocessing fit contract;
- preprocessing application code;
- inner chronological split builder;
- bounded alpha evaluator;
- deterministic selection rule;
- model-fit failure dispositions;
- preprocessing-selection manifest support.

#### Correctness requirements

- every fitted transformation uses inner or outer training membership only;
- validation mutation cannot change preprocessing or alpha;
- target maturity and purging apply to inner splits;
- no implicit library cross-validation;
- no random split;
- no automatic fallback;
- non-finite matrices fail closed.

#### Exit evidence

- leakage regression tests;
- deterministic alpha-selection tests;
- tie-break tests;
- insufficient-evidence tests;
- numerical replay tests;
- synthetic long-history tests proving chronological selection;
- representative native execution where available;
- explicit handling where the real bundle cannot form scientifically meaningful folds.

### R2.D — Local Ridge baseline

#### Dependencies

- R2.C.

#### Deliverables

- one model per target instrument, fold and horizon;
- local-feature ablation ladder;
- local fold-fit artefacts;
- local OOF forecasts;
- local forecast-coverage artefact;
- coefficient stability summary.

#### Correctness requirements

- use no other tradable instrument's price features;
- preserve all per-instrument state in manifests;
- emit no forecast on failed fit;
- bind each forecast to its R1 target and outer fold;
- verify feature and training cut-offs;
- support instruments with unequal coverage without changing thresholds silently.

#### Exit evidence

- synthetic local-signal recovery;
- synthetic no-signal control;
- independent forecast replay;
- local fits against representative native data where possible;
- real-data coverage and numerical diagnostics;
- explicit `IMPLEMENTATION_EVIDENCE_ONLY` classification for non-qualifying bundles.

A decision-grade local-model conclusion is deferred to R2.F2.

### R2.E — Pooled non-graph controls

#### Dependencies

- R2.C;
- R2.D for the final comparison, though implementation may proceed in parallel.

#### Deliverables

- pooled local-feature Ridge;
- pooled cross-asset Ridge;
- explicit instrument identity encoding;
- equal-instrument training weights;
- pooled fold-fit artefacts;
- pooled OOF forecasts and coverage;
- pooled-versus-local ablation report.

#### Correctness requirements

- fixed feature schema across instruments;
- no hidden dynamic peer selection;
- leave-one-out context excludes the target instrument;
- VIX remains context-only;
- pooled sample weights are manifested;
- no graph edges, adjacency or message passing;
- distinguish benefit from pooling from benefit of cross-asset context.

#### Exit evidence

- synthetic shared-signal and cross-asset-signal recovery;
- weighting and instrument-order invariance tests;
- pooled fits against representative native data where possible;
- common-support evaluation mechanics;
- explicit `IMPLEMENTATION_EVIDENCE_ONLY` classification for non-qualifying bundles.

A decision-grade pooled-versus-local conclusion is deferred to R2.F2.

### R2.F1 — Evaluation and selection machinery

#### Dependencies

- R2.D;
- R2.E.

#### Data requirement

Synthetic fixtures and a representative R1 bundle are sufficient.

#### Deliverables

- complete evaluation implementation;
- own-support and common-support comparisons;
- global and bucketed metrics;
- stability and concentration calculations;
- experiment-count register;
- retained rejected configurations;
- selection-manifest writer and verifier;
- independently referenced local-comparator manifest child for every persisted evaluation/selection
  artefact;
- synthetic and representative integration reports.

#### Correctness requirements

- recompute metrics from verified forecasts;
- use training-derived bucket thresholds;
- report undefined metrics explicitly;
- expose coverage differences;
- reauthenticate the referenced local-comparator manifest, including exact target/fold/feature-set
  coverage and explicit failed/unavailable coverage, when reconstructing common support;
- prevent selection from a single aggregate statistic;
- prove that holdout data cannot affect OOF artefacts;
- distinguish fixture, integration and confirmatory evidence.

#### Exit evidence

- verified synthetic reports;
- verified representative-data reports;
- mutation tests for metrics and support;
- selection-manifest mechanics proven using disposable fixture holdouts;
- no confirmatory model-selection claim.

Software implementation may be considered complete after R2.F1 and the corresponding R2.H software-verification work are complete.

### R2.F2 — Confirmatory chronological OOF experiment

#### Dependencies

- software-complete R2.A through R2.F1;
- a qualifying frozen R1 bundle;
- frozen feature eligibility and experiment configuration.

#### Deliverables

- decision-grade OOF forecasts;
- complete OOF coverage;
- local and pooled comparisons;
- feature-family ablations;
- stability and concentration evidence;
- complete configuration register;
- reviewable pre-holdout selection rationale.

#### Correctness requirements

- use only the frozen qualifying bundle;
- use no holdout features or outcomes;
- make no undeclared threshold changes;
- preserve failed and rejected configurations;
- require common-support comparisons;
- prevent one instrument or period from silently determining an aggregate claim.

#### Exit evidence

- independently verified OOF report;
- explicit positive, negative or inconclusive OOF findings;
- confirmed absence of holdout access;
- complete evidence needed for selection freeze.

### R2.G1 — Selection freeze

#### Dependencies

- verified R2.F2 evidence.

#### Deliverables

- immutable selection manifest;
- frozen required controls;
- frozen selected configurations;
- frozen final fitting procedure;
- frozen holdout metrics and thresholds;
- recorded experiment count.

#### Correctness requirements

- contain no holdout features or outcomes;
- bind the qualifying R1 bundle;
- bind all OOF evidence used;
- prevent later substitution of code, models, features or thresholds.

#### Exit evidence

- verified immutable selection identity;
- explicit authorisation to materialise the holdout.

### R2.G2 — Locked holdout evaluation

#### Dependencies

- verified R2.G1 selection manifest;
- untouched holdout range in the qualifying R1 bundle.

#### Deliverables

- separately materialised holdout feature dataset;
- final pre-holdout model fits;
- holdout forecasts and coverage;
- locked holdout evaluation;
- consumed-holdout record.

#### Correctness requirements

- permit only frozen configurations;
- use only pre-holdout fitting data;
- apply unchanged metrics and thresholds;
- expose every fit and coverage failure;
- prevent replacement of an existing holdout report;
- record post-holdout changes as exploratory.

#### Exit evidence

- independently verified holdout report;
- direct comparison of zero, local, pooled-local and pooled-context controls;
- positive, negative or inconclusive conclusion for every declared research question;
- explicit record that the holdout has been consumed.

### R2.H — Verification and evidence bundles

R2.H has two deliverable levels.

#### Software verification bundle

May be completed before the qualifying dataset exists. It contains:

- contracts;
- synthetic fixtures;
- representative-data integrations;
- feature replay;
- fit replay;
- forecast replay;
- evaluation replay;
- leakage mutation tests;
- selection and holdout machinery tests; and
- build and runtime evidence.

It establishes that R2 is software-complete.

#### Confirmatory research bundle

Requires R2.F2 through R2.G2. It adds:

- the qualifying frozen R1 bundle;
- confirmatory OOF features and fits;
- OOF forecasts and coverage;
- OOF evaluation;
- immutable selection manifest;
- holdout features and final fits;
- holdout forecasts and coverage;
- locked holdout evaluation; and
- the consumed-holdout record.

It establishes that R2 is research-complete.

#### Correctness requirements

- authenticate every child independently;
- replay features, transformations, forecasts and metrics;
- enforce exact lineage and declared numerical tolerances;
- reject orphaned, substituted or mismatched children;
- require the selection manifest to predate holdout artefacts;
- keep implementation status separate from this normative plan.

#### Exit evidence

- successful independent verification;
- green formatting, lint, typing and tests;
- verified software evidence using synthetic and representative data;
- verified confirmatory native evidence when the qualifying bundle exists;
- no unresolved correctness finding.

---

## 19. CLI workflow

Suggested commands:

```text
qtrad research baselines readiness
    --foundation-bundle
    --experiment
    --output

qtrad research baselines features-build
    --foundation-bundle
    --experiment
    --output

qtrad research baselines oof-build
    --foundation-bundle
    --experiment
    --features-manifest
    --evidence-class IMPLEMENTATION
    --output

qtrad research baselines oof-build
    --foundation-bundle
    --experiment
    --features-manifest
    --evidence-class CONFIRMATORY
    --output

qtrad research baselines oof-verify
    --bundle

qtrad research baselines selection-freeze
    --oof-bundle
    --output

qtrad research baselines holdout-evaluate
    --foundation-bundle
    --selection
    --output

qtrad research baselines verify
    --bundle
```

The readiness command reports separately:

```text
software_contract_ready
representative_integration_ready
confirmatory_data_ready
inner_validation_rows_ready
confirmatory_oof_ready
locked_holdout_ready
```

`confirmatory_data_ready` checks the frozen R1 foundation's 16 anchored weekly common-opportunity
buckets, source-active duration, per-instrument and per-block coverage, first-fold training
membership and outer-validation/holdout row minima.
`inner_validation_rows_ready` remains `PARTIALLY_READY` until R2.C has produced a verified
chronological inner-split artefact. Overall confirmatory OOF readiness cannot be `READY` while that
model-specific row evidence or representative integration evidence is pending.

`CONFIRMATORY` OOF execution fails unless the configured data-readiness gate passes.

The holdout command accepts only a verified selection manifest produced from a confirmatory OOF run.

All build commands:

- run against the offline research root;
- require new output paths;
- refuse symlinks;
- write bounded manifests;
- preflight final outputs before writing children where practical;
- print semantic and physical identities; and
- verify their own output before returning success.

The holdout command should be operationally distinct from OOF work to make accidental reveal difficult.

---

## 20. Test plan

### 20.1 Feature tests

- exact 1, 5, 15, 30 and 60-minute endpoints;
- missing endpoint remains null;
- no interpolation;
- current-cutoff revision selection;
- correction after cutoff has no effect;
- correction before cutoff is included;
- ambiguous sources fail;
- late gaps are retrospective only;
- context aggregates exclude the target;
- input order does not change identity;
- holdout rows are absent from confirmatory OOF features;
- native and external provenance cannot be silently merged.

### 20.2 Preprocessing leakage tests

- changing outer validation features cannot change fitted scaling;
- changing outer validation targets cannot change alpha;
- changing holdout data cannot change any OOF artefact;
- changing an inner-validation target can change alpha but not inner preprocessing;
- changing a row after the training cutoff cannot change a fold fit;
- missingness values come from training only;
- volatility and spread bucket thresholds come from training only.

### 20.3 Model tests

- known local linear signal is recovered by local Ridge;
- common shared signal is recovered by pooled Ridge;
- cross-asset signal improves pooled context over pooled local;
- shuffled targets do not show stable gain;
- constant targets fail with an explicit disposition;
- insufficient rows produce no forecast;
- sample weighting gives equal instrument total weight;
- coefficient and prediction dimensions are exact;
- stored coefficients independently reproduce forecasts.

### 20.4 Evaluation tests

- zero baseline metrics are correct;
- own-support and common-support denominators differ as expected;
- dropping difficult rows lowers coverage but cannot silently improve pairwise comparison;
- Pearson and Spearman undefined cases are explicit;
- direction zero handling is exact;
- bucket thresholds do not use validation targets;
- aggregate metrics reconcile to child groups where mathematically applicable;
- concentration statistics identify a single-instrument result;
- metric recomputation detects report tampering;
- implementation evidence cannot be accepted as confirmatory evidence.

### 20.5 Holdout tests

- no confirmatory OOF child contains a holdout target ID;
- holdout build requires a verified confirmatory selection manifest;
- changed configuration is rejected;
- a second write to the same holdout output is rejected;
- holdout thresholds match frozen OOF definitions;
- post-freeze model substitution is rejected;
- holdout report records consumption;
- disposable fixture holdouts cannot be relabelled as confirmatory.

### 20.6 Readiness tests

- software readiness can pass while confirmatory readiness fails;
- representative integration can pass with insufficient common duration;
- qualifying subset selection is independent of model outcomes;
- coverage excludes causally inactive intervals;
- missing active-source intervals reduce qualification coverage;
- six targets across fewer than three groups fail;
- three groups with fewer than two targets in one group fail;
- insufficient holdout duration fails;
- non-primary horizon sparsity does not block the 15-minute first path;
- readiness output identifies each unmet condition.

### 20.7 Integration tests

Use at least three synthetic chronological datasets:

1. **No signal:** local and pooled models should not show stable improvement.
2. **Local signal:** per-asset Ridge should recover instrument-specific linear structure.
3. **Shared cross-asset signal:** pooled context should improve over pooled local features.

Also run one end-to-end build from a real verified representative R1 bundle. Short native history may prove mechanics and coverage but cannot by itself prove effectiveness.

When available, run one full confirmatory integration against the frozen qualifying R1 bundle.

---

## 21. Suggested source layout

```text
src/qtrad/domain/r2_features.py
src/qtrad/domain/r2_models.py
src/qtrad/domain/r2_evaluation.py
src/qtrad/domain/r2_bundle.py
src/qtrad/domain/r2_readiness.py

src/qtrad/application/r2_features.py
src/qtrad/application/r2_preprocessing.py
src/qtrad/application/r2_baselines.py
src/qtrad/application/r2_evaluation.py
src/qtrad/application/r2_readiness.py

src/qtrad/adapters/parquet/r2.py

src/qtrad/runtime/r2_bundle.py
```

The exact module boundaries may follow existing repository conventions. Domain contracts and pure transformations remain separate from persistence, CLI and numerical-library adapters.

---

## 22. Completion criteria

### 22.1 Software implementation complete

R2 software implementation is complete when:

1. Synthetic fixtures exercise no-signal, local-signal, shared-signal and cross-asset-signal cases.
2. Raw features are causally materialised and independently verified.
3. Preprocessing and alpha selection are demonstrably training-only.
4. Local Ridge emits verified chronological forecasts and explicit coverage.
5. Pooled local and pooled cross-asset controls emit equivalent evidence.
6. Evaluation reconciles globally and across required breakdowns.
7. Common-support comparisons prevent selective-row bias.
8. Fit, forecast and evaluation failures remain visible.
9. Coefficients and predictions can be independently replayed.
10. Selection-manifest mechanics prevent holdout influence.
11. Holdout mechanics have been proven using disposable fixtures.
12. At least one representative native bundle has completed the integration path.
13. The readiness command correctly distinguishes software and data readiness.
14. No unresolved chronology, leakage, identity or numerical-replay finding remains.

Software completion does not require:

- a qualifying native bundle;
- a model-selection conclusion;
- use of the eventual holdout;
- positive predictive performance; or
- purchased historical data.

### 22.2 Confirmatory research experiment ready

The confirmatory experiment is ready when:

1. The software implementation is complete.
2. The qualifying bundle criteria in Section 6.3 pass.
3. The exact native source cutoff is frozen.
4. The target subset and market groups are frozen.
5. Feature eligibility is frozen.
6. Fold and holdout ranges are frozen.
7. Model comparisons and thresholds are frozen.
8. The holdout has not been inspected.

### 22.3 R2 research milestone complete

R2 is research-complete when:

1. A qualifying frozen R1 bundle has driven the confirmatory workflow.
2. Local and pooled models have produced verified chronological OOF evidence.
3. Coverage, stability and concentration have been evaluated.
4. Every tested configuration and failure has been retained.
5. An immutable selection manifest was frozen before holdout access.
6. The locked holdout was evaluated without changing the selected procedure.
7. The complete research bundle independently replays features, fits, forecasts and reports.
8. The result is stated as positive, negative or inconclusive without exceeding the available evidence.
9. No unresolved chronology, leakage or identity finding remains.

R2 research completion does not require:

- positive forecast performance;
- profitability after costs;
- a successful graph model;
- portfolio construction;
- production deployment; or
- live shadow-paper operation.

The essential distinction is:

> R2 software can be completed while the collector and data-acquisition work continue. R2's confirmatory research conclusion cannot be completed until the qualifying dataset exists.

---

## 23. Deferred register

Defer until later evidence justifies them:

- nonlinear tree or kernel baselines;
- elastic net or automatic feature selection;
- Bayesian or probabilistic Ridge;
- conformal intervals;
- learned session experts;
- dynamic feature thresholds;
- pairwise cross-asset interactions;
- graph structure;
- deep temporal models;
- automated tuning services;
- model registries and promotion workflows;
- cost-adjusted model targets;
- portfolio-aware loss functions;
- online incremental fitting;
- GPU training; and
- shadow-live model serving.
