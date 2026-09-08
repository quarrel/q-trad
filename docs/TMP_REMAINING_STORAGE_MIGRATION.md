# Remaining retained-data migration

Date: 2026-09-08. Status: COMPLETE. All five retained roots are served through canonical read-only mounts from `/data`; verified underlying originals have been emptied, retaining their mountpoint directories. The temporary cleanup mount configuration has been removed.

## Verified copies

Each basename below is preserved beneath `/data/q-trad/retained-workspace-tmp/`:

- `qtrad-r4`
- `r2-confirmatory-ibkr-historical-20260816T090943Z`
- `qtrad-r2-lab`
- `capture-snapshot-20260717T044430Z`
- `ibkr-run-20260806T035300Z`

The copies contain 5,367 regular files and 1,783 directories, totalling 12,084,708,388 logical bytes (11.255 GiB; approximately 11.28 GiB allocated). An independent destination traversal verified exact names, types, SHA-256 bytes, mode, owner/group, modification times, base64 extended attributes and hardlink groups. There were no symlinks or internal hardlinks, and no missing or mismatched entries. Source metadata was unchanged before hashing and after copying. No scientific computation or provider operation occurred.

Evidence is retained in `/workspace/tmp/storage-migration-20260908-remaining/`:

| Evidence | SHA-256 |
| --- | --- |
| `source-sha256.json` | `92d9fdcd3fb23ab34e922fc82c7bb47e3bf3760b4a56375cbc8222a6b4bd7a58` |
| `destination-verification.json` | `c86c84ffd2af647461234a4b1a4af0af8098b45f5a598e3633cff63e0410a711` |
| `verify_destination.py` | `98b9d730a29b1f0c2f94d7819cd7f389b2863d61e3fe953a8f74685525eafaed` |

Source hashing took 10.63 seconds, copying 14.22 seconds and independent destination verification 3.96 seconds. The accepted byte verification was reused at cutover after both source and destination metadata matched the pinned inventory exactly.

## Completed cutover

Native Compose binds in `.devcontainer/compose.devcontainer.yaml` preserve exact original `/workspace/tmp/<basename>` targets, corresponding host `/mnt/wsl/data/q-trad-bulkdata/q-trad/retained-workspace-tmp/<basename>` sources, `read_only: true`, and `create_host_path: false`. All five aliases were verified as canonical ordinary directories, actual read-only mountpoints and `samefile` with their `/data` destinations.

R4 readers reject symlink roots and children; attempt identity requires a canonical absolute output root. LAB foundation identities include resolved paths. Canonical binds preserve those contracts without rewriting evidence. The remaining historical scripts/consumers examined revealed no bind incompatibility, but the audit does not establish that every historical consumer would accept symlinks. Preserve the original paths for all five roots.

The temporary native Compose bind from `./tmp` to `/workspace-tmp-migration-source` exposed underlying originals for cleanup. Checks confirmed distinct workspace-device source directories, no nested mounts and no process users by source inode across working directories, executables, open descriptors and memory mappings. Root-owned processes were checked with their required access privileges.

Both complete metadata inventories matched the pinned source records, excluding only SHA fields already covered by accepted byte verification. Only the five named source roots' contents were removed. A subsequent destination inventory remained identical, and original-path identity and read-only mount checks passed again.

Create-only receipts in the audit directory are `cutover-accepted.json`, five `reclaimed-<basename>.json` records and `cutover-complete.json`; the before/after metadata inventories and `finish_cutover.py` preserve verification and deletion scope. The first privileged process-scan attempt stopped on a permission error before acceptance or deletion; the completed scan used ordinary-user access plus bounded privileged checks for root-owned processes.

The source allocation fell from 11,823,468 KiB to 28 KiB (empty mountpoint directories), reclaiming about 11.28 GiB. Destination data and scientific identities were unchanged. The temporary mount has been removed from Compose configuration; its current runtime instance expires on the next normal rebuild. No further rebuild is needed to complete data reclamation.

## Worktree cleanup completed

Two superseded dirty worktrees were preserved as Git archives before ordinary, non-forced removal:

| Removed checkout | Retained archive branch | Archive commit |
| --- | --- | --- |
| `tmp/worktrees/r4base2` | `archive/r4base2-draft-20260908` | `73374679a0060dba547d5aa55bd960ebea405f67` |
| `tmp/worktrees/r4-p0-milestone-static` | `archive/r4-p0-milestone-draft-20260908` | `de308f83beab30b7ea7f3e81fabf913a2e48d424` |

All four changed/draft file blobs matched pre-archive hashes. Original bases remain reachable, and the original milestone branch is unchanged. The unused self-contained r4base2 environment and caches were removed with its checkout. `worktree-custody-closeout.json` retains the audit and exact hashes. Archives are local, not pushed or integrated.

The seven remaining source/runtime worktrees were subsequently archived and removed; see [the successor handoff](R4_SUCCESSOR_HANDOFF.md). Their old environment and dependent links are retired. Retained scientific evidence, pytest outputs outside those worktrees, `r3f-base.Uv7C6r` and unclassified diagnostics remain untouched.

## Interpreting disk usage

`du -ks tmp` follows retained-data mounts and therefore includes data physically stored on `/data`. Use `du -xks tmp` to measure only the workspace filesystem, noting that mounted paths can hide underlying originals. The earlier pre-cutover measurements were 79,592,344 KiB including mounts and 20,142,496 KiB on the workspace filesystem. After the subsequent seven-worktree retirement and mount activation, `du -xks tmp` reported 2,895,856 KiB; this excludes underlying originals exposed through `/workspace-tmp-migration-source` and is not proof of their reclamation.

After verified source reclamation, `du -xsk /workspace-tmp-migration-source` measured 2,900,352 KiB (about 2.77 GiB). This directly measures the underlying workspace tmp tree, including the retained empty mountpoint directories, rather than counting the `/data` aliases.
