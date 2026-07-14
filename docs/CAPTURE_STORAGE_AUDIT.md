# Capture storage audit

**Status:** evidence gathering; no schema change approved

This audit decomposes the observed physical growth without weakening raw or canonical retention.
ADR 0018's changed-field raw payload and storage-snapshot commands are implemented locally but are
not deployed during the active `capture-v1` qualification.

## Current per-update storage

Every healthy provider update normally appends one row to each primary capture relation.

`raw.market_messages` retains provider/environment/subscription identity, the deduplication key,
receive and persistence times, the redacted provider delta, its hexadecimal SHA-256 and adapter
version. Migration `0007` additionally retains a compact `SMALLINT` representation code so legacy,
changed-field and fixture rows cannot be mixed by payload-shape inference. It has a primary-key index
and a unique provider/environment/deduplication index.

`canonical.events` retains the complete normalised quote plus event, stream, causal, producer,
receive and persistence identity and a foreign key to raw capture. It has:

- the global-position primary key used by projections, readiness and the bounded feed;
- unique event-ID enforcement;
- unique stream-ID/version enforcement used by optimistic append and latest-version lookup; and
- `events_type_time_idx` on event type/time.

The first three canonical indexes and raw uniqueness are correctness constraints, not optional
query accelerators. Raw and canonical payloads are deliberately separate facts: one supports
provider forensics/re-normalisation, the other deterministic domain replay. Removing either or
downsampling quote events is outside this optimisation.

## Evidence added

Storage snapshot schema version 2 remains backward-readable with version 1 and adds:

- average physical JSONB payload bytes and PostgreSQL's JSON text rendering over each
  bounded recent sample; and
- per-index physical-byte and scan-counter deltas, including bytes per newly captured raw message.

Offline comparison also attributes raw, canonical and combined growth to main heap, indexes and
auxiliary relation storage. Auxiliary storage is the remainder of total relation size after the
main heap and indexes; it includes such PostgreSQL-managed allocation as TOAST, free-space and
visibility-map storage. It reports both bytes per raw message and bytes per new relation row, plus
the observed canonical-event/raw-message ratio. This makes the headline retained-growth number
actionable without pretending that sampled payload width equals page allocation.

The snapshot also binds `pg_stat_database.stats_reset`. If it changes, offline comparison marks
index scan deltas unavailable rather than presenting reset counters as usage evidence. Physical
allocation occurs in pages, so use a representative active-market window rather than a handful of
updates.

Comparison fails closed if capture source, database, universe, configuration hash, application
version or immutable image differs between the two snapshots. It emits a machine-readable
measurement gate requiring both six elapsed hours and 100,000 new raw messages. These automated
thresholds do not prove that the interval represents active market conditions, so operator review
remains explicit. Index-scan evidence is usable only when both thresholds pass and PostgreSQL's
statistics-reset timestamp is unchanged.

The output also reports observed raw-message, canonical-event and retained-relation byte rates per
second, plus mechanical combined-relation extrapolations for one, 30 and 365 days. The basis is
labelled `mechanical_continuation_of_observed_interval`; it is not a demand forecast. Use an interval
only after its automated thresholds pass and the operator confirms representative market activity.

## Candidate decisions

1. **Changed-field raw capture — implemented candidate.** This corrects raw semantics and normally
   removes repeated fields. Qualify it as a new image and compare equivalent active-market windows.
2. **`events_type_time_idx` — evidence-gated removal candidate.** No implemented query filters the
   canonical store by event type/time. Consider a reversible drop/recreate maintenance migration
   only if a representative interval records no scans and the index contributes material growth.
3. **JSON rather than JSONB — benchmark candidate.** Raw and canonical payload columns have no JSON
   operator index or filter dependency. The text rendering is a comparison input, not the exact
   compact input a JSON column would retain. Consider a copy/restore benchmark only if that benchmark
   is materially smaller; replay/rebuild decoding time and rollback compatibility must also pass.
4. **Fixed-width binary hashes — deferred compatibility candidate.** Hexadecimal payload hashes and
   text deduplication identity consume avoidable width, but replacing them needs dual-read/write or
   a maintenance rewrite. Do not pursue it before relation/index measurements show material value.
5. **Partitioning — retention/maintenance tool, not compression.** It does not by itself reduce the
   retained bytes. Revisit only after measured growth establishes a retention horizon and operational
   deletion requirement.

Legacy merged-state rows must not be rewritten as `CHANGED_FIELDS`: per-row connection-generation
evidence is absent, so reconstructed differences would be derived compression rather than original
provider deltas. A whole legacy epoch may be archived out of the operational database only through a
later retention decision with permanent hash-bound backup, verified restore/replay and explicit
treatment of canonical raw-record references.

Do not force small payloads into out-of-line storage, remove uniqueness constraints, mutate old raw
or canonical rows, or infer transport gaps from storage compaction.

## Measurement gate

For both the pinned representation and the later changed-field candidate:

1. use `ops/capture/storage-snapshot.sh` to take non-overwriting before/after snapshots from one
   immutable application image and configuration without starting collector dependencies;
2. use at least six active-market hours or 100,000 new raw messages, whichever is longer;
3. reject intervals containing a PostgreSQL restart/statistics reset for index-usage conclusions;
4. compare raw/canonical/combined heap, index and auxiliary allocation, individual indexes, the
   canonical/raw row ratio and JSONB/text sample evidence;
5. record database-wide growth only as context because backups, catalogues and unrelated relations
   may contribute; and
6. use the observed-rate extrapolation for capacity scenarios, not as an unqualified forecast; and
7. make one schema decision at a time, with restore/replay and application rollback evidence.

The first result may legitimately be “retain the schema”. Storage cost alone does not outweigh the
audit, idempotency and deterministic-replay contracts.
