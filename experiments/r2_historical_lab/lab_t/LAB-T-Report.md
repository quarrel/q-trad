# LAB-T Report

## STATUS

COMPLETE — `EXPLORATORY_POST_HOC_ONLY`.

The corrected retained run completed successfully in 5m17s. No nonlinear model qualified; the former consumed holdout was not accessed.

## BASE SHA / BRANCH / WORKTREE

- Authorised base: `f31cf4731fc233726f45f67f54064c40965d01d7`
- Branch: `agent/r2-lab-tabular`
- Final clean head: `a47e65f75ce4d142c20f3b7dd36b1f649418663b`
- Worktree: `/workspace/.worktrees/r2-lab-tabular`

## INPUT LAB MANIFEST

- Path: `/workspace/tmp/qtrad-r2-lab/LAB-0/lab-manifest.json`
- SHA-256: `462e40fa84038156b16c68bde4b68d574ab7862c680657ebd1a0035b39bf0072`
- Source class: `IBKR_HISTORICAL_RESEARCH`
- CORE_6 support reproduced exactly: `239,535`
- ZERO_RETURN MSE: `2.840458667132031e-06`
- LAB-0 ordering authenticated: ZERO < POOLED < LOCAL

## CONFIGURATIONS ATTEMPTED

Seven fitted configurations plus direct ZERO_RETURN, independently evaluated on CORE_6 and ALL_20:

- P0 pooled Ridge
- P0 histogram boosting: fixed and conservative
- P0 MLP: widths 16 and 32, seed 1701
- Secondary P1 histogram fixed
- Secondary P1 MLP width 32

Total: 16 aggregate pre-holdout rows. All fits completed; no failed fit was converted into a zero forecast.

## PRE-HOLDOUT RESULTS

| Universe | Model | Skill vs zero | Positive blocks | Positive instruments |
|---|---:|---:|---:|---:|
| CORE_6 | Ridge P0 | -0.000665631 | 1/3 | 0/6 |
| CORE_6 | Histogram fixed P0 | -0.00451396 | 0/3 | 0/6 |
| CORE_6 | Histogram conservative P0 | -0.00452656 | 0/3 | 0/6 |
| CORE_6 | Histogram fixed P1 | -0.00303180 | 0/3 | 1/6 |
| CORE_6 | MLP width 16 P0 | -1504.27 | 0/3 | 0/6 |
| CORE_6 | MLP width 32 P0 | -1188.62 | 0/3 | 0/6 |
| CORE_6 | MLP width 32 P1 | -1167.06 | 0/3 | 0/6 |
| ALL_20 | Ridge P0 | -0.00000189013 | 1/3 | 4/20 |
| ALL_20 | Histogram fixed P0 | -0.00322144 | 0/3 | 2/20 |
| ALL_20 | Histogram conservative P0 | -0.00507412 | 0/3 | 3/20 |
| ALL_20 | Histogram fixed P1 | -0.00222061 | 1/3 | 3/20 |
| ALL_20 | MLP width 16 P0 | -310.059 | 0/3 | 0/20 |
| ALL_20 | MLP width 32 P0 | -597.382 | 0/3 | 0/20 |
| ALL_20 | MLP width 32 P1 | -319.212 | 0/3 | 0/20 |

Coverage was 1.0 throughout. Detailed delta MSE, calibration, Spearman and concentration statistics are in `model-results.parquet`.

## FORMER-HOLDOUT FINALIST RESULTS

Not evaluated. No nonlinear configuration passed the pre-holdout advancement criteria, so the finalist freeze was empty and `terminal_accessed=false`.

## TOP FINDINGS

- Simple tabular nonlinearity did not improve on ZERO_RETURN in either universe.
- The closest fitted comparator was ALL_20 pooled Ridge, but its skill remained slightly negative.
- P1 improved histogram boosting relative to its P0 counterpart, but remained negative and concentrated: only one positive block, three positive instruments, best-instrument share `0.933`, best-period share `1.0`.
- Permutation rankings repeatedly surfaced availability counts, recent range/absolute-return, time-of-day, and P1 leave-one-out group features. These are weak exploratory hints only; their models failed the aggregate screen.

## NEGATIVE OR FAILED FINDINGS

- Both MLP variants were numerically ineffective at the specified small-model settings, with errors hundreds to thousands of times worse than zero.
- The first retained attempt at `/workspace/tmp/qtrad-r2-lab/LAB-T` was rejected because timestamp-derived membership produced `239,655` CORE_6 rows instead of `239,535`. It was never used for selection; its directory was removed after the failure, configuration count, terminal-access state, and original output hashes were compacted into `failed-attempts.jsonl`.
- The owning correction now authenticates retained target-ID memberships and fails before register creation if CORE_6 support or zero MSE differs from LAB-0.
- The former holdout was never accessed in either attempt.

## FILES CHANGED

- `experiments/r2_historical_lab/tabular.py`
- `experiments/r2_historical_lab/tabular-config.json`
- `experiments/r2_historical_lab/LAB-T-REMEDIATION.md`
- `tests/experiments/test_r2_lab_tabular.py`
- `experiments/r2_historical_lab/__init__.py`

## OUTPUT DIRECTORY

`/workspace/tmp/qtrad-r2-lab/LAB-T-rerun-1`

Key outputs:

- `model-results.parquet`: `cd250ba5…43afc0`
- `feature-importance.parquet`: `603524f8…902c0`
- `run-register.jsonl`: `c7630ecf…2269da`
- `result.md`: `fda32b06…3afc0`
- Empty finalist freeze: `0448b782…8cf6b`
- `run-summary.json`: `536f7bd7…bd248`
- `failed-attempts.jsonl`: `13ecb869…f366c1`

## FOCUSED CHECKS

- Ruff check: passed
- Ruff formatting: passed
- Focused pytest: `5 passed`
- Pyright: `0 errors, 0 warnings`
- Exact production smoke: passed with support `239,535`, authenticated zero MSE, all deliverables, and no terminal access
- Corrected retained run: exit 0
- Full `ops/dev/verify.sh`: intentionally not run; no production contracts or schemas changed

## RECOMMENDATION FOR LAB-Z

Do not nominate histogram boosting or the tested MLP design for integration. Retain ZERO_RETURN as the governing comparator and treat the apparent availability/range/group-feature rankings only as low-priority hypotheses for a future untouched experiment.

The MLP result supports redesigning target/output scaling and optimisation before any future neural comparison, not further tuning on these consumed data.
