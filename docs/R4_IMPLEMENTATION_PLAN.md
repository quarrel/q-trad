# R4-P0 implementation plan — bounded residual structural graph experiment

- **Status:** DRAFT FOR OPERATOR REVIEW; no R4 execution authority yet
- **Milestone:** R4 — residual structural graph experiment
- **Scope:** bounded offline historical exploration only
- **Source class:** `IBKR_HISTORICAL_RESEARCH`
- **Primary horizon:** 15 minutes
- **Safety boundary:** no provider access, acquisition, deployment, native-source claim, broker order,
  production endpoint or real-capital path
- **Planning base:** `8b82297c0c873964c66183d84878f3680ef7cc65`

This document becomes the active R4 implementation authority only after operator approval. Until then it
is a review draft and does not change `PLAN.md`, `docs/STATUS.md` or the current statement that R4 is
not started or authorised.

## 1. Authority and execution discretion

`PLAN.md` and `docs/TRADING_RESEARCH.md` require R4 to derive graph targets from out-of-fold local
residuals and compare local-only, pooled non-graph, fixed economic graph, learned structural GNN-LSTM
and shuffled-graph controls. A graph is retained only on incremental held-out evidence; dynamic
adjacency and session experts remain deferred.

`AGENTS.md` and `docs/EVIDENCE_GOVERNANCE.md` govern scientific class, causal correctness, source
separation, evidence mutation and validation proportionality. `.codex/map/MAP_Orchestrator.md`
governs generic multi-agent procedure when MAP is invoked; it does not create scientific authority.

This plan deliberately separates authority from operating guidance:

- Sections labelled **BINDING** are scientific, evidence, safety or completion requirements.
- Sections labelled **OBSERVED** record current evidence that may be revalidated.
- Sections labelled **ADVISORY** give a preferred implementation or custody shape. The orchestrator
  and delegated item owner may revise them from live evidence without amending the plan, provided the
  binding meaning and work class are preserved.
- There are no unresolved operator-owned scientific choices in this draft. A later implementation
  detail becomes `DECISION_REQUIRED` only if the admissible choices materially change the experiment,
  claim boundary, source, candidate register, selection boundary or compute budget.

File names, function names, PR count and the advisory work-item decomposition do not become binding
merely because they appear below.

## 2. Plan-declared work class — BINDING

```text
experiment_class: POST_HOC_HISTORICAL_EXPLORATORY

evidence_state:
  development evidence during implementation and model execution;
  the accepted final attempt register and report may be retained as historical exploratory evidence;
  no output is decision-grade or operational authority

permitted_actions:
  authenticate and consume the named historical/laboratory inputs;
  create bounded experiment code, configuration, predictions, metrics and attempt records;
  fit and evaluate the closed candidate register chronologically;
  inspect the already-consumed terminal historical block only through the terminal stage below;
  retain negative, failed and inconclusive results

prohibited_actions:
  provider calls, reacquisition, collector or deployment mutation;
  mutation, replacement, promotion or invalidation of retained R2 or R3 evidence;
  OPENED, CONSUMED, promotion or decision-grade holdout machinery;
  source-native, executable, post-cost, profitability, portfolio or production claims;
  broker-order, external-submission or real-capital capability;
  expansion into the U-lane, a new source, a new historical period or a wider product catalogue;
  post-result architecture, feature, universe or hyperparameter search

new_authority_required_for:
  changing source class or the fixed twenty-instrument historical universe;
  adding a new evaluation period or provider dataset;
  using native quote-state features or executable-side evidence;
  integrating a retained graph into R5 or the frozen native protocol;
  adding dynamic adjacency, session experts, graph ensembles or a materially different temporal engine;
  changing the terminal selection boundary after any R4 model result exists

binding_controls:
  every residual label is generated from an out-of-fold local forecast;
  every feature and graph input is available by its decision time;
  graph candidates are fixed before development execution;
  the terminal historical block cannot influence architecture, graph, feature, seed or training policy;
  all primary comparisons use exact common support and direct zero/local/pooled controls;
  failed models remain failed and never become zero forecasts;
  parent decision-grade evidence does not promote this exploratory child
```

Retained input, create-only output, elapsed time, GPU use or archival intent does not reclassify R4.
Ordinary exploratory diagnosis and rerun remain within the same lineage unless a stop condition above
is reached.

## 3. Purpose and claim boundary — BINDING

R4 asks one primary question:

> Does a bounded static structural graph predict the out-of-fold local-model residual sufficiently
> well to improve total 15-minute return forecasts beyond a same-capacity pooled non-graph temporal
> control on chronologically later historical IBKR data?

Secondary questions are:

1. Does a fixed economically specified graph improve on the pooled non-graph control?
2. Does a learned static graph improve on the fixed graph?
3. Do either structural graph beat a graph with deterministically shuffled structure?
4. Does any incremental graph result also beat the exact local Ridge, pooled local Ridge and
   zero-return total-forecast controls?
5. Is any improvement broad across periods and instruments rather than supplied by one node or block?

A trustworthy negative or inconclusive result completes R4. R4 does not ask whether the graph is
profitable, executable, native-valid, suitable for continuous paper operation or ready for R5.

## 4. Current baseline — OBSERVED

The current evidence materially lowers the prior for a positive graph result but does not answer R4:

- The terminal R2 historical result found local Ridge negative versus zero, pooled local Ridge positive
  versus local Ridge, no frozen pooled-versus-zero conclusion and the fixed pooled cross-asset Ridge
  rejected at OOF.
- The completed historical laboratory found no promoted horizon, cadence, universe, target-scaling,
  calibration, recency, histogram-boosting, MLP or LSTM configuration. Its weak all-20 pooled result was
  temporally and cross-sectionally concentrated and reversed on the terminal development block.
- R3.H's tiny fixed and learned graph checks were negative, but they were deliberately lightweight and
  did not implement the R4 residual target, shared temporal control or full fixed/learned/shuffled
  comparison. They neither promote nor cancel R4.
- R3 has already implemented cost, risk, sleeves, physical targets and independent accounting. Those
  production-path mechanics are not a prerequisite for this forecast-only exploratory experiment.

R4 therefore receives a bounded compute budget and a closed candidate register. It is not a broad GNN
research programme.

## 5. Input and universe — BINDING

### 5.1 Immediate exploratory parent

Prefer the authenticated LAB-0 manifest already named by
`docs/R2_HISTORICAL_EXPLORATORY_FINDINGS.md`:

```text
/workspace/tmp/qtrad-r2-lab/LAB-0/lab-manifest.json
SHA-256 462e40fa84038156b16c68bde4b68d574ab7862c680657ebd1a0035b39bf0072
```

It binds the exact historical source, original R2 OOF parent, twenty candidate instruments, causal
features, 5/15/30/60-minute targets and the chronological `DEV_1`, `DEV_2`, `DEV_3` and
`TERMINAL_FORMER_HOLDOUT` blocks. Ordinary R4 use authenticates this immediate parent and only the
parts consumed. It does not recursively replay Stage 7, Stage 8 or R2.

If those local bytes are unavailable, the item owner may create one byte- or semantics-equivalent
exploratory rebuild from the exact retained parents through the merged LAB-0 code. The replacement
must reproduce the baseline gate in section 5.4 before any R4 model fit. This is a rebuild of
exploratory input, not provider reacquisition or evidence promotion.

### 5.2 Fixed universe

R4 uses the exact twenty LAB-0 canonical instruments as nodes and forecast targets. The universe is not
selected or reduced using R4 outcomes. Instruments with unavailable rows remain represented through
explicit masks and eligibility dispositions; they are not silently removed to improve metrics.

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

R4 uses only the 15-minute MIDPOINT return target. Five-, 30- and 60-minute horizons are excluded to
isolate structural graph value and avoid another horizon search. Historical BID and ASK extrema are
not executable sides and do not enter R4.

### 5.4 Baseline authentication gate

Before graph work begins, the input path must reproduce the original six-target 15-minute development
baseline:

```text
support:                  239,535
ZERO_RETURN MSE:          0.0000028404586671320294
POOLED_LOCAL_RIDGE MSE:   0.000002841663414474555
LOCAL_RIDGE MSE:          0.0000028481068080631273
ordering:                 ZERO < POOLED < LOCAL
```

A numerical tolerance may cover ordinary deterministic library representation differences only when
its cause is identified and it cannot change support, ordering or any candidate comparison. A material
mismatch stops R4 before candidate fitting.

## 6. Residual foundation and chronology — BINDING

For target instrument `i` and decision time `t`, the graph label is:

```text
local_residual[i,t] = realised_15m_return[i,t] - local_oof_forecast[i,t]
```

Every residual consumed for fitting or evaluation must use a local forecast made without training on
that target. In-sample fitted residuals are prohibited.

### 6.1 Local residual construction

- Reuse the exact retained/reconstructed local OOF forecasts for the original six where available.
- For the remaining fourteen, generate local Ridge forecasts using the same causal features,
  fold-local preprocessing, deterministic Ridge policy and chronological LAB-0 blocks.
- Training rows must have target availability and dependency intervals complete before the applicable
  fit boundary.
- Residual identity binds source, instrument, target, decision time, horizon, local-model configuration,
  fold and local forecast identity.
- Missing or failed local forecasts produce unavailable residual rows, not zero residuals.

### 6.2 Graph fit/evaluation schedule

Only already-OOF residual blocks become graph training labels:

```text
DEV_1 residuals                       -> fit for DEV_2 evaluation
DEV_1 + DEV_2 residuals               -> fit for DEV_3 evaluation
DEV_1 + DEV_2 + DEV_3 residuals       -> fit for TERMINAL_FORMER_HOLDOUT evaluation
```

`DEV_1` is graph warm-up and is not counted as a graph evaluation block. `DEV_2` and `DEV_3` are the
chronological development evaluations. The former consumed R2 holdout is a terminal post-hoc
development block, not a new holdout or confirmation.

This schedule avoids a nested residual-generation framework: graph models never train on a local
residual from a target that helped fit that local forecast.

### 6.3 Terminal boundary

The closed model configuration, graph specifications, node/feature order, preprocessing, temporal
lookback, seeds, optimiser policy, stopping policy and candidate register are frozen before terminal
rows are loaded.

Development and terminal execution must be separate commands or equivalent explicit stages. The
terminal stage authenticates the frozen configuration and complete development attempt register. It
needs no promotion, verifier receipt, `OPENED` marker or irreversible lifecycle. All successfully
completed predeclared candidates are evaluated once; there is no terminal-driven finalist selection.

## 7. Feature and tensor contract — BINDING

All candidates use the same causal input tensor and target rows.

- One-minute node sequences end at the decision time's permitted feature cut-off.
- The temporal lookback is one fixed value chosen in the pre-run configuration; 60 minutes is the
  default operating choice.
- Inputs are limited to local return/range, time, availability, source-activity, quality and missingness
  fields already present in LAB-0. R4 does not add native spread, quote imbalance, P1 aggregate features,
  event data or external covariates.
- Continuous preprocessing is fitted only on the graph training rows. Missing continuous values are
  represented through the frozen imputation policy plus explicit masks; binary state indicators remain
  semantically distinct.
- Nodes absent at an exact sequence position are masked. Prices or features are not forward-filled to
  manufacture simultaneous evidence.
- Node, feature and target ordering are canonical and independent of map/dictionary iteration order.
- Every model predicts a residual correction in raw 15-minute log-return units. Total forecast is the
  exact local forecast plus that correction.

The item owner may choose a numerically stable training-only residual scaling policy before execution,
provided predictions are mapped back to raw return units and the same policy is applied across all
residual-model controls.

## 8. Closed candidate register — BINDING

The register contains exactly these families:

| ID | Role |
| --- | --- |
| `ZERO_RETURN` | exact zero total-return control |
| `LOCAL_RIDGE` | exact local total-return baseline and zero-residual control |
| `POOLED_LOCAL_RIDGE` | exact pooled linear total-return baseline |
| `LOCAL_TEMPORAL_RESIDUAL` | shared temporal encoder using only the target node; no cross-node context |
| `POOLED_NON_GRAPH_RESIDUAL` | same temporal encoder plus permutation-invariant pooled node context; no edges or message passing |
| `FIXED_ECONOMIC_GRAPH_RESIDUAL` | same encoder/head with one frozen economic graph |
| `LEARNED_STATIC_GRAPH_RESIDUAL` | same encoder/head with one learned static structural adjacency |
| `SHUFFLED_GRAPH_RESIDUAL` | same graph architecture with deterministically permuted fixed structure |

A family that cannot fit or forecast remains `FAILED`. It is not replaced with a nearby architecture,
new seed, new graph or zero forecast.

### 8.1 Shared temporal and prediction capacity

The five residual candidates use one shared-capacity design so the comparison isolates cross-node
structure:

- one shared single-layer LSTM temporal encoder;
- one bounded hidden width, no larger than 32;
- at most one message-passing layer, no larger than 32 hidden units;
- one target-node residual head;
- the same lookback, features, preprocessing, loss, optimiser family, epoch ceiling and early-stopping
  policy;
- one primary deterministic seed and two fixed auxiliary seeds used only to report seed stability;
- no best-seed selection or seed averaging hidden from the report.

Exact widths, seeds, learning rate, weight decay, batch size, epoch ceiling and device are frozen in the
pre-run configuration after shape/performance smoke testing and before development outcomes are
computed. Smoke-driven changes may correct infeasibility or numerical breakage; they may not select on
forecast performance.

### 8.2 Fixed economic graph

The fixed graph is a versioned explicit edge list frozen before model fitting. It may use canonical
instrument semantics such as shared currency, asset class, region, index family or commodity complex.
It must not use observed returns, correlations, R2/R3 outcomes or R4 labels.

Every edge carries a short economic rationale. Self-loops and directionality are explicit. The graph
must remain sparse enough that its structural hypothesis differs from generic all-to-all pooling.

### 8.3 Learned structural graph

The learned graph is static across decisions and sessions. It may learn directed weights from the graph
training rows, but adjacency cannot depend on the current validation example or later data. Use one
bounded parameterisation, such as low-rank node embeddings with row-normalised weights and one frozen
sparsity/entropy policy.

Dynamic adjacency, state-conditioned edges, session experts, multi-layer graph stacks, architecture
search and learned graph ensembles are excluded.

### 8.4 Shuffled control

The shuffled graph is generated from the fixed graph by deterministic node/edge permutation while
preserving self-loop policy, node count and, where practical, in/out degree or at least the edge-count
profile. Each fixed seed has one corresponding permutation. No shuffle is selected based on its result.

## 9. Training and compute budget — BINDING

- Use instrument-balanced residual MSE as the training loss unless the owner demonstrates before any
  result that an equivalent weighting is required by tensor layout.
- Candidate selection, architecture search, automated hyperparameter optimisation and post-result grid
  expansion are prohibited.
- The bounded register permits at most five residual-model families × three seeds × three chronological
  fit/evaluation stages: 45 substantive model fits. Baseline reconstructions and smoke fits are reported
  separately and do not create additional scientific candidates.
- A stopped or failed fit records exact family, seed, stage, failure and whether any terminal rows were
  loaded.
- The pre-run configuration states elapsed-time, memory/VRAM and output-size limits. CUDA use must be
  explicit; there is no silent fallback to a materially different CPU experiment.
- Long healthy execution uses MAP `LONGRUN` or an equivalent passive observation contract, not repeated
  model polling.

A material increase in candidate count, architecture families, temporal engines or fit budget requires
new operator authority.

## 10. Evaluation and retention — BINDING

### 10.1 Support

Every primary pairwise comparison uses exact common target support. All candidates are expected to
cover the same eligible rows. Missing candidate forecasts remain coverage failures; a model cannot
appear superior by omitting difficult observations.

Report the all-twenty aggregate and fixed six-target anchor slice separately. Do not choose between
weightings after results.

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
each structural graph versus SHUFFLED_GRAPH_RESIDUAL
each candidate versus LOCAL_RIDGE, POOLED_LOCAL_RIDGE and ZERO_RETURN
```

### 10.3 Required diagnostics

For every candidate and comparison report:

- common support and forecast coverage;
- total-forecast MSE and direct skill versus zero;
- incremental MSE versus local Ridge and pooled non-graph;
- Pearson and Spearman association;
- calibration slope/intercept;
- direction accuracy and forecast-bucket ordering where defined;
- result by `DEV_2`, `DEV_3` and terminal block;
- result by instrument and market group;
- best-instrument and best-period positive-contribution share;
- primary and auxiliary seed results;
- residual-correction magnitude and total-forecast magnitude; and
- a clearly labelled comparison with the R3.H historical break-even/cost grid, without claiming
  executable cost or profitability.

A descriptive paired UTC-day block bootstrap interval is permitted when inexpensive. It is not a
confirmatory p-value or substitute for breadth.

### 10.4 Retention rule

A structural graph may be nominated as a **future exploratory hypothesis** only if all of the following
hold on exact common support:

1. it improves on `POOLED_NON_GRAPH_RESIDUAL` in both `DEV_2` and `DEV_3`;
2. it improves on `POOLED_NON_GRAPH_RESIDUAL` on the terminal block;
3. its total forecast has positive direct skill versus `ZERO_RETURN` on combined development and on
   the terminal block;
4. at least one third of eligible target instruments improve on the pooled non-graph control in both
   combined development and terminal evaluation;
5. best-instrument and best-period positive-contribution shares are each at most 0.8;
6. the primary seed is positive and at least two of three fixed seeds agree in sign for the primary
   graph-versus-pooled comparison;
7. the structural graph beats its shuffled control; and
8. calibration remains positive and does not reverse sign between combined development and terminal.

Meeting this rule does not promote code or create decision-grade evidence. It only nominates the exact
configuration for a later separately authorised prospective or native experiment.

If no graph meets every condition, R4 completes with `NOT_RETAINED` for this source, period, universe,
horizon and representation. Do not search for a nearby rescue configuration.

## 11. Outputs and evidence — BINDING

R4 must retain:

- one frozen pre-run configuration with source, input, universe, chronology, feature/tensor, graph,
  candidate, seed, training and compute identities;
- one compact append-only attempt register covering every candidate/seed/stage, including failures;
- exact development and terminal prediction/metric outputs outside Git or in another declared bounded
  result location;
- fixed and learned graph/adjacency outputs sufficient to inspect what structure was used;
- a concise final report covering all required candidates, negative/failed outcomes, experiment count,
  support, breadth, concentration and the retention rule; and
- code and focused tests sufficient to reproduce the experiment from the authenticated parent.

Preferred tracked locations are:

```text
experiments/r4_residual_graph/
tests/experiments/r4_residual_graph/
docs/R4_HISTORICAL_EXPLORATORY_FINDINGS.md
```

Preferred large-output location is:

```text
/workspace/tmp/qtrad-r4/<run-id>/
```

These paths are advisory. The required content and evidence class are binding.

R4 creates no verification receipt, promotion, generic evidence framework, database migration,
production API or permanent neural dependency merely because outputs are retained. A final report
may authenticate its immediate input/configuration/result bytes with ordinary hashes; that does not
make it decision-grade.

## 12. Validation and review — BINDING

Validation is proportional to the exploratory class:

1. Focused deterministic tests cover causal cut-offs, target maturity, OOF residual provenance,
   canonical node order, exact masks, no forward fill, common support, graph freezing/permutation,
   failure recording and terminal-stage separation.
2. A small exact-path smoke run exercises all candidate families, graph serialisation, attempt
   recording and both development/terminal commands without using performance to change the
   configuration.
3. The full development run completes before terminal execution.
4. Ruff, formatting and strict typing cover changed code.
5. Experimental PRs use focused tests and static checks. Do not run the complete repository gate on
   every helper or work item merely because the experiment is retained.
6. Run `ops/dev/verify.sh` once on the final exact R4 candidate head because R4 completion is a
   milestone boundary. GitHub static workflow remains static evidence only.
7. Independently review the exact residual/chronology boundary and the final exact result/report.
   Ordinary helper code may be reviewed with its owning candidate; do not create a review handoff for
   every function.

A reviewer must cite a binding obligation or concrete current-experiment failure path. Preference,
speculative production hardening, hypothetical compatibility, unrequested abstraction or future R5/R6
needs are not blockers.

## 13. Advisory implementation and MAP shape — ADVISORY

The orchestrator may execute DIRECT, DELEGATED or PROGRAMME. A useful logical decomposition is:

### R4.A — input capsule and residual foundation

- authenticate/rebuild the immediate exploratory parent;
- reproduce the baseline gate;
- construct all-twenty local OOF forecasts and residual rows;
- establish the exact chronological training/evaluation memberships.

Immediate consumer: every R4 model family. This is the highest-value independent scientific review
surface.

### R4.B — common tensor path and graph specifications

- materialise causal masked node sequences;
- freeze canonical node/feature order;
- create and review the fixed graph plus deterministic shuffled controls;
- freeze the bounded pre-run configuration and device/resource policy.

R4.B can proceed alongside the later portion of R4.A once the input schema is stable.

### R4.C — shared-capacity controls and graph candidates

- implement the local temporal, pooled non-graph, fixed, learned and shuffled residual models through
  one shared training/evaluation interface;
- reuse the temporal encoder and head rather than build parallel model stacks;
- run the exact-path smoke and development stages.

### R4.D — terminal execution and evaluation

- authenticate the frozen configuration and complete development register;
- run every successful predeclared candidate once on the terminal block;
- produce exact common-support metrics, breadth, concentration, seed stability and cost-adjacent
  diagnostics.

### R4.E — synthesis and close

- apply the retention rule mechanically;
- publish the concise exploratory report and complete attempt count;
- update active status documents and archive this plan only after operator acceptance of the result.

These are ownership and immediate-consumer seams, not mandatory PRs. The orchestrator may combine or
split them, adjust internal sequencing, and choose specialist/reviewer use while preserving one owner
per concurrent mutation surface and the binding experiment shape.

## 14. Explicit non-goals — BINDING

R4 does not:

- change or answer the U-lane universe-selection questions;
- add rates, agriculture, sectors, spreads, single stocks, options or another provider;
- test more horizons, target definitions or decision cadences;
- add dynamic or event-conditioned graphs;
- add session experts, ensembles, graph-depth searches or competing temporal engines;
- use native bid/ask, spread, imbalance or executable evidence;
- run the R3 portfolio optimiser or produce a post-cost portfolio result;
- amend the frozen `R3_IG_NATIVE_PORTFOLIO_V1` protocol;
- promote a model to R5, R6 or continuous shadow paper;
- create public interfaces, compatibility bridges or production infrastructure.

A promising R4 result is a hypothesis for future data, not a reason to widen this run.

## 15. Completion conditions — BINDING

R4-P0 is complete when:

1. the exact source/input and baseline gate authenticate;
2. the all-twenty OOF residual foundation is causal and independently reviewed;
3. the fixed candidate register and pre-run configuration are frozen before development outcomes;
4. every predeclared family/seed/stage has a successful or explicit failed attempt record;
5. development and terminal stages respect the declared chronology and terminal boundary;
6. all required exact-common-support comparisons and diagnostics are reported;
7. the retention rule is applied without post-result expansion;
8. the final report is clearly `POST_HOC_HISTORICAL_EXPLORATORY` and states all nonclaims;
9. focused checks, final exact-head complete verification and the required independent reviews pass;
10. no retained R2/R3 evidence, provider, collector, native protocol or broker boundary is mutated;
11. `PLAN.md` and `docs/STATUS.md` record the actual accepted result; and
12. R4 either nominates one exact future exploratory graph hypothesis or closes `NOT_RETAINED` for the
    declared source/period/universe/horizon/representation.

Positive forecast skill is not required for completion.
