# Paper vertical-slice decision register

**Status:** Accepted for the no-live-order paper phase
**Superseding authority:** accepted ADRs remain authoritative where wording differs.

This register resolves or explicitly defers the `PREPLAN.md` open questions needed for the
paper vertical slice. “Deferred” means outside this phase, not silently decided.

## Broker and product boundary

| PREPLAN item | Decision |
|---|---|
| B01 | Demo market-data capability is sufficient for this phase. Legal-entity, live-account and live capability work is deferred. |
| B02 | Use the seven validated standard rolling-CFD demo listings already recorded by the instrument master. Revalidate effective metadata at each sync. |
| B03–B08 | Defer all live-broker choice, IG order features, IBKR products, manual-order attribution and shared-account policy. No broker order route exists. |

## Data, time and accounting

| PREPLAN item | Decision |
|---|---|
| D02 | Decisions use completed one-minute bars. Moving-average warm-up is 60 complete bars; breakout warm-up is 30. Configurations may lengthen but not shorten these minima. |
| D03 | Signals use q-trad-derived midpoint bars. Bid/ask quotes are execution evidence. Provider historical bars are provenance-distinct warm-up inputs only. |
| D04 | Multi-year licensed history is a later purchase gate and does not block the contract-proof slice. No profitability inference is permitted from the initial capture. |
| D05 | Use versioned venue-time session profiles with explicit holidays and DST. Unknown session state fails closed. |
| D06 | Reporting currency is AUD. Conversion requires a contemporaneous healthy path with recorded observations and timestamps. |
| D07 | Use weighted-average virtual cost basis and retain immutable fill history. |
| D08 | Spread and configured adverse slippage are modelled. Commission is explicitly zero for the contract proof. Maximum holding is intraday, so financing is unsupported rather than estimated. |
| D09 | Retain canonical paper events indefinitely. Keep research manifests immutable. Add a local PostgreSQL backup/restore drill; off-site destination and long-term raw retention remain deferred. |
| D10 | Defer tamper-evident event hashes; database constraints, immutable events and manifest hashes remain required. |

## Allocation and market state

| PREPLAN item | Decision |
|---|---|
| A01 | A budget is a fixed maximum gross notional in AUD, supplemented by per-instrument and portfolio caps. For the contract-proof run, each instrument is capped at one validated minimum quantity step; its start-price AUD notional is recorded as the fixed run budget. The portfolio gross cap is the sum of those seven caps. This is not broker margin or a performance optimiser. |
| A02 | Budgets are fixed for a run. Changing them creates a new configuration hash and run; no intrarun adaptive allocation. |
| A03 | Eligibility requires complete warm-up, healthy current data, no open relevant gap, known session/product economics and no halt. |
| A04 | Allocated sleeve targets are summed by instrument then clipped by risk. Shadow targets never enter deployable netting and retain an isolated ledger. |
| A05 | Shadow activity cannot affect allocated exposure, risk budgets or promotion. |
| A06–A07 | Automatic/human promotion workflow and covariance-aware allocation are deferred. |
| R01–R04 | Emit one per-instrument observation with `NORMAL`, `HIGH_RANGE` or `UNKNOWN`, quality, score and version. `HIGH_RANGE` means the latest true range exceeds twice the median of the previous 20 complete bars. The first slice records this annotation but does not alter allocation or select a strategy. |
| R05 | Any later control authority requires locked, time-ordered comparison against no-state and simple-state baselines. |

## Paper execution

| PREPLAN item | Decision |
|---|---|
| P01 | Subsequent healthy top-of-book bid/ask observations are the only fill evidence. |
| P02 | Default model latency is one second and adverse slippage is one price increment. Both are versioned and tested over a stress grid. |
| P03 | No partial fills in v1; fill completely or not at all. |
| P04 | Spread is embodied by side selection. Unknown session, stale quote or missing product economics prevents a fill. Stops and financing are unsupported. |
| P05 | Use a conservative canonical model with versioned product-economics inputs, not an asserted IG simulator. |
| P06 | Comparison with later broker-demo fills is a separate phase and neither model is ground truth. |

## Operations, observability and validation

| PREPLAN item | Decision |
|---|---|
| O01 | Keep the operator console local/browser-only. |
| O02–O04 | Persist domain alerts and show them in the console. External notification delivery and mutating controls are deferred. |
| O05 | New exposure requires a quote received within five seconds. A projection lag over five seconds or 100 global positions is degraded; unknown lag fails readiness. |
| X01 | Primary host remains the Compose-backed Dev Container/local Linux environment. |
| X02 | Multi-day paper soaks may be unattended, but no external side effect occurs. A restart must recover without duplicate decisions or fills. |
| X03 | Continue gitignored `.env` secrets for local demo market data. No paper component receives broker credentials. |
| X04 | Plain PostgreSQL remains sufficient. |
| X05 | Add repeatable local backup and restore verification. Off-site RPO/RTO are deferred until a deployment target is chosen. |
| X06 | Keep one image and separate command roles. Ingestion, paper orchestration and API may be separate processes only for lifecycle isolation. |
| X07 | Releases are identified by commit, configuration hash, schema head and dataset manifest. Rollback uses the previous image/config and forward-compatible event readers; events are never rolled back. |
| T01 | Fixtures are synthetic or field-bounded and redacted; account identifiers and tokens are forbidden. |
| T02 | The current 24-hour soak is the paper-phase entry gate. Broker order connectivity remains separately gated. |
| T03 | Live-canary criteria are deferred. |
| T04 | Use property tests for accounting conservation, cap enforcement, ordering and idempotency; example tests cover lifecycle and operator views. |
| T05 | A separate pure fill-arithmetic calculation must reproduce ledger and P&L projections. |
| T06 | Use deterministic fake clocks/adapters and database interruption tests; do not add a fault-injection framework without evidence. |

## Contract-proof strategy configuration

- The allocated moving-average signal uses 15- and 60-bar simple moving averages of
  complete midpoint closes. It emits normalised target `+1` when short is above long,
  `-1` when below and only emits when that target changes.
- The shadow breakout signal compares the latest complete midpoint close with the high/low
  of the previous 30 complete bars. A breakout emits normalised target `+1` or `-1`, holds
  for at most 15 bars and then emits `0`; it never averages down.
- Each sleeve has exactly one child and therefore uses an explicit pass-through policy.
  Configured budget and risk services, rather than signal strategies, translate normalised
  targets to quantities.
- Every target is flattened five minutes before its index reference-session close or
  before the configured FX rollover exclusion. No paper position crosses that boundary.
