# ADR 0008: checkpointed paper event processor

- **Status:** Accepted
- **Date:** 2026-07-06

## Context

Calling strategy and paper logic directly from ingestion would couple market capture to
downstream failures. A separate process that merely polls projections could also duplicate
decisions or fills if it crashes between writing output and advancing its cursor.

## Decision

Add a `paper` command role in the existing application image. It consumes canonical events
in global-position order and has no broker credentials or external order capability.

The processor reacts to completed bars, quotes, gaps and health/session facts. Bar events
advance market state and strategies; approved instructions create pending paper orders.
Only later quote events can fill those orders. A live paper run records its start global
position: earlier bars may initialise deterministic warm-up state but cannot create
execution instructions.

For each input global position, append the complete ordered set of derived canonical events
and advance a named processor checkpoint in one PostgreSQL transaction. Persist an
idempotency key composed of processor version, run, input global position and output
ordinal. Projection updates occur in the same transaction. If processing fails, neither
outputs nor checkpoint advance.

On restart, rebuild in-memory rolling strategy and pending-order state from the run's
canonical events, resume after the committed checkpoint and produce no duplicate decision,
order or fill. Historical deterministic replay invokes the same synchronous domain
processor with a pinned clock/configuration and an isolated event sink.

## Consequences

Ingestion and paper orchestration remain command roles of one modular monolith and image.
The event store needs an atomic batch-append/checkpoint operation and paper-specific
rebuildable projections. Processor failure is visible and halts paper decisions but does
not stop raw market capture. A queue or distributed event broker is not introduced.
