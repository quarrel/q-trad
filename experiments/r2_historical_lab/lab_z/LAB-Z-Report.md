# LAB-Z synthesis report

> **EXPLORATORY_POST_HOC_ONLY**
>
> This is hypothesis-generation and model-development evidence from scientifically consumed
> historical IBKR input. It is not a second holdout, confirmation, promotion, decision-grade
> conclusion, or production selection.

## Status

Complete. No configuration met the promotion standard, and the completed R2 conclusion was not
changed.

## Base SHA / branch / worktree

- Base SHA: f31cf4731fc233726f45f67f54064c40965d01d7
- Branch: agent/r2-lab-synthesis
- LAB-Z implementation commit: 95d6af2277c2b94b98b8ff382fd717564498f2e4
- Worktree: /workspace/.worktrees/r2-lab-synthesis
- Source class: IBKR_HISTORICAL_RESEARCH

## Input LAB manifest

- Path: /workspace/tmp/qtrad-r2-lab/LAB-0/lab-manifest.json
- SHA-256: 462e40fa84038156b16c68bde4b68d574ab7862c680657ebd1a0035b39bf0072
- Baseline support: 239,535
- ZERO_RETURN MSE: 0.0000028404586671320294
- POOLED_LOCAL_RIDGE MSE: 0.000002841663414474555
- LOCAL_RIDGE MSE: 0.0000028481068080631273
- Ordering: ZERO < POOLED < LOCAL

LAB-Z authenticated and reused LAB-0's compact manifest. It did not replay Stage 7/R2, reacquire
data, mutate retained evidence, or newly access the former consumed holdout.

## Configurations attempted

- LAB-H: 12 model attempts plus 156 cadence screens.
- LAB-U: 12 development configurations and 3 terminal finalists.
- LAB-S: 22 development configurations and 2 terminal finalists.
- LAB-T: 7 fitted configurations plus ZERO_RETURN across two universes.
- LAB-L: 12 configurations across CORE_6 and ALL_20.
- LAB-Z: 2 bounded combinations and 1 frozen finalist.

## Pre-holdout results

LAB-Z formed two combinations, below the twelve-configuration limit. Both retained the 15-minute
target, 60-second cadence, P0 features, raw-return/raw-forecast formulation, expanding training, and
fully pooled Ridge selected from the independent workstreams.

| Rank | Universe | Direct delta MSE vs zero | Skill vs zero | Positive blocks | Positive instruments | Calibration slope | Spearman | Coverage |
| ---: | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| 1 | ALL_20 | -1.82290403e-10 | +0.0000833761 | 1/3 | 4/20 | 0.635736 | 0.015731 | 1.0 |
| 2 | CORE_6 | +1.50512083e-9 | -0.000529887 | 1/3 | 0/6 | 0.185611 | 0.000592 | 1.0 |

The ALL_20 result failed for insufficient chronological breadth, period concentration of 1.0, and
failure to survive the cadence/non-overlap screen. CORE_6 additionally failed for non-positive skill
and insufficient instrument breadth. No nonlinear or sequence challenger was included because LAB-T
and LAB-L nominated none.

## Former-holdout finalist results

LAB-Z froze only the ALL_20 configuration and reused its exact already-frozen LAB-S terminal result,
without new terminal access:

- Support: 655,424
- Direct delta MSE versus zero: +1.56383411e-9
- Skill versus zero: -0.000858864
- Positive chronological blocks: 0/1
- Positive instruments: 3/20
- Calibration slope: -0.366164
- Spearman correlation: 0.009309
- Forecast coverage: 1.0

This is descriptive post-hoc development evidence only. It is not confirmation.

## Top findings

1. No configuration satisfied the stated promotion standard.
2. Fully pooled Ridge over ALL_20 was the only bounded hypothesis with positive aggregate
   pre-holdout direct skill, but the effect was tiny, present in only one block, and temporally
   concentrated.
3. Wider pooling may contain weak cross-sectional rank information, but it did not produce stable
   direct MSE skill or stable calibration.
4. Added nonlinear and sequence complexity provided no benefit.

## Negative or failed findings

- The reconstructed original six-target, 15-minute path remained worse than ZERO_RETURN.
- Every tested alternative horizon and full phase-offset cadence/non-overlap screen lost.
- Scaling, post-fit calibration, hierarchy, recency, histogram boosting, MLP, and LSTM variants did
  not rescue direct skill.
- LAB-U's first run was rejected for a terminal-maturity leak; only its corrected run was consumed.
- LAB-L's first retained-scale attempt timed out; its bounded reruns produced no qualifying finalist.
- LAB-T and LAB-L did not access the terminal block because they froze no finalists.

## Files changed

- docs/R2_HISTORICAL_EXPLORATORY_FINDINGS.md
- experiments/r2_historical_lab/lab_z/synthesis.py
- experiments/r2_historical_lab/lab_z/synthesis-config.json
- experiments/r2_historical_lab/lab_z/future-experiment-slate.json
- experiments/r2_historical_lab/lab_z/LAB-Z-Report.md
- tests/experiments/r2_historical_lab/test_lab_z.py

## Output directory

/workspace/tmp/qtrad-r2-lab/LAB-Z/

It contains the dimension summary, two-row combination register, finalist freeze, reused terminal
result, future experiment slate, and run summary. The tracked and raw slate files are byte-identical,
with SHA-256 7516d17c61f91121a57b3c9f533769e32d032e707209264d2c07d1d1e47f6207.

## Focused checks

- End-to-end synthesis smoke run: passed.
- Focused pytest: 2 passed.
- Ruff: passed.
- Pyright: 0 errors.

The full repository gate was not run because LAB-Z changed only exploratory code, tests, and
documentation.

## Recommendation for LAB-Z

Do not promote a model from this laboratory. If an untouched native or prospectively collected
future-data experiment is separately authorised, use the compact slate as a guarded falsification
test:

1. freeze ZERO_RETURN as the direct null;
2. freeze LOCAL_RIDGE as the immediate simple linear comparator;
3. test one FULLY_POOLED_LOCAL_RIDGE hypothesis over ALL_20 at the 15-minute horizon and 60-second
   decision cadence;
4. retain P0 features, raw-return targets, expanding chronological training, fold-local median
   standardisation, causal target maturity, and source-active opportunity membership; and
5. reject on non-positive direct skill, fewer than two positive blocks or instruments, concentration
   above 0.8, unstable or non-positive calibration, or failure of the full phase non-overlap cadence
   screen.

Do not carry a nonlinear tabular or sequence challenger forward. This recommendation does not
authorise provider access, reacquisition, source-class equivalence, production selection, or changes
to the completed R2 conclusion.
