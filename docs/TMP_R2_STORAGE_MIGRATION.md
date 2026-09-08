# First R2 storage migration: verified copy, cutover pending

Date: 2026-09-08. The operator authorised these two directories to move. Copies are independently
verified on `/data`; original files still occupy workspace storage. Container recreation is the
remaining external prerequisite. No scientific results, manifests, receipts or frozen paths changed.

## Exact scope and evidence

Both source basenames are preserved under `/data/q-trad/retained-workspace-tmp/`:

- `ibkr-historical-r2-20260810T081317Z`
- `r2-confirmatory-ibkr-historical-20260820T051751Z`

The copy contains **25,784 files, 1,261 directories and 60,795,125,720 logical bytes** (56.620 GiB;
about 56.70 GiB allocated). `rsync -aHAXS --numeric-ids --relative` copied into absent destinations
in 303.03 seconds. Independent destination-only verification took 209.46 seconds and matched every
relative entry, byte hash, mode, owner/group, modification timestamp, extended attribute and hardlink
group. There are no symlinks or internal hardlink groups. A fresh metadata inventory confirmed the
originals unchanged after copying.

Retained audit directory: `/workspace/tmp/storage-migration-20260908-r2/`.

| Evidence | SHA-256 |
| --- | --- |
| `source-sha256.json` | `70329af624856800d1c6c3d4e5c1e048aba3b4dc721f9eecac7d1d14887ae842` |
| `destination-verification.json` | `7601c0723833352210625b4d56d9d2194886be4bf93e3a4278a4bb61736db2cf` |
| `original-baseline.json` | `2257d9f9257bd0c0dc719c7bb0577284d71a574f6046751cae8794f0340ddef7` |
| `acceptance.py` | `999269791cd6b79bba01ffae6897129c67da0de4d877c693cdf3293e42f289fb` |

The directory also retains inventories before/after copying, the exact copy command/result,
independent verifier, comparison tests, root review and cutover authority. These small audit files
remain in workspace tmp. No consumer streams were iterated, models fitted, forecasts regenerated
or holdout analysis repeated.

## Why a container rebuild is needed

Stage 6 `ibkr_results._require_file` and Stage 7 `_require_exact_tree` reject symlink or
noncanonical ancestors. R3 `_authoritative_native_locators` binds frozen literal paths and
`_reject_native_authenticated_components` rejects symlink ancestors. A root symlink or edited
historical manifest would not preserve these contracts. The audit found no persisted device/inode
requirement in these consumers.

[devcontainer.json](../.devcontainer/devcontainer.json) now prepares read-only binds from
`/mnt/wsl/data/q-trad-bulkdata/q-trad/retained-workspace-tmp/<basename>` to each original
`/workspace/tmp/<basename>`. These are host paths beneath the already-working `/data` mount.
The original paths will remain real directories backed by the verified data disk.

This running container lacks `CAP_SYS_ADMIN`, including under sudo, and cannot reach a Docker daemon.
It cannot activate the mounts itself. Rebuild/recreate the dev container from the host to apply them;
the current container and its sources have not been disrupted.

A temporary fourth mount, `${localWorkspaceFolder}/tmp` to `/workspace-tmp-migration-source`,
exposes the underlying workspace originals after the read-only aliases hide them. Its sole purpose is
controlled original cleanup. Remove that entry after cleanup, and remove the active temporary mount
at a subsequent host/container recreation. Do not delete through the original read-only aliases or
through `/data`.

## Resume after rebuilding

1. Confirm both old paths are read-only mounts onto the corresponding verified `/data` directories.
   Confirm the temporary source alias refers to the workspace filesystem and does **not** expose the
   destination trees. The acceptance script checks old-path mount identity, canonical paths and read-only state.
2. Run the prepared metadata-only compatibility check from `/workspace`:

   ```sh
   uv run --no-sync --offline python /workspace/tmp/storage-migration-20260908-r2/acceptance.py --phase post-mount --baseline /workspace/tmp/storage-migration-20260908-r2/original-baseline.json --output /workspace/tmp/storage-migration-20260908-r2/post-mount-acceptance.json
   ```

   The output is create-only. Preserve a failed invocation and use a distinct receipt name for any
   justified subsequent check; never overwrite an audit receipt.
3. Require `COMPATIBLE_WITH_BASELINE` and `destination_acceptance: true`. Five original checks
   passed; the older remediation Stage 8 object already failed current-schema authentication with
   `ValueError: Stage 8 v3 payload fields are not exact`. The immutable baseline preserves this exact
   rejection. Post-mount compatibility must reproduce all six results, including details/errors,
   plus successful mount checks. It does not claim all historical objects authenticate under current code.
4. Reconcile the underlying originals through the temporary alias against the retained source inventory,
   establish no writers or divergent new files, and ensure they are not mountpoints or the same files/device
   as the destinations. Then remove only the two exact underlying original directories through that alias,
   retaining the verified copies, audit and external authority sibling. The verified copies become the
   retained originals; do not perform a broad tmp cleanup.
5. Record the cutover, source removal and measured storage change; remove the temporary config entry.
   Keep the two read-only retained-consumer mounts while the frozen R2/R3 evidence paths are required.
   Retire them only when all named consumers have an accepted replacement access contract.

No original deletion has occurred. Until mounted compatibility succeeds, the unchanged workspace
sources remain the rollback copy. Do not repeat bulk copying or scientific validation merely because
the container restarted. The R3 approval sibling
`/workspace/tmp/r2-confirmatory-ibkr-historical-20260820T051751Z-authority/` is outside this migration
and stays in place.
