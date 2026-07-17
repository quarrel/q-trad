# IG streaming continuity investigation

## Purpose

This investigation must establish whether long-lived IG demo streaming is sufficiently observable
and reliable to qualify `capture-v1` before any `capture-v2` expansion. Historical bars can show
that IG had historical price data during a live silence, but they cannot prove that the streaming
endpoint emitted an update. A running process, connected transport or active aggregate stream is
likewise insufficient evidence for one required subscription.

The collector remains data-only. Investigation against retained evidence is read-only; local tests
use the isolated development/test databases. No experiment may add a second concurrent stream for
the collector's IG API key under the current ADR 0010/architecture boundary.

## Evidence established from the failed candidate

The formally closed candidate is bound by qualification decision
`d7bcd88e3179aca9eda89673f14383d6525bcd92e602462c6c56815892fb5c3f`. The verified local snapshot
contains 3,047,086 raw market messages, 3,478,536 canonical events and all 70 retained live gaps.
The v2 historical comparison found complete IG historical coverage for every gap, but made no claim
about streaming emission.

A read-only query of the verified snapshot compared each gap's exact event-time interval with its
configured IG `PRICE` subscription:

- all 70 gaps contain zero raw callbacks for the affected instrument;
- none contains a non-dealable or failed-normalisation callback hidden by the healthy-quote gate;
- every gap contains continued canonical quotes from other subscriptions on the same connection;
- the minimum is 35 other quotes from two other instruments, and the maximum is 723 quotes from six.

The verified bounded ingestion log contains 11 transport-status changes and ten staleness reports.
It contains no subscription error, retry-watchdog expiry or reconnect event during any retained gap.
The unrelated transport recovery at `2026-07-15T11:23Z` completed in about 1.5 seconds. The only full
application reconnect occurred at `2026-07-16T17:19Z`, several hours before that day's clustered
gaps.

This evidence rules out PostgreSQL lag, q-trad queue overflow, normalisation rejection and a whole
Lightstreamer connection outage as causes of the 70 gaps. It does not distinguish:

1. IG's streaming data adapter emitted no update for that item;
2. the server or SDK filtered/conflated updates under an applied item-frequency policy;
3. a per-subscription server or client lifecycle fault occurred without evidence currently captured;
4. the pinned SDK failed to deliver an item update without reporting a whole-client status change.

## Observability correction

The official Lightstreamer Python 1.0.3 listener contract includes subscription renewal,
unsubscription, server error, real maximum frequency and lost-update notifications. It also states
that a new `onSubscription` notification invalidates previously received subscription data. The
adapter must therefore retain bounded health/log evidence for:

- every subscription establishment and end by epic and connection generation;
- bounded subscription/server error codes, never provider error messages;
- the server-applied real maximum frequency for every required subscription;
- Lightstreamer-reported lost-update counts as sticky loss evidence;
- the last bounded client transport status and its observation time.

Subscription renewal must clear q-trad's item field state, side timestamps and freshness evidence so
readiness requires a new complete healthy update. These additions are prospective evidence only and
cannot reclassify the failed candidate.

## Experiments

### A. Corrected seven-instrument endurance

Leave the corrected collector untouched while it crosses repeated `20:00Z`–`22:00Z` intervals and a
weekend maintenance boundary. Read-only checkpoints record readiness, exact projection lag,
connection generation, queue occupancy/high-water, q-trad and Lightstreamer loss counters,
subscription lifecycle counts, frequency evidence and new gaps. A healthy five-hour interval before
the recurrent window is useful operating evidence but is not an endurance result.

### B. Isolated synthetic fault and load matrix

Before a release, deterministic local tests must cover:

- subscription renewal invalidating old state and requiring a fresh complete update;
- `STALLED`, both library-managed retry states, terminal disconnect and server errors;
- positive lost-update notification and sticky degraded health;
- invalid v2 CST/X-SECURITY-TOKEN recovery with one bounded replay of an idempotent REST read;
- changing demo request allowances without relying on stale configured numbers;
- 40-subscription-equivalent callback bursts with deliberately slow PostgreSQL;
- event-loop hand-off, queue occupancy, persistence lag and shutdown under each fault.

The load test passes only with zero q-trad drops, zero unreported Lightstreamer loss, bounded queue
and persistence latency, no correction feedback loop, and verified process exit.

### C. Provider-backed independent observation

If experiment A still produces unexplained per-item silences, run a minimal reference observer for
the same listings and UTC window on a separate network/process and separate evidence store. It
records only bounded transport/subscription lifecycle and callback time/changed-field evidence; it
does not write to the collector database or perform historical backfill.

The current one-connection-per-key rule remains in force. The observer therefore requires either a
separately scoped IG demo API key/account or an accepted ADR and explicit operator approval for a
temporary two-connection experiment after confirming IG's effective session limits. It must not be
quietly attached to the collector credential.

Interpretation is mechanical:

| Collector callback | Reference callback | Transport/subscription evidence | Result |
|---|---|---|---|
| absent | present | collector channel otherwise subscribed | collector client/path fault |
| absent | absent | both channels continuously subscribed | evidence consistent with provider item silence |
| present | absent | reference channel otherwise subscribed | reference client/path fault |
| present | present | collector canonical event absent | q-trad hand-off/queue/persistence fault |
| absent | any | disconnect, renewal, error or loss brackets interval | explicit lifecycle/loss event |

Agreement between two observers still does not prove the provider generated no hidden update, but it
is substantially stronger than historical bars and gives a reproducible attribution boundary.

## Exit gate

Corrected `capture-v1` can qualify only after a representative endurance interval demonstrates:

- zero q-trad dropped records and zero Lightstreamer-reported lost updates;
- every required subscription has current lifecycle and frequency evidence;
- bounded event-loop, queue and persistence lag through representative volatility;
- forced invalid-token and disconnect recovery obtains a fresh session and fresh per-channel data;
- every retained gap is explained by bounded lifecycle/market evidence, or no gap occurs;
- clean shutdown and restart leave one terminal prior run and one current generation.

Until those facts are proven, `capture-v2`, paper runtime and any claim of long-term stream
reliability remain gated.
