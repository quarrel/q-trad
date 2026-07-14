# ADR 0014: zero-copy canonical capture feed

- **Status:** Accepted
- **Date:** 2026-07-14

## Context

Later paper and research workloads need ordered market facts without opening a second IG
stream, writing to the collector database or permanently duplicating its growing raw and
canonical estate. Direct database access would couple consumers to collector storage and
schema privileges.

## Decision

Expose persisted canonical events through a bounded, cursor-based read API on the existing
loopback-only collector service. Each page identifies the capture source, universe and
configuration hash; reports its high-water position; and returns ordered canonical envelopes
after the requested global position. Raw records and raw-record identifiers are not exposed.

A cursor beyond the source high-water position fails closed. Consumers preserve source event
identity and advance their own cursor only with their derived writes. The initial transport is
an SSH/Tailscale tunnel to the loopback API; no public listener, queue or second permanent
market-event database is introduced.

## Consequences

Feed interruption pauses downstream processing without affecting capture. Historical research
continues to use immutable Parquet manifests. Any later paper database stores derived paper
facts and cursor state rather than another full quote estate. The feed remains data-only during
WP8; no signal strategy, allocation, risk, paper execution or P&L behaviour is admitted by this
record.
