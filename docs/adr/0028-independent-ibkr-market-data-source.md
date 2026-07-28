# ADR 0028: independent IBKR market-data source

- **Status:** Accepted
- **Date:** 2026-07-28

## Context

R2.B's causal feature software is complete, but the first decision-grade R2 experiment remains
blocked on a sufficiently long, independently verified foundation. The live IG demo collector must
continue because its native bid/ask and executable-side history cannot be reconstructed. Interactive
Brokers can potentially provide longer account-visible historical bars and is also a planned future
market-data integration.

IBKR has a distinct Gateway authentication lifecycle, entitlements, contract identity, pacing,
sessions and historical revision behaviour. The current canonical stream identity does not include
provider identity, so writing IG and IBKR into one event store could intermingle stream versions and
quote-derived bar history. Historical IBKR bars also do not carry the measured canonical
receive/persistence lineage required by the first native R1 observation contract.

A durable boundary is required before provider code, account-gated acquisition or infrastructure is
implemented.

## Decision

q-trad will add IBKR paper market data as an independent source using the same modular Python
codebase and application image but a separate operational runtime, PostgreSQL event store, capture
API, universe, deployment descriptor, backup and restore lifecycle. IB Gateway's authentication,
restart and market-data-session lifecycle justifies this isolation. Neither collector writes to or
rewrites the other collector's history.

Stable q-trad instrument IDs remain provider-independent. An accepted IBKR listing is identified by
reviewed exact contract evidence including `conId` and exchange. Provider symbols and marketing
names are candidates only. Provider identifiers and direct TWS API types remain inside adapter and
external decoding boundaries; ambiguous mappings fail closed.

The IBKR adapter is market-data-only. It exposes no order operation, imports no order port and adds no
broker-order command, endpoint or production connectivity. Gateway credentials and 2FA remain in an
operator-controlled login boundary and never enter q-trad settings, logs or evidence.

IBKR evidence is classified separately:

```text
IBKR_HISTORICAL_RESEARCH
IBKR_NATIVE_CAPTURE
IG_NATIVE_CAPTURE
```

Historical, IBKR-native and IG-native observations are not combined in one foundation bundle.
Source-specific R2 experiments use distinct immutable identities. A later augmentation experiment
must be separately registered, compare native-only and augmented controls and retain an untouched
native holdout.

IBKR historical bars use a separate provider-history observation contract. They are never relabelled
as native `QUOTE_DERIVED` observations. Their declared availability and correction assumptions,
request lineage, contract mapping, session evidence and bar basis are part of semantic identity.
Refetches never overwrite retained evidence. Historical BID and ASK extrema do not establish a
contemporaneous spread without separate validation against observed top-of-book capture.

One-minute historical MIDPOINT acquisition is the initial research bootstrap. BID/ASK requests are
bounded follow-up evidence. One-second history is limited to predeclared investigations; live
streaming is the preferred high-frequency path.

Actual account access, bulk acquisition, host deployment and publication remain separately
authorised operations. The normative staged gates are defined by
`docs/IBKR_CAPTURE_IMPLEMENTATION_PLAN.md`.

## Consequences

R2.C through R2.F1 may continue while the IBKR track proceeds. A verified IBKR historical foundation
may drive `R2-IBKR-HISTORICAL`, but its result is limited to the named IBKR product and historical
assumptions. It cannot support IG quote, spread, slippage, fill, product-economics or paper conclusions
and does not remove the pending `R2-IG-NATIVE` experiment.

The provider-history path may reuse deterministic R1 panel, target, fold and bundle transformations
after explicit source decoding and verification, but it does not weaken the first R1 native source
restriction. The live runtime may reuse provider-neutral ports and ingestion services, but must not
create a second application architecture or allow provider library types into domain code.

Operational duplication is accepted only where source identity, Gateway lifecycle and failure
isolation require it. PostgreSQL remains the canonical operational store and immutable Parquet plus
manifests remains the research artefact boundary. Any later source mixing, order capability,
credential automation or production/live-account connection requires a superseding decision.
