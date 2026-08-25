# LAB-T retained-run remediation audit

All paths and results in this record are `EXPLORATORY_POST_HOC_ONLY` and retain source class
`IBKR_HISTORICAL_RESEARCH`.

## Failed attempt

Commit `9a086a579b835b54eab6f01c23cc36ff2fb74c57` completed its first retained
run at `/workspace/tmp/qtrad-r2-lab/LAB-T`, but its CORE_6 support was
239,655 rather than the authenticated LAB-0 support of 239,535. The original
output directory was removed after its configuration count, trust-check
failure, terminal-access state, and every child hash were compacted into
`/workspace/tmp/qtrad-r2-lab/LAB-T-rerun-1/failed-attempts.jsonl` (SHA-256
`13ecb869b279dabc4b080fd37d1aa6f26555a0e6fec893a302f51ff32df366c1`).
The rejected attempt must not be used for selection or terminal access. No
finalist qualified and the former consumed holdout was not accessed.

The cause was timestamp/block-label inference for development membership. It
admitted the final 20 not-yet-terminal-mature opportunities for each of six
instruments. LAB-0 requires the retained validation target-ID memberships and
the terminal-start maturity cutoff.

## Finite output and downstream audit

The launched CLI can emit a JSONL attempt register, a finalist freeze,
model-results Parquet, feature-importance Parquet, result Markdown, and a run
summary. The failed attempt emitted 16 result/register rows and 468 importance
rows. With at most two nonlinear finalists, the bounded successful shape is at
most 24 result/register rows; finalist IDs are bounded to pooled Ridge plus one
histogram and one MLP. Importance cardinality is bounded by configured
histogram variants, three folds, two universes, and the authenticated P0/P1
feature schemas. Polars reads every immediate output. The run has no database,
provider, promotion, deployment, collector, or external-state consumer.

The same membership defect affects ZERO_RETURN support, every model metric,
breadth/concentration statistics, finalist selection, and all sibling result
files. Therefore all first-attempt outputs are invalid together. The compact
failed-attempt record retains the failure and configuration count without
retaining the invalid result files. The create-only finalist gate nevertheless
prevented terminal access.

The first attempt used 52 KiB of output. Preflight had 84 GiB available memory,
13 GiB available swap, and 631 GiB available workspace disk. The exact run
finished below the 64 GiB virtual-memory ceiling and eight-hour timeout.
No per-file, nesting, transaction, decoder, or resource bound was approached.

## Owning correction and rerun gate

The loader will authenticate the LAB-0-bound terminal foundation and its three
fold children, consume the exact training and validation target-ID memberships
for CORE_6, and apply the pre-terminal maturity cutoff. ALL_20 has no historical
target-ID membership for the fourteen added instruments, so it retains the
declared chronological block extension with the same terminal-start maturity
cutoff. A fail-closed CORE_6 gate requires support 239,535 and the authenticated
ZERO_RETURN MSE before any attempt is registered.

A focused regression excludes timestamp-adjacent rows not present in retained
membership. The exact production smoke must exercise all six CORE instruments,
authenticate the fold children, pass the support/MSE gate, and write every
deliverable without terminal access. Only then may one rerun use the new
create-only destination `/workspace/tmp/qtrad-r2-lab/LAB-T-rerun-1`.
