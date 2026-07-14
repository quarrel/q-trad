# ADR 0012: isolated capture operations release

- **Status:** Accepted
- **Date:** 2026-07-11

## Decision

Run persistent IG demo collection as one data-only Compose deployment with PostgreSQL,
ingestion and a loopback-only read API. The deployment uses immutable multi-architecture
images, a versioned capture universe and one ingestion process per IG API key.

The capture database is not a development, test, research or paper-write target. Those
workloads consume immutable exports or a separate read-only/snapshot path. No order or
paper component is admitted by this record.

ADR 0016 defines the exceptional direct-read path: an independently authenticated reader
privilege, no raw-schema access and a database listener bound only to host loopback for SSH
tunnelling.

## Consequences

OCI provisioning, backup/restore, health monitoring, IPv6 SSH and Bastion recovery are
operational acceptance gates. Image/configuration rollback is allowed; canonical events
and schema history are never rolled back.
