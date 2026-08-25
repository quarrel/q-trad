# LAB-L temporal representation experiment

LAB-L tests whether causal one-minute sequences add information beyond engineered pooled Ridge and a
non-sequential MLP control. It consumes only the exact LAB-0 manifest named in
[sequence-configurations.json](sequence-configurations.json).

PyTorch remains an isolated experiment dependency:

```bash
uv run --isolated --with torch -m experiments.r2_historical_lab.lab_l.sequence \
  --config experiments/r2_historical_lab/lab_l/sequence-configurations.json \
  --scope CORE_6 --smoke --output <new-create-only-output>
```

The authoritative successful outputs are `/workspace/tmp/qtrad-r2-lab/LAB-L-attempt-2` and
`/workspace/tmp/qtrad-r2-lab/LAB-L-ALL20`. The first retained attempt and disposable smokes were
cleaned after their state and hashes were recorded in
`/workspace/tmp/qtrad-r2-lab/LAB-L-EXECUTION.md`. See [RESULTS.md](RESULTS.md).

The former consumed holdout was never accessed because no sequence model passed the declared
development screen and no finalist freeze was created.
