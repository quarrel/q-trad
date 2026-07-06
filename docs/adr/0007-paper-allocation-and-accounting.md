# ADR 0007: paper allocation and virtual accounting

- **Status:** Accepted
- **Date:** 2026-07-06

## Context

The paper slice needs deterministic sizing and attributable P&L without pretending to
replicate broker margin, tax lots or live financing.

## Decision

Use AUD as the reporting currency and weighted-average cost for virtual positions.
Preserve immutable fill-level history so independent FIFO or lot analysis remains
possible later.

An allocated sleeve receives fixed, versioned per-instrument gross-notional caps expressed
in AUD. Notional conversion uses the instrument's explicit value-per-price-unit metadata
and a contemporaneous healthy FX conversion path. Missing, stale or ambiguous conversion
or product metadata yields zero allocation. Portfolio gross and net caps are additional
hard limits, not optimisation targets.

The shadow sleeve always receives zero deployable allocation. Its hypothetical orders and
fills are evaluated by the same paper model in a separate virtual ledger and can never be
netted into allocated exposure. Shadow results cannot trigger automatic promotion.

P&L records owner, instrument, native and reporting currency, valuation basis/time,
conversion evidence, gross realised and unrealised amounts, spread/slippage, other costs,
net amount and calculation version. Spread and configured slippage are modelled in v1.
Commission and overnight financing are explicit zero/unsupported assumptions; the toy
runtime enforces intraday maximum holding periods and must not describe these omissions as
broker-equivalent costs.

Opposing allocated targets are resolved to a portfolio target before paper execution.
Any later external fill allocation remains a separate event and is outside this phase.

## Consequences

Provider listing metadata must gain quantity step and value-per-price-unit before an
instrument is paper-eligible. Independent fill arithmetic must reproduce every ledger and
P&L projection. Tax reporting, broker margin replication, external netting and confirmed
broker charges remain deferred.
