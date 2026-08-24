# LAB-0-v2 independent-review candidate

STATUS: COMPLETE — EXPLORATORY_POST_HOC_ONLY

This is reusable exploratory infrastructure, not a confirmatory result, promotion, or decision-grade conclusion. It preserves source class IBKR_HISTORICAL_RESEARCH and uses only retained local Stage 7/R2 artefacts.

## Candidate

- Branch: agent/r2-lab-foundation
- Authorised base: f31cf4731fc233726f45f67f54064c40965d01d7
- Output: /workspace/tmp/qtrad-r2-lab/LAB-0-v2-review-candidate-2
- Manifest: /workspace/tmp/qtrad-r2-lab/LAB-0-v2-review-candidate-2/lab-manifest.json
- Manifest SHA-256: 0c61a3373432e4c12cf3ee49b1a46e01df7bf8ebb763f1cdd7f92431b8e2f296
- Parts: 723; all declared file hashes rechecked
- Rows: 3,378,933 feature; 3,378,933 context; 13,515,732 target

## Baseline reconstruction

The candidate authenticated retained fold and fit children, reconstructed fold-local preprocessing, independently refit 18 LOCAL_RIDGE and three POOLED_LOCAL_RIDGE models, predicted the three original OOF development blocks from LAB-derived rows, and recomputed instrument-balanced MSE.

| Model | Instrument-balanced MSE |
|---|---:|
| ZERO_RETURN | 0.0000028404586671320235 |
| POOLED_LOCAL_RIDGE | 0.0000028416634094414198 |
| LOCAL_RIDGE | 0.0000028481068098183943 |

- Common support: 239,535 exactly.
- Ordering: ZERO_RETURN < POOLED_LOCAL_RIDGE < LOCAL_RIDGE.
- Maximum absolute MSE delta from the retained result: 5.033135417638465e-15 (bound: 1e-14).
- Maximum preprocessing delta: 1.350224728031979e-15 (bound: 1e-12).
- Maximum coefficient delta: 1.1408392657755347e-10 (bound: 1e-8).
- Maximum intercept delta: 4.8689975662430385e-12 (bound: 1e-9).

The small numerical discrepancy is attributable to decoding retained decimal-string prices to Float64 and independently rerunning fold-local LSQR Ridge fits. Support, weighting, schemas, fold membership, and ordering are unchanged.

## Checks

- Ruff passed for the experiment package and focused tests.
- Pyright passed with zero diagnostics.
- Eight focused tests passed.
- One end-to-end smoke passed.
- The complete 20-instrument build passed.
- The downstream harness authenticated the final manifest, rechecked all parts, and returned DEV-only rows by default for feature, context, and target reads.

## Failed and superseded attempts

Earlier LAB-0 and LAB-0-v2 outputs remain preserved outside Git. They are not authorised downstream inputs. Failures exposed and led to fixes for incomplete feature semantics, circular baseline extraction, provider-gap timing, exact fold membership, terminal-boundary exclusion, and fail-closed downstream loading.

Independent review should use only the exact candidate manifest and SHA above. Do not inspect the terminal former-holdout block for variant selection.
