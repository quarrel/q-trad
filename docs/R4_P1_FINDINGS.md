# R4-P1 learnability diagnostic

R4-P1 is a post-hoc historical exploratory diagnostic. It cannot promote R4-P0, access terminal
former-holdout outcomes, establish untouched-holdout performance, or authorise R5 or live trading.
The operator activated `R4_P1_EXECUTION_PLAN.md`; its earlier draft label is not the execution state.

## Scope and method

The new `experiments.r4_p1_learnability` namespace consumes the exact authenticated LAB-0 feature
children and the accepted DEV-only all-twenty local Ridge residual foundation. It authenticates the
immediate accepted preparation receipt and consumed bytes without replaying P0 ancestry or metrics.
Terminal block requests fail before filesystem access. No target parquet from LAB-0 is decoded:
DEV residual targets and local predictions come from the authenticated accepted foundation.

The temporal model shares a 64-wide LSTM across nodes, uses the exact inclusive 61-minute sequence,
and gates unobserved inputs to zero without forward fill. Local, pooled other-node, fixed graph and
exact shuffled graph families use the same optimiser and zero-initialised final linear head.
Pooled context excludes the target node and is permutation invariant. The fixed and shuffled neural
messages use the maintained normalised adjacency exactly. Linear context uses the corresponding
adjacency-weighted available-feature mean, with an explicit zero for an absent context. Feature
standardisation and residual RMS scaling use training data only. Residual scaling is inverted to raw
returns before addition to the exact local Ridge comparator.

Capability uses deterministic planted local temporal, pooled and fixed-graph signals with disjoint
synthetic training/evaluation samples and relevant incapable controls, plus eight fixed, complete
all-twenty DEV_1 timestamps. Only these capability cases can revise the policy. There are at most
three distinct attempted policies, predeclared as 2,000, 4,000 and 8,000 Adam updates, learning rate
0.003, timestamp batch size 32, hidden width 64, gradient norm clipping at 10 and seed 17. The final
head starts at zero; the encoder uses PyTorch's seeded default initialisation. Numerical computation
uses float32 on CUDA with deterministic algorithms, deterministic cuDNN, benchmarking disabled,
TF32 disabled, highest matmul precision and `CUBLAS_WORKSPACE_CONFIG=:4096:8`. CUDA absence fails.
The selected policy, or final capability failure, is recorded in one append-only freeze before B/C.

Linear probes use alpha-one Ridge with an unpenalised intercept, training-only population
standardisation and one common eligible support for all families. The eight fits are precisely four
families for DEV_1→DEV_2 and DEV_1+DEV_2→DEV_3. Training target maturity precedes evaluation.
Neural samples are evenly spaced support timestamps, selected and written before their metrics:
at most 5,000 training and 2,000 evaluation timestamps per stage, retaining every eligible target at
each selected timestamp. Four nongraph fits are mandatory only after capability; four graph fits
require the independent linear graph gate.

Both-block and combined reducers use row means within each instrument, then equal weights across
all twenty instruments. Combined reductions pool DEV_2 and DEV_3 rows within instrument before
averaging. Positive delta means baseline MSE minus candidate MSE. Concentration is the largest
positive instrument contribution divided by their sum, with zero sum classified as concentration 1.
Contemporaneous residual correlations are descriptive and noncausal. Directed lag correlations use
15/30/60-minute source residuals only when mature at the target cutoff; source common-component
subtraction obeys the same maturity boundary. Matched nonedges retain the source and match target
support count, with canonical-order ties and replacement, using no residual values.

## Resource argument and attempts

Input preparation was measured at 6.0 seconds for a 151,371×20×26 feature panel and 767,770 eligible
DEV target rows. A single panel is reused. Temporal tensors and CUDA optimiser work dominate cost;
bounded empirical tensors contain at most 7,000 selected timestamps per stage. Outputs contain
compact JSON diagnostics and register records, not model checkpoints or full predictions; projected
new retained output is below 1 GB. The first completed capability fit is the duration observation
boundary before accepting a longer schedule. The 20 GB retained-output boundary and impractical
sequential-neural-work boundary remain escalation points.

The initial invocation at `/workspace/tmp/qtrad-r4-p1/r4-p1-20260908` failed in quantile reporting
after the first planted local optimiser loop because its quantile tensor had the wrong dtype.
No capability evaluation metric, policy freeze, linear fit or neural empirical fit was reached.
Its register records the failure. The repair changes reporting only and reruns the same numerical
policy in a new directory, `/workspace/tmp/qtrad-r4-p1/r4-p1-20260908-repair1`; it is not another policy.

## Results and disposition

The corrected training path established capability on its first distinct policy. Empirical
optimisation also worked, but neither local nor pooled neural corrections improved this bounded
development screen. The fixed graph failed the linear entry gate. Further work on this graph
hypothesis is **not justified by R4-P1**; no graph neural fit was authorised by the measured gate.

### Capability and frozen policy

The selected policy identity is
`6a7a73836e9b5f45b9acf71eb41f7fad5a42fd3cec808b779a2ed124acc2cf89`.
Exactly one `R4P1_POLICY_FREEZE` records `CAPABILITY_ESTABLISHED` before B/C.
The 4,000- and 8,000-update alternatives were never attempted.

| Planted mechanism | Held-out MSE | Zero MSE | Relevant incapable MSE | Pass |
| --- | ---: | ---: | ---: | --- |
| Local temporal | 0.000103893 | 1.24750614 | 0.244539455 contemporaneous oracle | yes |
| Pooled other-node | 0.000537114 | 0.055395145 | 0.094192649 local | yes |
| Fixed graph | 0.000024044 | 0.165031880 | 0.156971869 pooled; 0.158147147 shuffled | yes |

Every capable mechanism clears both the 90% reduction against zero and 50% reduction against its
relevant incapable control. The fixed eight-timestamp, 160-row, all-twenty DEV_1 batch reduced
standardised training MSE from approximately 1 to `1.68327314475805e-10`, comfortably below both
required 1% limits. Initial correction was exactly zero; gradients and parameter changes were
finite and nonzero. All seven completed capability fits performed 2,000 optimiser updates.
This establishes trainability, not empirical predictability.

### Residual structure

Each block has 134 directed fixed edges, 134 exact shuffled edges and 134 source-matched nonedges.
The full report retains every edge, sign, distribution, group and source/target concentration;
the table below summarises raw correlations. These are unweighted means across supported edges,
not a forecast score.

| DEV block | Contemporaneous fixed / shuffled / nonedge | 30-minute fixed / shuffled / nonedge | 60-minute fixed / shuffled / nonedge |
| --- | --- | --- | --- |
| DEV_1 | 0.337582 / 0.197391 / -0.154921 | 0.007572 / -0.001994 / -0.003201 | 0.008362 / 0.001700 / -0.004225 |
| DEV_2 | 0.375128 / 0.241254 / -0.083606 | 0.004904 / 0.007507 / 0.019067 | -0.002317 / 0.002233 / 0.016773 |
| DEV_3 | 0.305904 / 0.243027 / 0.030846 | -0.000642 / -0.000155 / -0.003676 | -0.024321 / -0.007339 / 0.035263 |

The 15-minute lag has no mature pair support in any block and remains explicitly null, with zero
supported pairs; no score is substituted. Removing the equal-market common component leaves
contemporaneous fixed-edge means 0.400087, 0.452451 and 0.368454. Those descriptive, noncausal
relationships do not establish usable lead-lag signal. All three causal lags and both views remain
visible in the output; no winning lag was selected.

### Eight fixed linear probes

The table uses equal-instrument raw-return MSE; combined rows pool DEV_2/DEV_3 within each
instrument before averaging instruments. Residual MSE is algebraically equal to total MSE here
because predictions add the residual correction to the exact local Ridge comparator.

| Prediction | DEV_2 MSE | DEV_3 MSE | Combined MSE | Combined zero skill |
| --- | ---: | ---: | ---: | ---: |
| Zero return | 2.487959448e-6 | 1.967873079e-6 | 2.230183297e-6 | 0 |
| Exact local Ridge | 2.494583894e-6 | 1.963729277e-6 | 2.231517306e-6 | -0.000598161 |
| Local P0 residual probe | 2.504784917e-6 | 1.963050928e-6 | 2.236354120e-6 | -0.002766958 |
| Pooled other-node probe | 2.504443231e-6 | 1.962411838e-6 | 2.235864089e-6 | -0.002547231 |
| Fixed graph probe | 2.505347554e-6 | 1.963137854e-6 | 2.236674930e-6 | -0.002910807 |
| Exact shuffled probe | 2.504321322e-6 | 1.962909182e-6 | 2.236052201e-6 | -0.002631579 |

The fixed graph loses to pooled in DEV_2 and DEV_3 (signed gains
`-9.043227388e-10` and `-7.260161037e-10`) and to shuffled
(`-1.026231803e-9` and `-2.286716707e-10`). It also lacks positive zero skill in
DEV_2. Its combined pooled-relative breadth of 9/20 and best-instrument share 0.202141 pass their
individual requirements, but cannot rescue the failed block/control/zero-skill requirements.
The graph gate is mechanically false.

### Four mandatory corrected neural fits

All four seed-17 fits use the frozen policy and exactly the outcome-blind selected timestamp keys
retained before neural fitting. Fixed/shuffled neural fits are skipped because B failed.

| Prediction | DEV_2 MSE | DEV_3 MSE | Combined MSE | Combined zero skill |
| --- | ---: | ---: | ---: | ---: |
| Zero return | 2.515290340e-6 | 1.975107561e-6 | 2.247187350e-6 | 0 |
| Exact local Ridge | 2.523924211e-6 | 1.968747673e-6 | 2.248419209e-6 | -0.000548178 |
| Local temporal | 2.812205005e-6 | 2.055574721e-6 | 2.437319681e-6 | -0.084609025 |
| Pooled other-node | 2.831221055e-6 | 2.069537725e-6 | 2.453285408e-6 | -0.091713785 |

Local training MSE fell from approximately 1 to 0.854009 and 0.928875; pooled fell to 0.847066
and 0.927069. Every fit has status `TRAINED`, 2,000 updates, positive finite gradient norms
and parameter movement. Evaluation correction RMS is 0.000575877 / 0.000321667 for local and
0.000605397 / 0.000362458 for pooled, versus training target RMS 0.001438465 / 0.001530230.
No correction-scale explosion or training-failure classification occurred. Parameter counts
are 23,617 local and 23,681 pooled; raw correction quantiles and per-fit diagnostics are retained.

Both neural families lose to Ridge and zero in both blocks. Their combined Ridge-relative breadth
is 0/20 and the zero-positive-contribution concentration convention returns 1. Pooled also loses
to local in both blocks. These results establish a lack of usable improvement for this policy,
sample and evidence source; they do not prove the absence of all nonlinear predictability.
No fixed-graph neural result exists or is inferred from the skip.

### Exact outputs, resources and disposition

The completed output root is
`/workspace/tmp/qtrad-r4-p1/r4-p1-20260908-repair1`. It contains:

- `input.json`, `numerical-policy.json` and `tiny-sample.json`;
- `capability-policy-1.json` and the append-only `run-register.jsonl`;
- `residual-diagnostics.json` and `linear-probes.json`;
- `neural-sample-keys.json` and `neural-screen.json`;
- `resources.json` and `implementation-validation.json`, which binds exact output and source hashes;
- `focused-validation.json`, the retained command/outcome receipt for tests, Ruff, formatting and Pyright.

The complete account is one distinct capability policy, seven completed capability fits and one
earlier reporting-failed fit of that same policy, eight empirical Ridge fits, four empirical neural
fits, one empirical seed and one freeze. The original failed invocation remains at the root named
above in the attempt-account section. Small synthetic test fits are implementation evidence only.

The completed run took 113.63 seconds. Individual completed capability fits took 6.1–9.2 seconds,
and empirical neural fits 8.9–9.4 seconds. Peak process RSS was 5,652,632 KiB (5.39 GiB), peak CUDA
allocation 1,540,134,912 bytes (1.43 GiB), and reported output size about 3.46 MiB. The process
completed before the next planned model observation; its terminal tool return supplied both
per-fit timings and completion evidence. These timings confirm the sequence was practical.
No memory, storage or runtime escalation threshold was approached.

The recommendation is to **deprioritise the current graph hypothesis**. Training failure has been
separated from the absence of useful development improvement and from the missing graph increment.
R4-P0 remains `NO_HYPOTHESIS_NOMINATED`; R4's held-out retention gate remains unsatisfied.
There is no native, economic, prospective, promotion, R5 or real-capital conclusion.
Independent final review of this candidate remains the owner's delivery gate.

## Validation

Focused tests exercise rejection before terminal I/O, freeze ordering and final-failure B execution,
exact contexts, pooled permutation invariance, zero heads, actual updates and gradient/parameter
movement, raw residual scaling, common-support enforcement, maturity, unequal-block reducers,
delta signs and concentration. The synthetic shaped smoke traverses capability-fit plumbing,
residual diagnostics, all eight small linear fits, and the four bounded nongraph neural fits.
Its successful-freeze fixture is explicitly a plumbing fixture, not research capability evidence.
Actual planted recovery/discrimination and real-batch overfit are checked by the recorded capability
execution and focused assertions over that exact retained result, without fit replay.
Namespace-local strict Pyright, Ruff and formatting pass without shared policy changes.
The retained summary receipt is
`/workspace/tmp/qtrad-r4-p1/r4-p1-20260908-repair1/focused-validation.json`.
It records the exact commands, 23-test result and static-check outcomes; raw command output remains
in the implementation tool transcript rather than separate log files.

- `P1-INPUT`, `P1-FREEZE`, `P1-CAP`, `P1-B` and `P1-C`: PASS; exact output/source hashes,
  register counts and reducer/common-support checks are in `implementation-validation.json`.
- `P1-VALIDATE-TEST`: 23 focused tests pass, including retained planted thresholds, real capacity,
  direct missing-freeze rejection at all three public B/C entry points and the shaped synthetic smoke.
  Command: `P1_CAPABILITY_RESULT=/workspace/tmp/qtrad-r4-p1/r4-p1-20260908-repair1/capability-policy-1.json uv run --no-sync pytest tests/experiments/r4_p1_learnability -q --override-ini addopts=''`.
- `P1-VALIDATE-TYPE`: `uv run --no-sync pyright -p experiments/r4_p1_learnability/pyrightconfig.json`;
  zero errors and warnings in strict mode.
- `P1-VALIDATE-LINT`: `uv run --no-sync ruff check experiments/r4_p1_learnability tests/experiments/r4_p1_learnability`.
- `P1-VALIDATE-FORMAT`: `uv run --no-sync ruff format --check experiments/r4_p1_learnability tests/experiments/r4_p1_learnability`.
- `P1-VALIDATE-REVIEW`: pending independent owner review of the exact committed candidate.

The complete retained result, including the failed invocation, remains under the two named P1 output
roots for review and the experiment account. Disposable implementation scratch is removed at handoff;
the worktree's fresh environment remains for independent review.
