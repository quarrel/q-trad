# ADR 0009: canonical provider-listing economics

- **Status:** Accepted
- **Date:** 2026-07-06

## Context

Paper quantity, notional and P&L cannot be derived from currency and minimum deal size
alone. IG market details contain contract and pip economics, but the current sync hashes
the full response and then persists only the reduced listing directly into a reference
table. Repeated sync rows are collapsed at read time rather than closed as explicit
effective versions.

## Decision

Extend listing discovery with a bounded, canonical `ProductEconomics` value containing:

- provider quantity unit and minimum/step quantity;
- contract/lot size;
- one-pip meaning and value;
- derived value per one price unit and its currency;
- minimum price increment;
- opening-hour source metadata;
- source detail version and validation time.

Conversion to `value_per_price_unit` must be deterministic and covered by fixtures for all
seven selected listings. Missing, unparsable or dimensionally inconsistent fields make the
listing ineligible for paper allocation.

Instrument sync appends a `ProviderListingValidated` canonical event and projects it in the same
database transaction. The bounded canonical payload excludes account data and the unbounded
provider response. Each event makes exactly one listing effective for its provider, environment and
instrument: it closes every earlier effective epic or metadata version at the new event time before
inserting the selected version. An identical effective version and universe hash is idempotent.
Migration `0008` enforces at most one open-ended listing for that identity. Projection rebuild deletes
only event-backed listing rows, preserves legacy rows, and replays canonical validation events to
recreate effective listings and economics; the first event closes any superseded legacy selection.

Before a new capture universe is approved, provider discovery may instead run in review mode.
Review mode emits a hash-addressed, bounded manifest of every relevant candidate and stable
fail-closed reasons. It neither chooses the smallest contract nor writes a preferred epic,
listing event or projection. The manifest explicitly has no selection authority; operator review
and a separate versioned universe release remain required.
Promotion into such a release requires a complete operator-authored mapping bound to the exact
catalogue and review hashes. Every selected listing must be an eligible candidate for exactly one
instrument. Promotion only renders an undeployed configuration; sync and deployment remain
separate reviewed actions.

Do not reinterpret legacy reference rows as canonical history. The migration preserves
them until the first new sync, then the event-backed projection becomes authoritative and
can be rebuilt independently.

## Consequences

Paper allocation can fail closed using auditable product economics. Instrument sync now
uses the canonical event store rather than direct projection-only truth. No broker order
method, account balance or margin replica is introduced.

A previous application remains usable for rollback with the same selected epics, but the database
constraint deliberately rejects an attempted alternate-epic write that would create two effective
listings. A migration preflight must find no existing duplicate open-ended selections; ambiguity is
not repaired by guessing which row should survive.
