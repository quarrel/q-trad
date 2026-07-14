# ADR 0020: Raw payload representation and legacy capture epochs

**Status:** Accepted

## Context

ADR 0018 changes future IG Lightstreamer raw capture from repeated merged state to callback-changed
fields. Existing rows remain useful audit evidence, but their stored payloads cannot always be
reconstructed as original provider deltas: connection generation is not part of each legacy raw row,
and a repeated null cannot reliably be distinguished from an explicitly changed null after the fact.

Deleting or rewriting those rows would break their payload hashes and the canonical event's raw
record reference. Repeating a descriptive text label on every tick would also work against the
storage reduction being measured. A rolled-back pre-ADR application must remain able to insert
through a forward schema.

## Decision

- Every new `MarketDataRecord` and `RawMessage` carries a required `RawPayloadRepresentation`.
- Persist its stable `SMALLINT` code on `raw.market_messages`:
  - `0`: `LEGACY_UNCLASSIFIED` — pre-marker rows or a rollback writer;
  - `1`: `MERGED_STATE` — an explicitly identified merged-state producer;
  - `2`: `CHANGED_FIELDS` — callback-changed fields with explicit-null semantics; and
  - `3`: `FIXTURE` — deterministic fixture input.
- Migration `0007` adds code zero as a constant fast default and does not update existing rows.
  This avoids a raw-table rewrite and keeps the previous application INSERT shape valid. A bounded
  check constraint rejects all other codes.
- The ADR 0018 IG adapter writes `CHANGED_FIELDS`; fixture records write `FIXTURE`. No current writer
  claims `MERGED_STATE`: qualification-era IG rows are known from their frozen image/epoch evidence,
  but their per-row column remains conservatively `LEGACY_UNCLASSIFIED`.
- Do not transform legacy merged snapshots into records labelled `CHANGED_FIELDS`. A future
  checkpoint-and-difference archive may losslessly encode what q-trad stored, but it is derived
  compression and must not be described as provider wire deltas.
- Do not delete legacy raw rows independently of their canonical events. Retiring a legacy epoch
  requires a separate retention decision, a permanent hash-bound archive, verified restore/replay,
  and an explicit treatment of raw-to-canonical references.
- Downgrade refuses to discard the representation column once any `CHANGED_FIELDS` row exists.

## Consequences

Mixed capture history is queryable without inferring representation from payload shape. The marker
adds a compact fixed-width value rather than repeated text and preserves application rollback
compatibility. Existing payloads, hashes, deduplication keys, canonical events and foreign-key links
remain unchanged.

The marker does not itself reclaim storage. Measurement still determines whether a whole legacy
capture epoch is worth archiving out of the operational database. Until that separate gate passes,
the qualification history remains immutable in PostgreSQL and its verified backups.
