# R4 successor handoff

R4-P0 is complete and integrated. On 2026-09-08 its seven remaining worktrees and six working branches were retired after source archival and runtime-dependency checks. Start new work from current main, with a separate branch/worktree and output root for each development lane. Do not resume an R4-P0 execution packet or use an archived checkout's guidance as current authority.

## Current code and evidence

- Maintained R4-P0 implementation and tests: `experiments/r4_residual_graph/` and `tests/experiments/r4_residual_graph/` on main. Final-delivery's versions matched main exactly at retirement.
- Accepted result and qualifications: [R4-P0 findings](R4_P0_FINDINGS.md). The result remains `NO_HYPOTHESIS_NOMINATED`; no scientific identities, outputs, failures or acceptance records were changed.
- Generation-8 retained evidence: `/data/q-trad/r4-p0/r4-p0-development-g8-faef646/`.
- Earlier R4 evidence, including failed generation-6 history: `/workspace/tmp/qtrad-r4/`.
- Programme acceptance, monitoring and diagnostic records: `/workspace/tmp/MAP_orchestrator/r4-p0-20260828-ddd66f9/`. Paths inside these records describe historical execution; they do not require permanent working checkouts.
- The five-root data migration is complete: canonical original paths are read-only mounts to verified `/data` copies, and underlying originals have been reclaimed. See [the completion record](TMP_REMAINING_STORAGE_MIGRATION.md).

## R4-P1 boundary

[R4-P1's plan](R4_P1_EXECUTION_PLAN.md) remains labelled draft pending operator approval. Cleanup grants no scientific execution authority. Once authorised, use a fresh environment and the proposed `experiments/r4_p1_learnability/` namespace.

The immediate LAB-0 development parent is `/workspace/tmp/qtrad-r2-lab/LAB-0/lab-manifest.json`, SHA-256 `462e40fa84038156b16c68bde4b68d574ab7862c680657ebd1a0035b39bf0072`. Authenticate the parent and consumed children under the plan. R4-P1 permits only DEV_1–DEV_3; do not load terminal-former-holdout rows or use published terminal aggregates as evaluation data. Accepted development artefacts may be reused only where their immediate identities and semantics match.

The second development lane needs its own authority, ownership and output boundary. Neither lane inherits R4-P0's old environment or permission to mutate its evidence.

## Retained source

Every removed checkout has an archive branch `archive/<checkout-name>-20260908` in the main repository. These are historical references, not active development branches.

| Retired checkout | Exact commit |
| --- | --- |
| r4-p0-final-delivery | `724e2e0c20c681f41023096cabc1c7db115035a0` |
| r4-p0-final-metrics | `40a9d01a7e69abbd1af521396b0b3828b11656be` |
| r4-p0-inference-recovery | `1bdc976f942a6c1c8d1a603118000f175a08db29` |
| r4-p0-remediation-7 | `cfe35cfe74c61cd407ac8117f743f269a9d0f8dc` |
| r4-p0-scientific-faef646 | `faef6462c6929da7e91eea12970a72e53a2647e6` |
| r4-p0-terminal-consumers | `338710041aa982900d3c0a69f6a71795ac77e7d2` |
| r4-p0-terminal-reporting | `27166ffeec8059fc3a8fd4c280552676bf61ea49` |

The self-contained archive is `/data/q-trad/source-archives/r4-p0-closeout-20260908/source.bundle`. It includes these seven references and the earlier `r4base2` and milestone draft archives. Bundle verification, a separate bare restoration and full Git object checking passed; each restored reference matched its original commit. `archive.json` records the bundle SHA-256, original paths, refs and runtime provenance; per-worktree removal receipts and `completion.json` record retirement. Archive refs remain local; this operation did not push them.

For example, inspect the original CUDA setter without restoring a worktree:

```sh
git show faef6462c6929da7e91eea12970a72e53a2647e6:experiments/r4_residual_graph/grouped.py
```

If local archive refs are unavailable, restore the bundle into a separate repository:

```sh
git clone --bare /data/q-trad/source-archives/r4-p0-closeout-20260908/source.bundle /path/to/absent/r4-p0-archive.git
```

## Runtime retirement

All seven worktrees were clean, with no ordinary untracked changes. Ignored content consisted of environments and test/lint/bytecode caches. Workspace symlink and editable-install checks found only dependencies within the retiring worktrees; no nested mounts or observed process users remained. The three root-owned shell processes inaccessible to the ordinary process scan were checked separately and referenced the main workspace and system shell.

The removed environment's 101 installed package versions, editable-install records, interpreter configuration and environment symlinks are preserved in `archive.json`. This is a provenance inventory, not a runnable environment snapshot or a claim that a newly created environment reproduces historical numerical behaviour. Existing historical runtime evidence remains authoritative.

Removal used ordinary `git worktree remove` without force. Historical source refs and the independently restored archive preserve the exact source; no scientific fitting, metric recomputation, provider access or evidence migration was needed.
