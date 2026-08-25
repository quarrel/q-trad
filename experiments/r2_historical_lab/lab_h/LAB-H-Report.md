## STATUS

COMPLETE — negative exploratory result. No horizon/cadence variant qualifies as a future candidate. All claims are `EXPLORATORY_POST_HOC_ONLY`, source class `IBKR_HISTORICAL_RESEARCH`.

## BASE SHA / BRANCH / WORKTREE

- Required base: `f31cf4731fc233726f45f67f54064c40965d01d7`
- Branch: `agent/r2-lab-horizons`
- Worktree: `/workspace/.worktrees/r2-lab-horizons`
- Clean head: `b581b699d30d115eca690436b27d9f5dbd6c27c2`
- Commit: `Add exploratory R2 horizon and overlap screen`
- LAB-0 parent: `73b35e2be152f9bacb8882dadb09a01e28ad4d37`

## INPUT LAB MANIFEST

- Path: `/workspace/tmp/qtrad-r2-lab/LAB-0/lab-manifest.json`
- SHA-256: `462e40fa84038156b16c68bde4b68d574ab7862c680657ebd1a0035b39bf0072`
- Authenticated LAB-0 baseline reproduced support `239,535` and ordering `ZERO < POOLED < LOCAL`.

## CONFIGURATIONS ATTEMPTED

- Four horizons: 5, 15, 30 and 60 minutes.
- `LOCAL_RIDGE` and `POOLED_LOCAL_RIDGE`: eight DEV configurations.
- Frozen DEV-only finalists: 30 and 5 minutes.
- Both finalists evaluated at every minute, UTC five-minute phase zero, and every non-overlapping phase.
- Register: 12 successful model attempts; cadence screen: 156 rows.

## PRE-HOLDOUT RESULTS

| Horizon | Model | Zero MSE | Skill vs zero | Positive blocks | Positive instruments | Support |
|---:|---|---:|---:|---:|---:|---:|
| 5m | Local | 9.52163e-7 | -0.001337 | 1/3 | 0/6 | 241,591 |
| 5m | Pooled | 9.52163e-7 | -0.000438 | 1/3 | 0/6 | 241,591 |
| 15m | Local | 2.84046e-6 | -0.002883 | 0/3 | 0/6 | 239,535 |
| 15m | Pooled | 2.84046e-6 | -0.000527 | 1/3 | 0/6 | 239,535 |
| 30m | Local | 5.50714e-6 | -0.005736 | 0/3 | 0/6 | 237,042 |
| 30m | Pooled | 5.50714e-6 | -0.000467 | 1/3 | 1/6 | 237,042 |
| 60m | Local | 1.08723e-5 | -0.012610 | 0/3 | 0/6 | 232,703 |
| 60m | Pooled | 1.08723e-5 | -0.000891 | 1/3 | 0/6 | 232,703 |

Coverage was 1.0 throughout. Effective opportunity counts fell from 48,323 at 5 minutes to 3,941 at 60 minutes; corresponding overlap rose from 0.8000 to 0.9831.

Non-overlapping DEV results:

- 5m pooled: mean skill `-0.000437`; 1/5 positive offsets.
- 30m pooled: mean skill `-0.000490`; 9/30 positive offsets.
- Both local models were negative at every offset.

## FORMER-HOLDOUT FINALIST RESULTS

Explicitly post-hoc external development evaluation:

| Horizon | Model | Zero MSE | Model MSE | Skill | Positive instruments |
|---:|---|---:|---:|---:|---:|
| 5m | Local | 7.26723e-7 | 7.29054e-7 | -0.003207 | 0/6 |
| 5m | Pooled | 7.26723e-7 | 7.27362e-7 | -0.000880 | 1/6 |
| 30m | Local | 4.20795e-6 | 4.22632e-6 | -0.004365 | 0/6 |
| 30m | Pooled | 4.20795e-6 | 4.21387e-6 | -0.001407 | 1/6 |

Terminal non-overlapping mean skills were also negative; the 5-minute variants had no positive offsets.

## TOP FINDINGS

- The negative 15-minute result was not rescued by changing the horizon.
- Reducing overlap did not reveal robust positive skill.
- Pooled Ridge consistently reduced the damage relative to local Ridge, but never beat `ZERO_RETURN` in aggregate.

## NEGATIVE OR FAILED FINDINGS

- No horizon/model combination achieved positive aggregate DEV skill.
- Positive pooled phase offsets were isolated and outweighed by negative offsets.
- Both finalists remained negative on the former consumed holdout.
- Earlier failed and scientifically superseded attempts are consolidated in `development-attempts.jsonl`; their raw directories were removed after recording their status, defects, terminal-access state and artefact hashes.

## FILES CHANGED

- [horizons.py](/workspace/.worktrees/r2-lab-horizons/experiments/r2_historical_lab/horizons.py)
- [horizons-config.json](/workspace/.worktrees/r2-lab-horizons/experiments/r2_historical_lab/horizons-config.json)
- [test_r2_historical_lab_horizons.py](/workspace/.worktrees/r2-lab-horizons/tests/experiments/test_r2_historical_lab_horizons.py)

## OUTPUT DIRECTORY

`/workspace/tmp/qtrad-r2-lab/LAB-H/`

Includes:

- `horizon-screen.parquet`
- `cadence-screen.parquet`
- `run-register.jsonl`
- `finalists.json`
- `run-summary.json`
- [result.md](/workspace/tmp/qtrad-r2-lab/LAB-H/result.md)
- [development-attempts.jsonl](/workspace/tmp/qtrad-r2-lab/LAB-H/development-attempts.jsonl)

## FOCUSED CHECKS

- Ruff formatting/check: passed.
- Pyright: zero errors or warnings.
- Focused tests: `14 passed`.
- End-to-end smoke: passed without terminal access.
- Full bounded run: exit 0 with authenticated finalist freeze and terminal access.
- `ops/dev/verify.sh` was intentionally not run because no production source, schema, migration or durable active contract changed.

## RECOMMENDATION FOR LAB-Z

Do not nominate a horizon or cadence change for integration or a future untouched experiment. Treat horizon/overlap as deprioritised for this feature/Ridge family. Pooled shrinkage’s consistent relative advantage may inform other workstreams, but it is not evidence of positive predictive skill.
