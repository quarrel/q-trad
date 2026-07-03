# ADR 0002: Docker-first development

- **Status:** Accepted
- **Date:** 2026-07-02

## Decision

Use Docker Compose as the supported development runtime for Python 3.13, PostgreSQL 18, tests and tooling.

## Consequences

Host Python is optional. Commands in documentation must work through Compose.
