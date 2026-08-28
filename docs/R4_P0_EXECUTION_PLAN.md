# R4-P0 execution plan — bounded residual structural graph historical screen

- **Status:** DRAFT FOR OPERATOR REVIEW; no R4 execution authority yet
- **Milestone:** R4-P0 — historical residual-graph hypothesis screen
- **Experiment class:** `POST_HOC_HISTORICAL_EXPLORATORY`
- **Source class:** `IBKR_HISTORICAL_RESEARCH`
- **Primary horizon:** 15 minutes
- **Safety boundary:** no provider access, acquisition, deployment, native-source claim, broker order,
  production endpoint or real-capital path
- **Planning base:** `8b82297c0c873964c66183d84878f3680ef7cc65`

This document becomes active R4-P0 execution authority only after operator approval. Until then it is a
review draft and does not change `PLAN.md`, `docs/STATUS.md` or the current statement that R4 is not
started or authorised.

## 1. Authority and execution discretion

`PLAN.md` and `docs/TRADING_RESEARCH.md` require R4 to derive graph targets from out-of-fold local
residuals and compare local-only, pooled non-graph, fixed economic graph, learned structural GNN-LSTM
and shuffled-graph controls. They permit a graph to be retained only on incremental held-out evidence.

R4-P0 cannot satisfy that retention gate. Every historical outcome block available to this plan has
already been consumed and inspected in R2 or later post-hoc work. R4-P0 may only reject graph families
or nominate one or more exact configurations as historical hypotheses for a separately authorised test
on untouched prospective or native evidence. `HYPOTHESIS_NOMINATED` is not retention, promotion or
evidence that the graph adds held-out information.

`AGENTS.md` and `docs/EVIDENCE_GOVERNANCE.md` govern scientific class, causal correctness, source
separation, evidence mutation and validation proportionality. `.codex/map/MAP_Orchestrator.md`
governs generic multi-agent procedure when MAP is invoked; it does not create scientific authority.

This plan separates authority from operating guidance:

- Sections labelled **BINDING** are scientific, evidence, safety or completion requirements.
- Sections labelled **OBSERVED** record current evidence that may be revalidated.
- Sections labelled **ADVISORY** give a preferred implementation or custody shape. The orchestrator
  and delegated item owner may revise them from live evidence without amending the plan, provided the
  binding meaning and work class are preserved.
- A later implementation detail becomes `DECISION_REQUIRED` only if the admissible choices materially
  change the experiment, claim boundary, source, candidate register, selection boundary, retry policy
  or compute budget.

File names, function names, PR count and the advisory work-item decomposition do not become binding
merely because they appear below.

## 2. Plan-declared work class — BINDING

```text
experiment_class: POST_HOC_HISTORICAL_EXPLORATORY

evidence_state:
  implementation and model-run evidence during execution;
  the accepted attempt register and final report may be retained as historical exploratory evidence;
  no output is decision-grade, promotion authority or operational authority

permitted_actions:
  authenticate and consume the named historical/laboratory input;
  create bounded experiment code, configuration, predictions, metrics and attempt records;
  fit and evaluate the closed candidate register chronologically;
  inspect the already-consumed terminal historical block only through the terminal stage below;
  retain negative, failed, invalidated and inconclusive results;
  nominate exact historical hypotheses under section 10 without promoting them

prohibited_actions:
  provider calls, reacquisition, collector or deployment mutation;
  mutation, replacement, promotion or invalidation of retained R2 or R3 evidence;
  OPENED, CONSUMED, promotion or decision-grade holdout machinery;
  source-native, executable, post-cost, profitability, portfolio or production claims;
  broker-order, external-submission or real-capital capability;
  expansion into the U-lane, a new source, a new historical period or a wider product catalogue;
  post-result architecture, feature, universe, seed or hyperparameter search;
  describing a historical nomination as RETAINED, SELECTED_FOR_PRODUCTION or PROMOTED

new_authority_required_for:
  changing source class or the fixed twenty-instrument historical universe;
  adding a new evaluation period or provider dataset;
  using native quote-state features or executable-side evidence;
  treating an R4-P0 nomination as satisfying the R4 held-out retention gate;
  integrating a nominated graph into R5 or the frozen native protocol;
  adding dynamic adjacency, session experts, graph ensembles or a materially different temporal engine;
  changing the terminal boundary or candidate register after any scientific result exists;
  starting a replacement scientific release after terminal execution has begun, except an unchanged
  outcome-blind operational retry permitted by section 9

binding_controls:
  every residual label is generated from an out-of-fold local forecast;
  every feature and graph input is available by its decision time;
  candidate families and graph specifications are fixed before scientific execution;
  the terminal historical block cannot influence architecture, graph, feature, seed or training policy;
  all primary comparisons use exact common support and direct zero/local/pooled controls;
  failed models remain failed and never become zero forecasts;
  every started substantive fit is recorded before it begins;
  no scientific output is overwritten in place;
  parent decision-grade evidence does not promote this exploratory child
```

Retained input, create-only output, elapsed time, GPU use or archival intent does not reclassify R4-P0.
Exploratory diagnosis and repair follow the bounded release and retry policy in section 9; they do not
create a new holdout or erase prior attempts.

## 3. Purpose, comparison meaning and outcome vocabulary — BINDING

R4-P0 asks one primary question:

> Does a bounded static learned graph improve total 15-minute return forecasts beyond a pooled
> non-graph residual model that uses the same temporal backbone, on chronologically later historical
> IBKR data?

Secondary questions are:

1. Does a fixed economically specified graph improve on the pooled non-graph control?
2. Does the learned static graph improve on the fixed graph?
3. Does the fixed economic graph beat an isomorphic graph whose node-to-market assignment is frozen
   and permuted?
4. Does any graph result also beat the exact local Ridge, pooled local Ridge and zero-return
   total-forecast controls?
5. Is any improvement broad across periods and instruments rather than supplied by one node or block?

The temporal backbone, feature tensor and optimisation policy are shared, but the models are not
claimed to have identical capacity. Graph message passing, learned adjacency and node embeddings may
add parameters. Graph-versus-pooled comparisons measure the value of the complete bounded graph
mechanism. Fixed-graph versus shuffled-fixed-graph is the cleaner structural assignment control.
Trainable parameter counts are reported for every family.

A trustworthy negative, failed or inconclusive result completes R4-P0. The terminal outcomes support
historical falsification and nomination only; they are not a second holdout.

The only successful-result vocabulary is:

- `HYPOTHESIS_NOMINATED` — an exact fixed or learned graph configuration passes section 10's historical
  nomination rule;
- `NO_HYPOTHESIS_NOMINATED` — no graph passes every condition;
- `RUN_INVALIDATED_IMPLEMENTATION` — a concrete correctness defect invalidates a release's affected
  scientific outputs; and
- `RUN_FAILED` — the declared register cannot be completed within the authorised release/retry policy.

Neither `HYPOTHESIS_NOMINATED` nor completion of R4-P0 satisfies the active R4 held-out retention gate.

## 4. Current baseline — OBSERVED

Current evidence materially lowers the prior for a positive graph result but does not answer R4-P0:

- The terminal R2 historical result found local Ridge negative versus zero, pooled local Ridge positive
  versus local Ridge, no frozen pooled-versus-zero conclusion and the fixed pooled cross-asset Ridge
  rejected at OOF.
- The completed historical laboratory found no promoted horizon, cadence, universe, target-scaling,
  calibration, recency, histogram-boosting, MLP or LSTM configuration. Its weak all-twenty pooled
  result was temporally and cross-sectionally concentrated and reversed on the terminal development
  block.
- R3.H's tiny fixed and learned graph checks were negative, but they were deliberately lightweight and
  did not implement the R4 residual target, shared temporal control or full fixed/learned/shuffled
  comparison. They neither promote nor cancel R4-P0.
- R3 has already implemented cost, risk, sleeves, physical targets and independent accounting. Those
  production-path mechanics are not prerequisites for this forecast-only historical screen.

R4-P0 therefore receives a closed candidate register and bounded execution policy. It is not a broad
GNN research programme.

## 5. Input, universe and authentication — BINDING

### 5.1 Exact immediate exploratory parent

R4-P0 requires the authenticated LAB-0 manifest named by
`docs/R2_HISTORICAL_EXPLORATORY_FINDINGS.md`:

```text
/workspace/tmp/qtrad-r2-lab/LAB-0/lab-manifest.json
SHA-256 462e40fa84038156b16c68bde4b68d574ab7862c680657ebd1a0035b39bf0072
```

It binds the exact historical source, original R2 OOF parent, twenty candidate instruments, causal
features, 5/15/30/60-minute targets and the chronological `DEV_1`, `DEV_2`, `DEV_3` and
`TERMINAL_FORMER_HOLDOUT` blocks. Ordinary R4-P0 use authenticates this immediate parent and the exact
parts consumed. It does not recursively replay Stage 7, Stage 8 or R2.

There is no fallback rebuild under this plan. If the manifest or any required declared child is absent
or fails exact authentication, R4-P0 stops before model fitting. A later reconstruction requires
separate authority, a new explicit input identity and complete structural/semantic authentication of
rows, memberships, features, target maturity and block assignments. Reproducing aggregate support and
MSE is a useful regression gate, not proof of input identity.

### 5.2 Fixed universe

R4-P0 uses the exact twenty LAB-0 canonical instruments as nodes and forecast targets. The universe is
not selected or reduced using R4-P0 outcomes. Every one of the twenty instruments is part of the
breadth denominator in section 10.

The exact six R2 confirmatory targets are also reported as a fixed anchor slice:

```text
commodity:spot-gold
commodity:us-crude
fx:aud-usd
fx:eur-usd
index:australia-200
index:us-500
```

The all-twenty result is primary. The six-target slice is descriptive continuity with R2 and cannot
replace the primary result.

### 5.3 Fixed horizon and price basis

R4-P0 uses only the 15-minute MIDPOINT return target at the 60-second decision cadence. Five-, 30- and
60-minute horizons and alternate cadence phases are excluded. Historical BID and ASK extrema are not
executable sides and do not enter R4-P0.

### 5.4 Input and baseline gate

Before residual or graph work begins, the input capsule must establish:

- exact LAB-0 manifest and consumed-child authentication;
- canonical twenty-node order and exact block memberships;
- source opportunity counts of 771,140 rows across `DEV_1`–`DEV_3` and 655,424 rows in
  `TERMINAL_FORMER_HOLDOUT` for the all-twenty P0/15-minute source view;
- exact per-block and per-instrument source counts, frozen in the pre-run capsule; and
- the original six-target 15-minute development baseline:

```text
support:                  239,535
ZERO_RETURN MSE:          0.0000028404586671320294
POOLED_LOCAL_RIDGE MSE:   0.000002841663414474555
LOCAL_RIDGE MSE:          0.0000028481068080631273
ordering:                 ZERO < POOLED < LOCAL
```

A numerical tolerance may cover ordinary deterministic library representation differences only when
its cause is identified and it cannot change support, ordering or any candidate comparison. Exact input
authentication remains primary; aggregate reproduction cannot substitute for it. A material mismatch
stops R4-P0 before candidate fitting.

## 6. Residual foundation and chronology — BINDING

For target instrument `i` and decision time `t`, the graph label is:

```text
local_residual[i,t] = realised_15m_return[i,t] - local_oof_forecast[i,t]
```

Every residual consumed for fitting or evaluation must use a local forecast made without training on
that target. In-sample fitted residuals are prohibited.

### 6.1 Local residual construction

- Reuse the exact retained local OOF forecasts for the original six where the authenticated parent
  supplies them.
- For the remaining fourteen, generate local Ridge forecasts using the same causal P0 features,
  fold-local preprocessing, deterministic Ridge policy and chronological LAB-0 blocks.
- Training rows must have target availability and dependency intervals complete before the applicable
  fit boundary.
- Residual identity binds source, instrument, target, decision time, horizon, local-model configuration,
  fold and local forecast identity.
- Missing or failed local forecasts produce unavailable residual rows, not zero residuals.
- Exact local-forecast and residual support by block and instrument is fixed before any residual-model
  scientific fit.

### 6.2 Graph fit/evaluation schedule

Only already-OOF residual blocks become graph training labels:

```text
DEV_1 residuals                       -> fit for DEV_2 evaluation
DEV_1 + DEV_2 residuals               -> fit for DEV_3 evaluation
DEV_1 + DEV_2 + DEV_3 residuals       -> fit for TERMINAL_FORMER_HOLDOUT evaluation
```

`DEV_1` is graph warm-up and is not counted as a graph evaluation block. `DEV_2` and `DEV_3` are
chronological development evaluations. The former consumed R2 holdout is a terminal post-hoc
development block, not a new holdout or confirmation.

This schedule avoids a nested residual-generation framework: graph models never train on a local
residual from a target that helped fit that local forecast.

### 6.3 Terminal boundary

The release identity, candidate register, graph specifications, node/feature order, preprocessing,
temporal lookback, seeds, optimiser policy, epoch/stopping policy and support schedule are frozen
before terminal rows are loaded.

Development and terminal execution are separate commands or equivalent explicit stages. The terminal
stage authenticates the frozen release and complete development attempt register. It needs no
promotion, verifier receipt, `OPENED` marker or irreversible lifecycle. Every predeclared candidate
with a valid development disposition proceeds according to the frozen register; there is no
terminal-driven finalist selection.

Terminal rows, labels and metrics are unavailable to training, scheduling, checkpoint selection,
early stopping and any configuration decision.

## 7. Feature, tensor and eligibility contract — BINDING

All residual-model candidates use the same causal input tensor and target-row eligibility policy.

- One-minute node sequences end at the decision time's permitted feature cut-off.
- The temporal lookback is one fixed value frozen in the pre-run configuration; 60 minutes is the
  default operating choice.
- Inputs are limited to local return/range, time, availability, source-activity, quality and
  missingness fields already present in LAB-0. R4-P0 does not add native spread, quote imbalance, P1
  aggregate features, event data or external covariates.
- Continuous preprocessing is fitted only on graph-training rows. Missing continuous values are
  represented through the frozen imputation policy plus explicit masks; binary state indicators
  remain semantically distinct.
- Nodes absent at an exact sequence position are masked. Prices or features are not forward-filled to
  manufacture simultaneous evidence.
- Node, feature and target ordering is canonical and independent of map/dictionary iteration order.
- Every residual model predicts a correction in raw 15-minute log-return units. Total forecast is the
  exact local forecast plus that correction.
- An evaluation row is eligible only when its mature target, local OOF forecast and complete causal
  sequence satisfy the frozen policy. The pre-run capsule records exact eligible counts by
  `DEV_2`, `DEV_3`, terminal block and instrument before any scientific fit.
- Every fixed target must have positive eligible support in all three evaluation periods. Otherwise
  the pre-run gate fails rather than shrinking the twenty-instrument denominator.
- A candidate must forecast every frozen eligible row. Candidate-specific omissions are coverage
  failures, not a smaller comparison sample.

The item owner may choose one numerically stable training-only residual scaling policy before the
pre-run freeze, provided predictions are mapped back to raw return units and the same policy is
applied across all residual-model families.

## 8. Closed candidate register — BINDING

The register contains exactly these families:

| ID | Role |
| --- | --- |
| `ZERO_RETURN` | exact zero total-return control |
| `LOCAL_RIDGE` | exact local total-return baseline and zero-residual control |
| `POOLED_LOCAL_RIDGE` | exact pooled linear total-return baseline |
| `LOCAL_TEMPORAL_RESIDUAL` | shared temporal backbone using only the target node |
| `POOLED_NON_GRAPH_RESIDUAL` | shared temporal backbone plus permutation-invariant pooled node context; no edges or message passing |
| `FIXED_ECONOMIC_GRAPH_RESIDUAL` | shared temporal backbone and one frozen economic adjacency |
| `LEARNED_STATIC_GRAPH_RESIDUAL` | shared temporal backbone and one learned static structural adjacency |
| `SHUFFLED_FIXED_GRAPH_RESIDUAL` | fixed-graph architecture with the frozen economic topology assigned to permuted market identities |

A family that cannot fit or forecast remains `FAILED`. It is not replaced with a nearby architecture,
new seed, new graph or zero forecast.

### 8.1 Shared temporal backbone and bounded structural capacity

The five fitted residual families use:

- one shared single-layer LSTM temporal backbone;
- one bounded temporal width, no larger than 32;
- at most one message-passing layer, no larger than 32 hidden units;
- one target-node residual head;
- the same lookback, features, preprocessing, loss, optimiser family and batch policy;
- one primary deterministic seed and two fixed auxiliary seeds used only for seed-stability evidence;
- no best-seed selection and no hidden seed averaging; and
- recorded trainable parameter counts for every family/seed/stage.

The pooled non-graph control should use a reasonable bounded context projection, but exact parameter
matching is not required and must not be claimed. The final report distinguishes:

- graph versus pooled non-graph: complete bounded graph mechanism, including extra parameters;
- fixed graph versus shuffled fixed graph: same architecture and parameter count, different
  node-to-market structural assignment; and
- learned graph versus fixed graph: learned adjacency plus any associated parameter difference.

Exact widths, seeds, learning rate, weight decay, batch size, epoch policy and device are frozen after
shape/resource smoke testing and before scientific development outcomes are computed.

The default is a fixed epoch count selected only for numerical feasibility and bounded resource use.
If early stopping is used instead, its exact policy and chronological inner-validation split are
frozen before scientific execution and draw solely from the applicable graph-training blocks.
Evaluation-block labels and metrics are unavailable to the optimiser, scheduler, checkpoint selector
and stopping rule. The same stopping policy applies to every fitted residual family.

### 8.2 Fixed economic graph

The fixed graph is a versioned explicit directed edge list frozen before scientific fitting. It may
use canonical instrument semantics such as shared currency, asset class, region, index family or
commodity complex. It must not use observed returns, correlations, R2/R3 outcomes or R4-P0 labels.

Every edge carries a short economic rationale. Self-loops, directionality and weights are explicit.
The graph remains sparse enough that its structural hypothesis differs from generic all-to-all
pooling.

### 8.3 Learned structural graph

The learned graph is static across decisions and sessions. It may learn directed weights from graph
training rows, but adjacency cannot depend on the current evaluation example or later data. Use one
bounded parameterisation, such as low-rank node embeddings with row-normalised weights and one frozen
sparsity or entropy policy.

Dynamic adjacency, state-conditioned edges, session experts, multi-layer graph stacks, architecture
search and learned graph ensembles are excluded.

### 8.4 Shuffled fixed-graph control

The pre-run configuration contains the canonical node order and one explicit fixed-point-free
permutation `π`, chosen without outcome access and used for every model seed. Let `P` be its
permutation matrix and `A_fixed` the exact fixed economic adjacency:

```text
A_shuffled = P @ A_fixed @ P.T
```

This preserves exactly:

- node count and tensor shape;
- the self-loop/diagonal policy;
- directed edge count;
- edge-weight multiset;
- graph topology up to relabelling; and
- in-degree and out-degree multisets.

It deliberately changes which market receives which structural neighbourhood. The explicit
permutation, adjacency bytes and hashes are frozen before scientific fitting. Tests prove the formula
and invariants. No alternate permutation is selected or substituted after results.

This is a direct structural null for the fixed economic graph. It is a required reference, but not
misrepresented as an exact capacity-matched null for the learned-adjacency model.

## 9. Pre-run release, attempts, retries and compute — BINDING

### 9.1 One exact-head pre-run acceptance gate

After the causal foundation, tensor path and candidate implementations exist—but before any
scientific development fit—the orchestrator accepts one `R4P0_G0` pre-run record for an exact
committed candidate head. It records:

- code commit SHA and clean candidate state;
- exact LAB-0 parent and consumed-child identities;
- residual-foundation and tensor-capsule identities;
- frozen configuration and candidate-register hashes;
- exact node/feature order, support schedule, graphs, permutation, seeds and epoch/stopping policy;
- Python, lockfile/application, PyTorch, CUDA, GPU/device and relevant numerical-library identity;
- output root and create-only naming policy;
- exact-path smoke results for every family;
- elapsed-time, VRAM/RAM, row/partition and output-size projections with stated margins; and
- independent approval of the residual/chronology boundary.

The smoke uses a correctly shaped bounded sample and exercises the real training, serialisation,
attempt and result paths. It must not use scientific development or terminal performance to choose a
configuration. Scientific output destinations are absent or empty before acceptance.

`R4P0_G0` is an execution acceptance record, not a promotion or reusable evidence receipt. Any change
to scientific code, configuration, parent input, runtime policy or graph identity invalidates it and
requires a fresh gate for the new exact head.

### 9.2 Primary fit slots and attempt recording

The closed release contains 45 primary fit slots:

```text
5 fitted residual families × 3 model seeds × 3 chronological stages
```

Baseline reconstructions and smoke fits are logged separately and are not scientific candidates.

Immediately before model initialisation/training, append a unique attempt record with at least release
ID, slot ID, family, seed, stage, exact input/configuration/runtime identity, output destination and
`STARTED`. From that point the fit counts as started even if the process crashes. Completion appends a
terminal disposition; prior records are never rewritten. Prediction, metric and model outputs use
unique create-only paths and identities. A retry never overwrites or adopts the failed output path.

### 9.3 Unchanged operational retries

`R4P0_G0` freezes an aggregate unchanged-operational-retry budget between zero and five, with no
more than one retry for any primary slot. The budget is chosen from smoke/resource evidence rather
than after failures or model results. Total started substantive fits under one accepted release
therefore cannot exceed 45 plus that frozen budget.

A retry is permitted only when all of the following hold:

- failure was outcome-blind infrastructure or process interruption rather than model performance,
  deterministic numerical behaviour or scientific logic;
- no candidate metric from the failed attempt was used for a decision;
- code, configuration, parent, support, runtime policy, seed and scientific slot are unchanged;
- the failed attempt remains recorded with its partial-output disposition; and
- the retry uses a new create-only attempt and destination.

Changing device class, numerical policy, seed, epoch/stopping policy or model code is not an unchanged
operational retry.

### 9.4 Correctness repair and release replacement

Before terminal execution begins, a concrete implementation defect may be repaired without changing
the authorised scientific question or candidate register. The affected release is marked
`RUN_INVALIDATED_IMPLEMENTATION`; affected results remain retained. The orchestrator may authorise
one corrected release under this plan when:

- the defect and affected slots are identified independently of candidate performance;
- the candidate register and scientific meaning remain unchanged;
- a new exact committed head and `R4P0_G0` record are produced; and
- all slots from the earliest affected stage are rerun consistently, not only favourable or failed
  candidates.

A second corrected release, a performance-motivated change or any scientific configuration change
requires fresh operator authority.

Once terminal execution begins, any scientific code, configuration, graph, support or runtime-policy
change closes the release as invalidated or failed and requires fresh operator authority for a new
scientific release. An exact unchanged operational retry under section 9.3 remains permitted when its
conditions hold.

### 9.5 Other compute controls

- Use instrument-balanced residual MSE as the training loss unless the owner establishes before
  `R4P0_G0` that an equivalent weighting is required by tensor layout.
- Candidate selection, architecture search, automated hyperparameter optimisation and post-result
  grid expansion are prohibited.
- CUDA use is explicit; there is no silent fallback to a materially different CPU experiment.
- Long healthy execution uses MAP `LONGRUN` or an equivalent passive observation contract, not
  repeated model polling.
- A material increase in candidate count, architecture families, temporal engines, corrected releases
  or fit/retry budget requires new operator authority.

## 10. Evaluation and historical hypothesis nomination — BINDING

### 10.1 Fixed support and weighting

Every primary pairwise comparison uses the exact eligible support frozen in `R4P0_G0`. Every candidate
must cover all of it. Missing candidate forecasts are coverage failures; a model cannot improve its
reported metric by omitting difficult observations.

The breadth denominator is exactly twenty instruments. Positive support for every instrument in
`DEV_2`, `DEV_3` and terminal evaluation is a pre-run requirement.

For a period and instrument, MSE is the mean row loss on exact common support. Period-level primary MSE
is the equal-weighted mean of the twenty instrument MSEs. Combined-development MSE is calculated on
the union of `DEV_2` and `DEV_3` rows within each instrument, then equal-weighted across the twenty
instruments. It is not a post-result choice between equal-period and row weighting.

Report the all-twenty aggregate and fixed six-target anchor slice separately. The anchor slice is
descriptive and cannot replace the primary result.

### 10.2 Primary metric and questions

The primary metric is instrument-balanced MSE of the **total** 15-minute return forecast, lower is
better.

Primary comparison:

```text
LEARNED_STATIC_GRAPH_RESIDUAL versus POOLED_NON_GRAPH_RESIDUAL
```

Required secondary comparisons:

```text
FIXED_ECONOMIC_GRAPH_RESIDUAL versus POOLED_NON_GRAPH_RESIDUAL
LEARNED_STATIC_GRAPH_RESIDUAL versus FIXED_ECONOMIC_GRAPH_RESIDUAL
FIXED_ECONOMIC_GRAPH_RESIDUAL versus SHUFFLED_FIXED_GRAPH_RESIDUAL
LEARNED_STATIC_GRAPH_RESIDUAL versus SHUFFLED_FIXED_GRAPH_RESIDUAL
each candidate versus LOCAL_RIDGE, POOLED_LOCAL_RIDGE and ZERO_RETURN
```

The learned-versus-shuffled-fixed result is reported as a reference, not as a capacity-matched learned
adjacency null.

### 10.3 Fixed contribution and concentration definitions

For candidate `c`, comparator `b`, instrument `i` and period
`p ∈ {DEV_2, DEV_3, TERMINAL_FORMER_HOLDOUT}`:

```text
delta[i,p] = MSE(b, i, p) - MSE(c, i, p)
```

Positive `delta` means the candidate improves on the comparator. Aggregate period delta is the
equal-weighted mean of the twenty instrument deltas.

For concentration against `POOLED_NON_GRAPH_RESIDUAL`:

```text
instrument_contribution[i] =
    max(0, mean(delta[i,DEV_2], delta[i,DEV_3], delta[i,TERMINAL_FORMER_HOLDOUT]))

period_contribution[p] = max(0, mean_i(delta[i,p]))

best_instrument_share =
    max(instrument_contribution) / sum(instrument_contribution)

best_period_share =
    max(period_contribution) / sum(period_contribution)
```

Clipping is used only for concentration shares; signed deltas remain unchanged for skill and
comparison metrics. If the applicable positive-contribution sum is zero, the share is defined as
`1.0` and the nomination gate fails.

An instrument counts as improved on combined development only when its union-`DEV_2`/`DEV_3` MSE delta
is positive. It counts as improved on terminal only when terminal delta is positive.

### 10.4 Required diagnostics

For every candidate and comparison report:

- frozen eligible support and forecast coverage by period and instrument;
- total-forecast MSE and direct skill versus zero;
- incremental MSE versus local Ridge and pooled non-graph;
- Pearson and Spearman association;
- calibration slope/intercept;
- direction accuracy and forecast-bucket ordering where defined;
- results by `DEV_2`, `DEV_3` and terminal block;
- results by instrument and market group;
- the fixed concentration definitions above;
- primary and auxiliary seed results;
- trainable parameter counts and recorded fit/resource costs;
- residual-correction magnitude and total-forecast magnitude; and
- a clearly labelled comparison with the R3.H historical break-even/cost grid, without claiming
  executable cost or profitability.

All nomination conditions other than seed agreement use the frozen primary seed. Auxiliary seeds
supply only the stated sign-stability check and are never used to choose or average a result.

A descriptive paired UTC-day block-bootstrap interval is permitted when inexpensive and frozen in
`R4P0_G0`. It is not a confirmatory p-value or substitute for breadth.

### 10.5 Historical hypothesis-nomination rule

A fixed or learned structural graph receives `HYPOTHESIS_NOMINATED` only if all of the following hold
on exact common support for the primary seed:

1. it improves on `POOLED_NON_GRAPH_RESIDUAL` separately in `DEV_2` and `DEV_3`;
2. it improves on `POOLED_NON_GRAPH_RESIDUAL` on the terminal block;
3. its total forecast has positive direct skill versus `ZERO_RETURN` on combined development and on
   the terminal block;
4. at least 7 of the fixed 20 instruments improve on the pooled non-graph control in both combined
   development and terminal evaluation;
5. best-instrument and best-period positive-contribution shares are each at most 0.8;
6. at least two of the three fixed seeds agree in sign with the primary-seed graph-versus-pooled
   result on combined development and terminal;
7. the fixed graph beats `SHUFFLED_FIXED_GRAPH_RESIDUAL`; the learned graph beats both
   `FIXED_ECONOMIC_GRAPH_RESIDUAL` and `SHUFFLED_FIXED_GRAPH_RESIDUAL`;
8. calibration slope is positive on combined development and terminal and does not reverse sign; and
9. forecast coverage is exactly the frozen eligible support in every required period.

Each graph is assessed independently. If both fixed and learned graphs pass, both exact configurations
are nominated without ranking or choosing between them. If neither passes, the result is
`NO_HYPOTHESIS_NOMINATED`. No nearby rescue configuration is searched.

A nomination records exact release, configuration, graph, seed, parent, support and result identities.
It does not promote code, satisfy the R4 held-out retention gate, authorise R5 integration or support a
native/executable claim. Genuine graph retention requires separately authorised untouched prospective
or native evidence.

## 11. Outputs and evidence — BINDING

R4-P0 retains:

- the exact `R4P0_G0` pre-run acceptance record;
- one frozen configuration with source, parent, universe, chronology, feature/tensor, graph, candidate,
  seed, stopping, retry and compute identities;
- one compact append-only release/attempt register covering primary slots, retries, failures and
  invalidations;
- exact development and terminal prediction/metric outputs outside Git or in another declared bounded
  result location;
- fixed, shuffled and learned graph/adjacency outputs sufficient to inspect exact structure;
- a concise final report covering all candidates, negative/failed/invalidated outcomes, release and
  attempt counts, support, breadth, concentration, capacity differences and the historical nomination
  rule; and
- code and focused tests sufficient to reproduce the experiment from the authenticated parent.

Preferred tracked locations are:

```text
experiments/r4_residual_graph/
tests/experiments/r4_residual_graph/
docs/R4_P0_HISTORICAL_FINDINGS.md
```

Preferred large-output location is:

```text
/workspace/tmp/qtrad-r4/<release-id>/
```

These paths are advisory. The required content and evidence class are binding.

Attempt and result IDs are simple boundary-local execution identities, not a generic verification
service. R4-P0 creates no promotion, decision-grade receipt, database migration, production API or
permanent neural infrastructure merely because outputs are retained. A final report may authenticate
its immediate parent/configuration/result bytes with ordinary hashes; that does not make it
decision-grade.

## 12. Validation and review — BINDING

Validation is proportional to the exploratory class:

1. Focused deterministic tests cover causal cut-offs, target maturity, OOF residual provenance,
   canonical node order, exact masks, no forward fill, support freezing, graph permutation invariants,
   stopping-data isolation, attempt/retry transitions, create-only outputs and terminal separation.
2. A correctly shaped exact-path smoke exercises all fitted families, graph serialisation, attempt
   recording and result loading without using scientific performance to change configuration.
3. The residual/chronology boundary receives independent review before `R4P0_G0`.
4. `R4P0_G0` is accepted once for the exact scientific release head before development execution.
5. The complete development register finishes before terminal execution.
6. Ruff, formatting and strict typing cover changed code.
7. Experimental implementation PRs use focused tests and static checks. Do not run the complete
   repository gate on every helper or work item merely because the experiment is retained.
8. Run `ops/dev/verify.sh` once on the final exact R4-P0 candidate head because completion is a
   milestone boundary. GitHub static workflow remains static evidence only.
9. Independently review the final exact attempt register, metrics, nomination calculation and report.

Ordinary helper functions remain within their owning candidate and do not require separate review
handoffs, receipts or gates. A reviewer must cite a binding obligation or concrete current-experiment
failure path. Preference, speculative production hardening, hypothetical compatibility, unrequested
abstraction or future R5/R6 needs are not blockers.

## 13. Advisory implementation and MAP shape — ADVISORY

The orchestrator may execute DIRECT, DELEGATED or PROGRAMME. A useful logical decomposition is:

### R4.A — exact input capsule and residual foundation

- authenticate the exact immediate exploratory parent;
- reproduce the source-count and six-target baseline gates;
- construct all-twenty local OOF forecasts and residual rows;
- establish exact chronological memberships and support.

Immediate consumer: every residual-model family. This is the highest-value independent scientific
review surface.

### R4.B — common tensor path and frozen graphs

- materialise causal masked node sequences;
- freeze canonical node/feature order and exact eligible support;
- create the fixed graph and explicit shuffled adjacency;
- establish resource and output-shape projections.

R4.B can proceed alongside the later portion of R4.A once the input schema is stable.

### R4.C — shared-backbone controls and graph candidates

- implement local temporal, pooled non-graph, fixed, learned and shuffled-fixed residual models through
  one shared training/evaluation interface;
- keep capacity differences visible through parameter counts;
- exercise the exact-path smoke without running scientific development fits;
- freeze the candidate configuration and release identity.

### R4P0_G0 — one pre-run acceptance gate

The orchestrator confirms the exact candidate head, causal-boundary review, frozen input/configuration,
successful smoke, resource/output projections, empty create-only destinations and runtime identities.

### R4.D — development execution

- execute the complete `DEV_2` and `DEV_3` fit/evaluation schedule;
- retain every primary slot, retry and failure;
- close or replace a defective release only under section 9.

### R4.E — terminal execution and synthesis

- authenticate the unchanged release and complete development register;
- execute every eligible predeclared terminal slot once, subject only to unchanged operational retry;
- produce exact-common-support metrics, breadth, concentration, seed stability and cost-adjacent
  diagnostics;
- apply the historical nomination rule mechanically;
- publish the concise exploratory report and complete release/attempt count;
- update active status documents and archive this plan only after operator acceptance of the result.

These are ownership and immediate-consumer seams, not mandatory PRs. The orchestrator may combine or
split them, adjust internal sequencing and choose specialist/reviewer use while preserving one owner
per concurrent mutation surface and the binding experiment shape.

## 14. Explicit non-goals — BINDING

R4-P0 does not:

- complete the active R4 held-out retention gate;
- change or answer the U-lane universe-selection questions;
- add rates, agriculture, sectors, spreads, single stocks, options or another provider;
- test more horizons, target definitions or decision cadences;
- add dynamic or event-conditioned graphs;
- add session experts, ensembles, graph-depth searches or competing temporal engines;
- use native bid/ask, spread, imbalance or executable evidence;
- run the R3 portfolio optimiser or produce a post-cost portfolio result;
- amend the frozen `R3_IG_NATIVE_PORTFOLIO_V1` protocol;
- promote a model to R5, R6 or continuous shadow paper;
- create public interfaces, compatibility bridges or production infrastructure; or
- manufacture a learned-adjacency shuffled null that the declared register does not actually test.

A promising R4-P0 result is a historical hypothesis for future data, not a reason to widen this run.

## 15. Completion conditions — BINDING

R4-P0 is complete when:

1. the exact source/input authenticates and the source-count/baseline gates pass;
2. the all-twenty OOF residual foundation is causal and independently reviewed;
3. every fixed target has frozen positive support in `DEV_2`, `DEV_3` and terminal evaluation;
4. the candidate register, release identity and `R4P0_G0` record are frozen before scientific fits;
5. every primary slot has a successful, failed or invalidated disposition and every retry is within
   section 9;
6. development and terminal stages respect the declared chronology, stopping isolation and terminal
   boundary;
7. all required exact-common-support comparisons, capacity disclosures and diagnostics are reported;
8. the historical hypothesis-nomination rule is applied without post-result expansion or ranking;
9. the final report is clearly `POST_HOC_HISTORICAL_EXPLORATORY` and states all nonclaims;
10. focused checks, final exact-head complete verification and required independent reviews pass;
11. no retained R2/R3 evidence, provider, collector, native protocol or broker boundary is mutated;
12. `PLAN.md` and `docs/STATUS.md` record the actual accepted R4-P0 result; and
13. each exact qualifying graph is recorded as `HYPOTHESIS_NOMINATED`, or the run closes
    `NO_HYPOTHESIS_NOMINATED`.

Positive forecast skill is not required for completion. R4 graph retention remains unsatisfied until
a separately authorised untouched prospective or native experiment provides the required held-out
evidence.
