# LAB-S statistical experiment

LAB-S tests one-factor explanations for the Ridge failure: pooling degree, target scale, forecast
calibration, and training-history recency. It imports LAB-0's authenticated loader, preprocessing,
market groups, evaluator, register, and finalist gate.

Entrypoint and configuration:

```bash
uv run -m experiments.r2_historical_lab.lab_s.statistical --help
```

Configuration: [statistical-config.json](statistical-config.json). Historical output:
`/workspace/tmp/qtrad-r2-lab/LAB-S`. See [RESULTS.md](RESULTS.md).

Selection used only the three chronological development blocks. Exactly two frozen finalists were
evaluated once on the former consumed holdout as an explicitly post-hoc development block.
