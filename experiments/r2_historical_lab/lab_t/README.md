# LAB-T nonlinear tabular experiment

LAB-T tests pooled Ridge, histogram boosting, and small MLP controls over CORE_6 and ALL_20. The
module intentionally remains self-contained: archival integration preserves the implementation that
produced the corrected result rather than deduplicating it against LAB-0.

Entrypoint and configuration:

```bash
uv run -m experiments.r2_historical_lab.lab_t.tabular --help
```

Configuration: [tabular-config.json](tabular-config.json). Historical corrected output:
`/workspace/tmp/qtrad-r2-lab/LAB-T-rerun-1`. See [RESULTS.md](RESULTS.md) and
[LAB-T-REMEDIATION.md](LAB-T-REMEDIATION.md).

The rejected first attempt was never used for selection and was removed after its state and hashes
were compacted into the corrected output's `failed-attempts.jsonl`. No nonlinear finalist
qualified, so the former consumed holdout was never accessed.
