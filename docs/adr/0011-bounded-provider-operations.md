# ADR 0011: bounded synchronous provider operations

- **Status:** Accepted
- **Date:** 2026-07-06

## Context

The repeated-reconnect qualification reached its bounded `STOPPED` run state but left the
Python process resident. Cancelling an `asyncio.to_thread` await does not stop a running
worker, and `asyncio.run` subsequently waited for the default executor. The pinned
`trading-ig` rate limiter also creates non-daemon threads; its public `logout()` stops
them only after a remote logout request succeeds. A failed or blocked request can
therefore prevent process exit.

The adapter also discarded its Lightstreamer client reference before disconnect was
confirmed. That made a failed close appear complete and removed the only handle available
for a retry or truthful failure report.

## Decision

All synchronous IG and Lightstreamer calls run as named, structured provider operations
with explicit deadlines. The operation threads are daemon threads and are not owned by
the asyncio default executor. Start, completion, cancellation and timeout are logged with
bounded operation names and durations.

A timed-out provider operation is terminal for that adapter lifecycle. q-trad must not
create another client while an unresolved call may still own the old resource. Shutdown
waits for outstanding operations for a bounded interval and reports `FAILED` if they do
not finish.

The adapter retains its Lightstreamer client and subscription references until
`DISCONNECTED` is observed. Remote logout failure is distinct from local cleanup:
q-trad closes the HTTP session and invokes the pinned `trading-ig` local rate-limiter
shutdown hook even when remote logout fails. The private hook is isolated to this adapter
boundary and protected by the existing dependency pin.

Reconnect cycles are finite. A forced reconnect is recorded as completed only after full
fresh-data readiness; reaching the ingestion deadline with it pending finalises the run
as `FAILED`.

## Consequences

Provider-operation timeouts may abandon a daemon worker until the ingestion process
exits, but they cannot be followed by another provider connection in that process.
Process-level regression coverage proves such a timeout does not keep the command
resident.

A separate broker subprocess is not introduced now: the observed non-daemon ownership
was the `trading-ig` rate limiter and is explicitly stopped. If future qualification finds
another uncooperative non-daemon provider resource, process isolation requires a
superseding ADR.
