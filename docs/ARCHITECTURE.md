# Implemented architecture

**Status:** data foundation, IG PRICE streaming, historical backfill and resilience
verified; 24-hour operational soak pending.

This document describes implemented reality, not the complete aspiration in `PREPLAN.md`.

## System boundary

The current deployment is one modular Python application with several command roles and one PostgreSQL instance. Parquet files form the research data store.

```mermaid
flowchart LR
    SRC["Fixture or IG demo"] --> ADAPTER["Market-data adapter"]
    ADAPTER --> RAW["Raw audit record"]
    RAW --> NORMALISE["Canonical normalisation"]
    NORMALISE --> EVENTS["Canonical event store"]
    EVENTS --> BARS["One-minute bar builder"]
    EVENTS --> PROJ["Operational projections"]
    BARS --> EVENTS
    EVENTS --> EXPORT["Parquet exporter"]
    EXPORT --> REPLAY["Deterministic replay"]
    PROJ --> API["Read-only API"]
    API --> CONSOLE["Operator console"]
```

## Dependency direction

```text
domain ← ports ← application ← adapters/runtime/API
```

- `domain`: immutable values and deterministic transformations.
- `ports`: provider-agnostic I/O contracts.
- `application`: ingestion, bar, gap, quota and replay workflows.
- `adapters`: IG, PostgreSQL persistence and read-model queries, fixtures and Parquet.
- `runtime/API`: configuration, composition, commands and read-only presentation.

Only these data-phase packages exist. Strategy, allocation, risk and execution
packages are intentionally absent.
Automated architecture tests enforce that domain, ports and application modules do not
reverse this dependency direction.

## Data stores

- PostgreSQL `raw` schema: redacted provider input.
- PostgreSQL `canonical` schema: immutable domain events.
- PostgreSQL `reference` schema: instrument and provider-listing projections.
- PostgreSQL `read_model` schema: rebuildable quote, bar, health and gap views.
- PostgreSQL `ops` schema: runs, quotas, checkpoints and manifests.
- Parquet filesystem: versioned research/replay datasets.

Raw messages and their canonical event or quarantine result are committed in
one PostgreSQL transaction. Each canonical stream uses optimistic versions and
an advisory transaction lock. Projection checkpoints advance in the same
transaction as event projection.

## Market-data semantics

- Quotes preserve provider event time and q-trad receive time.
- One-minute intervals are UTC and half-open: `[start, end)`.
- Bid, ask and midpoint bars are separate.
- Midpoint samples require bid and ask timestamps within five seconds.
- A five-second watermark closes a bar.
- Late samples create a later revision.
- Missing prices are not forward-filled.
- A healthy-stream silence over two minutes creates a gap event.
- IG historical bars retain `IG_HISTORICAL` provenance.
- Market-bar projection identity includes provenance and the complete source listing,
  so overlapping quote-derived and historical series remain distinct.

## Public surfaces

- Standard-library CLI under `python -m qtrad`.
- Read-only FastAPI endpoints under `/api/v1`.
- Jinja/HTMX operator console at `/`.
- No order, fill, position or broker-execution interface.

## Safety boundary

Only IG demo market-data surfaces are in scope. There is no canonical order port, broker execution adapter or live IG endpoint.

IG listing discovery remains fail-closed. Candidates must use the canonical
instrument's quote currency, and the adapter validates an explicit standard-contract
preference for each instrument rather than selecting a mini or alternate-currency
contract from minimum size alone.

The seven-instrument stream uses one Lightstreamer connection with seven
`PRICE:{account identifier}:{epic}` subscriptions and the `Pricing` data adapter.
The deprecated `MARKET` and `L1` subscriptions are not used. Account identifiers are
required at the provider boundary but are removed from persisted subscription labels.
Concurrent streaming connections for the same IG API key are an operational safety violation.

The adapter treats Lightstreamer's `DISCONNECTED:WILL-RETRY` as an in-client retry and
does not create another connection. A terminal `DISCONNECTED` or a stale healthy stream
closes the old client, refreshes the IG REST session and rebuilds one stream with bounded
exponential backoff. Queue insertion is non-blocking; overflow is counted and reported
as degraded health without blocking the provider callback.

Historical requests use IG's v2 UTC date format. Each source, interval and price basis
has a deterministic canonical stream identity, making overlapping backfills idempotent.
Operator-supplied and provider-reported historical allowances are stored separately.
