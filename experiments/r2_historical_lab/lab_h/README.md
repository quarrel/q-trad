# LAB-H horizon experiment

LAB-H tests whether horizon, decision cadence, or overlapping opportunities explain the original
Ridge failure. It depends on LAB-0's authenticated manifest, preprocessing, evaluator, register, and
finalist gate.

Entrypoint and configuration:

```bash
uv run -m experiments.r2_historical_lab.lab_h.horizons --help
```

Configuration: [horizons-config.json](horizons-config.json). Historical output:
`/workspace/tmp/qtrad-r2-lab/LAB-H`. Use a new create-only path for any reproduction.

See [RESULTS.md](RESULTS.md) and [LAB-H-Report.md](LAB-H-Report.md). Model selection used only
the three chronological development blocks; the two frozen finalists were evaluated once on the
former consumed holdout as an explicitly post-hoc development block.
