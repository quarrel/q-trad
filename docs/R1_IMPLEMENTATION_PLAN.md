# R1 implementation design — causal multi-asset research foundation

## Purpose

Implement the minimum trustworthy research foundation needed to determine whether short-horizon multi-asset forecasting is worth pursuing.

R1 must prevent look-ahead, preserve missing and revised data honestly, support chronological out-of-fold evaluation, and produce model-independent artefacts that R2 can consume.

R1 is not a production platform. It must not add robustness or abstraction that is unnecessary to obtain a trustworthy first forecasting result.

The implementation should optimise for:

1. causal correctness;
2. deterministic reproducibility;
3. a short 15-minute vertical path;
4. reuse of existing repository infrastructure; and
5. easy replacement of experimental research schemas before the first decision-grade result.

## Required outcome

The completed flow is:

```text
canonical bar events
    ↓
causal observation dataset retaining every revision
    ├───────────────→ as-of feature panel
    └───────────────→ frozen realised targets
                            ↓
                   chronological fold definitions
                            ↓
               model-independent OOF forecast artefact
```

Feature panels and realised targets are sibling products of the observation dataset. Targets must not be derived from the feature panel because features and labels use different revision cut-offs.

## Scope

R1 must implement:

* native `QUOTE_DERIVED` data only;
* all revisions of every selected bar;
* explicit receive, persistence and global-position lineage;
* an as-of aligned feature panel;
* explicit causal missingness;
* exact completed-bar midpoint targets at 5, 15, 30 and 60 minutes;
* a complete first vertical path at 15 minutes;
* a declared target-revision freeze policy;
* upper and lower path excursions;
* deterministic expanding chronological folds;
* target-maturity checks, purging, embargo and locked-holdout isolation;
* a model-independent point-forecast contract;
* a deterministic zero-return OOF probe;
* immutable and independently verifiable research artefacts; and
* real-data coverage and disposition evidence without an effectiveness claim.

## Non-goals

R1 must not implement:

* Ridge or another trained forecasting model;
* a feature library;
* a cost model;
* an optimiser;
* portfolio construction;
* a session-calendar integration;
* external historical-data adapters;
* collector changes;
* a new database schema unless unavoidable;
* continuous or live shadow operation;
* probabilistic forecasts or calibration;
* generic model-update dependency machinery;
* production compatibility layers for experimental research schemas; or
* a new general-purpose artefact framework.

The existing `bars-v2` format and retained strategy proof must remain readable. They do not need to become the source format for new R1 research work.

---

# 1. Existing infrastructure to reuse

The repository already has content-authenticated Parquet research storage with:

* exclusive immutable writes;
* semantic partition hashes;
* per-file byte hashes;
* canonical manifest hashes;
* bounded manifest decoding;
* safe relative-path validation;
* semantic replay verification; and
* existing `bars-v2` compatibility.

R1 must reuse these mechanics.

Extract only the small stable subset needed for a second manifested dataset type:

```text
canonical manifest encoding
semantic dataset hashing
physical file hashing
exclusive file creation
safe path validation
manifest loading
dataset verification
```

Do not create a broad artefact framework, plugin system or storage abstraction hierarchy.

A small composition-oriented helper is sufficient. Existing `ParquetResearchStore` behaviour must remain intact.

---

# 2. Identity model

R1 must distinguish semantic dataset identity from physical manifest identity.

## Semantic dataset identity

`dataset_id` identifies the meaning and contents of a dataset.

It is a SHA-256 over:

* dataset contract and schema version;
* canonical semantic configuration;
* ordered semantic rows;
* ordered source dataset identities; and
* declared selection policies.

It must not include:

* creation time;
* output path;
* Parquet byte representation;
* process/run identity; or
* incidental file partitioning.

Rebuilding identical semantic content from reordered inputs must produce the same `dataset_id`.

## Physical manifest identity

`manifest_id` identifies one immutable physical representation.

It binds:

* `dataset_id`;
* creation time;
* application version and immutable image or commit identity;
* ordered file paths;
* file byte hashes;
* schema metadata;
* source snapshot/import evidence; and
* bounded build evidence.

Two builds may have the same `dataset_id` but different `manifest_id` values.

Verification must check both physical integrity and semantic content.

---

# 3. Causal observation dataset

## Contract

Add a new manifested dataset contract:

```text
qtrad-research-observations-v1
```

Each row represents one canonical bar event revision.

Required fields:

```text
event_id
stream_id
stream_version
event_type

event_time
received_at
persisted_at
global_position

instrument_id
basis
interval_start
interval_end

open
high
low
close
sample_count
revision
provenance
quality

source_provider
source_environment
source_external_id
```

Source prices remain decimal text or another lossless decimal representation in storage.

The dataset must contain every selected canonical bar revision, not only the final projection row.

## Source restrictions

The first R1 path accepts only:

```text
provenance = QUOTE_DERIVED
```

Historical candles must not be silently included.

All 23 captured markets are included in the observation dataset. VIX remains context-only in later foundation configuration.

## Canonical lineage

The export must join each bar revision to its canonical event and preserve:

* canonical event identity;
* receive time;
* persistence time;
* stream version; and
* canonical global position.

A projection row without exactly one matching canonical event must fail the export.

## Availability basis

The observation dataset stores both `received_at` and `persisted_at`.

The foundation configuration declares which timestamp defines availability:

```text
availability_basis = "persisted_at"
```

`persisted_at` is the initial decision-grade default because downstream research consumes persisted canonical events.

No derived `available_at` field should obscure its source. Consumers calculate:

```text
availability_time =
    persisted_at when availability_basis == "persisted_at"
    received_at  when availability_basis == "received_at"
```

Naive or non-UTC timestamps fail closed.

## Deterministic ordering

Canonical semantic ordering is:

```text
instrument_id
basis
interval_start
source_provider
source_environment
source_external_id
stream_version
global_position
```

Reversing database or input iteration order must not change semantic identity.

## Fail-closed source handling

For a configured instrument, basis and bar interval, the as-of builder must resolve at most one eligible source listing.

Overlapping eligible source listings or otherwise ambiguous source identity must fail rather than silently selecting one.

---

# 4. Foundation configuration

Add one strict configuration contract:

```text
qtrad-research-foundation-config-v1
```

The configuration contains:

```text
name
schema_version

observation_dataset_id
ordered_instruments
instrument_roles

range_start
range_end
grid_resolution

availability_basis
feature_lag_policy
feature_lag_calibration_range
feature_lag_percentile
feature_lag_safety_margin
selected_feature_lag

target_horizons
primary_vertical_horizon
target_revision_delay

required_feature_bases
target_basis

fold_policy
holdout_range
embargo
minimum_training_duration
minimum_validation_duration
```

## Instrument roles

Each of the 23 instruments has exactly one role:

```text
TARGET
CONTEXT
```

VIX must be `CONTEXT`.

A context instrument may appear in feature panels but receives no tradable target role.

Duplicate instruments, missing roles, unknown instruments and role changes outside the selected observation universe fail closed.

## Time range

The range is explicit, UTC and half-open:

```text
[range_start, range_end)
```

The one-minute grid is explicit.

## Separate time concepts

The implementation must preserve distinct concepts:

```text
decision_time
feature_data_asof
latest_feature_bar_end
target_start_time
target_end_time
target_freeze_at
training_cutoff
```

Do not conflate decision delay with feature-bar age.

## Feature-lag selection

The implementation must measure persisted native bar-availability delays over an explicit calibration range.

Delay is:

```text
availability_time - interval_end
```

The selected lag is calculated from declared policy values:

```text
selected_feature_lag =
    ceil_to_whole_grid_unit(
        configured_percentile_of_eligible_delays
        + configured_safety_margin
    )
```

The report must include at least:

* calibration interval;
* eligible and excluded row counts;
* delay percentiles;
* maximum observed delay;
* configured percentile;
* safety margin; and
* selected lag.

The percentile and margin are configuration, not hidden code defaults.

The first real foundation config is created only after this report is produced. Once selected, its numerical lag is stored explicitly and included in configuration identity.

Model outcomes must never influence the lag.

---

# 5. As-of feature panel

## Output contract

Add a manifested long-form panel dataset:

```text
qtrad-research-panel-v1
```

The initial complete path requires MID only:

```text
required_feature_bases = ["MID"]
```

BID and ASK may be added only when a named R2 feature requires them.

Each expected cell is keyed by:

```text
decision_time
instrument_id
basis
```

The panel emits one row for every configured decision time, instrument and required basis.

## Feature cut-off

For each decision time:

```text
feature_data_asof = decision_time - selected_feature_lag
latest_feature_bar_end = feature_data_asof
```

The latest eligible completed one-minute bar is the bar whose:

```text
interval_end == latest_feature_bar_end
```

Any later warm-up feature implementation may declare a wider historical dependency window, but R1 does not build a general feature library.

## As-of revision selection

For a required bar interval, select only observations satisfying:

```text
availability_time <= feature_data_asof
```

Then select the newest available revision by:

```text
highest stream_version
then highest global_position
```

A correction received or persisted after the feature cut-off must not alter the earlier panel row.

## Causal state

The model-visible status is deliberately small:

```text
OBSERVED
MISSING_AS_OF_CUTOFF
```

When observed, the row contains:

```text
selected_event_id
selected_stream_version
selected_global_position
selected_availability_time
bar interval
OHLC
sample_count
quality
```

When missing, all price values are null.

Prices are never forward-filled.

## Retrospective audit disposition

Retrospective diagnosis must be stored separately from the causal status:

```text
EVENTUALLY_OBSERVED_LATE
RECORDED_GAP_KNOWN_BY_CUTOFF
RECORDED_GAP_DETECTED_LATER
SOURCE_NOT_ACTIVE
NO_NATIVE_EVIDENCE
AMBIGUOUS_OR_INVALID_SOURCE
```

This may be a separate audit table or explicitly marked non-feature columns.

It must never be included automatically in model matrices.

A recorded gap is causally known only when:

```text
gap.detected_at <= feature_data_asof
```

A gap detected later remains future audit information.

## Panel identity

The panel `dataset_id` binds:

* observation `dataset_id`;
* foundation configuration identity;
* instrument order;
* role mapping;
* range;
* grid;
* availability basis;
* selected feature lag;
* required bases; and
* canonical panel rows.

---

# 6. Realised targets

## Independent construction

Targets are built directly from the observation dataset and foundation configuration.

They must not use the as-of feature panel as their source.

## Output contract

Add:

```text
qtrad-research-targets-v1
```

Target identity is keyed by:

```text
instrument_id
decision_time
horizon
target_basis
target_revision_policy
```

Only instruments with role `TARGET` receive targets.

The initial target basis is:

```text
MID
```

## Horizon definition

For horizon `H`:

```text
target_start_time = decision_time
target_end_time = decision_time + H
```

The target requires exact completed one-minute closes at those times.

The label is:

```text
log(label_end_close / label_start_close)
```

Both prices must be positive.

The stored prices are named:

```text
label_start_close
label_end_close
```

Do not call them entry or exit prices; executable entry and exit concepts belong to later economic evaluation.

## Target-revision freeze policy

Targets must not silently use the final revision known at dataset-build time.

The foundation configuration declares:

```text
target_revision_delay
```

For each target:

```text
target_freeze_at = target_end_time + target_revision_delay
```

Each required target bar uses the newest revision available by `target_freeze_at`.

Revisions arriving after `target_freeze_at` remain in the observation dataset but do not mutate that target dataset.

There is no hard-coded target delay.

The first real configuration must record whether its delay is:

```text
MEASURED
PROVISIONAL_CONSERVATIVE
```

and retain the evidence used to select it.

## Return disposition

Store a stable return disposition such as:

```text
VALID
MISSING_START
MISSING_END
NON_POSITIVE_START
NON_POSITIVE_END
AMBIGUOUS_SOURCE
UNAVAILABLE_BY_FREEZE
```

A target is eligible for training only when its return disposition is `VALID`.

## Path excursions

Store raw path quantities:

```text
upper_log_excursion
lower_log_excursion
```

These are relative to `label_start_close`.

Do not store favourable/adverse excursions because those depend on a later forecast or position direction.

Excursions require every exact completed midpoint bar across the declared path.

Store a separate excursion disposition:

```text
VALID
INCOMPLETE_PATH
MISSING_START
MISSING_END
AMBIGUOUS_SOURCE
```

A missing interior bar does not invalidate an otherwise valid endpoint return.

## Availability

For R1 training eligibility:

```text
target_available_at = target_freeze_at
```

A missing target remains a disposition at that time. Later observations do not retroactively make it eligible in the same target dataset.

## Numerical representation

Source prices remain `Decimal`.

Derived log returns and excursions may use finite float64 values at the explicit numerical boundary.

NaN and infinity are forbidden.

---

# 7. Chronological fold planner

## Output contract

Add:

```text
qtrad-research-folds-v1
```

Implement expanding walk-forward folds.

Each fold declares:

```text
fold_id
training_start
training_cutoff
validation_start
validation_end
embargo_end
holdout_excluded
```

Membership is explicit and hash-bound.

## Training eligibility

A target row may enter training only when:

```text
decision_time < training_cutoff
target_available_at <= training_cutoff
return_disposition == VALID
```

The target dependency interval must not consume prohibited validation evidence.

For the current fixed-horizon targets, a conservative eligible condition is:

```text
target_end_time <= training_cutoff
target_available_at <= training_cutoff
```

## Validation membership

Validation rows satisfy:

```text
validation_start <= decision_time < validation_end
```

Validation targets may be absent or unavailable at forecast time. Forecast generation uses only features and fold lineage; later evaluation joins outcomes separately.

## Purging and embargo

R1 implements only the dependency checks exercised by current fixed-horizon targets and declared panel construction.

Do not create a generic dependency graph or model-update interval engine.

Training labels whose target dependencies reach into prohibited validation time are purged.

Embargo is a configured time interval between relevant training and validation evidence.

Historical feature data shared across the training/validation boundary is not automatically leakage. Validation features may legitimately look backwards into the training period.

## Holdout

The final holdout range is present in configuration but excluded from:

* fold training;
* fold validation;
* OOF forecast output;
* feature selection;
* model selection;
* calibration; and
* configuration comparison.

The zero probe must not emit holdout forecasts.

## Determinism

Fold IDs and membership hashes must be independent of input ordering.

Fixture evidence must demonstrate at least two chronological folds.

Real native evidence must use the configured minimum training and validation durations. It must not create scientifically meaningless folds merely to satisfy a count requirement.

---

# 8. OOF point-forecast contract

## Output contract

Add:

```text
qtrad-research-forecasts-v1
```

Each forecast contains:

```text
forecast_id

instrument_id
decision_time
horizon

expected_return
return_unit

feature_data_asof
training_cutoff

observation_dataset_id
panel_dataset_id
target_dataset_id
fold_dataset_id

experiment_id
fold_id
model_id
model_contract
```

The return unit is explicit, for example:

```text
LOG_RETURN
```

Forecast consumers must not need to import or load model code.

Later realised outcomes remain separate immutable data and never mutate a forecast row.

## OOF proof

A forecast is OOF only when its fold membership proves that:

* the row belongs to the fold validation interval;
* its training cutoff matches the fold;
* no holdout row is present; and
* its referenced foundation datasets match the fold dataset.

Do not trust a caller-supplied Boolean `is_oof`.

## Deterministic probe

R1 produces one probe model:

```text
model_contract = "DETERMINISTIC_PROBE"
model_id = hash("zero-return-v1")
expected_return = 0.0
```

It emits forecasts for eligible 15-minute validation rows only.

It is not Ridge, not a baseline result and not effectiveness evidence.

Its purpose is to prove:

```text
panel → fold membership → immutable OOF forecasts
```

---

# 9. Foundation bundle

## Bundle contract

Add a thin top-level manifest:

```text
qtrad-research-foundation-bundle-v1
```

It binds child dataset identities:

```text
observation_dataset
panel_dataset
target_dataset
fold_dataset
forecast_dataset
foundation_configuration
availability_delay_report
build_summary
```

The bundle does not duplicate child rows.

Each child remains independently verifiable.

The bundle verifies:

* child manifest integrity;
* expected child contract versions;
* source identity relationships;
* configuration consistency;
* matching instrument universe and range;
* fold references;
* forecast fold membership;
* absence of holdout forecasts; and
* semantic dataset identities.

A byte or metadata change in a child artefact must invalidate the affected child and therefore the bundle.

---

# 10. CLI

Add bounded commands consistent with the existing CLI style.

Suggested shape:

```text
qtrad research observations build
qtrad research observations verify

qtrad research foundation build
qtrad research foundation verify
```

## Observations build

Inputs:

```text
--universe
--start
--end
--snapshot-import-evidence
```

Output:

* observation manifest path;
* observation `dataset_id`;
* manifest identity;
* row and revision counts;
* canonical lineage summary;
* availability-delay report.

## Foundation build

Inputs:

```text
--observations-manifest
--config
```

Output:

* panel dataset;
* target dataset;
* fold dataset;
* deterministic probe forecasts;
* top-level bundle;
* coverage and disposition summaries.

The build should execute the complete 15-minute path first.

The same implementation then supports all configured horizons.

## Verification

Verification performs no database access and loads no model code.

It verifies child artefacts, semantic identities and cross-dataset references.

Existing `research export`, `replay`, `bars-v2` and retained ranking commands remain unchanged unless a small shared manifest helper can be adopted without changing their external behaviour.

---

# 11. Development sequence

R1 should be implemented as five bounded pieces.

## R1.A — causal observation dataset

### Outcome

* Extract the minimal reusable manifested-dataset mechanics.
* Add the observation contract.
* Export every native quote-derived bar revision with canonical event lineage.
* Add semantic and physical identity.
* Produce availability-delay evidence.

### Exit tests

* A late correction is unavailable before its configured availability time.
* `received_at` and `persisted_at` remain distinct.
* A projection revision without exactly one canonical event fails.
* Reversing input order preserves `dataset_id`.
* Rebuilding at another clock time preserves `dataset_id` but changes physical manifest identity.
* File, metadata or semantic-row tampering fails verification.
* Existing `bars-v2` manifests remain readable.

## R1.B — 15-minute causal vertical slice

### Outcome

Implement:

```text
strict foundation config
MID-only as-of panel
15-minute frozen midpoint targets
causal missingness
retrospective audit dispositions
```

Run against focused fixtures, then a verified isolated native snapshot.

### Exit tests

* A correction after feature cut-off cannot alter an earlier panel row.
* A correction before target freeze may alter the target.
* A correction after target freeze cannot alter the target.
* Missing cells are explicit and never forward-filled.
* A later-arriving bar remains `MISSING_AS_OF_CUTOFF` causally and is diagnosed separately as late.
* A gap detected after the cut-off is not exposed as causally known.
* Exact target endpoints are required.
* A missing interior path may leave return valid while excursion is unavailable.
* VIX receives no target rows.
* Input ordering does not change semantic identities.

## R1.C — folds and deterministic OOF probe

### Outcome

* Add expanding chronological fold definitions.
* Enforce target maturity.
* Implement bounded purging and embargo.
* Lock the final holdout.
* Emit deterministic zero-return 15-minute OOF forecasts.

### Exit tests

* No target unavailable at training cutoff enters training.
* No prohibited target dependency crosses into validation evidence.
* Forecasts belong only to their fold validation intervals.
* Every forecast proves its training cutoff and dataset lineage.
* No holdout row is emitted.
* At least two fixture folds are demonstrated.
* Consumers load forecasts without model code.

## R1.D — bounded horizon generalisation

### Outcome

Parameterise the working path to:

```text
5 minutes
15 minutes
30 minutes
60 minutes
```

Add upper and lower excursion outputs and per-horizon coverage summaries.

Do not add new feature families or model abstractions.

### Exit tests

* All four horizon contracts pass the same target-selection rules.
* The 15-minute vertical path remains the primary integrated proof.
* Horizon changes alter configuration and dataset identity.
* Return and excursion dispositions remain independent.
* No NaN or infinite model values are written.

## R1.E — integrated bundle and milestone evidence

### Outcome

* Add the thin foundation bundle.
* Complete CLI wiring.
* Add independent verification.
* Run the full clean project gate.
* Build a real 23-market native bundle.
* Record coverage, missingness, target disposition and fold summaries.
* Update active documentation.

### Exit tests

* Every child artefact verifies independently.
* The top-level bundle verifies all cross-references.
* Tampering with any child invalidates the bundle.
* A real 23-market bundle replays deterministically.
* Real-data folds are emitted only when configured minimum durations are satisfied.
* The locked holdout never appears in OOF data.
* No effectiveness claim is made from the zero probe or short native history.

---

# 12. Dependency order

```text
R1.A
  ↓
R1.B
  ↓
R1.C
  ↓
R1.D
  ↓
R1.E
```

Each piece should leave a complete, tested vertical capability.

Do not implement all schemas first and defer integration until the end.

---

# 13. Domain and adapter boundaries

R1 transformations should remain predominantly pure and deterministic.

## Domain/application

Appropriate responsibilities:

* strict frozen values;
* revision selection;
* feature cut-off calculations;
* panel-cell construction;
* target construction;
* disposition logic;
* fold membership;
* semantic identity payloads; and
* cross-dataset validation.

These components must not import:

* filesystem code;
* Parquet libraries;
* database libraries;
* CLI configuration;
* provider libraries; or
* model frameworks.

## Adapters/runtime

Appropriate responsibilities:

* PostgreSQL event/projection queries;
* joining projection rows to canonical events;
* Parquet encoding and decoding;
* manifest files;
* Pydantic or equivalent strict external decoding;
* CLI composition;
* snapshot evidence loading; and
* reporting.

Polars may be used at the adapter/application orchestration boundary where it materially simplifies table construction. Core causal rules must remain independently testable without Polars or filesystem access.

---

# 14. Required fixture cases

Tests must include at least the following scenarios.

## Observation and revision fixtures

1. Initial bar available before feature cut-off.
2. Correction available before feature cut-off.
3. Correction received before but persisted after feature cut-off.
4. Correction available after feature cut-off.
5. Multiple revisions with reversed input ordering.
6. Duplicate or ambiguous eligible source listings.
7. Missing canonical event lineage.
8. Conflicting global positions or stream versions.

## Panel fixtures

1. Observed exact completed bar.
2. Missing at cut-off but observed later.
3. Recorded gap detected before cut-off.
4. Gap detected after cut-off.
5. Source not active during part of the range.
6. Missing market without forward fill.
7. VIX present as context.
8. Decision and feature cut-off kept distinct.

## Target fixtures

1. Exact start and end closes.
2. Missing start.
3. Missing end.
4. Non-positive price rejection.
5. Correction before target freeze.
6. Correction after target freeze.
7. Complete path excursion.
8. Missing interior bar with valid endpoint return.
9. Ambiguous source.
10. Each configured horizon.

## Fold fixtures

1. Target matures before training cutoff.
2. Target matures after training cutoff.
3. Target dependency crossing validation boundary.
4. Embargo exclusion.
5. Two expanding folds.
6. Locked holdout exclusion.
7. Deterministic membership under reordered inputs.

## Artefact fixtures

1. Same semantic content built at different times.
2. Changed configuration with unchanged rows.
3. Changed row content.
4. Changed child dataset reference.
5. Modified Parquet bytes.
6. Modified manifest metadata.
7. Forecast referencing another fold.
8. Forecast referencing holdout data.

---

# 15. R1 completion criteria

R1 is complete when:

* every selected native bar revision and its canonical availability lineage is retained;
* feature as-of selection cannot see a future revision;
* causal missingness is separate from retrospective diagnosis;
* a deterministic long-form MID panel is reproducible without forward filling;
* target labels use an explicit revision-freeze policy;
* exact completed-bar targets work at 5, 15, 30 and 60 minutes;
* the 15-minute path works end to end;
* endpoint returns and path excursions have separate dispositions;
* chronological folds prove target maturity, bounded purging, embargo and holdout isolation;
* a model-independent deterministic OOF forecast artefact verifies without model code;
* semantic dataset identities remain stable across equivalent rebuilds;
* physical manifests authenticate files, metadata and run evidence;
* a thin foundation bundle binds all source data, configuration, folds and outputs;
* existing retained `bars-v2` evidence remains readable;
* a real 23-market bundle is produced from a verified isolated native snapshot;
* insufficient native history is reported honestly rather than converted into artificial folds; and
* no forecasting, economic or profitability claim is made from the zero probe.

---

# 16. Implementation guidance for Codex

Before modifying code:

1. Read `AGENTS.md`.
2. Read the R1 section of `PLAN.md`.
3. Read `docs/TRADING_RESEARCH.md`.
4. Read the research-contract and evaluation sections of `docs/ARCHITECTURE.md`.
5. Read ADR 0017 and the current `ParquetResearchStore`.
6. Inspect the canonical event and market-bar projection contracts and their tests.

During implementation:

* work one development piece at a time;
* prefer focused pure functions over service classes;
* do not create speculative interfaces for R2;
* do not change the collector;
* do not query or write the live collector database;
* use fixtures and an isolated restored research database;
* keep experimental schemas replaceable;
* preserve en-GB text, strict typing and existing repository conventions;
* run focused tests and static checks during each piece; and
* run the complete clean project gate at each completed R1 piece that changes a schema or artefact boundary.

When implementation details conflict with this plan, preserve the causal and evidence guarantees while choosing the smaller design.
