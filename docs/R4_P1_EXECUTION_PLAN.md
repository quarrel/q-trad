# R4-P1 execution plan — lightweight learnability and residual-structure triage

- **Status:** OPERATOR APPROVED; computation and focused validation complete; independent final review pending
- **Work item:** R4-P1 — training-capability, residual-structure and corrected-development investigation
- **Experiment class:** `POST_HOC_HISTORICAL_EXPLORATORY`
- **Source class:** `IBKR_HISTORICAL_RESEARCH`
- **Historical universe:** the fixed LAB-0 twenty instruments
- **Primary horizon:** 15 minutes
- **Planning base:** `f23c44a2ca73e08a7b7ef47d5fd6d60f3a8633f8`
- **Safety boundary:** no provider access, collector/deployment mutation, terminal-former-holdout row access,
  broker operation, native-source claim, production endpoint or real-capital path

The operator approved and activated this R4-P1 authority on 2026-09-08. It does not alter the
accepted R4-P0 result, reopen R4-P0, satisfy R4's held-out retention gate, authorise R5, or begin the
separate universe/opportunity-selection lane.

## 1. Intent and authority

R4-P1 is a deliberately lightweight follow-on to the accepted
`NO_HYPOTHESIS_NOMINATED` R4-P0 result in `docs/R4_P0_FINDINGS.md`.

It answers three narrower questions:

1. Can the implemented temporal and graph mechanisms learn simple known signals and fit a small real
   batch under a credible optimisation schedule?
2. Do the authenticated all-twenty local residuals contain stable descriptive or causal structure
   aligned with the fixed economic graph rather than its shuffled control?
3. After training capability is established, do a very small number of corrected development-only
   temporal or fixed-graph fits justify a later, separately authorised full-scale or prospective test?

The operator task, `AGENTS.md`, `PLAN.md`, `docs/TRADING_RESEARCH.md` and
`docs/EVIDENCE_GOVERNANCE.md` control required behaviour. `.codex/map/MAP_Orchestrator.md` controls
MAP procedure when invoked. The classifications below identify authority; they do not create it.

The active orchestrator retains discretion over DIRECT, DELEGATED or PROGRAMME execution, custody
boundaries, sequencing, exact helper design and delivery shape. The plan fixes scientific and safety
boundaries, observable gates and resource limits—not a mandatory PR count or function-level recipe.

## 2. Plan-declared work class — BINDING

```text
experiment_class: POST_HOC_HISTORICAL_EXPLORATORY

evidence_state:
  ordinary development evidence while work proceeds;
  one accepted findings document and compact run outputs may be retained as historical exploratory evidence;
  no output is decision-grade, held-out retention, promotion authority or operational authority

permitted_actions:
  authenticate and consume the exact LAB-0 development inputs and applicable accepted R4-P0 development artefacts;
  use synthetic data and DEV_1–DEV_3 outcomes for capability, diagnostics and exploratory evaluation;
  create bounded experimental code, tests, configuration, metrics, predictions and compact run records;
  repair implementation defects and rerun ordinary exploratory work under new run identities;
  recommend whether a later full-scale or prospective graph experiment is worth authorising

prohibited_actions:
  loading or inspecting row-level TERMINAL_FORMER_HOLDOUT targets, outcome prices, predictions or metrics;
  treating published R4-P0 terminal aggregates as R4-P1 evaluation data;
  mutating, replacing, correcting or relabelling accepted R4-P0 artefacts or findings;
  provider calls, reacquisition, collector/deployment mutation or external publication;
  native, executable, post-cost, profitability, portfolio, production or real-capital claims;
  learned adjacency, dynamic graphs, session experts, architecture search, broad hyperparameter search or model ensembles;
  expanding the universe, horizon, source, feature family or target definition;
  beginning or deciding the separate U-lane

new_authority_required_for:
  any terminal-former-holdout row access;
  another full-scale retained R4 scientific release;
  a learned-adjacency candidate;
  a new source, period, universe, horizon, feature family or target;
  integration into R5 or the frozen native protocol;
  provider, collector, deployment, promotion or other protected mutation

binding_controls:
  exact source and development chronology remain explicit;
  all empirical features and labels respect their availability and target-maturity times;
  capability-policy choices use only synthetic data and a fixed DEV_1 fit sample, never DEV_2/DEV_3 metrics;
  no DEV_1–DEV_3 diagnostic or forecast metric is exposed while the capability policy remains revisable;
  DEV_2/DEV_3 results cannot trigger unrecorded schedule, architecture or feature changes;
  every attempted empirical configuration and result is reported, including failures;
  negative or inconclusive completion is valid;
  R4-P0 remains the authoritative record of its exact frozen candidates
```

R4-P1 does **not** opt into `CONSEQUENTIAL_RETAINED_EXECUTION`. Retained input, GPU use, elapsed time
or an archived findings document does not change that classification. Do not build promotion,
receipt, support-capsule, supervisor, crash-reconciliation or per-function review machinery for this
investigation.

## 3. Observed starting point — OBSERVED

The accepted R4-P0 result is mechanically negative for its exact frozen configurations:

- all five residual families and all three seeds lost materially to zero and the linear controls;
- every primary residual family improved on zero/local/pooled Ridge on 0 of 20 instruments;
- fixed and learned graphs also lost to pooled non-graph and shuffled controls;
- no historical graph hypothesis was nominated.

That result remains accepted and unchanged.

The current implementation and archived remediation plan also establish a material capability concern:

- the frozen `TrainingConfig` used `epochs = 1`;
- the primary loop accumulated gradients through a full epoch and called `optimizer.step()` once per
  epoch;
- therefore each frozen fit received one Adam parameter update;
- the residual heads began with ordinary random linear-layer initialisation rather than a guaranteed
  zero correction;
- terminal residual-model MSEs were roughly four orders of magnitude larger than zero-return MSE.

This is a plausible dominant explanation for the catastrophic residual scale, not a proven historical
counterfactual. R4-P1 tests that explanation prospectively on synthetic and development-only material;
it does not reconstruct what R4-P0 would have produced under another policy.

The separately disclosed R4-P0 CUDA-policy deviation and full-LAB control discrepancy remain part of
R4-P0's qualified result. R4-P1 does not investigate or erase their historical cause. It merely ensures
that any new local training process installs and records its actual numerical policy before use.

## 4. Exact inputs and protected surfaces — BINDING

### 4.1 Development parent

Use the exact canonical LAB-0 manifest:

```text
/workspace/tmp/qtrad-r2-lab/LAB-0/lab-manifest.json
SHA-256 462e40fa84038156b16c68bde4b68d574ab7862c680657ebd1a0035b39bf0072
```

Consume only authenticated parts required for the twenty-instrument 15-minute P0 development view.
Authenticate the immediate manifest and consumed children; do not replay Stage 7, Stage 8 or R2.

The exact all-twenty local residual foundation may be reconstructed through merged R4-P0/LAB-S code or
reused from an already accepted development artefact when its input/configuration identities match.
The work need not mint another evidence identity merely because it reuses or reconstructs exploratory
development rows.

### 4.2 Chronology

Use only:

```text
DEV_1
DEV_2
DEV_3
```

The empirical schedule is:

```text
DEV_1 residuals                  -> DEV_2 evaluation
DEV_1 + DEV_2 residuals          -> DEV_3 evaluation
```

`TERMINAL_FORMER_HOLDOUT` must not be requested by an R4-P1 loader. Add a focused failure test for an
attempt to request it.

### 4.3 Fixed linear foundation

Use the exact all-twenty local Ridge configuration already bound by R4-P0:

```text
configuration_id: 64124c5fdb3d66b01338688b3f8283ac663461fa87e3cb815b5a002b34bf6180
universe: ALL_20
horizon_minutes: 15
feature_set: P0
target_scale: RAW_RETURN
calibration: RAW
recency: EXPANDING
ridge_alpha: 1.0
source_class: IBKR_HISTORICAL_RESEARCH
```

Every graph or temporal target is the causal OOF residual:

```text
local_residual = realised_15m_return - LOCAL_RIDGE_oof_forecast
```

Missing local forecasts fail the affected row/foundation construction; they do not become zero.

## 5. R4-P1.A — training-capability investigation — BINDING OUTCOMES

This stage establishes whether the proposed training path can learn before any DEV_1–DEV_3 diagnostic
or forecast metric is exposed.

### 5.1 Corrected training properties

The capability candidate must satisfy all of these observable properties:

1. **Comparator-preserving initial output.** Before training, residual correction is exactly zero or
   within a predeclared negligible numerical tolerance, so total forecast initially equals
   `LOCAL_RIDGE`.
2. **Training-only residual scale.** One numerical target-scaling policy is fitted solely from the
   applicable training rows, recorded, and inverted back to raw return units for prediction.
3. **Real optimisation.** The implementation records actual optimiser-update count. A training pass
   cannot be described only by epochs when it performs one update over the complete dataset.
4. **Fixed empirical policy.** Optimiser, learning rate, update schedule, batch/accumulation policy,
   epoch or step ceiling, initialisation, scaling and device are frozen after capability work and
   before any DEV_1–DEV_3 diagnostic or forecast metric is calculated.
5. **Numerical policy installed.** The chosen CPU/CUDA policy is applied before the relevant runtime is
   initialised and is recorded from actual settings. There is no silent device fallback.
6. **Learning diagnostics.** Record initial/final loss, optimiser updates, target RMS, correction RMS,
   gradient norms or another direct non-zero-gradient check, and parameter-change magnitude.

Zero-initialising the residual head and stepping Adam once per minibatch are the simplest expected
mechanisms, but those mechanisms are ADVISORY. Equivalent implementations are acceptable when they
satisfy the observable properties above.

### 5.2 Capability cases

Use deterministic small fixtures with strong planted effects so pass/fail is unambiguous:

1. **Local temporal signal:** target depends only on the target node's causal sequence.
2. **Pooled context signal:** target depends on a permutation-invariant cross-node summary and cannot
   be recovered from the target node alone.
3. **Fixed-graph signal:** target depends on the exact fixed economic adjacency.
4. **Shuffled structural control:** the fixed graph must materially outperform the shuffled graph on
   the fixed-graph planted signal.
5. **Tiny real-batch overfit:** use one frozen, all-instrument DEV_1 sample solely as a capacity check.

For planted tasks, the mechanism capable of representing the planted signal must reduce MSE by at
least 90% from the zero predictor and beat the relevant incapable control by at least 50%. For the tiny
real batch, final standardised training MSE must be at most 1% of its initial value and at most `0.01`.
All outputs must remain finite and comparator-preserving before training.

The item owner may revise the training policy at most three times using only these fixtures and the
fixed DEV_1 overfit sample. Record each attempted policy and why it failed or was selected. Do not use
DEV_1–DEV_3 diagnostic outputs or DEV_2/DEV_3 forecast outcomes to choose among policies.

### 5.3 Capability decision and empirical-policy freeze

At the end of capability work, write one compact `R4P1_POLICY_FREEZE` entry in the ordinary run
register. It is not a promotion, receipt or scientific gate. It records exactly one of:

```text
CAPABILITY_ESTABLISHED
  selected empirical training policy and configuration identity

TRAINING_CAPABILITY_NOT_ESTABLISHED
  no empirical neural policy selected; empirical neural screen prohibited
```

No residual-structure correlation, lead-lag result, linear-probe forecast metric or neural empirical
metric for `DEV_1`, `DEV_2` or `DEV_3` may be calculated or exposed before this entry exists. Code,
metadata-only support preparation and tests for R4-P1.B may proceed in parallel while capability work
runs.

If capability is not established within the three-policy budget, record
`TRAINING_CAPABILITY_NOT_ESTABLISHED` and skip R4-P1.C. R4-P1.B still executes after that final decision
because its fixed diagnostics and linear probes remain required and cannot alter a neural policy that
was not selected.

Passing capability proves only that the mechanism can learn known or memorisable relationships. It is
not evidence of market predictability.

## 6. R4-P1.B — residual-structure diagnostics and linear probes — BINDING

R4-P1.B is deliberately cheap. Its implementation, exact residual joins, fixed edge sets, matched
non-edge construction and output schemas may be prepared while capability work runs. Calculation or
exposure of empirical `DEV_1`–`DEV_3` results begins only after `R4P1_POLICY_FREEZE`, whether that entry
records capability success or final capability failure.

### 6.1 Descriptive residual structure

For each of `DEV_1`, `DEV_2` and `DEV_3`, report:

- contemporaneous residual correlation on fixed economic edges;
- the same statistic on the exact shuffled-fixed edges;
- a matched non-edge reference;
- raw and equal-weighted market-common-component-removed views;
- distribution, sign, market-group breakdown and concentration by source/target instrument.

Contemporaneous residual correlation is labelled **descriptive and non-causal**. It may show common
unexplained outcomes but cannot by itself justify a forecast.

### 6.2 Causal lead-lag diagnostic

For directed source-to-target relationships, examine fixed lags of 15, 30 and 60 minutes. A source
residual is admissible only when its exact `target_available_at` is no later than the target decision's
feature cut-off. Compare fixed economic edges, shuffled edges and matched non-edges by block.

Report breadth and stability; do not select the best lag and present it as though predeclared. All
three lags remain visible.

### 6.3 Fixed evaluation reducers

All R4-P1 empirical forecast gates use the R4-P0 equal-instrument convention explicitly defined here.
Candidate and comparator use identical row support; a candidate-specific omission is a failed result,
not a reduced comparison set.

For model `m`, instrument `i` and block `p`:

```text
MSE[m,i,p] = mean_row((forecast[m] - realised_return)^2)
```

Block aggregate MSE is the equal-weighted mean of the twenty instrument MSEs:

```text
MSE[m,p] = mean_i(MSE[m,i,p])
```

Combined-development MSE is calculated on the union of `DEV_2` and `DEV_3` rows within each
instrument, then equal-weighted across the twenty instruments:

```text
MSE[m,i,DEV_COMBINED] = mean_row_union_DEV2_DEV3((forecast[m] - realised_return)^2)
MSE[m,DEV_COMBINED]   = mean_i(MSE[m,i,DEV_COMBINED])
```

For candidate `c` and comparator `b`:

```text
delta[c,b,i,p] = MSE[b,i,p] - MSE[c,i,p]
delta[c,b,p]   = MSE[b,p]   - MSE[c,p]
```

Positive delta favours the candidate. Direct skill versus zero is:

```text
skill[c,p] = 1 - MSE[c,p] / MSE[ZERO_RETURN,p]
```

For a breadth or concentration gate against comparator `b`, an instrument improves only when
`delta[c,b,i,DEV_COMBINED] > 0`. Positive contribution and concentration are:

```text
positive_contribution[c,b,i] = max(0, delta[c,b,i,DEV_COMBINED])

best_instrument_share[c,b] =
  max_i(positive_contribution[c,b,i])
  / sum_i(positive_contribution[c,b,i])
```

If the positive-contribution sum is zero, `best_instrument_share` is defined as `1.0` and the gate
fails. Clipping is used only for the concentration share; signed deltas remain unchanged elsewhere.
Group and row-weighted summaries may be reported descriptively but cannot determine whether a graph
fit is allowed or recommended.

### 6.4 Closed linear residual probes

Fit exactly four Ridge probes with `alpha = 1.0`, training-only standardisation and identical support:

```text
LOCAL_LINEAR_RESIDUAL
POOLED_LINEAR_RESIDUAL
FIXED_GRAPH_LINEAR_RESIDUAL
SHUFFLED_GRAPH_LINEAR_RESIDUAL
```

At each decision:

- local probe uses the target node's P0 vector;
- pooled probe adds an availability-weighted permutation-invariant mean of other nodes' P0 vectors;
- fixed probe adds the exact adjacency-weighted neighbour P0 vector;
- shuffled probe uses the exact R4-P0 shuffled-fixed adjacency.

No additional feature engineering, alpha search, lag selection or nonlinear learner is permitted.
Predicted residuals are added to the exact all-twenty `LOCAL_RIDGE` forecast and evaluated as total
forecasts.

Use the two chronological evaluations in section 4.2 and the reducers in section 6.3. Report
total-forecast MSE and direct skill versus zero, residual MSE, fixed-versus-pooled and
fixed-versus-shuffled deltas, results by instrument and group, and positive-contribution concentration.

### 6.5 Graph empirical gate

The fixed graph passes the lightweight empirical structure gate only when all are true under section
6.3:

1. `FIXED_GRAPH_LINEAR_RESIDUAL` improves on `POOLED_LINEAR_RESIDUAL` in both `DEV_2` and `DEV_3`;
2. it improves on `SHUFFLED_GRAPH_LINEAR_RESIDUAL` in both blocks;
3. its total forecast has positive direct skill versus `ZERO_RETURN` in both blocks;
4. at least 7 of 20 instruments improve on `POOLED_LINEAR_RESIDUAL` on `DEV_COMBINED`; and
5. `best_instrument_share[FIXED_GRAPH_LINEAR_RESIDUAL, POOLED_LINEAR_RESIDUAL] <= 0.8`.

Failure does not prove that every graph is useless. It means the present fixed graph and information
set do not justify even a small neural graph follow-on in this work item.

## 7. R4-P1.C — corrected bounded development screen — BINDING

This stage is conditional and intentionally small.

### 7.1 Outcome-blind bounded sample

Before calculating any empirical neural metric, select and record a deterministic evenly spaced
sample of structurally eligible decision timestamps:

- up to 5,000 training timestamps for each chronological stage;
- up to 2,000 evaluation timestamps from each of `DEV_2` and `DEV_3`;
- include every eligible target row at each selected timestamp;
- choose keys from chronology/support metadata only, not outcomes.

This is a triage sample, not a retained-scale result. Full-development fitting requires a later plan.

### 7.2 Candidate budget

After R4-P1.A records `CAPABILITY_ESTABLISHED`, always run:

```text
LOCAL_TEMPORAL_RESIDUAL
POOLED_NON_GRAPH_RESIDUAL
```

using:

- the one frozen training policy from `R4P1_POLICY_FREEZE`;
- one seed, `17`;
- `DEV_1 -> DEV_2` and `DEV_1 + DEV_2 -> DEV_3`;
- fixed sample keys and exact common support.

This is four empirical neural fits.

Only if the R4-P1.B graph empirical gate passes, also run:

```text
FIXED_ECONOMIC_GRAPH_RESIDUAL
SHUFFLED_FIXED_GRAPH_RESIDUAL
```

under the identical policy and stages. The total empirical neural budget is therefore at most eight
fits. `LEARNED_STATIC_GRAPH_RESIDUAL` is excluded.

### 7.3 Interpretation controls

For every fit report, using section 6.3 where applicable:

- actual optimiser-update count;
- initial and final training loss;
- correction RMS and quantiles in raw return units;
- training residual RMS;
- total-forecast MSE and direct skill versus zero;
- delta versus `LOCAL_RIDGE` and the applicable pooled/shuffled control;
- per-block, instrument and group breadth;
- positive-contribution concentration; and
- parameter count and bounded runtime/resource observations.

If training loss does not decline materially, gradients are absent/non-finite, or correction RMS is
more than ten times training residual RMS, classify the fit as `TRAINING_FAILURE` and do not interpret
its forecast metrics as evidence against the model family.

The training policy cannot be changed in response to `DEV_2`/`DEV_3` results. A changed policy is
another exploratory configuration and requires fresh operator authority under this plan's closed
budget.

### 7.4 Recommendation threshold

A fixed graph may be recommended for a separately authorised full-scale or prospective investigation
only if, under section 6.3, it:

1. passes the R4-P1.B linear graph gate;
2. beats `POOLED_NON_GRAPH_RESIDUAL` in both `DEV_2` and `DEV_3`;
3. beats `SHUFFLED_FIXED_GRAPH_RESIDUAL` in both blocks;
4. has positive total-forecast skill versus `ZERO_RETURN` in both blocks;
5. improves at least 7 of 20 instruments versus `POOLED_NON_GRAPH_RESIDUAL` on `DEV_COMBINED`; and
6. has `best_instrument_share[FIXED_ECONOMIC_GRAPH_RESIDUAL, POOLED_NON_GRAPH_RESIDUAL] <= 0.8`.

This recommendation is not `HYPOTHESIS_NOMINATED`, graph retention or promotion. It is a go/no-go
recommendation for designing another experiment on untouched or prospectively acquired evidence.

## 8. Attempt, output and resource discipline — BINDING

R4-P1 uses ordinary exploratory run discipline:

- one compact configuration per attempted capability or empirical policy;
- one append-only JSONL or equivalent run register, including the single `R4P1_POLICY_FREEZE` entry;
- a new output directory for a rerun rather than overwriting an earlier result;
- failed and superseded attempts remain listed in the final summary;
- no release journal, G0 gate, promotion, reusable receipt, immutable support capsule or process
  supervisor is required;
- no cache is built unless measured repeated work makes it cheaper than direct loading;
- existing accepted R4-P0 development caches may be reused only when their semantics and immediate
  identities match the current consumer.

Scientific empirical budget:

```text
4 linear probe families × 2 chronological evaluations = 8 linear fits
2 mandatory neural families × 2 evaluations           = 4 neural fits
2 conditional graph families × 2 evaluations           = up to 4 further neural fits
1 empirical neural seed                                = 17
terminal evaluations                                   = 0
learned-adjacency fits                                 = 0
```

Synthetic/capability fits are implementation checks and remain bounded by the three-policy revision
limit.

If the chosen implementation projects more than 20 GB of new retained output or cannot execute the
maximum eight empirical neural fits sequentially on the available single GPU without a new
performance/remediation programme, stop and simplify or return the decision to the orchestrator. Do
not rebuild R4-P0's cache/supervisor/evidence architecture to force completion.

Healthy long-running commands use MAP `LONGRUN` when triggered. Autonomous OS resource sampling may
continue at an appropriate cadence, but it must not create repeated short model wake-ups for liveness.

## 9. Outputs — BINDING CONTENT, ADVISORY PATHS

Preferred tracked paths:

```text
experiments/r4_p1_learnability/
tests/experiments/r4_p1_learnability/
docs/R4_P1_FINDINGS.md
```

Preferred untracked output root:

```text
/workspace/tmp/qtrad-r4-p1/<run-id>/
```

The final output contains:

- the selected capability/training policy and all rejected policy attempts;
- the `R4P1_POLICY_FREEZE` decision;
- synthetic capability results and tiny-batch overfit evidence;
- descriptive residual-edge diagnostics;
- causal lead-lag diagnostics;
- the complete linear-probe table under the fixed reducers;
- corrected neural-control results or the exact gate that skipped them;
- every attempted empirical configuration and failure;
- a concise explanation of what R4-P0 can and cannot be taken to show after this investigation; and
- one or more of these recommendations:

```text
TRAINING_CAPABILITY_NOT_ESTABLISHED
NO_CURRENT_GRAPH_STRUCTURE_SIGNAL
CORRECTED_TEMPORAL_SCREEN_NEGATIVE
CORRECTED_GRAPH_SCREEN_NEGATIVE
CONSIDER_SEPARATE_PROSPECTIVE_GRAPH_EXPERIMENT
```

These are findings categories, not promotion states.

## 10. Validation and review — BINDING

Use proportionate validation:

1. Focused tests prove comparator-preserving initial output, actual optimiser-step counting,
   training-only target scaling, planted-signal recovery, fixed-versus-shuffled discrimination,
   target-maturity checks and terminal-loader rejection.
2. Focused tests cover linear context construction, exact graph/shuffle use, common support, the fixed
   equal-instrument reducers, combined-block calculations, contribution concentration and metric sign
   conventions.
3. Focused tests prove that empirical B/C metric execution rejects a missing `R4P1_POLICY_FREEZE` and
   that B remains executable after a recorded `TRAINING_CAPABILITY_NOT_ESTABLISHED` decision.
4. Run formatting, Ruff and strict typing for changed experimental code.
5. Run one correctly shaped end-to-end smoke covering capability, policy freeze, diagnostics, one
   linear probe and one bounded neural fit.
6. Do not run `ops/dev/verify.sh` solely because this exploratory investigation completes. Run the
   complete gate only if the candidate changes active `src/`, schemas, shared runtime/dependency
   policy, or another governing milestone/release boundary requires it.
7. Use one independent final review of the exact candidate and findings. The reviewer checks the
   capability gates, policy-freeze ordering, chronology/terminal exclusion, empirical reducers,
   complete attempt account and claim boundary. The reviewer does not rerun every fit or create review
   handoffs for helper functions.

A reviewer must cite a binding requirement or concrete current-investigation failure path. Preferences,
speculative production hardening, future R5/U-lane requirements, generic evidence infrastructure and
unrequested compatibility are not blockers.

## 11. Advisory MAP execution shape — ADVISORY

The investigation is small enough for one delegated item owner. The orchestrator may instead use a
small programme when parallelising preparation saves material time. Useful logical seams are:

### R4-P1.A — learnability owner

- implement the corrected training path in a new experimental namespace;
- pass the synthetic and tiny-batch capability gates;
- record `R4P1_POLICY_FREEZE` with either capability success and one policy or final capability failure.

### R4-P1.B — diagnostic owner

- prepare the residual joins, fixed edge sets, matched non-edges, linear design and reducers while A
  runs;
- do not calculate or expose any empirical block result before `R4P1_POLICY_FREEZE`;
- after that entry, run edge/shuffle/non-edge and causal lead-lag diagnostics;
- execute the four linear probes and apply the graph empirical gate even when A closed
  `TRAINING_CAPABILITY_NOT_ESTABLISHED`.

### R4-P1.C — conditional empirical owner

- after `CAPABILITY_ESTABLISHED`, select the outcome-blind bounded sample;
- run the four mandatory temporal-control fits;
- add the four fixed/shuffled graph fits only when R4-P1.B passes;
- produce the complete result table under section 6.3.

### R4-P1.Z — synthesis

- combine the exact outputs without refitting;
- produce `docs/R4_P1_FINDINGS.md`;
- obtain one independent final review.

These are custody/consumer seams, not mandatory branches or PRs. A and B preparation may run
concurrently once the common residual input is established. B empirical execution waits for A's final
policy-freeze entry. C depends on capability success and, for its graph fits, the B graph gate. The
orchestrator may combine all work under one owner, change file placement, or omit a separate synthesis
agent while preserving the binding gates and mutation ownership.

## 12. Completion conditions — BINDING

R4-P1 is complete when:

1. the exact LAB-0 development parent and all-twenty local residual foundation authenticate;
2. no terminal-former-holdout row or outcome is loaded;
3. the capability policy either passes every required capability case within three revisions or closes
   `TRAINING_CAPABILITY_NOT_ESTABLISHED`;
4. one `R4P1_POLICY_FREEZE` entry precedes every empirical B/C metric;
5. descriptive, causal lead-lag and all four linear-probe results are reported for `DEV_2` and `DEV_3`
   using the fixed reducers;
6. the graph empirical gate is applied mechanically;
7. the mandatory corrected local/pooled neural screen runs only after capability passes;
8. fixed/shuffled neural fits run only after the linear graph gate passes;
9. no more than eight empirical neural fits and one empirical seed are used;
10. all attempted policies/configurations and failures are retained in the compact account;
11. the final findings distinguish training failure, lack of signal and lack of graph increment;
12. focused checks and one independent final review pass; and
13. the final recommendation states whether any further graph work is justified, without changing the
    accepted R4-P0 result or claiming held-out/native/economic validity.

Positive forecast skill is not required for completion. The expected successful outcome may simply be
that the training path is repaired and the current graph hypothesis is cheaply deprioritised.
