# Workspace tmp storage relocation proposal

Date: 2026-09-08. Status: proposal only; no bulk data has been moved.

## Completed administrative cleanup

The operator accepted and merged R4-P0 delivery `724e2e0`. Its execution and remediation plans
are archived unchanged under `docs/archive/r4/`; current PLAN, STATUS and findings links are updated.
The two registrations pointing to missing `/tmp/r4base` and `/tmp/r4base2` were pruned.
Clean completed checkouts `tmp/worktrees/r4-p0-a`, `r4-p0-b` and `r4-p0-c` were removed through
`git worktree remove`, including their unused local environments and caches. Their three Git branches
and source commits remain. The owner checked cleanliness, ignored/untracked content, runtime references
and process use before removal. No retained scientific outputs or failure evidence were deleted.

`du -x` measured tmp at 87.356 GiB before and 80.968 GiB after cleanup, a 6.389 GiB reduction
in attributed allocated space. These are directory measurements, not a physical block reclamation guarantee.
One subtree, `/workspace/tmp/tmp.ywVMu1hpTZ/new`, was unreadable (permission denied); totals are lower
bounds. Its permissions and contents were left unchanged. The inventory includes top-level regular files;
none appeared among the largest 30 entries. Measurements are a snapshot, not ongoing capacity monitoring.

`/data` is an ext4 mount from `/dev/sde[/q-trad-bulkdata]`, with about 882 GiB available at inspection.
It currently contains about 71.78 GiB under `/data/q-trad/r4-p0`. Keep those existing accepted objects
separate from the proposed relocation; do not merge or overwrite similarly named artefacts.

## Recommended relocation: about 67.97 GiB

Preserve each directory name and hierarchy beneath `/data/q-trad/retained-workspace-tmp/`.
All sources below are relative to `/workspace/tmp/`; each destination is that new base plus the same name.
These are evidence-preserving migration candidates, not disposable data. Each needs the input/path audit
and verified-copy procedure below before cutover.

| Source directory | Allocated GiB | Contents and disposition |
| --- | ---: | --- |
| `ibkr-historical-r2-20260810T081317Z` | 34.225 | Stage 6–8 historical inputs, checkpoints, remediation attempts and evidence. First large candidate; preserve all stages and failed attempts. |
| `r2-confirmatory-ibkr-historical-20260820T051751Z` | 22.471 | OOF/preprocessing, forecasts and G2 preparation. Directly cited by the retained R3 report; migrate as a complete retained parent. |
| `qtrad-r4` | 5.966 | Earlier R4 preparations, generation-6 attempt history, smoke and performance material. Preserve generations and failures; do not combine with newer `/data/q-trad/r4-p0` identities. |
| `r2-confirmatory-ibkr-historical-20260816T090943Z` | 3.456 | Earlier confirmatory-run material; retain its original identity and failed/intermediate state until disposition is established. |
| `qtrad-r2-lab` | 1.240 | LAB-0 features/targets/context and LAB-S/Z reports. The LAB manifest is the cited immediate R4 parent; path compatibility is particularly important. |
| `capture-snapshot-20260717T044430Z` | 0.538 | Snapshot/import evidence cited in the historical ranking report. Relocate the retained snapshot, without altering live collectors. |
| `ibkr-run-20260806T035300Z` | 0.076 | Historical run evidence; preserve original run layout and manifests. |

Start with the two largest R2 roots: together they represent **56.70 GiB**. This is a proposed order,
not a finding that their path contracts have already been cleared. No forecast/model recomputation is
needed merely to copy bytes and authenticate retained evidence.

## Keep in place for now

| Location under tmp | Approximate size after cleanup | Reason |
| --- | ---: | --- |
| `worktrees/` | 10.29 GiB | Accepted environment/source dependencies and two unresolved dirty worktrees; see below. |
| `pytest-of-vscode/` | 1.60 GiB | Potential disposable test output, but age alone is insufficient: check active users and retained failure references before deleting exact obsolete runs. Do not migrate blindly as evidence. |
| `r3f-base.Uv7C6r/` | 0.64 GiB | Mostly an editable Python environment; source/ownership and continuing dependency audit remains. |
| `MAP_orchestrator/` | 0.047 GiB | Small programme journals, receipts, failure diagnostics and absolute-path control scripts. Keep available at the original locations. |
| Other probes and scratch directories | individually small | Several R4 calibration/oracle/memory probes contain about 13 MiB each; classify retained diagnostic evidence versus reproducible scratch before removal. Unnamed tmp directories are not assumed disposable. |

Retain `r4-p0-remediation-7` (about 5.1 GiB): it supplies the accepted environment, its editable install
points to its source, and final-delivery's environment links depend on it. Retain original scientific,
terminal, recovery and metrics checkouts because evidence names those paths. Their source trees are small.
Retain final-delivery for its validation/document references. Retain `r4-p0-milestone-static` because it
has modified PLAN/STATUS and an untracked differing findings draft, and `r4base2` because it has a modified
test plus a roughly 5.1 GiB environment. None should be force-removed or moved as an ordinary directory.

If these runtime environments are relocated later, audit editable `.pth`/distribution records, interpreter
links and script shebangs. Do not copy a virtual environment and assume it remains functional or has the
same execution provenance. Prefer retaining the accepted environment until a separately validated replacement
is actually needed. Use Git-aware worktree operations for any future source relocation.

## Migration procedure and acceptance

1. Establish exact source/destination and process ownership for one retained root. Inventory file types,
   relative paths, sizes, SHA-256 values, hardlinks, symlinks and relevant metadata. Capture the existing
   manifests and their identities; identify absolute-path and filesystem-identity consumers before copying.
2. Create an absent destination on `/data`; copy without rewriting JSON/manifests or following symlinks
   outside the selected root. Preserve bytes, relative layout and applicable metadata/hardlinks. Retain
   the source until destination and consumer acceptance are complete. A partial copy is not an accepted archive.
3. Independently verify the copied inventory and the required retained-evidence consumers. Reuse accepted
   scientific proofs; do not retrain, regenerate predictions, reacquire provider data or reopen holdouts.
4. Resolve old absolute paths explicitly. Some R4 attempt identities require
   `Path(output_root) == Path(output_root).resolve()` and hash that output root into identity
   (`experiments/r4_residual_graph/attempt_artifacts.py:138–151`). A replacement symlink can fail that rule.
   An approved path-preserving mount may suit an archive consumer, but must be tested for its actual
   device/inode/path contracts and container persistence. Otherwise use an explicit relocation map and
   newly authenticated physical-location receipt while preserving original provenance. Neither strategy
   is selected or installed by this proposal; never rewrite immutable path fields to make a check pass.
5. Record source-to-destination provenance, verification evidence and the tested access arrangement.
   Remove original bulk files only after acceptance and a clear rollback/retention decision. Any temporary
   compatibility bridge must have a named consumer and a removal trigger; do not accumulate permanent
   unexplained symlinks. Deleting the sole original before copy validation is not part of the proposal.

The next decision is approval of a migration batch and its verified cutover method. Bulk migration,
additional cache deletion, runtime relocation and remote publication have not been performed.
