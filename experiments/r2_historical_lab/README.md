# R2 IBKR historical exploratory lab

This package builds the shared LAB-0 dataset from retained local Stage 7 provider-history
bytes. Every output is EXPLORATORY_POST_HOC_ONLY with source class
IBKR_HISTORICAL_RESEARCH; it is not a second holdout, confirmation, promotion, or
decision-grade result.

Run from the repository root:

    uv run -m experiments.r2_historical_lab.lab inspect --config experiments/r2_historical_lab/default-config.json
    uv run -m experiments.r2_historical_lab.lab smoke --config experiments/r2_historical_lab/default-config.json
    uv run -m experiments.r2_historical_lab.lab replay-baseline --config experiments/r2_historical_lab/default-config.json
    uv run -m experiments.r2_historical_lab.lab build --config experiments/r2_historical_lab/default-config.json

The full build hashes and decodes each selected Stage 7 part once, processes one instrument
at a time, and writes partitioned Parquet under /workspace/tmp/qtrad-r2-lab/LAB-0/.
Downstream labs authenticate lab-manifest.json, hash only the listed parts they consume,
and never re-read Stage 7. Use DEV_1 through DEV_3 for variant ranking. Freeze a small
finalist set before a one-time evaluation on TERMINAL_FORMER_HOLDOUT.
