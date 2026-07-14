# ADR 0015: bounded loopback capture-feed client

- **Status:** Accepted
- **Date:** 2026-07-14

## Context

ADR 0014 defines a cursor feed through an operator-established SSH or Tailscale tunnel. Saved-page
validation proves the data contract but cannot exercise the transport boundary before a downstream
paper or research store exists. A client must not turn an endpoint typo, redirect, ambient proxy,
unbounded body or stalled response into access outside that tunnel or an unbounded process.

Adding HTTPX as a runtime dependency requires an architectural record. Its async streaming API
allows the application to bound decoded response bytes without buffering an arbitrary body and to
cancel the whole request independently of HTTP inactivity timeouts.

## Decision

Provide one async HTTP capture-feed client in the runtime layer. It accepts only `http` URLs using
the literal loopback addresses `127.0.0.1` or `::1`, an explicit port and no credentials, path,
query or fragment. The operator establishes and owns the tunnel separately.

The client disables redirects and environment-derived proxy settings, sends no credentials,
requires HTTP 200 and `application/json`, applies both HTTPX inactivity timeouts and an overall
async deadline, and rejects more than 16 MiB of decoded response data. Each request is limited to
1–1,000 events. Strict schema decoding must prove that the response cursor matches the request and
that the returned event count does not exceed the requested limit.

`qtrad feed probe` fetches and validates exactly one page against an operator-supplied source,
universe and configuration identity. It reports a candidate next cursor with
`cursor_persisted=false`; it does not save or acknowledge that cursor. There are no automatic
retries. A future derived-data transaction remains responsible for committing its writes and
cursor atomically.

## Consequences

The feed transport can be exercised through a local tunnel without exposing the collector API or
granting database access. HTTPX becomes an application runtime dependency. Transport failures,
redirects, identity changes, malformed pages and limits fail closed and cannot silently advance a
consumer. The client introduces no daemon, datastore, paper execution, strategy or collector-side
write path and is not deployed during the active `capture-v1` qualification.
