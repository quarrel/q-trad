# First deterministic shadow-strategy ranking report

This is the first framework-proof report, not evidence of strategy effectiveness or profitability.
It uses a short retained `capture-v1` interval solely to exercise causal forecasts, outcomes,
executable-side shadow fills, isolated accounting, ranking and deterministic replay.

## Evidence identity

- Experiment: `capture-v1-asx-shadow-proof`
- Instrument: `index:australia-200`
- Decision interval: `[2026-07-16T00:10:00Z, 2026-07-16T04:00:00Z)`
- Immutable bar manifest: `5289530e6b5d946c626593f74eda8d14774d1454774fff666c9c313a9946565d`
- Experiment configuration: `95a40619004ad791dc054a6420d0f2a80bc89053d5ab894baf6ed96b3ccdc175`
- Dataset identity: `9a460c1c944121bf1f0f1d5e58a8ad1a2104c84b09eb02579f9f6e9be470df3a`
- Report identity: `14b12c8db363f95ece2797c2c6e74e0e6594a8021904142e63cb37271a3f2d1f`
- Inputs: 714 quote-derived bid/ask/mid bars and 5,070 canonical-positioned quotes
- Bar trace: `78ef4e8affbde14e5dd163a7e8af1d389b83159cd7047564c754582ca64aaa42`
- Quote trace: `78168bd6ad6d4218e3ed6da6956e50428ce5a38312ca736c20ee71ec4486c1f2`

The paper contract uses the effective provider listing metadata version `0454fc612d2ed4b3`:
minimum quantity 1 contract, `1 Index Point`, AUD 25 per point, and AUD reporting without currency
conversion. Both entry and exit must be later healthy complete bid/ask quotes within the versioned
ASX cash-session profile. The base model applies one second latency and one adverse index-point
slippage to each executable side.

## Score and paper results

The v1 score is full-dataset time-series Spearman Rank IC for five-minute midpoint-close returns,
sampled at every completed bar with overlapping observations included and a 30-sample minimum.
Coverage was 228 of 229 forecasts (99.56%) for every strategy.

| Rank | Strategy | State | Rank IC | Round trips | Net P&L (AUD) | Execution cost (AUD) | Max drawdown (AUD) | Turnover (AUD) |
|---:|---|---|---:|---:|---:|---:|---:|---:|
| 1 | reversal-1 | SHADOW | 0.060282 | 207 | -13,980.00 | 15,525.00 | 14,562.50 | 91,337,265.00 |
| 2 | persistence-3 | SHADOW | 0.009874 | 226 | -17,362.50 | 16,950.00 | 17,670.00 | 99,716,262.50 |
| 3 | persistence-1 | SELECTED | -0.060282 | 207 | -17,070.00 | 15,525.00 | 17,070.00 | 91,337,265.00 |
| — | no-signal | SHADOW benchmark | undefined | 0 | 0.00 | 0.00 | 0.00 | 0.00 |

The selected strategy and every unselected shadow strategy remain separately attributable. Rank IC
orders the eligible strategies but does not override the cost result: all active strategies lost
money under the base paper model.

## Cost and latency sensitivity

| Latency | Adverse slippage per side | reversal-1 | persistence-3 | persistence-1 |
|---:|---:|---:|---:|---:|
| 1 s | 1 point | -13,980.00 | -17,362.50 | -17,070.00 |
| 2 s | 2 points | -24,365.00 | -28,940.00 | -27,385.00 |
| 5 s | 3 points | -34,627.50 | -40,375.00 | -37,822.50 |

Every active strategy deteriorates as latency and adverse slippage increase. This result supports
only that the framework exposes an unfavourable short sample honestly; it does not establish that
reversal is effective out of sample.

## Reproduction

Restore the manifest-bound verified snapshot, configure `QTRAD_DATABASE_URL` for that isolated
`qtrad_research_*` database, and run:

```bash
uv run qtrad research rank \
  --manifest tmp/capture-snapshot-20260717T044430Z/research/manifests/5289530e6b5d946c626593f7.json \
  --experiment config/research-proof-v1.toml \
  --snapshot-import-evidence tmp/capture-snapshot-20260717T044430Z/import-20260717.json \
  --output tmp/research-proof-v1-report.json
```

The command validates snapshot, manifest, listing economics, session and canonical-position
identity; independently recomputes fill and ledger arithmetic; builds the report twice with reversed
bar input; refuses an existing output; and emits the report and dataset hashes above. A second
non-overwriting run produced byte-identical JSON.
