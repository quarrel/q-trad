# q-trad agent instructions

## Purpose and authority

This file contains enduring instructions for humans and automated agents working on q-trad. It is not a progress log.

Read sources in this order:

1. `AGENTS.md`
2. accepted records in `docs/adr/`
3. `docs/ARCHITECTURE.md`
4. `PLAN.md`
5. `docs/STATUS.md`
6. `PREPLAN.md`
7. executable tests and code

When sources disagree, stop and reconcile the higher-authority source. Update this file only when an enduring rule, boundary, term or safety invariant changes. Put current progress in `docs/STATUS.md`.

## Current phase

The current phase is data-only:

> IG demo data → raw audit record → canonical quote events → one-minute bars → PostgreSQL → Parquet → deterministic replay → read-only operator console.

No order placement, paper execution, signal strategy, allocation, risk or P&L implementation belongs in this phase.

The fixed canonical universe is:

- `fx:aud-usd`
- `fx:eur-usd`
- `fx:usd-jpy`
- `fx:gbp-usd`
- `index:australia-200`
- `index:us-500`
- `index:ftse-100`

## Non-negotiable architecture

- Build a modular monolith with one application image.
- Dependency direction is `domain ← ports ← application ← adapters/runtime/API`.
- Domain code must not import FastAPI, SQLAlchemy, `trading-ig`, environment configuration or filesystem code.
- Broker/provider types and identifiers stop at adapter boundaries.
- Convert external values to canonical types immediately.
- Use immutable domain values and append-only canonical events.
- Treat projections as rebuildable views, never canonical truth.
- Use timezone-aware UTC internally and inject clocks.
- Use `Decimal` for prices, sizes, money and quantities.
- No production IG endpoint or order-submission command may exist during this phase.
- Secrets and session tokens must never enter events, raw capture, logs, fixtures or version control.

## External I/O safety

- Treat broker, streaming and database connections as explicit state machines, not
  booleans.
- A successful method return, socket connection or subscription request is not readiness.
  Readiness requires bounded, domain-relevant evidence from every required channel.
- Measure stream freshness per required channel from received transport evidence, not from
  price movement or aggregate stream activity. Quiet markets are not, by themselves,
  transport failures.
- Tag callbacks and queued records with a connection generation and ignore superseded
  generations.
- Give library-managed retries an application watchdog; no degraded or retrying state may
  suppress staleness detection indefinitely.
- Share retry budgets across recreated client objects, classify provider failures and use
  capped jittered backoff with a circuit-breaker cooldown.
- Do not report an unbounded process as `COMPLETED` because an external iterator ended.
  Recovery exhaustion is `FAILED`; an explicit clean stop is `STOPPED`.
- Verify transport tasks, threads, sessions and processes have ended before declaring
  disconnect or restart complete.

## Terminology

Use the vocabulary in `PREPLAN.md`. In particular:

- operator console
- market-state model
- allocation engine
- strategy sleeve
- signal strategy
- execution algorithm
- broker adapter
- paper execution engine
- canonical event store
- research data store

Do not use “algorithm” when “signal strategy” or “execution algorithm” is meant. Do not use “trade” where order, fill, position change or round trip is the actual concept.

## Python style

- Target Python 3.13.
- Use en-GB in documentation, comments and operator-facing text.
- Use frozen dataclasses for domain values and Pydantic at I/O boundaries.
- Require strict typing in `domain`, `ports` and `application`.
- Keep domain transformations synchronous and deterministic.
- Keep `asyncio` at I/O and orchestration boundaries.
- Prefer protocols and composition over inheritance.
- Catch broad exceptions only at a process or adapter boundary; classify them before reporting.
- Use structured logs with stable event names and bounded fields.
- Add docstrings where they explain a public contract or invariant, not obvious syntax.

## Reuse and sprawl controls

- Search before adding an abstraction.
- Do not create `utils.py`, generic base classes or a “common” package.
- Allow local duplication until two concrete uses demonstrate the same stable invariant.
- A new top-level package, process, datastore, queue, framework or runtime dependency requires an ADR.
- A process split requires evidence of failure isolation, security or scaling need.
- Spike code must be deleted or promoted through normal contracts and tests.
- Do not add empty packages for future PREPLAN phases.
- Do not add Redis, Kafka, Celery, TimescaleDB, React, Kubernetes or NautilusTrader in this phase.

## Required workflow

Before changing code:

1. Read `docs/STATUS.md` and the active `PLAN.md` work package.
2. Inspect existing contracts and tests.
3. Confirm the change is inside the current phase.

Before marking work complete:

1. Run formatting checks, linting, strict type checks and relevant tests.
2. Update `PLAN.md` status and evidence.
3. Update `docs/STATUS.md`.
4. Update `docs/ARCHITECTURE.md` if implemented structure or flow changed.
5. Add or supersede an ADR if an architectural decision changed.

For long-running external I/O, also prove readiness, degraded recovery, retry exhaustion,
clean shutdown and process exit. A short happy-path smoke cannot substitute for those
lifecycle gates.

Run projection rebuild and other whole-store integration tests against an isolated,
migrated database by default. Use a long-lived soak database only when measuring its
accumulated-data behaviour is the explicit objective.

Stop and report rather than guess if:

- an IG instrument mapping is ambiguous;
- an event’s timestamp or price basis cannot be established;
- a schema change would reinterpret existing facts;
- a proposed change could reach a live broker endpoint;
- a broker-specific convenience would leak into the domain;
- credentials or account data could be exposed.
