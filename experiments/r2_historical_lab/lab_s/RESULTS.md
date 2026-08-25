# LAB-S statistical workstream result

Status: COMPLETE
Evidence label: EXPLORATORY_POST_HOC_ONLY
Source class: IBKR_HISTORICAL_RESEARCH

LAB-S tested whether the original Ridge failure was explained by pooling degree, target scale,
forecast calibration, or stale training history. It used the original causal 15-minute P0 rows,
the three pre-holdout development blocks for selection, and direct ZERO_RETURN comparisons throughout.

## Development selection

Twenty-two unique one-factor configurations were attempted with no failures. CORE_6 support was
239535 and ALL_20 support was 771140; coverage was 1.0 throughout.

Both universes nominated the fully pooled, raw-return, raw-forecast, expanding-history configuration
for every factor:

| universe | delta MSE versus zero | skill versus zero | positive blocks | positive instruments | slope | Spearman |
|---|---:|---:|---:|---:|---:|---:|
| ALL_20 | -1.822904030e-10 | 8.337606938e-05 | 1/3 | 4/20 | 0.6357357379 | 0.01573064092 |
| CORE_6 | 1.505120833e-09 | -0.0005298865464 | 1/3 | 0/6 | 0.1856106642 | 0.0005923775455 |

The ALL_20 aggregate improvement was extremely small and concentrated in one block; its
best-period contribution was 1.0 and best-instrument contribution was 0.6348. It is not robust
development evidence.

Group pooling, local fitting, and every fixed hierarchical penalty were worse than full pooling.
Causal volatility standardisation worsened direct MSE in both universes. Affine and non-negative
slope-only calibration also worsened MSE. The fixed rolling and exponential-decay policies did not
beat expanding history on aggregate MSE, although ALL_20 exponential decay was almost neutral and
positive in two of three blocks.

## Former consumed holdout

Only the two frozen finalists were evaluated, once, as an explicitly post-hoc external development
block:

| universe | support | delta MSE versus zero | skill versus zero | positive blocks | positive instruments | slope | Spearman |
|---|---:|---:|---:|---:|---:|---:|---:|
| ALL_20 | 655424 | 1.563834111e-09 | -0.0008588639044 | 0/1 | 3/20 | -0.3661636733 | 0.009309430953 |
| CORE_6 | 205145 | 1.639154624e-09 | -0.0007775188085 | 0/1 | 0/6 | -0.3950094771 | 0.005508775987 |

Both finalists lost directly to ZERO_RETURN and their calibration slopes changed sign. Positive rank
correlation therefore did not translate into stable return magnitude or lower MSE.

## Interpretation and LAB-Z recommendation

The tested target scaling, calibration, and recency choices do not explain the Ridge failure.
Greater pooling helped materially relative to local fitting, but the small ALL_20 development edge
was temporally and cross-sectionally fragile and failed on the post-hoc external block.

Do not nominate a Ridge model for active-programme integration. If a future untouched native or
future-data experiment carries one LAB-S hypothesis, freeze a narrow test of weak fully pooled
cross-sectional rank information separately from magnitude calibration, with ZERO_RETURN retained as
the direct benchmark. Treat full pooling as a control and the sign stability of calibration as an
explicit rejection gate.

No second-holdout, confirmation, promotion, native-execution, or decision-grade conclusion is claimed.
