# LAB-S statistical workstream result

Status: STOPPED_SCIENTIFIC_BOUNDARY
Evidence label: EXPLORATORY_POST_HOC_ONLY
Source class: IBKR_HISTORICAL_RESEARCH

LAB-S implemented the fixed one-factor Ridge laboratory for degree of pooling, causal volatility
target scaling, fold-local calibration, and training recency over CORE_6 and ALL_20. It also replaced
the retained-scale hierarchical dense interaction matrix with an exactly equivalent bounded
normal-equation solver.

No LAB-S scientific result is nominated.

The first retained development attempt timed out while constructing the dense hierarchical design.
After remediation, the second attempt completed, froze three finalists, and evaluated them on the
former consumed holdout. Post-run review found that development selection included the last twenty
decision minutes of DEV_3 even though their targets matured at or after the former-holdout boundary.
CORE_6 support was therefore 239655 instead of the required 239535. The attempt-2 freeze and every
metric derived from it are scientifically invalid and remain preserved outside Git.

The corrected development selector excludes targets not available before the terminal boundary and
reproduces support 239535 exactly. Focused static checks and seven tests pass. No third retained run
or second terminal access was performed.

Recommendation for LAB-Z: nominate neither code nor model findings yet. Review the corrected causal
boundary and the preserved invalidation checkpoint before authorising a fresh development run.
