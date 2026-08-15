# q-trad R2 decision-grade scientific execution plan

**Status:** ACTIVE execution authority; PR-C1 evidence-handoff simplification and PR-S0 run enablement are merged and complete
**Scope:** first real decision-grade `R2-IBKR-HISTORICAL` run through F2, G1, unopened G2, irreversible reveal and R2.H  
**Safety boundary:** offline research only; IBKR paper historical market data; no broker orders, production trading endpoint or real-capital operation  
**Primary scientific question:** do the fixed local or pooled Ridge controls produce stable out-of-sample information about 15-minute future midpoint returns beyond the zero-return control?  
**Conclusion boundary:** IBKR historical midpoint research under the declared provider-history availability policy only; no native-IG, execution-cost, portfolio or profitability conclusion

---

## 1. Current position and the actual next step

The evidence-handoff simplification programme is complete. The software path now supports:

```text
Stage 6 receipt
  -> Stage 7 receipt
  -> Stage 8 receipt
  -> optional Stage 8 confirmatory promotion
  -> R2 OOF
  -> R2 OOF verification receipt
  -> F2 promotion
  -> G1 selection
  -> unopened G2 preparation
  -> irreversible reveal
  -> R2.H
```

However, **do not begin F2 immediately**.

The H4 retained-file migration proved that the old retained Stage 8 “qualifying” result is not valid under the corrected causal contracts. Its immutable invalidation record shows:

- target return dispositions changed from `VALID` to `UNAVAILABLE_BY_FREEZE`;
- chronological folds changed from nine to zero;
- readiness changed from `QUALIFYING_HISTORY_READY` to `INSUFFICIENT_HISTORY_FOR_MODEL_CONCLUSION`; and
- the old Stage 8 confirmatory promotion is superseded and has no replacement.

Therefore the first operational objective is:

> **Build, independently verify and promote a new qualifying Stage 8 foundation from the current authenticated Stage 7 v3 authority, using a source-appropriate configuration frozen before any model output is inspected.**

Only after that authority exists may the real confirmatory R2 run begin.

Do **not** reacquire IBKR history first. H4 already produced/authenticated current Stage 6/7 evidence before the Stage 8 divergence. Reuse that current v3 authority if its files are still available and authenticate successfully. Reacquisition is a fallback requiring separate explicit operator authorisation.

Native IG and IBKR continuous capture continue in parallel, but they are not candidates for this first decision-grade R2 run: they do not yet provide the fixed 16-week history required by the R2 confirmatory gate.

---

# 2. Hard scientific and operational constraints

These constraints apply to the entire run.

## 2.1 No outcome-driven changes

Before the first F2 model output is inspected, freeze all of the following:

- source class;
- exact foundation source authority;
- decision range;
- locked holdout range;
- six target instruments and three market groups;
- 15-minute target horizon;
- feature sets and feature-family eligibility;
- feature windows;
- feature lag and target revision delay;
- fold schedule;
- alpha grid;
- preprocessing and solver policy;
- model families;
- metric/bucket definitions;
- model-selection policy;
- acceptance/concentration thresholds; and
- final fitting and holdout question policy already fixed by the implementation contracts.

After F2 output exists, do not alter any of those choices for this confirmatory experiment.

After holdout outcomes are opened, no scientific or implementation change permits a second reveal of the same holdout.

## 2.2 Fixed source-specific experiment

The first decision-grade experiment is:

```text
MarketDataSourceClass = IBKR_HISTORICAL_RESEARCH
EvidenceClass         = CONFIRMATORY
profile               = ibkr-historical-v1
primary horizon       = 15 minutes
```

Fixed targets:

```text
FX
  fx:aud-usd
  fx:eur-usd

INDEX
  index:australia-200
  index:us-500

COMMODITY
  commodity:spot-gold
  commodity:us-crude
```

All other authenticated Stage 7 instruments remain context-only.

Fixed R2 feature ladder:

```text
L0
L1
P0
P1
```

Provider-history does **not** make spread or quote-imbalance features eligible. Do not add the optional Garman–Klass ablation to the first confirmatory experiment unless a separate pre-F2 scientific amendment explicitly activates it before any model output is viewed.

Fixed Ridge policy remains the current `ibkr-historical-v1` profile, including the registered alpha grid and solver settings. Do not edit thresholds merely because they appear permissive or because initial OOF results are disappointing.

## 2.3 Confirmatory data gates

Before F2 the verified foundation must support at least:

- 16 calendar weeks of common causal evidence;
- exactly the six fixed target instruments;
- the three fixed groups with two targets per group;
- at least 90% valid 15-minute target coverage for every target in every configured research block;
- at least six weeks initial training history;
- exactly three chronological OOF validation periods of two weeks each; and
- a final locked holdout of at least four weeks.

The source-specific experiment implementation also requires the holdout to be the final 20% of the foundation range and requires exactly three folds. The configuration below is constructed to satisfy all of these constraints simultaneously rather than weakening any of them.

## 2.4 Evidence and authority

At every boundary:

- use the current create-only contract;
- authenticate the immediate-parent receipt/promotion;
- perform the current transform once;
- independently verify the current transform once;
- retain the receipt/promotion IDs;
- do not invoke exceptional deep-audit commands unless a defined audit trigger occurs; and
- never copy or re-prove ancestor evidence merely for reassurance.

## 2.5 Holdout boundary

Before G2 reveal:

- holdout outcomes remain unavailable to the operator and model-selection logic;
- no holdout-derived metric is printed, inspected or exported;
- G2 preparation may create outcome-blind features, final fits, forecasts, coverage and a seal only; and
- the reveal stage requires a **new explicit operator instruction after review of the unopened preparation**.

The instruction to execute this plan does not by itself authorise irreversible holdout reveal.

---

# 3. PR-S0 — scientific-run enablement and active-authority cleanup

**State:** COMPLETE and merged as PR-S0; this section records the accepted prerequisite boundary for the first real run.

This PR must not alter any scientific contract, threshold, model, feature or evidence semantics.

## 3.1 Documentation cleanup

The completed PR-S0 reconciles current authority so agents do not follow stale pre-run state:

- `AGENTS.md`: simplification is complete; immediate priority is first decision-grade R2 execution under this plan.
- `PLAN.md` / `docs/STATUS.md`: record the H4 invalidation as the current Stage 8 state and state that no replacement Stage 8 promotion exists yet.
- `docs/IBKR-HISTORICAL-ACQUISITION.md`: remove any stale top-level sentence still describing the superseded retained foundation/promotion as current qualifying authority.
- add this document as the active execution plan, preferably `docs/R2_SCIENTIFIC_EXECUTION_PLAN.md`.

Do not rewrite historical chronology merely for neatness.

## 3.2 Close the two thin CLI gaps if they still exist at exact head

### A. OOF verification receipt output

Current runtime already has the one-time semantic verifier:

```python
verify_r2_oof_semantics(path, receipt_output=...)
```

The completed `research baselines oof-verify` CLI exposes the existing create-only receipt output:

```text
qtrad research baselines oof-verify \
  --bundle <oof-manifest> \
  --receipt-output <new-receipt>
```

The command must call the existing one-time semantic verifier and write the existing receipt contract. Do not invent another verification API or receipt format.

Regression:

```text
build OOF semantic replay count      = 0
oof-verify semantic replay count     = 1
authenticate using receipt replay    = 0
```

### B. Outcome-blind holdout-target-source persistence

`oof-build` requires an authenticated `R2HoldoutTargetSource` path. PR-S0 now persists that current contract from the verified/promoted Stage 8 authority through the smallest existing outcome-blind application path:

Supported surface:

```text
qtrad research baselines holdout-target-source \
  --foundation-bundle <stage8> \
  --foundation-receipt <stage8-receipt> \
  --foundation-promotion <stage8-promotion> \
  --experiment <experiment> \
  --output <new-json>
```

Requirements:

- authenticate the exact Stage 8 receipt and promotion;
- use the Stage 8 target-index/causal/outcome-blind authorities already implemented;
- emit only the existing `qtrad-r2-holdout-target-source-v1` contract;
- contain no realised holdout return or price value;
- do not decode/return the protected holdout target child merely to construct the source;
- print only path/identity/count metadata; and
- create-only output.

PR-S0 would have reused an existing supported command name if one existed; no compatibility alias is retained.

## 3.3 Validation

Because this PR touches runtime CLI plumbing, run:

- focused CLI/OOF/holdout tests;
- static/type checks; and
- clean `ops/dev/verify.sh`.

No provider call, Stage 8 build, model run or holdout access in PR-S0.

---

# 4. Run root, code freeze and provenance

After PR-S0 merges:

1. update to exact `main`;
2. require a clean worktree;
3. run `ops/dev/verify.sh` once on that exact commit;
4. record exact commit, image identity and numerical environment as execution provenance;
5. create one new run root outside Git, e.g.:

```text
/workspace/tmp/r2-confirmatory-ibkr-historical-<UTC timestamp>/
```

6. never reuse a path from the abandoned H4 Stage 8 attempt;
7. all scientific artefacts under the run root are create-only.

Do not change code after the scientific freeze merely to keep the run moving. If a pre-holdout software defect is found, stop, fix it through review, and restart the affected scientific boundary under the new exact commit. Do not relabel earlier output.

A documentation-only change need not change scientific identity, but avoid changing the execution commit during the run unless necessary.

---

# 5. Phase A — recover and authenticate the current historical parent authority

## A1. Locate H4 current-contract artefacts

Locate the H4-created current:

```text
Stage 6 v3 manifest
Stage 6 v3 verification receipt
Stage 7 v3 manifest
Stage 7 v3 verification receipt
```

Use the immutable H4 invalidation record and retained operation evidence to locate them. Do not use the abandoned Stage 8 foundation as authority.

Do not resurrect v1/v2 readers or migration code.

## A2. Authenticate Stage 7 normally

Use the current ordinary authentication command:

```text
qtrad research observations authenticate-provider-history \
  --manifest <stage7-v3-manifest> \
  --receipt <stage7-v3-receipt>
```

Record at minimum:

- Stage 7 dataset semantic ID;
- Stage 7 closure ID;
- Stage 7 verification ID;
- Stage 6 result ID named by the receipt;
- source plan start/end;
- declared availability policy/delay;
- row count and instrument set.

This is authentication, not deep verification.

### A2 stop gate

If current Stage 7 v3 evidence cannot be located or authenticated, **stop**. Do not silently fall back to old retained contracts.

At that point prepare a separate acquisition proposal explaining exactly what source evidence is missing. Fresh IBKR historical acquisition requires explicit operator authorisation and is not implicit in this execution plan.

---

# 6. Phase B — freeze a causally correct Stage 8 configuration

This is the most important pre-model step.

The old H4 attempt failed because native-style causal timing was incompatible with the provider-history policy. Provider history declares:

```text
available_at = interval_end + PT5M
```

The Stage 8 adapter maps that authenticated provider availability into the generic observation availability fields. Therefore the first confirmatory provider-history configuration must not attempt to use a bar earlier than the declared source policy permits.

## B1. Fixed causal values

Unless the authenticated Stage 7 policy itself says otherwise, freeze:

```text
grid_resolution         = PT1M
selected_feature_lag    = PT5M
target_revision_delay   = PT5M
primary horizon         = PT15M
target_horizons         = [PT15M]
required feature basis  = MID
target basis            = MID
fold policy             = EXPANDING_WALK_FORWARD
```

Rationale:

- a bar ending at `t` is not available before `t + PT5M`;
- `selected_feature_lag = PT5M` is the least conservative whole-grid lag consistent with that declared policy;
- the target end bar is not available before `target_end + PT5M`, so a shorter target freeze necessarily creates `UNAVAILABLE_BY_FREEZE`;
- provider history is frozen first-success response evidence, so no additional data-driven correction search is introduced; and
- only the 15-minute horizon is required for the first confirmatory R2 result.

Use `PROVISIONAL_CONSERVATIVE` for provider-history feature/target timing policy unless current accepted source-specific authority defines a more precise existing label. The target policy reason must explicitly state that the delay equals the declared provider-history availability delay and is fixed independently of model outcomes.

Do not increase or decrease these delays after looking at readiness or model output merely to obtain a passing result.

## B2. Deterministic decision range

Derive the range mechanically from the authenticated Stage 6 plan bounds, not from returns or model outcomes.

Let:

```text
P0 = authenticated historical plan start
P1 = authenticated historical plan end
G  = PT1M grid
L  = PT5M feature lag
H  = PT15M horizon
R  = PT5M target revision delay
```

Set initially:

```text
range_start_raw = P0 + L + G
range_end_raw   = P1 - H - R
```

This makes the source bound sufficient for the earliest feature bar and latest target freeze.

To keep the final-20%-holdout boundary on the one-minute grid, trim **only the end** backwards by the smallest whole number of minutes required so:

```text
(range_end - range_start) / 5 minutes
```

is an integer.

Do not choose dates by looking for good returns, lower volatility, favourable model performance or convenient coverage gaps.

Record the discarded leading/trailing minutes and the mechanical reason.

## B3. Final 20% locked holdout

Let:

```text
T = range_end - range_start
holdout_start = range_start + 0.8 * T
holdout_end   = range_end
```

Preflight requirements:

```text
T / 5 >= 4 weeks
T >= 20 weeks
holdout_start is on the configured grid
```

If these fail, the retained source cannot support the fixed first confirmatory shape; stop before Stage 8 publication.

## B4. Exactly three two-week folds

Freeze:

```text
minimum_validation_duration = 2 weeks
```

Derive the outer embargo `E` **before looking at target or model outcomes** from the current dependency policy. Record the dependency calculation. Do not add a larger arbitrary embargo for “safety”; do not shrink a required embargo for convenience.

Choose `minimum_training_duration` so that exactly three two-week validation periods end at the locked holdout boundary under the actual `build_expanding_folds` formula:

```text
minimum_training_duration =
    (holdout_start - range_start)
    - 3 * minimum_validation_duration
    - primary_horizon
    - 3 * E
```

Then independently calculate the expected windows without reading target values:

```text
validation_1_start = range_start + minimum_training_duration + H + E
validation_1_end   = validation_1_start + 2 weeks
validation_2_start = validation_1_end + E
validation_2_end   = validation_2_start + 2 weeks
validation_3_start = validation_2_end + E
validation_3_end   = validation_3_start + 2 weeks
```

Require:

```text
validation_3_end == holdout_start
minimum_training_duration >= 6 weeks
```

If not, stop and correct the deterministic time arithmetic before building Stage 8.

## B5. Other configuration fields

- use every authenticated Stage 7 instrument in the foundation universe;
- six fixed candidates are `TARGET`; all others are `CONTEXT`;
- use the provider-history availability adapter only; do not mix native evidence;
- use a calibration-range field that is wholly before `range_start` and inside the authenticated source bound;
- if the timing policy is declared rather than measured, do not pretend the calibration range statistically estimated PT5M;
- no change to source mappings or product roles;
- no model-related value may influence this configuration.

## B6. Persist a freeze record

Before Stage 8 build, create a human-readable and machine-readable freeze record containing:

- exact Stage 7 semantic/closure/verification IDs;
- all FoundationConfig fields;
- derivation of range start/end;
- derivation of holdout start/end;
- derivation of feature lag and target revision delay;
- derivation of embargo;
- expected three fold windows;
- fixed six targets/groups;
- explicit statement that no R2 model output or holdout outcome was inspected.

Hash the freeze record and retain it with the run evidence.

Do not modify the freeze record after Stage 8 build begins. Corrections require a new run candidate and an explicit explanation.

---

# 7. Phase C — build and independently qualify the new Stage 8 foundation

## C1. Preflight

Use the current Stage 8 preflight against the frozen config:

```text
qtrad research foundation preflight \
  --stage7-manifest <stage7> \
  --stage7-receipt <stage7-receipt> \
  --configuration <frozen-config> \
  --output <new-stage8-path>
```

Preflight must perform no provider-row decode or publication.

## C2. Build once

```text
qtrad research foundation build \
  --stage7-manifest <stage7> \
  --stage7-receipt <stage7-receipt> \
  --configuration <frozen-config> \
  --output <stage8> \
  --workers <reviewed worker count>
```

Use a worker count selected for operational efficiency, not scientific meaning. Record it as provenance.

## C3. Deep-verify once and issue receipt

```text
qtrad research foundation verify \
  --bundle <stage8> \
  --stage7-manifest <stage7> \
  --stage7-receipt <stage7-receipt> \
  --receipt-output <stage8-receipt>
```

Then run ordinary authentication:

```text
qtrad research foundation authenticate \
  --bundle <stage8> \
  --receipt <stage8-receipt>
```

Do not run an exceptional cumulative audit.

## C4. Qualification gate

The run may continue only if all are true:

```text
readiness.state == QUALIFYING_HISTORY_READY
readiness.causes == []
fold_count == 3
blocking_coverage_cells == []
```

Also independently record/assert:

- each of the six target candidates has successful request/session evidence;
- each required coverage cell passes the fixed 90% threshold;
- common causal support meets the R2 16-week requirement;
- `minimum_training_duration >= 6 weeks`;
- each actual validation interval is exactly two weeks;
- holdout is the final 20% and at least four weeks;
- primary horizon is exactly 15 minutes;
- no holdout decision row is present in any fold validation membership; and
- the target disposition summary no longer shows systematic `UNAVAILABLE_BY_FREEZE` caused by a timing-policy mismatch.

### C4 failure rule

If the frozen candidate is nonqualifying:

- retain it as valid nonqualifying evidence;
- do not alter the date range/timing policy to dodge observed gaps or weak coverage;
- do not begin R2 models;
- classify the failure.

If failure is a **source limitation** (entitlement, missing session evidence, insufficient duration/rows/coverage), stop and prepare an operator decision on fresh historical acquisition.

If failure is a **demonstrable implementation/configuration defect** independent of market outcomes, fix it through normal review and start a new frozen candidate. Document why the correction is not an outcome-driven choice.

## C5. Promote qualifying Stage 8

Only after C4 passes, create confirmatory authority:

```text
qtrad research foundation promote-confirmatory \
  --bundle <stage8> \
  --receipt <stage8-receipt> \
  --authorized-by <operator> \
  --authorized-at <UTC minute> \
  --authorization-reference <run/freeze reference> \
  --output <stage8-promotion>
```

Authenticate the promotion using the current ordinary command.

Promotion performs no semantic replay.

Record:

```text
foundation semantic ID
foundation closure ID
foundation verification ID
foundation promotion ID
configuration ID
readiness ID/hash
```

This is the scientific entry authority for F2.

---

# 8. Phase D — freeze and register the real R2 experiment

From this point onward, the Stage 8 authority is immutable for this experiment.

## D1. Build the confirmatory experiment

Use only the fixed source profile and exact Stage 8 promotion:

```text
qtrad research baselines experiment-build \
  --foundation <stage8> \
  --foundation-receipt <stage8-receipt> \
  --foundation-promotion <stage8-promotion> \
  --profile ibkr-historical-v1 \
  --output <experiment.json>
```

Confirm that the resulting experiment is `EvidenceClass.CONFIRMATORY` and `MarketDataSourceClass.IBKR_HISTORICAL_RESEARCH`.

Do not edit the experiment JSON.

## D2. Record the experiment registry before model fitting

Create a run registry containing:

- experiment configuration ID;
- Stage 8 semantic/closure/verification/promotion IDs;
- six targets and groups;
- feature sets L0/L1/P0/P1;
- feature windows;
- all model families/configurations;
- alpha grid;
- solver/tolerance/max iterations;
- preprocessing policy;
- metric, bucket and selection policy;
- acceptance/concentration thresholds;
- holdout range;
- numerical replay tolerances;
- exact code/image/numerical provenance.

The registry is frozen before feature/model output is inspected.

## D3. Pre-OOF readiness

Run the current readiness command with the promotion:

```text
qtrad research baselines readiness \
  --foundation-bundle <stage8> \
  --foundation-receipt <stage8-receipt> \
  --foundation-promotion <stage8-promotion> \
  --experiment <experiment> \
  --output <readiness.json>
```

At this point foundation/contract readiness must be ready. Inner-validation/OOF states may remain incomplete until the OOF artefact exists; do not treat that as a reason to change the experiment.

---

# 9. Phase E — create the outcome-blind holdout source

Before OOF construction, persist the current `R2HoldoutTargetSource` through the supported outcome-blind command established in PR-S0.

The source must:

- bind the exact Stage 8/experiment/holdout scope;
- contain target identities/opportunity authority but no realised holdout values;
- contain only mature pre-holdout target rows for final fitting;
- authenticate the pre-holdout projection/opportunity registry lineage needed later; and
- be create-only.

Validate it by reloading the contract and checking its `source_id`.

Do not print or inspect the protected target child.

---

# 10. Phase F — materialise and independently verify the four fixed feature sets

For each of:

```text
L0
L1
P0
P1
```

run:

```text
qtrad research baselines features \
  --foundation-bundle <stage8> \
  --foundation-receipt <stage8-receipt> \
  --foundation-promotion <stage8-promotion> \
  --experiment <experiment> \
  --feature-set <NAME> \
  --output <feature-output>
```

Then independently verify each persisted feature manifest with the corresponding `features-verify` command and the same foundation authorities.

Record for each set:

- semantic feature dataset ID;
- manifest/closure ID;
- row count;
- feature schema ID;
- causal cut-off checks;
- holdout exclusion status; and
- source/evidence class.

Do not compare predictive outcomes while feature materialisation is in progress. Feature failure is a software/evidence failure, not a reason to redefine the feature set.

---

# 11. Phase G — real confirmatory F2 OOF

## G1. Build OOF once

Invoke `oof-build` with:

- exact Stage 8 bundle;
- exact Stage 8 receipt;
- exact Stage 8 promotion;
- frozen experiment;
- all four independently verified feature manifests; and
- the authenticated outcome-blind holdout-target source.

Conceptually:

```text
qtrad research baselines oof-build \
  --foundation-bundle <stage8> \
  --foundation-receipt <stage8-receipt> \
  --foundation-promotion <stage8-promotion> \
  --experiment <experiment> \
  --feature-manifest L0=<...> \
  --feature-manifest L1=<...> \
  --feature-manifest P0=<...> \
  --feature-manifest P1=<...> \
  --holdout-target-source <holdout-target-source.json> \
  --output <oof-root-or-manifest>
```

Use the exact argument syntax emitted by `--help`; do not guess the feature-manifest encoding if it differs from the conceptual form above.

`oof-build` must perform the R2 computation once. It must not invoke Stage 6/7/8 semantic verifiers.

## G2. Deep-verify F2 once and create OOF receipt

Use the PR-S0 receipt-capable command:

```text
qtrad research baselines oof-verify \
  --bundle <oof-manifest> \
  --receipt-output <oof-receipt>
```

This is the one independent R2 semantic replay.

Immediately authenticate the OOF using its receipt through the current receipt-backed authority path. Do not run `confirmatory-f2-audit`; that command is exceptional deep audit only.

## G3. F2 validity gate

Before promotion require:

- OOF receipt authenticates;
- run kind/evidence class is confirmatory;
- immediate Stage 8 promotion matches exactly;
- complete inner-validation selection register is authenticated;
- all required configurations have explicit dispositions;
- holdout exclusion is proven;
- F2 readiness is `READY` under the current verifier; and
- no parent semantic verifier was called during ordinary authentication.

A weak or negative forecasting result is **not** an F2 validity failure.

If F2 is valid but models are weak, continue according to the frozen selection policy. Do not change features, alphas, thresholds or questions because the OOF result is disappointing.

## G4. Record F2 results before promotion

Store a scientific F2 report containing the frozen metrics already produced by the OOF artefact, including:

- configuration dispositions;
- selected alphas/preprocessing by fold;
- primary/common-support MSE/RMSE metrics;
- rank/direction/bucket metrics where defined;
- fold/instrument/period stability;
- concentration diagnostics;
- coverage;
- local vs zero control;
- pooled vs local comparisons; and
- exact OOF semantic/closure/verification IDs.

Do not make a profitability claim. Do not access holdout outcomes.

---

# 12. Phase H — F2 promotion and immutable G1 selection

## H1. Promote F2

Create the replay-free F2 promotion:

```text
qtrad research baselines confirmatory-f2-promote \
  --oof-bundle <oof-manifest> \
  --oof-receipt <oof-receipt> \
  --authorized-by <operator> \
  --authorized-at <UTC timestamp> \
  --output <f2-promotion>
```

Authenticate the promotion. Do not run the exceptional F2 audit.

## H2. Freeze G1 from the promotion

```text
qtrad research baselines confirmatory-selection-freeze \
  --f2-promotion <f2-promotion> \
  --frozen-by <operator> \
  --output <g1-selection>
```

Then independently verify:

```text
qtrad research baselines confirmatory-g1-verify \
  --f2-promotion <f2-promotion> \
  --selection <g1-selection>
```

Record:

- F2 promotion ID;
- G1 selection ID;
- selected configurations;
- retained dependency/control configurations;
- holdout question IDs;
- comparator hierarchy;
- evaluation policy; and
- exact zero/local controls retained even if the selected set is otherwise empty.

Do not manually add/remove a model based on preference after G1 is frozen.

---

# 13. Phase I — prepare and verify G2 while the holdout remains unopened

## I1. Prepare

```text
qtrad research baselines confirmatory-g2-prepare \
  --f2-promotion <f2-promotion> \
  --selection <g1-selection> \
  --prepared-by <operator> \
  --output <g2-preparation>
```

This stage may:

- materialise outcome-blind holdout features;
- perform final fitting under the exact G1-selected policy;
- generate sealed holdout forecasts;
- generate opportunity coverage; and
- create the immutable seal.

It must not decode holdout outcomes.

## I2. Independently verify unopened preparation

```text
qtrad research baselines confirmatory-g2-preparation-verify \
  --f2-promotion <f2-promotion> \
  --selection <g1-selection> \
  --preparation <g2-preparation>
```

Require all G2 preparation checks pass.

## I3. Pre-reveal record

Before any reveal command, persist a pre-reveal record containing:

- exact code/image/numerical provenance;
- Stage 8 semantic/closure/verification/promotion IDs;
- experiment ID;
- OOF semantic/closure/verification IDs;
- F2 promotion ID;
- G1 selection ID;
- all holdout question IDs;
- G2 preparation ID;
- expected seal ID;
- selected and retained model IDs;
- final-fit dispositions;
- expected holdout opportunity/coverage counts;
- holdout range;
- statement that no holdout outcome has been opened;
- output paths and SHA-256s of all current authority files.

Review this record manually.

### Mandatory stop

**STOP HERE.**

Do not infer reveal authority from the instruction to execute earlier phases.

The operator must explicitly approve irreversible reveal after seeing the exact G1 selection, questions, G2 seal and preparation verification result.

---

# 14. Phase J — irreversible holdout reveal (separate explicit authorisation)

Only after a fresh explicit operator instruction, re-authenticate all exact authorities and ensure none moved.

Use the exact selection ID and seal ID from the verified pre-reveal record.

The acknowledgement string is the current contract constant:

```text
I_ACKNOWLEDGE_THIS_IRREVERSIBLY_CONSUMES_THE_FROZEN_HOLDOUT
```

Run:

```text
qtrad research baselines confirmatory-g2-reveal \
  --f2-promotion <f2-promotion> \
  --selection <g1-selection> \
  --preparation <g2-preparation> \
  --expected-selection-id <exact-g1-id> \
  --expected-seal-id <exact-seal-id> \
  --acknowledgement I_ACKNOWLEDGE_THIS_IRREVERSIBLY_CONSUMES_THE_FROZEN_HOLDOUT \
  --opened-by <operator> \
  --consumed-by <operator>
```

The command must create the OPENED authority before protected target decoding and finish in CONSUMED on success.

Do not run it twice.

If an error occurs after OPENED:

- do not delete markers;
- do not create a new preparation against the same holdout;
- do not manually reopen/reset the state;
- retain `OPENED_INCOMPLETE` unless the existing recovery contract can truthfully complete the same already-open lifecycle without changing scientific inputs.

Any recovery must preserve the exact original selection, preparation, seal and OPENED identity.

---

# 15. Phase K — independent R2.H terminal verification

Immediately after reveal, run:

```text
qtrad research baselines confirmatory-r2h-verify \
  --f2-promotion <f2-promotion> \
  --selection <g1-selection> \
  --preparation <g2-preparation>
```

Accepted terminal classifications remain the current contract states, including:

```text
VALID_CONSUMED_RESULT
OPENED_INCOMPLETE
INVALID
```

Do not describe R2 as scientifically complete unless R2.H authenticates a valid consumed result.

---

# 16. Phase L — scientific report and repository status update

For a `VALID_CONSUMED_RESULT`, write one decision-grade R2 report before undertaking R3 or changing the models.

## 16.1 Report exactly what was tested

State prominently:

```text
source: IBKR_HISTORICAL_RESEARCH
price basis: historical MIDPOINT OHLC
availability: declared provider-history delay
primary horizon: 15 minutes
six fixed targets / three fixed groups
```

The result does **not** establish:

- native IG predictability;
- live IBKR predictability;
- executable bid/ask performance;
- post-cost profitability;
- portfolio performance; or
- production readiness.

## 16.2 Keep OOF and holdout separate

Report separately:

- complete F2 OOF findings and selection rationale;
- G1 selection/questions;
- locked holdout question results;
- holdout coverage/support;
- stability/concentration diagnostics;
- positive/negative/inconclusive terminal conclusions; and
- any failed configurations.

A negative or inconclusive valid result is a successful completion of the scientific protocol.

Do not reinterpret thresholds after seeing the holdout.

## 16.3 Persist exact authority chain

Record:

```text
Stage 6 result / closure / verification IDs
Stage 7 semantic / closure / verification IDs
Stage 8 semantic / closure / verification / promotion IDs
R2 experiment ID
feature dataset/manifest IDs
OOF semantic / closure / verification IDs
F2 promotion ID
G1 selection ID
G2 preparation and seal IDs
OPENED / CONSUMED marker IDs
R2.H terminal verification ID/status
exact execution provenance
```

Retain raw scientific artefacts outside Git. Commit only sanitised identities, dispositions, summary metrics and documentation appropriate to repository policy.

Update `PLAN.md`, `docs/STATUS.md` and active research docs to mark R2 research milestone complete only after this terminal verification.

---

# 17. Failure and fallback matrix

| Failure point | Meaning | Required action |
|---|---|---|
| Current H4 Stage 7 v3 authority missing/unreadable | retained current source unavailable | stop; propose fresh acquisition; no legacy-reader resurrection |
| Stage 7 auth fails | source evidence changed/not accepted | stop; investigate exact evidence; no Stage 8 |
| Frozen Stage 8 config arithmetic cannot satisfy ≥4w holdout + 3×2w folds + ≥6w training | source range insufficient under fixed profile | stop; fresh acquisition decision |
| Stage 8 nonqualifying due source gaps/session/rows/duration | data insufficient | retain nonqualifying result; do not tune dates to pass; operator decides acquisition |
| Stage 8 nonqualifying due proven implementation/config bug | software/science contract defect | reviewed fix; new frozen candidate; no model run until resolved |
| F2 build/verify invalid | R2 evidence invalid | stop; diagnose; no F2 promotion or G1 |
| F2 valid but predictive result weak/negative | scientific result, not software failure | continue frozen G1/G2 protocol; no tuning |
| G1 verify invalid | selection authority invalid | stop before G2 |
| G2 preparation invalid | unopened preparation invalid | no reveal; fix only under unchanged F2/G1 or create a new preparation as contract permits before opening |
| Reveal fails before OPENED marker | holdout not opened if verifier proves marker absent | diagnose under contract; do not guess state |
| Reveal fails after OPENED | holdout consumed scientifically even if evaluation incomplete | preserve markers; no reset/reveal; R2.H must classify `OPENED_INCOMPLETE` unless same-lifecycle recovery is explicitly valid |
| R2.H `INVALID` | terminal evidence not trustworthy | retain invalid result; do not claim R2 conclusion |

---

# 18. Fresh historical acquisition fallback

Fresh provider acquisition is **not** part of the default path.

It becomes eligible only if Phase A/C establishes that the retained current Stage 7 authority cannot support the frozen source-independent confirmatory requirements.

Before any provider call, obtain explicit operator authorisation for a new historical acquisition.

The acquisition design must remain model-outcome-blind:

- no date selection based on F2 or holdout performance;
- no contract substitution based on model results;
- use the fixed six candidates and reviewed context universe;
- use current Stage 6 → Stage 7 → Stage 8 contracts only;
- acquire enough extra history to satisfy the deterministic range/fold/holdout formula with margin;
- independently verify each boundary once; and
- restart this execution plan at Phase B with the new Stage 7 authority.

If acquisition happens after any F2 model output from a different source candidate has been viewed, document that the new data window was selected solely to satisfy predeclared coverage/duration requirements and not to improve observed model performance.

---

# 19. Parallel native-data track

Continue normal IG `capture-v4` and IBKR native collection throughout this work.

Do not mix them into `R2-IBKR-HISTORICAL`.

A later `R2-IG-NATIVE` or `R2-IBKR-NATIVE` decision-grade run requires its own independently qualifying source-specific foundation and untouched holdout. The IBKR-historical holdout result cannot authorise a native-source conclusion.

---

# 20. Agent execution discipline

The executing agent must return after each consequential phase with only the decision-relevant evidence, not raw logs.

Suggested checkpoints:

```text
CHECKPOINT A — current Stage 7 authority authenticated
CHECKPOINT B — foundation configuration frozen
CHECKPOINT C — Stage 8 verified and qualifying
CHECKPOINT D — Stage 8 confirmatory promotion authenticated
CHECKPOINT E — experiment/features frozen and verified
CHECKPOINT F — F2 OOF verified + receipt authenticated
CHECKPOINT G — F2 promoted + G1 frozen/verified
CHECKPOINT H — G2 prepared/verified, HOLDOUT STILL UNOPENED
STOP / explicit reveal authorisation required
CHECKPOINT I — reveal terminal state
CHECKPOINT J — R2.H result and scientific conclusion
```

At each checkpoint report:

- status;
- exact head/provenance;
- current semantic/closure/verification/promotion IDs;
- required gate values and pass/fail;
- material uncertainty/blocker;
- next authorised action.

Do not paste long logs or datasets.

The agent must not “helpfully”:

- relax a threshold;
- trim a troublesome market interval after observing readiness;
- remove a weak target;
- change feature eligibility after F2;
- add a model after F2;
- omit a negative comparison;
- rerun a consumed holdout;
- use exceptional deep-audit commands as routine validation; or
- reacquire provider data without explicit operator authority.

---

# 21. Definition of R2 scientific completion

R2 is scientifically complete only when one frozen source-specific experiment reaches:

```text
qualifying verified Stage 8 foundation
+ authenticated Stage 8 confirmatory promotion
+ valid confirmatory F2 OOF
+ authenticated R2 OOF verification receipt
+ F2 promotion
+ independently verified G1 freeze
+ independently verified unopened G2 preparation
+ irreversible marker-first holdout reveal
+ CONSUMED terminal state
+ R2.H VALID_CONSUMED_RESULT
+ written positive / negative / inconclusive source-specific conclusion
```

Anything earlier is valid implementation, readiness, OOF-selection or unopened-holdout evidence, but not the completed R2 scientific result.
