# LAB-U universe experiment

LAB-U compares CORE_6, OMITTED_14, and ALL_20 pooling structures using LAB-0's authenticated
manifest, preprocessing, evaluator, register, and finalist gate.

Entrypoint and configuration:

```bash
uv run -m experiments.r2_historical_lab.lab_u.universe smoke --config experiments/r2_historical_lab/lab_u/universe-config.json
uv run -m experiments.r2_historical_lab.lab_u.universe run --config experiments/r2_historical_lab/lab_u/universe-config.json
```

Configuration: [universe-config.json](universe-config.json). Authoritative corrected output:
`/workspace/tmp/qtrad-r2-lab/LAB-U-attempt2`. See [RESULTS.md](RESULTS.md) and
[LAB-U-RETRY-AUDIT.md](LAB-U-RETRY-AUDIT.md).

The corrected implementation admits only chronological, horizon-mature pre-terminal targets.
Finalists were selected from `DEV_1` through `DEV_3` before one explicitly post-hoc terminal
evaluation. Outputs remain create-only and non-authoritative.
