# R3.E fixture implementation evidence

This is implementation evidence only. It is not a retained result, effectiveness claim, native execution claim, or promotion.

## Exact micro-run

This candidate is based on clean R3.D lineage `716288bf9e23a20d364bec19b22715bb68ec8013`; the exact source commit is recorded by the Git delivery receipt.

```text
TMPDIR=/workspace/tmp uv run python -m qtrad r3-e --output <new-output-directory>
```

The run executes the production CLI, immutable JSON persistence, independent evaluator, and create-only target/report receipts. The observed report semantic identity is generated from the exact candidate run; reusing the output directory is rejected.

## Fixture topology and component evidence

The fixture has one 15-minute asset with two opposing sleeves: long requested delta `+1` and short requested delta `-0.5`. Internal crossing is `0.5` and remaining external physical delta is `+0.5`; the audited finite physical notional is `AUD 50`, so gross contribution is `0.02 * 50 = AUD 1.00`, expected cost is `AUD 0.0055`, expected net contribution is `AUD 0.9945`, and expected net return is `0.01989`. Transaction costs are recomputed once on the external delta and financing once on the final holding. The evaluator consumes immutable R3.D `RoundedTarget`, `NettingResult`, and `RepairedSleeveAttribution` values, binding the target semantic identity and immediate target verification receipt. It independently reconciles positions, ordered pre/current and post/target risk and exposure caps, canonical per-sleeve physical/cost/P&L allocations with zero residuals, six cost components, gross/expected-cost/net separation, causal executable bid/ask selection, and Decimal P&L under the named reference-to-exit midpoint basis (latency movement is diagnostic, not charged twice). Missing, stale, closed, incomplete, or bad-FX evidence fails closed with explicit reason codes; no midpoint forward-fill or order surface is used.

Persisted closures are one decision, one target, one outcome closure, one report, one target receipt, and one report receipt. Report receipts authenticate the immediate target receipt and outcome-closure identities; no decision or outcome receipt is emitted. Every closure carries source class and evidence purpose, and create-only paths reject collisions/symlinks.

## Shape and limit inventory

The micro-run exercises one asset, two sleeve attributions, one target/current/delta vector, six cost components, two executable quote records (entry and exit), one outcome closure, one report with one asset reconciliation and canonical sleeve allocations/residuals, and two verifier receipts. It does not exercise retained-scale cardinality, byte-size, nesting-depth, partition, transaction, or decoder limits. No retained-scale or 10+ minute execution is authorised by R3.E; such a run remains blocked until a finite sibling-output and persistence-limit projection is recorded and reviewed.
