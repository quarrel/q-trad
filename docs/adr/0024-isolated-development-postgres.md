# ADR 0024: isolated development PostgreSQL roles

- **Status:** Accepted
- **Date:** 2026-07-17

## Decision

Use a dedicated `qtrad_dev` database plus separate PostgreSQL 18 Compose services for persistent interactive development and disposable
integration verification. The development service advances to migration head on Dev Container
start. The tmpfs-backed test service creates a fresh, guarded `qtrad_test_*` database for each full
verification run and reproduces the CI frozen-schema-then-head sequence.

Application integration tests accept only the explicit test database variable. They never fall
back to the application database URL. Both services remain internal to Compose, and the Dev
Container does not receive the host Docker socket.

## Consequences

Stale development state cannot make integration results misleading, and tests cannot mutate
interactive development data or the remote collector. GitHub CI remains independent confirmation
for clean PostgreSQL, workflow and multi-platform image behaviour rather than the first available
database integration environment.
