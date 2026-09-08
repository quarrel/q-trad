# R4-P0 historical exploratory findings

**Result: `NO_HYPOTHESIS_NOMINATED`. Updated 2026-09-07.**

All 15 terminal predictions and the completed metrics, nomination and exploratory report have been
independently reviewed. The frozen nomination rule selects no hypothesis: every residual family
loses to the zero-return and linear controls, including both graph candidates. Terminal outcomes
were accessed under explicit authority. A failed full-LAB control assertion remains retained; the
operator accepted only its specific reconstructed result and disclosed discrepancy. This report
does not claim graph retention, R5 readiness, native predictability, executable profitability or
real-capital readiness.

**A pre-existing numerical-policy deviation also limits interpretation.** The original terminal
entry path did not fully install the CUDA settings declared in G0. Historical live settings and their
numerical effect are unmeasured. The operator authorised disclosed completion on the unchanged path,
without refits or silently changing flags. Neither the retained G0 declaration nor smoke repeatability
proves terminal deterministic-policy compliance. Prospective observations below do not reconstruct
historical training settings.

## Forecast findings

The primary comparison is seed 17, with equal instrument weighting. MSE is in squared raw-return
units; lower is better. Skill is `1 - model MSE / zero MSE`, so the large negative values below
describe markedly worse forecasts, not a percentage improvement.

| Forecast | Primary MSE | Skill versus zero |
| --- | ---: | ---: |
| Zero return | 1.8208171323251572e-6 | 0 |
| Local ridge | 1.8259358122879021e-6 | -0.00281120 |
| Capsule pooled ridge | 1.8223809654450764e-6 | -0.00085886336 |
| Local temporal residual | 0.0191506164616048 | -10516.5946 |
| Pooled non-graph residual | 0.0268526499210081 | -14746.5820 |
| Fixed economic graph residual | 0.027221259257136983 | -14949.0237 |
| Learned static graph residual | 0.027194392811027875 | -14934.2685 |
| Shuffled fixed graph residual | 0.02717383049792093 | -14922.9756 |

Every primary residual family improves on zero, local ridge or pooled ridge on **0 of 20**
instruments. Local temporal is the least poor primary residual family. Fixed and learned graphs
also lose in aggregate to pooled non-graph and shuffled-graph controls. Comparator-minus-candidate
MSE deltas are negative: fixed versus pooled `-0.0003686093361`, learned versus pooled
`-0.0003417428900`; fixed versus shuffled `-0.0000474287592`, learned versus shuffled
`-0.0000205623131`. Positive deltas would favour the candidate.

Auxiliary seeds remain auxiliary, not an opportunity to replace the primary seed:

| Residual family | Seed 29 MSE | Seed 43 MSE |
| --- | ---: | ---: |
| Local temporal | 0.0191790966360 | 0.00508184699316 |
| Pooled non-graph | 0.00730809617596 | 0.00608351344074 |
| Fixed economic graph | 0.00514515207404 | 0.00637257414649 |
| Learned static graph | 0.00729445337381 | 0.00600988983468 |
| Shuffled fixed graph | 0.00682971381954 | 0.00616031759176 |

All fifteen family/seed performances are negative versus zero. Formal primary dispositions are
`NEGATIVE`; auxiliary dispositions remain `AUXILIARY`. Each per-seed core-six view contains
205,145 rows across six instruments. Fixed-graph primary core-six MSE is 0.0263969395251 and
learned-graph MSE is 0.0274671316727; neither rescues the all-twenty result. Learned graph identities
coincide across the three independently model-bound receipts, while their forecasts and core-six
metrics differ. No causal explanation for this coincidence is inferred.

Both graph candidates fail terminal improvement, development-period improvement, direct skill,
graph controls, breadth and concentration. Fixed graph also fails seed stability (one positive
seed under that gate); learned graph passes it (two), but fails the other required conditions.
Both pass coverage and calibration gates; passing those checks does not establish forecast utility.
Fixed graph passes combined-development breadth but fails terminal breadth; learned graph fails
both. Nomination concentration summaries have best-period share 1 for both, with best-instrument
shares 0.3342434 and 1 respectively. Shares of 1 with no positive contribution elsewhere represent
absence of improvement, not a profitable concentrated opportunity.

The rule therefore returns no nominated candidates and promotion authority `NONE`.
This supports rejecting these frozen historical hypotheses; it does not prove that all graph models
are ineffective in other, separately authorised experiments.

## Scope and completed evidence

R4-P0 is a `POST_HOC_HISTORICAL_EXPLORATORY` screen using `IBKR_HISTORICAL_RESEARCH` evidence.
The five families are local temporal residual, pooled non-graph residual, fixed economic graph
residual, learned static graph residual and shuffled fixed graph residual. Each has seeds 17, 29
and 43. Thirty development and fifteen terminal slots account for 45 substantive primary fits;
the final journal contains 90 paired PRIMARY attempt-0 records. No additional primary fit or
general operational retry was introduced by recovery.

The original scientific checkout is `faef6462c6929da7e91eea12970a72e53a2647e6`.
Prediction provenance remains per slot: first local-temporal seed 17 used `3387100`; the next nine
terminal completions used `27166ff`; the final five used recovery descendant `1bdc976`.
The recovered learned-static seed 29 preserves its model and metadata bytes from `27166ff`.
Metrics execution uses `40a9d01`; subsequent typing-only delivery preparation does not replace
execution provenance. The sealed terminal support has 655,424 rows; fifteen predictions create
9,831,360 forecast/outcome pairs. These are different counting units. Terminal fit evidence counts
15 fits; the full development-plus-terminal journal counts 45. Reporting and inference recovery
add zero primary-model fits.

[Final prediction verification](/workspace/tmp/MAP_orchestrator/r4-p0-20260828-ddd66f9/longrun/r4-terminal-runtime-current-policy/inference-recovery/final-prediction-validation.json)
authenticates all 15 bundles, worker/seal/journal bindings, prior journal preservation and the recovery
linkage. [Independent acceptance](/workspace/tmp/MAP_orchestrator/r4-p0-20260828-ddd66f9/longrun/r4-terminal-runtime-current-policy/inference-recovery/final-prediction-review.json)
carries the original verifier evidence. Root accepted predictions before permitting outcome access.

## Full-LAB control gate

The full-LAB regression is distinct from capsule metrics and the retained core-six comparison.
Its 655,424 instrument rows cover all twenty instruments. Equal aggregate counts do not establish
identical populations; current source lineage, keys and feature fingerprints are retained separately.

| Quantity | Historical anchor | Authorised diagnostic reconstruction |
| --- | --- | --- |
| Pooled instrument-balanced MSE | 1.8223809664366566e-6 | 1.822380965445977e-6 |
| Pooled-minus-zero MSE | +1.5638341114994004e-9 | +1.5638331208198297e-9 |
| Skill versus zero | -0.0008588639044175839 | -0.0008588633603325323 |

The MSE and delta residuals are `-9.906795707e-16`, within their absolute `1e-12` thresholds.
The skill residual is `+5.440850515e-10`, exceeding its `1e-10` threshold by 5.44 times.
The original unchanged assertion therefore still fails. Both versions of this pooled control
remain worse than zero; that observation is not a conclusion about the residual-model families.

The single authorised CPU diagnostic confirmed equality of reconstructed and persisted cross-market
feature values across 2,676,648 current training rows and 655,424 validation rows. Applying the
original Polars reducer to the same already-computed prediction vector gives essentially the same
skill discrepancy, so changing the reducer does not explain it. Configuration, cutoff and Ridge
producer lineage agree by source inspection; full historical input/backend equivalence and the
cause of the drift remain unproven.

At zero MSE `1.8208171323251572e-6`, the skill threshold corresponds to about `1.82e-16` in
MSE units; the existing `1e-12` delta bound corresponds to about `5.49e-7` in skill units.
This asymmetry does not itself permit a post-outcome tolerance relaxation. The plan requires the
frozen numerical regression gate. Event 418 expressly accepts only this retained reconstruction and
observed skill discrepancy. Historical anchors, the failed assertion and closure remain unchanged;
no general tolerance expansion, refit or claim of complete historical numerical parity is granted.

The [captured values and input fingerprints](/workspace/tmp/MAP_orchestrator/r4-p0-20260828-ddd66f9/longrun/r4-terminal-runtime-current-policy/full-lab-diagnostic/pre-assert.json)
have SHA-256 `a78297f59225cf20870d89ece4b67a0feb856db9b30d94742613051a5441944c`.
[Independent parity assessment](/workspace/tmp/MAP_orchestrator/r4-p0-20260828-ddd66f9/longrun/r4-terminal-runtime-current-policy/full-lab-diagnostic/parity-assessment.json)
records the diagnostic findings and the authority boundary before event 418. The diagnostic used one
mandatory linear-control reconstruction, no primary-model replay and no GPU interaction.

The successful [one-case exception companion](/workspace/tmp/MAP_orchestrator/r4-p0-20260828-ddd66f9/longrun/r4-terminal-runtime-current-policy/final-metrics-control-exception/full-lab-control-exception.json)
has SHA-256 `5993ab1b0ac4d05c574c526c2dd23e7b17f6dc3da6265bbdda3b485199418b36`.
Reporting authenticated the same current inputs and reused the accepted scalars without another
linear-control fit. It did not rerun or relabel the failed assertion as passing.
The full-LAB result identity is `33096c0b35899cc8b34fb23a437ea40d7de588ef4dfb844ac7a3388a6adab643`.
The capsule pooled MSE in the forecast table is slightly different from this full-LAB MSE;
equal row counts do not collapse their separate contracts or establish population equivalence.

## CUDA declaration and observed settings

The frozen declaration is
[G0 JSON](/data/q-trad/r4-p0/r4-p0-development-g8-faef646/config/R4P0_G0.json:1),
at `frozen_configuration.deterministic_cuda_policy`.
[The original setter at commit `faef6462c6929da7e91eea12970a72e53a2647e6`](R4_SUCCESSOR_HANDOFF.md#retained-source), in `experiments/r4_residual_graph/grouped.py`,
installs the settings when called; original terminal setup did not call it.

| Setting | Frozen declaration | Observed recovery and remaining-slot path |
| --- | --- | --- |
| CUBLAS workspace | `:4096:8` | `:4096:8` |
| Deterministic algorithms | enabled | disabled |
| cuDNN deterministic | enabled | disabled |
| cuDNN benchmark | disabled | disabled |
| CUDA matmul TF32 | disabled | disabled |
| cuDNN TF32 | disabled | enabled |
| Float32 matmul precision | highest | highest |

The [reporting disclosure companion](/workspace/tmp/MAP_orchestrator/r4-p0-20260828-ddd66f9/longrun/r4-terminal-runtime-current-policy/final-metrics-control-exception/runtime-deviation-disclosure.json)
binds the accepted final-five worker receipts and their prospective observations; its SHA-256 is
`b5e0cef502a13a99d537d3ec5e585c446ad93571b54a82213d14ea0917a857c1`.
[Metrics worker provenance](/workspace/tmp/MAP_orchestrator/r4-p0-20260828-ddd66f9/longrun/r4-terminal-runtime-current-policy/final-metrics-control-exception/metrics/worker-terminal.json)
links it before outcome evaluation. These observations include recovered inference, not the
interrupted model's earlier training. The G0 JSON remains the historical declaration unchanged.
The CPU linear-regression discrepancy has not been attributed to this separate CUDA deviation.

## Recovery and failed reporting evidence

The mixed-journal controller stopped on a successfully parsed GPU-free observation of 1,426 MiB,
below its 4,096 MiB floor. It killed and reaped only its owned group. That numerical telemetry
observation is distinct from a telemetry-command failure; exact external memory ownership and
historical driver effects were not established.

Ten terminal predictions were complete and learned-static seed 29 retained fitted model/metadata.
The operator authorised one inference-only recovery under its original STARTED attempt. Recovery
loaded all fitted tensors, preserved original staging and model bytes, published to the previously
absent original destination and appended SUCCEEDED without another fit or STARTED record. Owner
verification preceded admission of the four unopened slots. The
[recovery validation](/workspace/tmp/MAP_orchestrator/r4-p0-20260828-ddd66f9/longrun/r4-terminal-runtime-current-policy/inference-recovery/recovered-output-validation.json)
and original failed invocation remain retained.

The first final-metrics invocation failed before outcomes because its prepared root receipt omitted
two required context identities. The correction added those authenticated identities, rejected
incomplete receipts at the authoritative boundary and tested the actual setup path in a new
create-only control directory. The corrected invocation subsequently loaded outcomes and failed the
separate control gate. Neither failure was removed or represented as successful execution.

[Closure 000005](/data/q-trad/r4-p0/r4-p0-development-g8-faef646/result/closure-report-000005-6145e5c7b6ae54a920cfbee328cd0f3d49c8e154680f1f827f500faa419c089a.json)
records `RUN_FAILED`, `TERMINAL_POST_OUTCOME_EVALUATION`, performance `NOT_COMPUTED`
and nomination `NOT_EVALUATED` for that failed invocation. The later authorised invocation produced
separate successful outputs; it does not rewrite this earlier disposition or reset outcome exposure.

Monitoring also produced copied-slot and erroneous receipt-absence narratives. Owner checks used
actual files and rejected those narratives; they do not imply additional workload failures.
[The retained copied-slot incident](/workspace/tmp/MAP_orchestrator/r4-p0-20260828-ddd66f9/longrun/r4-terminal-runtime-current-policy/mixed-journal/observer-copied-slot-incident.json)
and recovery validation preserve the concrete corrections.

## Completed outputs and delivery boundary

The CPU-only event-419 invocation completed successfully in 4,890.976 seconds (about 81.5 minutes),
with peak sampled aggregate RSS 39,240,028,160 bytes. Its owned child exited zero and was reaped with
no residual process group. Event 421 released the operator's GPU reservation without changing the
CPU reporting path.

| Retained output | SHA-256 |
| --- | --- |
| [Terminal metrics](/data/q-trad/r4-p0/r4-p0-development-g8-faef646/metrics/terminal-metrics.json) | `24e49a0d33805145c299fdd548711ac9f75eb516b2c6b0bd07f100efa4223c64` |
| [Nomination](/data/q-trad/r4-p0/r4-p0-development-g8-faef646/result/nomination.json) | `d572bbb8212bcf97f836f5aa744ae4db6822e250b4c958ec89cd750577e94051` |
| [Final exploratory report](/data/q-trad/r4-p0/r4-p0-development-g8-faef646/result/final-exploratory-report.json) | `32da6dd77645450207abe414324d1e38e271fd0516bee293c2f2647ed8ef0e6a` |

[Output validation](/workspace/tmp/MAP_orchestrator/r4-p0-20260828-ddd66f9/longrun/r4-terminal-runtime-current-policy/final-metrics-control-exception/final-output-validation.json)
records 142 bounded metadata, identity and reference checks; its SHA-256 is
`412331adc3de518d5eab0343492031e160b0f1be2a0ab419acb0bba6e8497029`.
[Independent final-output review](/workspace/tmp/MAP_orchestrator/r4-p0-20260828-ddd66f9/longrun/r4-terminal-runtime-current-policy/final-metrics-control-exception/final-output-review.json)
accepts the exact artefacts and both qualifications without recomputing metrics. Root accepted
these outputs and the negative nomination in programme event 422.

The experiment remains exploratory and `decision_grade=false`; it grants no promotion,
held-out graph retention, R5, native-source, profitability or real-capital authority.
The final source candidate and complete guarded `ops/dev/verify.sh` milestone evidence are bound
separately in the programme record. Current governing documents are preserved in delivery;
original scientific source commits and all retained failures remain unchanged. Completed worktrees
have been retired; [source archives and successor boundaries](R4_SUCCESSOR_HANDOFF.md) preserve access.
The operator accepted the qualified result and authorised merging `724e2e0` on 2026-09-08.
The [execution and remediation plans](archive/r4/README.md) are archived as historical records;
this administrative archival does not change the accepted scientific artefacts or their identities.
