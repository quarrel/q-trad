# LAB-S Report

## STATUS

`COMPLETE — EXPLORATORY_POST_HOC_ONLY`.

The run completed with zero failed attempts. No provider calls, retained-artifact mutation, promotion, deployment, or external-state change occurred.

## BASE SHA / BRANCH / WORKTREE

- Programme base: `f31cf4731fc233726f45f67f54064c40965d01d7`
- LAB-0 dependency: `73b35e2be152f9bacb8882dadb09a01e28ad4d37`
- Branch: `agent/r2-lab-statistical`
- Worktree: `/workspace/.worktrees/r2-lab-statistical`
- Final clean commit: `9573e8451319c6cca0de7bc47cc4c4d9a556c8e7`

## INPUT LAB MANIFEST

- Path: `/workspace/tmp/qtrad-r2-lab/LAB-0/lab-manifest.json`
- SHA-256: `462e40fa84038156b16c68bde4b68d574ab7862c680657ebd1a0035b39bf0072`
- Source: `IBKR_HISTORICAL_RESEARCH`
- Evidence label: `EXPLORATORY_POST_HOC_ONLY`
- Finalist-freeze SHA-256: `1c8c02f8b423ba45a7f98a30968b05c1e891fe2f89966a2d7f625cfe1daa1ded`

## CONFIGURATIONS ATTEMPTED

- 22 unique one-factor development configurations
- 2 frozen combined finalists
- 24 registered attempts including terminal evaluations
- 0 failed attempts
- Forecast coverage: `1.0` throughout

## PRE-HOLDOUT RESULTS

Delta MSE below zero is better than `ZERO_RETURN`.

| Universe | Selected configuration | Support | Delta MSE | Skill | Positive blocks | Positive instruments | Slope | Spearman |
|---|---|---:|---:|---:|---:|---:|---:|---:|
| ALL_20 | Fully pooled, raw target/forecast, expanding | 771,140 | -1.8229e-10 | 0.00008338 | 1/3 | 4/20 | 0.6357 | 0.01573 |
| CORE_6 | Fully pooled, raw target/forecast, expanding | 239,535 | 1.5051e-09 | -0.00052989 | 1/3 | 0/6 | 0.1856 | 0.00059 |

Every factor selected the same baseline configuration in both universes.

## FORMER-HOLDOUT FINALIST RESULTS

These are explicitly post-hoc external-development results, not confirmation.

| Universe | Support | Delta MSE | Skill | Positive blocks | Positive instruments | Slope | Spearman |
|---|---:|---:|---:|---:|---:|---:|---:|
| ALL_20 | 655,424 | 1.5638e-09 | -0.00085886 | 0/1 | 3/20 | -0.3662 | 0.00931 |
| CORE_6 | 205,145 | 1.6392e-09 | -0.00077752 | 0/1 | 0/6 | -0.3950 | 0.00551 |

Both finalists lost directly to `ZERO_RETURN`, with calibration slopes changing sign.

## TOP FINDINGS

- Greater pooling materially improved Ridge relative to group, local, and hierarchical fitting.
- ALL_20 showed a tiny positive aggregate development result, but it was confined to one block and four instruments.
- ALL_20 retained weak positive rank correlation externally, while its calibration slope became negative and MSE lost to zero. Rank association did not translate into stable return magnitude.
- The bounded hierarchical solver successfully replaced the retained-scale dense design without changing its mathematical contract.

## NEGATIVE OR FAILED FINDINGS

- CORE_6 did not beat zero under any nominated factor.
- All hierarchical penalty ratios behaved approximately like the unsuccessful local model.
- Causal volatility standardisation worsened MSE in both universes.
- Affine and non-negative slope-only calibration worsened MSE.
- Rolling and exponentially decayed histories did not beat expanding history on aggregate MSE.
- The small ALL_20 development edge failed on the former consumed holdout and was temporally and cross-sectionally fragile.

## FILES CHANGED

- `experiments/r2_historical_lab/statistical.py`
- `experiments/r2_historical_lab/statistical-config.json`
- `experiments/r2_historical_lab/statistical-result.md`
- `tests/experiments/test_r2_historical_lab_statistical.py`

## OUTPUT DIRECTORY

- `/workspace/tmp/qtrad-r2-lab/LAB-S/`
- `result.md`
- `one-factor-results.parquet`
- `combined-finalists.parquet`
- `run-register.jsonl`
- `finalist-freeze.json`
- `development-state.json`
- Durable development and terminal logs

## FOCUSED CHECKS

- Ruff: passed
- Pyright: 0 errors
- Focused pytest: 7 passed
- Fresh package smoke: passed
- Development run: exit 0 in 6m41s
- Terminal run: exit 0 in 16.1s
- CORE_6 support: exactly 239,535
- ALL_20 support: 771,140
- Register: 24 attempts, zero failures
- Full `ops/dev/verify.sh` intentionally not run

## RECOMMENDATION FOR LAB-Z

Do not nominate a Ridge model for active-programme integration.

The only hypothesis worth carrying into a future untouched native or future-data experiment is a narrowly frozen test of weak fully pooled cross-sectional rank information, separated from magnitude calibration. Retain `ZERO_RETURN` as the direct comparator and make calibration-sign stability an explicit rejection gate.

This is not a second holdout, confirmation, promotion, native-execution result, or decision-grade conclusion.
