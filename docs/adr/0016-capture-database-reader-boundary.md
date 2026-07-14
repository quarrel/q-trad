# ADR 0016: capture database reader boundary

- **Status:** Accepted
- **Date:** 2026-07-14

## Context

ADR 0012 requires research and development to avoid writes to the capture database. Immutable
Parquet exports and snapshots remain the normal research path, but occasional operational or
research inspection needs a direct, independently authenticated read path. Sharing the collector
application login would make an accidental write possible and expose raw provider records.

## Decision

Migration `0004` owns a `NOLOGIN` PostgreSQL privilege role named `qtrad_capture_reader`. It has
`USAGE` and `SELECT` only in `canonical`, `reference`, `read_model` and `ops`, including default
`SELECT` grants for tables created by later migrations. It has no privilege on `raw`, cannot log
in, bypass row security, create roles or databases, replicate or act as a superuser.

An operator may create a separate login role, grant it inherited membership in
`qtrad_capture_reader`, set `default_transaction_read_only=on` on that login and assign its secret
interactively. Login credentials never enter the repository, collector application environment or
events. The role grants are the primary enforcement; the session default is defence in depth.

PostgreSQL is published only on the collector's literal IPv4 loopback address, on host port 15432
by default. A consumer must establish an SSH tunnel to that loopback listener. There is no public,
VCN-wide or Tailscale database listener, and direct access never uses the collector application
role.

## Consequences

The database can support exceptional read-only inspection without allowing capture mutation or
raw-audit access. PostgreSQL integration tests execute real reads under the privilege role and
prove raw reads and no-op updates are denied. Parquet manifests and snapshots remain preferred for
repeatable research; this path does not authorise development migrations, projection rebuilds,
paper writers or long-running ad-hoc load against the collector.
