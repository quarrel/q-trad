# LAB-U retained-scale retry audit

Status: `FIRST_ATTEMPT_REJECTED — EXPLORATORY_POST_HOC_ONLY`

The first full LAB-U output at `/workspace/tmp/qtrad-r2-lab/LAB-U` is preserved but must not be used.
Development selection included the final 20 CORE_6 decisions per instrument in `DEV_3`; their targets
became available at or after the former-holdout start. CORE_6 development support was therefore
239,655 instead of the genuine LAB-0 baseline support of 239,535. The first finalist freeze and its
terminal results are invalid for downstream use even though the runner completed successfully.

The finite emitted-output inventory is `universe-matrix.parquet`, `group-results.parquet`,
`run-register.jsonl`, `finalist-freeze.json`, and `result.md`. All five share the contaminated
selection boundary and are rejected together. No Stage 7 or retained R2 artefact was mutated, and no
provider, collector, deployment, promotion, receipt, G1, or G2 operation occurred.

The owning loader now authenticates the LAB-0 terminal boundary and excludes every non-terminal target
unless `target_available_at` is strictly before that boundary. Terminal-partition targets remain
available only through the finalist-freeze guard. A focused regression covers the strict boundary.
Before a second full run, CORE_6 development support must be exactly 239,535; Ruff, Pyright, focused
tests, and the authenticated smoke must pass. The replacement output is create-only at
`/workspace/tmp/qtrad-r2-lab/LAB-U-attempt2`. Any mismatch, existing destination, or failed check stops
the rerun.
