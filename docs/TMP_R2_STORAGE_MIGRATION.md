# First R2 storage migration completed

Date: 2026-09-08. Both operator-approved roots now reside on `/data`, with the original absolute
paths bound to those same directories. The redundant workspace contents were removed after
independent copy verification and mounted-consumer compatibility assessment. Empty underlying
directories remain as mountpoints. No scientific artefact, receipt or frozen path was rewritten.

## Scope and verification

Destinations beneath `/data/q-trad/retained-workspace-tmp/`:

- `ibkr-historical-r2-20260810T081317Z`
- `r2-confirmatory-ibkr-historical-20260820T051751Z`

The copy contains **25,784 files, 1,261 directories and 60,795,125,720 logical bytes** (56.620 GiB;
about 56.70 GiB allocated). `rsync -aHAXS --numeric-ids --relative` copied into absent destinations
in 303.03 seconds. Independent destination verification took 209.46 seconds and matched every
relative entry, byte hash, mode, owner/group, modification timestamp, extended attribute and
hardlink group. There are no symlinks or internal hardlink groups.

Audit directory: `/workspace/tmp/storage-migration-20260908-r2/`.

| Evidence | SHA-256 |
| --- | --- |
| `source-sha256.json` | `70329af624856800d1c6c3d4e5c1e048aba3b4dc721f9eecac7d1d14887ae842` |
| `destination-verification.json` | `7601c0723833352210625b4d56d9d2194886be4bf93e3a4278a4bb61736db2cf` |
| `original-baseline.json` | `2257d9f9257bd0c0dc719c7bb0577284d71a574f6046751cae8794f0340ddef7` |
| Original strict `acceptance.py` | `999269791cd6b79bba01ffae6897129c67da0de4d877c693cdf3293e42f289fb` |

The inventories, original failed strict post-mount check, adjudication and `source-removal.json`
remain immutable audit evidence. No consumer streams were iterated, models fitted, forecasts
regenerated or holdout analysis repeated.

## Mounted compatibility and read-only correction

Stage 6/7 and R3 require canonical, nonsymlink original paths. Both mounted paths were confirmed
to expose exactly the corresponding `/data` directory by filesystem identity. All six metadata/
path outcomes matched the original baseline, excluding timings only: five PASS and the already
known historical remediation Stage 8 rejection, `Stage 8 v3 payload fields are not exact`.
The current confirmatory promotion and R3 authority/path checks passed. This preserves an existing
historical rejection; it does not assert that every old object authenticates under current code.

The rebuilt aliases were **writable**, despite the original Dev Containers `readonly` strings.
The strict migration helper correctly rejected that extra safeguard. A pinned upstream Dev Containers
Compose converter emits only `source:target`, dropping read-only options; the installed host version
was unavailable, so this is consistent source evidence rather than proof of its exact version.

Root explicitly accepted the writable aliases for this cleanup after independent review: none of the
actual scientific consumers requires a read-only filesystem, the original roots and direct `/data`
access were already writable, and both full metadata inventories remained unchanged. This revises
only the added migration safeguard, not scientific authority or immutable-evidence requirements.
The rejected receipt is preserved and never relabelled as successful.

The two permanent aliases now use native Compose `read_only: true` in
[compose.devcontainer.yaml](../.devcontainer/compose.devcontainer.yaml), with `create_host_path: false`.
Their duplicate Dev Containers entries were removed. **The current aliases remain writable; the
corrected read-only configuration applies at the next normal container rebuild.** No immediate
additional rebuild is required to complete this migration.

## Original cleanup and retention

Before removal, the temporary `/workspace-tmp-migration-source` alias was verified to expose the
workspace device, distinct from the destination device, with no nested mounts or observed open
process references under either original root. Both source and destination metadata matched the
accepted inventory. Only contents of the two exact underlying source roots were removed; root
mountpoint directories, destinations and all audit evidence remain.

`source-removal.json` records PASS at 2026-09-08T06:41:17Z and a contemporaneous workspace free-space
increase of 60,876,877,824 bytes (about 56.70 GiB). This is a filesystem observation, not a guarantee
of host VHD compaction. The temporary source-alias configuration was removed; its running mount
disappears at the next normal rebuild.

Keep both permanent original-path aliases while frozen R2/R3 consumers require them. The external
`r2-confirmatory-ibkr-historical-20260820T051751Z-authority/` sibling remains in workspace tmp.
Other proposed batches, worktree/runtime relocations and cache cleanup were not performed.
