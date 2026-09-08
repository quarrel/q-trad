# Remaining retained-data migration

Date: 2026-09-08. Status: five approved roots copied and independently verified; original-path mount activation and source reclamation await a container rebuild. Original files remain intact.

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

Source hashing took 10.63 seconds, copying 14.22 seconds and independent destination verification 3.96 seconds. The root reviewed the verifier and accepted the byte/metadata evidence; this is copy acceptance, not completed cutover.

## Access and remaining cutover

Native Compose binds are prepared in `.devcontainer/compose.devcontainer.yaml`: exact original `/workspace/tmp/<basename>` targets, corresponding host `/mnt/wsl/data/q-trad-bulkdata/q-trad/retained-workspace-tmp/<basename>` sources, `read_only: true`, and `create_host_path: false`. The previous two R2 aliases are confirmed read-only after the latest rebuild.

R4 readers reject symlink roots and children; attempt identity requires a canonical absolute output root. LAB foundation identities include resolved paths. Canonical binds preserve those contracts without rewriting evidence. The remaining historical scripts/consumers examined revealed no bind incompatibility, but the audit does not establish that every historical consumer would accept symlinks. Preserve the original paths for all five roots.

A temporary native Compose bind from `./tmp` to `/workspace-tmp-migration-source` exposes the underlying originals after the new aliases cover them. Its sole purpose is verified source cleanup; remove its configuration immediately after that cleanup. The running temporary mount then expires on the next normal rebuild.

After rebuilding:

1. Confirm all five original aliases are canonical ordinary directories, actual read-only mountpoints and `samefile` with their `/data` destinations. Check there are no unexpected nested mounts.
2. Run the retained metadata inventory against `/data/q-trad/retained-workspace-tmp` and `/workspace-tmp-migration-source`, with distinct create-only output filenames. Compare complete records and hardlink groups with the pinned source inventory, excluding only its SHA fields. Reuse accepted byte verification if there is no evidence of change; do not rerun scientific verification.
3. Confirm cleanup roots are distinct workspace-device directories, not destination aliases/mountpoints, and have no observed process users. Preserve every original if there is a mismatch or unresolved ownership.
4. Remove only the contents of these five exact cleanup roots, retaining empty mountpoint directories and all destination data. Record exact deletion scope, completed roots, failures and space measurements in a create-only receipt. Never delete through the original mounted paths or `/data`.
5. Recheck destination metadata and original-path identity; remove the temporary Compose entry and record completion. No additional operator permission is needed for this already-authorised cleanup.

The current container cannot activate these host mounts. Do not delete originals before the rebuilt access arrangement is accepted. Other evidence, external authority siblings, caches and runtime environments remain outside this deletion scope.

## Worktree cleanup completed

Two superseded dirty worktrees were preserved as Git archives before ordinary, non-forced removal:

| Removed checkout | Retained archive branch | Archive commit |
| --- | --- | --- |
| `tmp/worktrees/r4base2` | `archive/r4base2-draft-20260908` | `73374679a0060dba547d5aa55bd960ebea405f67` |
| `tmp/worktrees/r4-p0-milestone-static` | `archive/r4-p0-milestone-draft-20260908` | `de308f83beab30b7ea7f3e81fabf913a2e48d424` |

All four changed/draft file blobs matched pre-archive hashes. Original bases remain reachable, and the original milestone branch is unchanged. The unused self-contained r4base2 environment and caches were removed with its checkout. `worktree-custody-closeout.json` retains the audit and exact hashes. Archives are local, not pushed or integrated.

Seven evidence-bound source/runtime worktrees remain, including remediation-7's accepted environment and final-delivery's dependent links. Pytest outputs, `r3f-base.Uv7C6r` and unclassified diagnostics remain untouched.

## Interpreting disk usage

`du -ks tmp` follows the retained-data mounts and therefore includes data physically stored on `/data`. Use `du -xks tmp` to measure only the workspace filesystem. After this worktree cleanup, those commands respectively reported 79,592,344 KiB (75.9 GiB) and 20,142,496 KiB (19.2 GiB). Their 56.7 GiB difference is the first migrated batch, not a duplicate workspace copy. The five pending roots still contribute to the 19.2 GiB until source reclamation.
