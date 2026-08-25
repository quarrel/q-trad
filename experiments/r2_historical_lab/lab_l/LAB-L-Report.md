STATUS

COMPLETE — `EXPLORATORY_POST_HOC_ONLY`.

Both CORE_6 and conditional ALL_20 completed. No model passed screening, no finalist was frozen, and the former consumed holdout was not accessed.

BASE SHA / BRANCH / WORKTREE

- Base: `f31cf4731fc233726f45f67f54064c40965d01d7`
- Branch: `agent/r2-lab-sequence`
- Head: `d224d49bc94d4f53261d036e4395a41cf7b5b004`
- Worktree: `/workspace/.worktrees/r2-lab-sequence`
- Worktree clean
- Reusable commits:
  - `e43d8f5` — controlled temporal sequence lab
  - `21aae53` — bounded retained runtime
  - `d224d49` — clarified post-completion output retention

INPUT LAB MANIFEST

- `/workspace/tmp/qtrad-r2-lab/LAB-0/lab-manifest.json`
- SHA-256: `462e40fa84038156b16c68bde4b68d574ab7862c680657ebd1a0035b39bf0072`
- Source class: `IBKR_HISTORICAL_RESEARCH`
- LAB-0 baseline reproduced required support, metrics and `ZERO < POOLED < LOCAL` ordering.

CONFIGURATIONS ATTEMPTED

Six fixed designs per scope:

- Pooled engineered Ridge
- Engineered-feature MLP
- LSTM lookback 15, hidden 8
- LSTM lookback 15, hidden 16
- LSTM lookback 60, hidden 8
- LSTM lookback 60, hidden 16

Retained executions:

- Attempt 1: four completed; fifth timed out; sixth did not start.
- Revised CORE_6: six completed.
- ALL_20: six completed.
- Successful registers remain in their final output directories. The failed register and driver log were intentionally deleted after their pre-deletion hashes and attempt summary were recorded in `LAB-L-EXECUTION.md`.

PRE-HOLDOUT RESULTS

All deltas are model MSE minus ZERO_RETURN MSE.

| Scope/model | MSE | Delta | Skill | Blocks | Instruments |
|---|---:|---:|---:|---:|---:|
| CORE_6 ZERO_RETURN | 2.8424817e-06 | — | — | — | — |
| Ridge | 2.8697266e-06 | +2.72449e-08 | -0.00958 | 0/3 | 0/6 |
| MLP | 2.9496554e-06 | +1.07174e-07 | -0.03770 | 0/3 | 0/6 |
| LSTM 15/8 | 2.9022023e-06 | +5.97206e-08 | -0.02101 | 0/3 | 0/6 |
| LSTM 15/16 | 2.8739474e-06 | +3.14657e-08 | -0.01107 | 0/3 | 0/6 |
| LSTM 60/8 | 2.8989052e-06 | +5.64235e-08 | -0.01985 | 0/3 | 0/6 |
| LSTM 60/16 | 2.8724345e-06 | +2.99528e-08 | -0.01054 | 0/3 | 0/6 |
| ALL_20 ZERO_RETURN | 2.1901705e-06 | — | — | — | — |
| Ridge | 2.2383254e-06 | +4.81550e-08 | -0.02199 | 0/3 | 0/20 |
| MLP | 2.3967617e-06 | +2.06591e-07 | -0.09433 | 0/3 | 0/20 |
| LSTM 15/8 | 2.3483898e-06 | +1.58219e-07 | -0.07224 | 0/3 | 0/20 |
| LSTM 15/16 | 2.2482432e-06 | +5.80727e-08 | -0.02652 | 0/3 | 0/20 |
| LSTM 60/8 | 2.3456008e-06 | +1.55430e-07 | -0.07097 | 0/3 | 0/20 |
| LSTM 60/16 | 2.2472827e-06 | +5.71122e-08 | -0.02608 | 0/3 | 0/20 |

Coverage was 1.0 throughout. Spearman correlations were approximately zero. Calibration slopes were weak, and best-instrument/best-period positive contributions were zero.

FORMER-HOLDOUT FINALIST RESULTS

None. No sequence model met the development screen, so no finalist freeze was created and terminal outcomes were never loaded.

TOP FINDINGS

- Pooled Ridge was the best tested model in both scopes, but still lost directly to ZERO_RETURN.
- The closest sequence design was the 60-minute, 16-unit LSTM; it remained worse than both Ridge and zero.
- Expanding from six to twenty instruments strengthened rather than reversed the negative result.
- Neural validation losses generally decreased through epoch four, but this did not translate into positive chronological development performance.

NEGATIVE OR FAILED FINDINGS

- No evidence of useful temporal memory.
- No evidence of useful generic nonlinearity: the MLP was worst in both scopes.
- Every model lost in every chronological block and every instrument.
- The first retained run timed out on the original oversized CPU training plan. Its truthful state and pre-deletion hashes were recorded, the owning runtime bounds were corrected and regression-tested, and the incomplete output was then cleaned after successful replacement runs.
- These are exploratory post-hoc findings, not confirmation or a decision-grade conclusion.

FILES CHANGED

- [sequence.py](/workspace/.worktrees/r2-lab-sequence/experiments/r2_historical_lab/sequence.py)
- [sequence-configurations.json](/workspace/.worktrees/r2-lab-sequence/experiments/r2_historical_lab/sequence-configurations.json)
- [README.md](/workspace/.worktrees/r2-lab-sequence/experiments/r2_historical_lab/README.md)
- [test_r2_historical_sequence.py](/workspace/.worktrees/r2-lab-sequence/tests/experiments/test_r2_historical_sequence.py)

OUTPUT DIRECTORY

- CORE_6: `/workspace/tmp/qtrad-r2-lab/LAB-L-attempt-2/`
- ALL_20: `/workspace/tmp/qtrad-r2-lab/LAB-L-ALL20/`
- Durable execution and cleanup record: `/workspace/tmp/qtrad-r2-lab/LAB-L-EXECUTION.md`

Each successful directory contains the configuration, Parquet results, learning curves, Markdown summary and JSONL register.

FOCUSED CHECKS

- Ruff formatting/check: passed
- Pyright: 0 errors/warnings
- Focused LAB-0/LAB-L tests: 13 passed
- CORE_6 all-model smoke: passed
- ALL_20 all-model smoke: passed
- CORE_6 retained execution: exit 0
- ALL_20 retained execution: exit 0
- Full `ops/dev/verify.sh`: intentionally not run; no production source, schema or durable active contract changed.

RECOMMENDATION FOR LAB-Z

Do not nominate this LSTM or engineered MLP design for integration. Preserve the negative result and avoid post-hoc tuning of these architectures. A future untouched-data experiment should pursue a materially different hypothesis or representation; the tested raw causal sequences provide no basis for further temporal-memory investigation.
