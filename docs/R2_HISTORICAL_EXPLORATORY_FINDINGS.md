# R2 historical exploratory laboratory findings

> **EXPLORATORY_POST_HOC_ONLY**
>
> This report is hypothesis-generation evidence from the scientifically consumed historical IBKR
> input. It is not a second holdout, confirmation, promotion, decision-grade conclusion, production
> selection, or evidence about native IG execution.

## Status and authority

- Status: complete
- Base SHA: `f31cf4731fc233726f45f67f54064c40965d01d7`
- Branch: `agent/r2-lab-synthesis`
- Worktree: `/workspace/.worktrees/r2-lab-synthesis`
- Source class: `IBKR_HISTORICAL_RESEARCH`
- Input LAB manifest: `/workspace/tmp/qtrad-r2-lab/LAB-0/lab-manifest.json`
- Manifest SHA-256:
  `462e40fa84038156b16c68bde4b68d574ab7862c680657ebd1a0035b39bf0072`
- Baseline reconstruction: support 239,535; ZERO_RETURN MSE
  0.0000028404586671320294; POOLED_LOCAL_RIDGE MSE 0.000002841663414474555;
  LOCAL_RIDGE MSE 0.0000028481068080631273; ordering ZERO < POOLED < LOCAL.

LAB-Z authenticated the compact LAB-0 manifest and consumed the compact registers and frozen
finalists from LAB-H, LAB-U, LAB-S, LAB-T, and LAB-L. It did not replay Stage 7/R2, rerun any
dependency matrix, reacquire data, mutate retained evidence, or newly access the former consumed
holdout.

## Stage 1: dimension summary

Direct skill is `1 - MSE(model) / MSE(ZERO_RETURN)`. Concentration is shown as the largest
instrument contribution / largest period contribution.

| Workstream | Configurations attempted | Best pre-holdout skill | Chronological breadth | Instrument/group breadth | Concentration | Former-holdout description | Complexity and material fragility |
| --- | ---: | ---: | ---: | ---: | ---: | --- | --- |
| LAB-H | 12 model attempts plus 156 cadence rows | -0.000437726 | 1/3 | 0/6 | 0.000 / 1.000 | Best frozen description -0.000879949; both 5m and 30m finalists lost | Ridge only, but every horizon and full phase-offset non-overlap screen lost to zero |
| LAB-U | 12 development plus 3 terminal | +0.000372875 | 1/3 | 1/6 | 1.000 / 1.000 | Best finalist -0.000675515; all three lost | Ridge plus wider loading/pooling; the first run was rejected for a terminal-maturity leak and corrected positives remained concentrated |
| LAB-S | 22 development plus 2 terminal | +0.0000833761 | 1/3 | 4/20 | 0.635 / 1.000 | Best workstream finalist -0.000777519; the selected ALL_20 result was -0.000858864 | Ridge scaling/pooling/calibration/recency variants; the sole aggregate positive reversed and its calibration slope changed sign |
| LAB-T | 7 fitted configurations plus ZERO_RETURN across 2 universes; 16 register rows | -0.00000189013 for its best non-zero comparator | 1/3 | 4/20 | 0.717 / 1.000 | Not accessed; no finalist | Adds histogram boosting and MLP; every nonlinear model lost, MLP losses were catastrophic, and boosting was concentrated |
| LAB-L | 6 designs across each of 2 universes; 12 attempts | -0.00958490 for its best non-sequence comparator | 0/3 | 0/6 | 0.000 / 0.000 | Not accessed; no finalist | Adds PyTorch and sequence training; every engineered Ridge, MLP, and LSTM lost in every block and instrument, and the first retained-scale run timed out |

The weak positive development results were not broad:

- LAB-U's best ALL_20-trained/CORE_6-evaluated model improved one of three blocks and one of six
  instruments.
- LAB-S's ALL_20 fully pooled Ridge improved one of three blocks and four of twenty instruments.
  Its largest period supplied all aggregate improvement.
- No tested horizon, cadence, nonlinear tabular model, or sequence model qualified independently.

## Stage 2: bounded combination

LAB-Z formed two combinations, well below the twelve-configuration limit. Both use the original
15-minute target and 60-second cadence selected from LAB-H, the fully pooled universe choice from
LAB-U, and the raw-return/raw-forecast expanding Ridge formulation from LAB-S. These exact linear
fits were already present in LAB-S's compact pre-holdout register, so LAB-Z ranked those records
rather than refitting or replaying dependency matrices.

| Rank | Universe | Model | Direct delta MSE vs zero | Skill vs zero | Blocks | Instruments | Slope | Spearman | Coverage | Promotion screen |
| ---: | --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- |
| 1 | ALL_20 | Fully pooled Ridge | -1.82290403e-10 | +0.0000833761 | 1/3 | 4/20 | 0.635736 | 0.015731 | 1.0 | Failed: insufficient chronological breadth, period concentration 1.0, and no surviving cadence/non-overlap test |
| 2 | CORE_6 | Fully pooled Ridge | +1.50512083e-9 | -0.000529887 | 1/3 | 0/6 | 0.185611 | 0.000592 | 1.0 | Failed: negative skill, insufficient breadth, period concentration 1.0, and no surviving cadence/non-overlap test |

No nonlinear or sequence challenger was admitted merely to fill the slate: LAB-T and LAB-L froze
none. No combined configuration met the promotion standard.

## Stage 3: terminal development block

LAB-Z froze only the rank-1 ALL_20 configuration. It reused the exact already-frozen LAB-S terminal
record, avoiding new access to the former consumed holdout.

The finalist lost directly to ZERO_RETURN:

- support: 655,424;
- direct delta MSE versus zero: +1.56383411e-9;
- skill versus zero: -0.000858864;
- chronological breadth: 0/1;
- instrument breadth: 3/20;
- calibration slope: -0.366164, reversing the development sign;
- Spearman correlation: 0.009309; and
- forecast coverage: 1.0.

This terminal result is descriptive post-hoc development evidence only. It is not a confirmation.

## Top findings

1. No configuration satisfied the stated promotion standard.
2. Fully pooled Ridge over ALL_20 is the only bounded hypothesis with positive aggregate
   pre-holdout direct skill, but the effect was tiny, present in only one block, and temporally
   concentrated.
3. The wider pooled universe may contain weak pooled rank association, but it did not
   translate into stable direct MSE skill or stable calibration.
4. Complexity was not rewarded: every tested nonlinear tabular and sequence challenger lost.

## Negative and failed findings

- The original 15-minute six-target reconstruction remained worse than ZERO_RETURN.
- Every tested alternative horizon and full phase-offset cadence/non-overlap screen lost.
- CORE_6, scaling, post-fit calibration, hierarchy, recency, boosting, MLP, and LSTM variants did not
  rescue direct skill.
- LAB-U's first run was invalidated and excluded after a terminal-maturity leak; only the corrected
  run is consumed here.
- LAB-L's first retained-scale attempt timed out; its successful bounded reruns still produced no
  qualifying sequence finalist.
- LAB-T and LAB-L did not access the terminal block because they froze no finalists.

## Future experiment recommendation

Do not promote a model from this laboratory. If an untouched native or prospectively collected
future-data experiment is authorised, use the compact slate as a guarded falsification test:

1. freeze ZERO_RETURN as the direct null;
2. freeze LOCAL_RIDGE as the immediate simple linear comparator;
3. test one FULLY_POOLED_LOCAL_RIDGE hypothesis on ALL_20 at the 15-minute horizon and 60-second
   decision cadence;
4. retain P0 features, raw-return targets, expanding chronological training, fold-local median
   standardisation, causal target maturity, and source-active opportunity membership; and
5. reject on non-positive direct skill, fewer than two positive blocks or instruments,
   instrument/period concentration above 0.8, unstable/non-positive calibration, or failure of the
   full phase non-overlap cadence screen.

Do not carry a nonlinear tabular or sequence challenger into that next experiment. This recommendation
does not authorise provider access, reacquisition, a new source-class equivalence claim, production
selection, or changes to the completed R2 conclusion.

## Files and outputs

Tracked LAB-Z files:

- `experiments/r2_historical_lab/lab_z/synthesis.py`
- `experiments/r2_historical_lab/lab_z/synthesis-config.json`
- `experiments/r2_historical_lab/lab_z/future-experiment-slate.json`
- `tests/experiments/r2_historical_lab/test_lab_z.py`
- `docs/R2_HISTORICAL_EXPLORATORY_FINDINGS.md`

Raw compact output directory:

- `/workspace/tmp/qtrad-r2-lab/LAB-Z/`

It contains the dimension summary, two-row combination register, finalist freeze, reused terminal
result, future experiment slate, and run summary. No retained R2 artefact was modified.
