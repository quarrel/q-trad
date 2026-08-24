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
authenticates only selected parts and defaults every feature, context, and target read to
`DEV_1` through `DEV_3`. Request `TRAINING_ONLY` explicitly for chronological fitting. Register
every configuration and result with `append_attempt`, freeze a small finalist set with
`freeze_finalists`, and supply the exact freeze SHA-256 and configuration ID for the single
`TERMINAL_FORMER_HOLDOUT` read. Compare every candidate directly with `ZERO_RETURN` using
`evaluate_against_zero`.
