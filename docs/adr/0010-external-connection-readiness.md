# ADR 0010: evidence-based external connection readiness

- **Status:** Accepted
- **Date:** 2026-07-06

## Context

The first seven-instrument soak failed because q-trad treated asynchronous
connect/subscribe method returns and transport status as operational readiness. After the
forced reconnect, Lightstreamer remained in its own retry lifecycle without delivering
updates. q-trad later exhausted REST/session rebuild attempts, incorrectly finalised the
run as `COMPLETED` and left client processes resident.

Network libraries, broker sessions and callback transports can be accepted, connected,
subscribed and still operationally useless. Their callbacks may also arrive after a client
has been superseded.

## Decision

Represent every external streaming connection as one explicit, generation-tagged state
machine owned by a single lifecycle coordinator:

```text
STOPPED → AUTHENTICATING → CONNECTING → SUBSCRIBING → READY
                                      ↘ DEGRADED → BACKING_OFF ↗
                                                   ↘ FAILED
READY → STOPPING → STOPPED
```

Method return, socket connection and transport `CONNECTED` status are necessary but not
sufficient for `READY`. IG market data is ready only after:

- the expected connection generation is active;
- all seven subscriptions acknowledge successfully;
- every subscription delivers a fresh, valid update;
- connection, subscription and first-update deadlines have not expired.

Ignore callbacks from superseded generations. Reset per-generation timestamps and partial
field state. A prolonged library-managed retry such as `DISCONNECTED:WILL-RETRY` has a
q-trad watchdog and cannot suppress application-level recovery indefinitely.

Retry policy is shared across recreated third-party client objects, classifies bounded
provider error/status codes and uses exponential full jitter with a cap and circuit-breaker
cooldown. It must stay within provider quotas, but longer backoff must not substitute for
readiness evidence or correct cleanup.

An unbounded ingestion command remains alive while recovery is possible and reports
degraded/disconnected state. Fatal recovery exhaustion finalises the run as `FAILED`;
only an explicit operator stop finalises it as `STOPPED`. An unbounded stream ending
naturally is never `COMPLETED`.

Shutdown is complete only when subscriptions, transport resources, library threads/tasks,
REST resources and process state are verified closed. Cleanup warnings, resident client
threads or an inability to prove closure fail lifecycle acceptance.

## Consequences

Connection transitions, generation, retry class, bounded error code, deadlines and
readiness evidence become structured operational facts. Deterministic lifecycle tests,
repeated credential-gated reconnect qualification and a shorter endurance gate are
required before another 24-hour soak.

The IG-compatible Python Lightstreamer client remains pinned until compatibility is
proven. Its observed disposal-callback defect must be fixed in a reviewable pinned source
or avoided through a verified lifecycle workaround; upgrading to a client that does not
support IG's deployed server is not acceptable.
