# ADR 0001: modular monolith

- **Status:** Accepted
- **Date:** 2026-07-02

## Decision

Use one Python codebase and application image with explicit internal layers. Add process boundaries only when failure isolation, security or measured scaling requires them.

## Consequences

Domain and port boundaries remain testable without creating a distributed system. New services require an ADR.
