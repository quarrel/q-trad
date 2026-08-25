# LAB-0 foundation

LAB-0 builds the shared authenticated exploratory dataset from retained local Stage 7 bytes and
reconstructs the original six-target, 15-minute baseline. See [RESULTS.md](RESULTS.md) for the
canonical manifest identity and reconstruction evidence.

Run from the repository root with the repository environment active:

```bash
uv run -m experiments.r2_historical_lab.lab_0.lab inspect --config experiments/r2_historical_lab/lab_0/default-config.json
uv run -m experiments.r2_historical_lab.lab_0.lab smoke --config experiments/r2_historical_lab/lab_0/default-config.json
uv run -m experiments.r2_historical_lab.lab_0.lab build --config experiments/r2_historical_lab/lab_0/default-config.json
uv run -m experiments.r2_historical_lab.lab_0.lab replay-baseline --config experiments/r2_historical_lab/lab_0/default-config.json
```

Build and smoke destinations are create-only. A reproduction must use a new job ID and output path.
The canonical historical output is `/workspace/tmp/qtrad-r2-lab/LAB-0`; do not overwrite it.

Downstream workstreams authenticate the exact LAB-0 manifest and call
`experiments.r2_historical_lab.lab_0.harness.load_parts`. Reads default to `DEV_1` through
`DEV_3`; chronological fitting requests `TRAINING_ONLY` explicitly. Every candidate is compared
directly with `ZERO_RETURN`. Only canonical development results can authorise a small create-only
finalist freeze and a guarded `TERMINAL_FORMER_HOLDOUT` read.
