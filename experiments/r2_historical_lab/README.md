# R2 IBKR historical exploratory lab

This package builds the shared LAB-0 dataset from retained local Stage 7 provider-history
bytes. Every output is `EXPLORATORY_POST_HOC_ONLY` with source class
`IBKR_HISTORICAL_RESEARCH`; it is not a second holdout, confirmation, promotion, or
decision-grade result.

Run from the repository root with the repository environment active:

    uv run -m experiments.r2_historical_lab.lab inspect --config experiments/r2_historical_lab/default-config.json
    uv run -m experiments.r2_historical_lab.lab smoke --config experiments/r2_historical_lab/default-config.json
    uv run -m experiments.r2_historical_lab.lab build --config experiments/r2_historical_lab/default-config.json
    uv run -m experiments.r2_historical_lab.lab replay-baseline --config experiments/r2_historical_lab/default-config.json

Build and smoke destinations are create-only. Choose a new job ID and output path for a new
attempt; preserve failed and superseded attempts. The full build hashes and decodes every
selected Stage 7 part once, writes instrument/month Parquet parts, and runs the genuine original
six-target, 15-minute OOF reconstruction before publishing a complete manifest.

Downstream workstreams must call
`experiments.r2_historical_lab.harness.load_parts` with the exact manifest SHA-256. The loader
authenticates only selected parts, rejects explicit empty selections, and defaults every feature,
context, and target read to `DEV_1` through `DEV_3`. Request `TRAINING_ONLY` explicitly for
chronological fitting. Evaluate every completed candidate directly against `ZERO_RETURN` with
`evaluate_against_zero`, then register that exact provenance-bearing result with `append_attempt`.
An explicit `{"status": "FAILED", ...}` result remains registerable but is never finalist-eligible.
Only results evaluated exclusively on canonical `DEV_1` through `DEV_3` blocks are finalist-eligible;
`TRAINING_ONLY` results may be registered for diagnostics but cannot authorise or coexist with a later
freeze for the same configuration. Unknown block labels are rejected. Freeze a small finalist set
with `freeze_finalists`, and supply the exact freeze SHA-256 and configuration ID for the single
`TERMINAL_FORMER_HOLDOUT` read. If any matching registered attempt failed, evaluated training-only
rows, or evaluated the terminal block, that configuration is permanently ineligible for a later
freeze in the same workstream and manifest.

## LAB-L temporal representation experiment

LAB-L consumes only the exact canonical LAB-0 manifest named in
`sequence-configurations.json`. Run the controlled smoke before a full CORE_6 execution:

    uv run --isolated --with torch -m experiments.r2_historical_lab.sequence \
        --config experiments/r2_historical_lab/sequence-configurations.json \
        --scope CORE_6 --smoke \
        --output /workspace/tmp/qtrad-r2-lab/LAB-L-attempt-2-smoke

The full fixed experiment omits `--smoke` and writes under
`/workspace/tmp/qtrad-r2-lab/LAB-L-attempt-2`. The first retained-scale attempt timed out after four
completed configurations; its summary and pre-deletion hashes remain in
`/workspace/tmp/qtrad-r2-lab/LAB-L-EXECUTION.md`. After the successful replacement runs, the
incomplete attempt and disposable smoke outputs were intentionally cleaned so they cannot be mistaken
for final results. Its completed configurations still count as attempted exploratory variants. The
former consumed holdout remains inaccessible unless a sequence
configuration first passes every declared development screening condition and is create-only frozen
by the shared LAB harness.
