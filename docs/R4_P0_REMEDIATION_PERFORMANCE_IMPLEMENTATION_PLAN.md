# R4-P0 remediation and performance implementation plan

## Status and authority

This operator-approved plan governs remediation-7 qualification before the benchmark-10 gate and the
subsequent operational preparation generation 8. Generation-1 through generation-8 evidence at their
existing paths is immutable historical evidence and is never adopted, repaired or selectively edited.
The only future qualification paths are the currently absent
`/data/q-trad/r4-p0/remediation-7/benchmark-10` and sibling `benchmark-10-cache`. Operational
preparation generation 8 is not authorised until independent exact-head owner review accepts this
candidate. Any code, scientific, cache, deterministic-device or numerical-runtime change requires a
fresh candidate, preparation and G0.
This work is outcome-blind: it does not access terminal outcomes, providers or terminal-former-holdout
inputs.

The fixed schedule is five fitted families × three seeds × three primary stages = **45 fits**.
R4.D comprises **30 fits**: 15 DEV_2 fits trained on DEV_1 and 15 DEV_3 fits trained on
DEV_1+DEV_2. Separately authorised R4.E comprises **15 terminal-former-holdout fits** only after
development closure and the terminal gate.

## Required release layout

A fresh release uses:

```text
config/
input/
stage-cache/<stage>/<cache-id>/
  manifest.json
  train/part-*.npy
  predict/part-*.npy
  keys/part-*.jsonl
  seal.json
cache-verification/<supervisor-epoch-id>.json
sessions/<supervisor-epoch-id>.json
staging/cache/<build-id>/
staging/attempt/<attempt-id>/
attempts/<slot-id>/attempt-<n>/
  model.pt
  model.json
  prediction/part-*.npy
  prediction/keys-*.jsonl
  result.json
  manifest.json
  seal.json
failures/<slot-id>/attempt-<n>/
  payload/
  closure.json
register/attempts/<attempt-id>.STARTED.json
register/attempts/<attempt-id>.SUCCEEDED.json
register/attempts/<attempt-id>.FAILED.json
register/attempts/<release-event-id>.INVALIDATED.json
benchmark/<benchmark-id>/
```

The register directory is the one authoritative create-only attempt journal. A fresh release must not
create `attempts.jsonl` or any second ledger. Historical generation-specific JSONL remains untouched.

## WP0 — frozen contract

The implementation preserves the five fitted families, seeds, chronological blocks, common support,
equal-instrument objective, missing-target semantics and terminal isolation. Builder semantic closure
is defined narrowly enough to prove that adopted bytes retain the same meaning. Stop for any change
to scientific meaning, chronology, support or terminal boundaries.

## WP1 — CPU persistence and sealed attempt publication

Residual predictions are detached and moved to CPU FP32 before adding the CPU FP32 local Ridge
forecast. Shape and finiteness are checked before any public write.

A successful attempt is one transaction:

1. construct all payloads in `staging/attempt/<attempt-id>`;
2. validate hashes, shapes, canonical keys and cross-references;
3. fsync every file and nested directory;
4. write and fsync `seal.json` last;
5. prove staging and destination are on the same filesystem;
6. atomically rename onto an absent final bundle and fsync both parents; and
7. authenticate the public bundle before create-only publication and read-back of SUCCEEDED.

No model, metadata, prediction or result is independently public. Crash testing covers each boundary.

## WP2 — durable authenticated stage cache

`stage_cache.py` owns `build-stage-cache` and `verify-stage-cache`. Each unique decision timestamp
is materialised once. Cache content includes preprocessed FP32 values, value/availability/node masks,
training residuals, target-node indices, equal-instrument weights, block membership and canonical
prediction keys. Arrays are sharded mmap-compatible `.npy`; keys are canonical JSONL. Evaluation
outcomes are forbidden.

Preparation generation 8 first seals one canonical raw-tensor mmap store, generated once in canonical
order with at most eight bounded workers. It also seals compact stage-specific packages binding the
raw-store, support, row mapping and fitted preprocessor identities. Cache construction consumes those
authenticated preparation outputs directly, without reloading the LAB-0 parent or regenerating feature
windows. Incomplete staging is not adopted and these packages are not scientific-attempt artefacts.

The manifest binds schema; producing head and application provenance; training/stage blocks;
foundation, support, tensor, preprocessor, configuration and numerical-runtime identities; canonical
universe/node/feature order; dtype and endianness; row/timestamp counts; and each shard path, byte size,
semantic digest and container digest. The cache identity is content-addressed.

`cache_builder_semantic_closure` hashes only code, schema and configuration capable of changing cache
meaning. Producer head is provenance, not semantic identity. Cross-head adoption requires a
byte-identical closure, unchanged semantic inputs, a complete re-verification, and a new G0 that binds
both adopted cache and provenance. A builder change requires rebuild.

The seal is written last. Every supervisor epoch fully rehashes the cache and writes a receipt binding
canonical no-symlink paths plus device, inode, size and ctime. Slot workers check that metadata before
and after mmap and bind the receipt. Drift stops execution; final closure performs another complete
rehash.

## WP3 — grouped deterministic FP32 hot path

Rows are grouped by decision timestamp. Pooled and graph families encode the 20-node graph once per
timestamp, calculate context once, then score all eligible targets. Local-family sequences remain
independent per target, with no cross-instrument hidden state or context.

Canonical row order, row weight `1 / (20 * instrument_count)`, one full-epoch weighted loss sum and
exactly one Adam update per epoch are unchanged. The implementation performs ordered chunk-wise
backward gradient accumulation so no full-epoch autograd graph is retained; because every chunk uses
its unchanged global row weights, the accumulated gradient is exactly the gradient of that loss sum.
Missing targets retain their existing meaning. Data loads use bounded mmap shards, pinned host buffers,
bounded double buffering and non-blocking CUDA.
Prediction output is streamed into ordered shards and digests. Operational batch size is identity
bearing. Masked-LSTM performance is profiled before any semantic alteration.

Before CUDA initialisation set `CUBLAS_WORKSPACE_CONFIG=:4096:8`; enable deterministic Torch and
cuDNN, disable cuDNN benchmarking and TF32, set matmul precision to highest, seed CPU and CUDA, and
preserve canonical ordering. Semantic cache identity, ordered state-dict identity, canonical
key/FP32-value identity and container integrity are distinct.

On the same RTX 5080 with bound driver, CUDA, Torch and deterministic settings, semantic repeats must
be exact. Tolerance-only equivalence is not a fallback. The float64 grouped/scalar oracle bounds are
objective and gradient rtol/atol 1e-12. FP32 loss error is at most
`1e-6 + 5e-5 * abs(oracle)`, gradient error at most `1e-6 + 1e-4 * abs(oracle)`, and predictions
use rtol 1e-5, atol 1e-6. Tests prove all-target graph gradients, local isolation, missing masks,
canonical ordering and one optimiser update.

## WP4 — incremental one-slot supervision

`development-slot` is a one-slot worker. Production `--all` is rejected and removed.
`development-supervise` verifies the whole cache once per epoch, follows canonical slot order, starts
one independently receipted durable wrapper, and does not proceed until reconciliation. A child owns
one slot and survives loss of the launching session.

Ownership binds boot ID, PID and `/proc` start ticks, executable and argument hashes, exact head, G0,
root, slot, attempt and wrapper receipt. There is no mutable lease. A restarted supervisor creates a
new epoch, fully verifies the cache, reconciles ownership, live processes, staging, sealed bundles,
failures and journal records, monitors an exactly matching live process, and only then advances.

## WP5 — journal and crash reconciliation

Attempt identity canonically binds release/G0, canonical output root, slot, family, seed, stage,
attempt number and mode. Each record binds that identity, status and complete canonical payload.
Records are create-only files with the same absent-target atomic rename and fsync discipline as
bundles. Exact duplicate identity and content is idempotent; same status with different content,
SUCCEEDED plus FAILED, or any conflicting identity fails closed. STARTED has exactly one terminal
record. Presentation order derives from the frozen schedule, attempt and lifecycle—never a mutable
global sequence.

Crash classifications are:

- pre-STARTED: session/pre-attempt failure; consumes no scientific attempt or retry;
- post-STARTED, pre-seal: preserve staging under failure, close FAILED; any retry requires authority;
- valid sealed staging: re-authenticate and atomically publish;
- published bundle without SUCCEEDED: authenticate and append SUCCEEDED; reconciliation, not retry;
- moved failure without closure: complete create-only closure then FAILED;
- wholly absent/present matching records: idempotent;
- conflicting or ambiguous state: stop;
- exactly matching live process: monitor and never duplicate.

A future fresh G0 may freeze at most five aggregate unchanged-operational retries, at most one per
slot. Pre-STARTED failures and sealed reconciliation consume none. Only unchanged
infrastructure/process interruption qualifies. Code, scientific, cache, deterministic, device and
runtime failures require a new exact candidate and G0.

## WP6 — consumer migration and qualification

Close-register, metrics, terminal-support prerequisites, terminal prediction and metrics, smoke and
outcome-blind closure consume only authenticated sealed bundles and the create-only journal. A fresh
release rejects loose legacy artefacts. No compatibility projection is added without a discovered,
authorised consumer. Synthetic closure proves 30 development slots and 15 terminal slots UNOPENED
without bypass.

Bounded outcome-blind instrumentation compares the existing row-wise reference with the grouped path
for all five families on the same development slice and seed. It records rows/timestamps per second,
H2D bytes/transfers, forward/backward/optimiser counts, wall and CPU time, RSS/cgroup use, CUDA
allocated/reserved memory, loss, gradients, ordered predictions and exact repeats. It creates no
attempt and adopts no evidence.

A complete cache calibration is separately authorised. Preferred cache build is at most four hours
and 64 GiB, with at least 20% VRAM free. Projections separately cover cache verification, 30 R4.D
fits, 15 R4.E fits and all 45 fits. A first primary slot taking more than twice the frozen projection
stops. CUDA/OOM, identity corruption and disk failures fail closed.

The canonical bulk base is `/data/q-trad/r4-p0`, on the actual ext4 mount `/data` from
`/dev/sde[/q-trad-bulkdata]`. Operator-authorised capacity evidence records statvfs total
1,078,442,151,936 bytes and available 1,023,584,808,960 bytes observed at
2026-09-02T14:47:54.373Z. Container/workspace `df` is forbidden and non-authoritative. The executable
gate requires an existing canonical non-symlink writable bulk base, authenticates mount, source,
filesystem and total bytes against live mountinfo/statvfs, and binds staging, final and cache paths to
the same filesystem. A sealed fresh current-availability observation may decline after writes but must
never drift upwards; projection is compared with the live current value. Reserve is 100,000,000,000
bytes. The qualification benchmark projection is independently derived as four retained qualification
benchmark generations × a conservative 20,000,000-byte per-generation allowance = 80,000,000 bytes.
The conservative production additional projection is 264,585,656,952 bytes, so required available
capacity is 364,585,656,952 bytes and the authorised observation passes. The separately gated terminal
cache uses no terminal evidence or outcomes: its prediction support is upper-bounded by the authorised
664,380 raw-row population and the 52,411 timestamps in the inclusive frozen public window
2026-06-26T14:06:00Z through 2026-08-01T23:36:00Z at 60-second cadence. The receipt binds the source,
operands, formula and policy identity. Separately, actual aggregate qualification benchmark/cache use is
capped at 5,000,000,000 bytes. A create-only authenticated inventory of the exact preserved generation-1
through generation-8 benchmark/cache roots records every regular file's canonical relative path, mode,
size and SHA-256, records absent counterparts truthfully, rejects symlinks, non-regular files and drift,
and independently derives prior immutable use of 4,113,112 bytes. The aggregate cap is not an operand in
the benchmark projection. Atomic rename must not duplicate cache payload bytes.

Qualification requires durable create-only exact-candidate validation under
`/data/q-trad/r4-p0/remediation-7/benchmark-10/validation`. After independent review, its runner executes
focused tests, all residual-graph tests, Ruff format/check, `uv run ty check`, `ops/dev/verify.sh` and
`git diff --check`; it binds the canonical repository working root and its identity, executes every
command there, and binds exact head, commands, exit codes, timestamps, runtime identity and sealed
stdout/stderr logs. Exact head and clean tracked/untracked state are required before and after every
command. Failed commands or repository drift are retained honestly and cannot authenticate as PASS. Full cache
build, preparation, smoke, G0, R4.D, closure, terminal authorisation and R4.E remain later gated.

## Non-goals and gate order

There is no AMP, TF32, `torch.compile`, concurrent GPU fitting, new family, architecture,
hyperparameter, data source, period, universe, provider access, terminal access, compatibility
machinery without a real consumer, promotion, publication, broker operation or capital path.

The gate order is authority → persistence bundles → cache → grouped FP32 → supervisor → consumers →
CUDA equivalence/calibration → complete repository gate → independent exact-head review → only then a
future G0 binding the exact `/data/q-trad/r4-p0` bulk base and the operational preparation generation-8
capacity receipt → the create-only `/data/q-trad/r4-p0/remediation-7/benchmark-10` validation and cache/
preparation authorised only after independent review → R4.D → development closure → terminal gate →
separately authorised R4.E.
