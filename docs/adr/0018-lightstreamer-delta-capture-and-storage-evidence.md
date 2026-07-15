# ADR 0018: Lightstreamer delta capture and storage-growth evidence

**Status:** Accepted

## Context

IG's Lightstreamer client presents each `ItemUpdate` as a merged view: `getValue()` returns
the current field value even when that field was last supplied by an earlier update. The
collector was consequently repeating all available PRICE fields in every raw message. That
representation was useful for diagnosis but was not the provider update received on the wire,
and it materially increased the append-only raw store.

The observed approximately 1,500 bytes per incoming update also includes PostgreSQL tuple,
canonical-event and index growth. JSON length alone cannot establish the useful storage rate,
and PostgreSQL's physical sizes need comparable observations over a non-trivial interval.

## Decision

- Persist only fields for which Lightstreamer reports `isValueChanged()` in that callback.
- Preserve an explicitly changed null in raw capture. Remove that field from the adapter's
  per-generation merged state so stale values cannot be emitted canonically.
- Continue to construct canonical quotes from the adapter's bounded merged state. Reset that
  state on every connection generation as already required by the lifecycle contract.
- Do not rewrite existing raw or canonical records. The representation change begins only with
  a later reviewed application release. ADR 0020 gives each new raw record a compact representation
  code while conservatively labelling pre-marker and rollback-writer rows without rewriting them.
- Provide a read-only `storage snapshot` command. It records exact raw/canonical counts from one
  repeatable-read transaction, observed database/relation/index sizes, and bounded recent JSONB
  payload samples. The evidence binds capture source, universe/configuration and application
  identity, is hash verified, size bounded and cannot overwrite an existing file.
- Provide an offline `storage compare` command. It requires chronological observations from the
  same capture source and database and writes a bounded, non-overwriting, self-hashed artifact with
  the snapshot and release identity plus physical byte deltas per newly persisted raw message.
  Relation-level measurements, not a JSON example, are the primary comparison.
- Evolve snapshot evidence compatibly: version 2 adds JSONB-versus-JSON-text sample sizes and
  per-index growth/scan deltas, while version 3 adds pre-marker/coded schema identity and exact raw
  representation counts. The loader continues to verify version-one and version-two hashes.
- Compare separately accepted merged-state and changed-field artifacts only through offline
  `storage contrast`. It requires matching source/configuration, distinct digest-pinned images,
  passed automated thresholds and an all-changed-fields candidate. Contrast reports mechanical
  per-message change but cannot accept the storage decision or satisfy active-market review.
- Record each interval's active-market judgement through offline `storage review`. The bounded
  operator input targets the exact comparison hash; the resulting self-hashed assertion inherits
  the measured source, configuration, image and interval identity. Offline `storage qualify` binds
  the contrast to both assertions and preserves either `PASS` or a valid negative `FAIL`. Review and
  qualification evidence cannot accept the later schema, retention or archive decision.
- Take measurements far enough apart to exceed PostgreSQL allocation noise. Treat whole-database
  growth as contextual because unrelated maintenance and relations may contribute.

## Consequences

New raw records represent provider deltas faithfully and are normally smaller, while canonical
events retain complete quote state. Explicit null handling becomes testable rather than being
silently converted into an absent update.

The active `capture-v1` qualification remains frozen on its pinned image. Its evidence can
measure the old representation, but this change is neither deployed nor mixed into that 72-hour
window. A subsequent candidate release must pass the normal ARM, lifecycle, readiness and
rollback gates. Before/after storage comparisons must identify their different application
images and configuration hashes; they are not interpreted as a single uninterrupted release.
