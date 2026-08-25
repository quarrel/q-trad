# LAB-U Report

## STATUS

`COMPLETE — EXPLORATORY_POST_HOC_ONLY`

The corrected LAB-U run completed successfully. The original run is rejected because 120 development targets matured across the terminal boundary. Its output directory and run log were removed on 2026-08-25 after the corrected replacement was validated.

## BASE SHA / BRANCH / WORKTREE

- Required base: `f31cf4731fc233726f45f67f54064c40965d01d7`
- Branch: `agent/r2-lab-universe`
- Executed code head: `8a63846abf4e3da08fdc6dc801f8efd051b8cffc`
- Worktree: `/workspace/.worktrees/r2-lab-universe`
- Worktree: clean
- Implementation commit: `48e0d2f8444e167b2ab06ba1d1a6a5744b1f5b6f`
- Boundary-fix commit: `8a63846abf4e3da08fdc6dc801f8efd051b8cffc`

## INPUT LAB MANIFEST

- Path: `/workspace/tmp/qtrad-r2-lab/LAB-0/lab-manifest.json`
- SHA-256: `462e40fa84038156b16c68bde4b68d574ab7862c680657ebd1a0035b39bf0072`
- Source: `IBKR_HISTORICAL_RESEARCH`
- LAB-0 baseline support: exactly `239,535`
- Ordering reproduced: `ZERO < POOLED < LOCAL`
- No provider access or retained-evidence mutation occurred.

## CONFIGURATIONS ATTEMPTED

- 12 development configurations: four train/evaluate matrices × three Ridge families.
- Three finalists frozen before terminal access.
- 15 successful register records; zero failed configurations.
- Corrected development supports:
  - CORE_6: `239,535`
  - OMITTED_14: `531,605`
  - ALL_20: `771,140`
- Forecast coverage: `1.0` throughout.

## PRE-HOLDOUT RESULTS

Skills are ordinary equal-instrument / primary equal-group-then-instrument.

| Train → Evaluate | Model | Skill | Positive blocks | Positive instruments |
|---|---|---:|---:|---:|
| CORE_6 → CORE_6 | Local | `-0.00269257` | 1/3 | 0/6 |
| CORE_6 → CORE_6 | Fully pooled | `-0.000424137` | 1/3 | 0/6 |
| CORE_6 → CORE_6 | Group pooled | `-0.00115006` | 0/3 | 0/6 |
| ALL_20 → CORE_6 | Local | `-0.00288298` | 0/3 | 0/6 |
| ALL_20 → CORE_6 | Fully pooled | `+0.000372875` | 1/3 | 1/6 |
| ALL_20 → CORE_6 | Group pooled | `-0.000117853` | 1/3 | 1/6 |
| OMITTED_14 → OMITTED_14 | Local | `-0.000243526 / -0.000392993` | 1/3 | 3/14 |
| OMITTED_14 → OMITTED_14 | Fully pooled | `-0.000257811 / +0.000161315` | 1/3 | 3/14 |
| OMITTED_14 → OMITTED_14 | Group pooled | `-0.000520571 / -0.000465994` | 1/3 | 6/14 |
| ALL_20 → ALL_20 | Local | `-0.00133282 / -0.00166643` | 1/3 | 2/20 |
| ALL_20 → ALL_20 | Fully pooled | `+0.0000846779 / +0.000273896` | 2/3 | 4/20 |
| ALL_20 → ALL_20 | Group pooled | `-0.000304088 / -0.000181902` | 1/3 | 4/20 |

## FORMER-HOLDOUT FINALIST RESULTS

Explicitly post-hoc external development results:

| Finalist | Skill |
|---|---:|
| ALL_20 → CORE_6 fully pooled | `-0.000675515` |
| ALL_20 → ALL_20 fully pooled | `-0.000858747 / -0.000634470` |
| ALL_20 → ALL_20 group pooled | `-0.00122467 / -0.00108287` |

All three lost directly to zero. No finalist produced a positive terminal block.

## TOP FINDINGS

- Training the fully pooled model on ALL_20 improved the CORE_6 development aggregate relative to training on CORE_6, but the gain came entirely from US crude. Only 1/6 instruments and 1/3 blocks were positive.
- The ALL_20 fully pooled result was slightly positive under both weightings, but only 4/20 instruments contributed positively.
- Its positive development improvement was concentrated in US crude (`63.4%`), silver (`18.5%`), Hong Kong HS50 (`14.1%`) and FTSE 100 (`4.1%`).
- OMITTED_14’s positive group-weighted pooled result was similarly concentrated: silver `65.2%`, Hong Kong HS50 `27.4%`, and FTSE 100 `7.4%`.
- Separate group pooling did not outperform one fully pooled slope vector.

## NEGATIVE OR FAILED FINDINGS

- All terminal post-hoc finalists lost to zero.
- FX and index group aggregates were negative during development for the fully pooled broad-universe model.
- The apparent broad-universe gain was not broad across instruments.
- The first full run is scientifically invalid because development selection included targets unavailable before terminal start. The corrected loader now enforces strict pre-terminal maturity, and a regression covers it.
- These results provide no grounds to remove any instrument from future native capture.

## FILES CHANGED

- `experiments/r2_historical_lab/universe.py`
- `experiments/r2_historical_lab/universe-config.json`
- `experiments/r2_historical_lab/README.md`
- `experiments/r2_historical_lab/LAB-U-RETRY-AUDIT.md`
- `tests/experiments/test_r2_historical_lab_universe.py`

## OUTPUT DIRECTORY

Authoritative replacement: `/workspace/tmp/qtrad-r2-lab/LAB-U-attempt2`

- `universe-matrix.parquet` — SHA `a7d1bd3129fc50d3b72c5cda2f9b94b4605db72732340ef527cc9e5b4f3932fa`
- `group-results.parquet` — SHA `847a18ac0d7f2b5ed42e5ea590ae981f704eb7fd7df974c28375c67ab4e89f4a`
- `result.md` — SHA `d1de56ad07009e416f7d3f27be9b7585bec2be32a562c2299247bda32dd01b48`
- `run-register.jsonl` — SHA `b9f47203070380811a2d72752588788e40f1f0751bdc7014e2dd97db20b0552d`
- `finalist-freeze.json` — SHA `94c7d7fbb4e105ee2bf0d8f6393f3d341510a22ebf46509430836301754fd496`

## FOCUSED CHECKS

- Ruff: passed
- Pyright: 0 errors
- Focused tests: 3 passed
- Authenticated end-to-end smoke: passed
- Preflight support/maturity boundary: passed
- Corrected full run: exit `0`, `17m10s`
- Complete repository gate: intentionally not run; no production code or durable active contract changed.

## RECOMMENDATION FOR LAB-Z

Do not nominate group pooling or broad-universe expansion as a retained model design.

The only potentially useful hypothesis is that wider fully pooled training may help a small commodity-led subset, but the effect is concentrated, weak, and reversed on the terminal post-hoc block. LAB-Z should record it as a low-priority future-data hypothesis requiring untouched evidence—not as a model recommendation or an effectiveness result.
