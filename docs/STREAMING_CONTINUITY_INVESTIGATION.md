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
- IG historical MID bars show price movement in all 70 intervals; none is completely flat, and each
  gap overlaps three to eight historical minute bars.

The verified bounded ingestion log contains 11 transport-status changes and ten staleness reports.
It contains no subscription error, retry-watchdog expiry or reconnect event during any retained gap.
The unrelated transport recovery at `2026-07-15T11:23Z` completed in about 1.5 seconds. The only full
application reconnect occurred at `2026-07-16T17:19Z`, several hours before that day's clustered
gaps.

This evidence rules out PostgreSQL lag, q-trad queue overflow, normalisation rejection and a whole
Lightstreamer connection outage as causes of the 70 gaps. It does not distinguish:

1. IG's demo streaming data adapter emitted no update for that item despite later historical movement;
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

IG's official Java and JavaScript samples also subscribe to the application data item
`TRADE:HB.U.HEARTBEAT.IP` in MERGE mode with field `HEARTBEAT`; the JavaScript sample explicitly uses
it to verify the connection. This is separate from Lightstreamer's protocol keepalive. No primary
source found in this investigation supports the stronger claim that the application subscription is
required to stop an IG session expiring. The next candidate nevertheless requires a fresh heartbeat
as independent whole-connection evidence and records it separately from every PRICE channel.

`trading-ig` 0.0.24 pins Lightstreamer Python client 1.0.3. Lightstreamer's IG-specific matrix
reports Server 7.3.3 and Python 1.0.3 as the matching deployment. A newer client's source-API
compatibility and local synthetic performance cannot establish compatibility with that provider
server. Proposed ADR 0025 therefore retains 1.0.3 plus q-trad's version-guarded disposal correction;
any Python 2.x evaluation is a separate future provider experiment.

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
- `STALLED`, both library-managed retry states, terminal disconnect and server errors, with fresh
  post-recovery PRICE and heartbeat evidence required before readiness returns;
- positive lost-update notification and sticky degraded health;
- invalid v2 CST/X-SECURITY-TOKEN recovery with one bounded replay of an idempotent REST read;
- changing demo request allowances without relying on stale configured numbers;
- 40-subscription-equivalent callback bursts with deliberately slow PostgreSQL;
- event-loop hand-off, queue occupancy, persistence lag and shutdown under each fault.

The load test passes only with zero q-trad drops, zero unreported Lightstreamer loss, bounded queue
and persistence latency, no correction feedback loop, and verified process exit.

The checked-in `ops/dev/stream-load-experiment.sh` creates a uniquely named tmpfs-backed test
database, migrates it to head, drives a worker-thread callback stream through the actual adapter
handoff, ingestion service and PostgreSQL store, then removes the database. It writes self-hashed,
non-overwriting evidence. Calibration at 40 subscriptions and 200 callbacks/second produced:

- 2,000/2,000 raw and canonical quotes with zero drops and 9.9 ms maximum lag with a 1 ms injected
  persistence delay;
- 2,000/2,000 with zero drops, queue high-water 799/10,000, p95 lag 6.58 seconds and maximum lag
  6.91 seconds with a 5 ms injected delay; and
- a superseded 1,000/1,000 zero-drop run under 2.2.2, retained only as local handoff evidence and not
  as provider compatibility or part of the heartbeat candidate;
- 60,000/60,000 over five minutes at 200 callbacks/second with zero loss, queue high-water 51/10,000,
  p95 lag 4.33 ms and maximum lag 257 ms; and
- 2,000/2,000 with an all-item renewal at five seconds, 40 renewal events and complete state
  re-established for every subscription.

These bounded profiles prove sustained local handoff, finite backlog drain and renewal ordering.
They do not prove provider compatibility, real Lightstreamer recovery or IG per-item delivery;
those remain provider-backed gates.

Python client 2.0.0 and later explicitly require Server 7.4.0, while IG publishes Server 7.3.3 and
Python 1.0.3 as its matching deployment. Do not run 2.x against IG. As a separate source audit,
compare the tagged 1.0.3 and 2.1.0 implementations behind 2.1.0's stated high-update-rate
improvement. Promote only a minimal understood change whose prerequisites fit 1.0.3, whose licence
and attribution are retained, and whose synthetic load, renewal and provider-recovery evidence all
pass. This audit is not a prerequisite for the heartbeat-only candidate unless it exposes a defect in
the pinned path.

The source audit identified upstream commit `3acadac599b06a64ff607b47f8973ae545be5a87` as the
performance change. It replaces message/subscription-manager linked and associative collections with
tombstone arrays plus an ordered integer map, and compacts those collections after mutation-prone
event fan-out. The companion commit tests collection iteration/removal semantics but supplies no
throughput benchmark. This is approximately 340 lines of shared Haxe core which must be transpiled
into the Python distribution, not a narrow Python runtime repair. With 7–40 subscriptions and q-trad's
200-callback/s handoff evidence already passing, no backport is proposed unless provider evidence
isolates 1.0.3 manager dispatch as a bottleneck.

The supported 1.0.3 API also exposes a narrower callback optimisation. Lightstreamer confirms that
`ItemUpdate.getValue(name)` first resolves the name to a numeric field position, while both
`getValue` and `isValueChanged` accept the 1-based subscription-field position directly. The
candidate therefore binds positions to the exact subscription field order and uses integer access
for price and heartbeat callbacks. Tests lock the first field to position 1 and preserve changed-field
and explicit-null semantics. The provider contrast uses the same access path; its evidence will show
whether callback pressure improves under real updates. See the
[Lightstreamer performance discussion](https://forum.lightstreamer.com/d/9325-python-client-itemupdategetvalue-method-very-slow-to-retrieve-value).

The checked-in `ops/dev/provider-recovery-experiment.sh` prepares the remaining provider-backed
fault proof without touching PostgreSQL. Under the same exact collector-stopped acknowledgement it
uses the production adapter, requires initial PRICE/heartbeat/frequency readiness, terminates the
actual Lightstreamer client and requires automatic recovery with fresh records from every
instrument. It then replaces only the local CST/XST request headers with a fixed invalid probe value;
one idempotent listing-review read must cause exactly one bounded reauthentication/replay and a
second fully ready stream generation. The non-overwriting manifest requires exactly two reconnects,
one REST reauthentication, current-key-validated published and effective trading/non-trading rates
after each login, zero
q-trad/SDK loss or subscription/server errors, no abandoned provider operation and verified cleanup.
It remains unexecuted while the corrected collector measurement owns the API key.

`ops/dev/verify_stream_experiment_evidence.py` independently validates either experiment. It
recomputes the manifest self-hash; for the contrast it additionally confines the event path beside
the manifest, streams every gzip JSON-lines record, rejects unreviewed fields or malformed lifecycle
records, requires increasing callback sequence, reconciles attempted/written/drop counts, and
recomputes the uncompressed count and SHA-256. It requires a non-empty boolean check set and verifies
that `PASS` means every check passed. Schema v1 additionally requires the exact contrast or recovery
check set, preventing a partial manifest from weakening the gate. A structurally valid `FAIL`
remains a failure.
For recovery evidence it additionally validates the ordered initial/disconnect/token phases, exact
reconnect and reauthentication progression, seven advancing instrument counts, per-phase
PRICE/heartbeat/frequency/rate evidence, zero loss and final client/session/worker/consumer cleanup.
The named recovery checks must agree with those structured facts.

The REST/session portion is implemented locally. An explicit invalid-token exception serialises
reauthentication and permits exactly one replay of the idempotent read. With an active stream it uses
the existing full stream-rebuild path so REST and streaming credentials cannot diverge; a standalone
research/backfill adapter replaces only its REST session. The adapter validates exactly one
configured-key row in the same `get_client_apps()` response used by the pinned library during each
login. It retains only the numeric published rates and requires the library's effective rates to equal
those values minus its reviewed two-request safety margin; missing, duplicate, malformed or changed
semantics fail closed. This makes no second request and records no returned API key. Any `exceeded-*`
response remains authoritative, is
recorded by bounded code and is not automatically retried. Historical remaining allowance continues
to come from each historical response and remains separate from these short-window rates.

### C. Provider-backed same-connection feed contrast

If experiment A still produces unexplained per-item silences, run a bounded observer that subscribes
to both IG feed types for the same explicitly reviewed epics on one Lightstreamer connection:

- operational `PRICE:{account identifier}:{epic}` in MERGE mode;
- reference `CHART:{epic}:TICK` in DISTINCT mode with `BID`, `OFR` and `UTM`.

IG documents a default limit of 40 subscriptions on one connection, describes MERGE PRICE delivery
as rate-regulated, and explicitly warns that multiple concurrent connections can suspend the API
key. Seven PRICE plus seven CHART subscriptions and the heartbeat remain within the published
single-connection limit.
See the [IG streaming guide](https://labs.ig.com/streaming-api-guide.html) and
[streaming field reference](https://labs.ig.com/streaming-api-reference.html). Because DEMO limits can
differ, the experiment requires explicit acknowledgement and fresh data from all 15 subscriptions;
any rejection or partial set fails closed.

Run this as a separate bounded experiment after the corrected collector measurement, not as an
unreviewed sidecar or a concurrent connection. It records callback time, provider timestamp,
changed-field identity, subscription lifecycle, applied frequency and loss/error counters in a
separate evidence store. It does not write to the collector database or perform historical backfill.
Adding the CHART diagnostic feed to an operational collector release would require a separately
reviewed architecture/release decision.

The checked-in `ops/dev/provider-stream-contrast.sh` enforces this boundary with an exact
`COLLECTOR_STOPPED_AND_NO_OTHER_STREAM` acknowledgement. It accepts only the reviewed seven-item
universe, caps duration at six hours and requires all 15 subscriptions to acknowledge and emit data.
The mode-0600 manifest binds a compact gzip JSON-lines event stream by uncompressed SHA-256. Events
contain channel identity, receive time, provider timestamp, changed fields and bounded lifecycle
codes, but no account identifier, session token, provider message or price value. Any writer queue
loss, SDK loss report, partial subscription, server/subscription error, threshold-exceeding heartbeat
silence, unexplained feed discrepancy, stale terminal channel evidence, non-connected pre-shutdown
transport or unverified unsubscribe/disconnect fails closed. REST logout, HTTP-session close and termination of
every bounded provider worker are also explicit gates. Provider login/readiness rejection leaves a
non-overwriting failure artifact rather than disappearing as console output.

Interpretation is mechanical:

| PRICE callback | CHART callback | Transport/subscription evidence | Result |
|---|---|---|---|
| absent | present | both subscriptions otherwise current | PRICE adapter/subscription path fault or suppression |
| absent | absent | both subscriptions current while other epics update | evidence consistent with provider per-item silence |
| present | absent | both subscriptions otherwise current | CHART adapter/subscription path fault or suppression |
| present | present | PRICE canonical event absent | q-trad hand-off/queue/persistence fault |
| absent | any | disconnect, renewal, error or loss brackets interval | explicit lifecycle/loss event |

Agreement between the feeds still does not prove the provider generated no hidden update, but it is
substantially stronger than historical bars and gives a reproducible attribution boundary. Only if
same-connection contrast remains ambiguous should a separate-network observer be considered; that
requires a separately scoped demo API key/account, because the collector key must never open a
second concurrent connection.

## Execution sequence

The evidence stages are deliberately non-substitutable:

1. Leave the currently deployed overload-corrected Lightstreamer 1.0.3 collector untouched through
   the recurrent `20:00Z`–`22:00Z` windows and a weekend maintenance boundary. This measures whether
     the callback-overload correction removed the earlier loss, but cannot qualify heartbeat.
2. Close and stop that run only through an operator-approved procedure which proves no remaining
   connection for the API key. During an active market window, run the three-hour 15-subscription
   contrast and then the short q-trad recovery experiment. Market closure is not a valid way to make
   all CHART:TICK channels data-ready.
3. If either provider experiment fails, retain its failure evidence and do not accept ADR 0025 or
   deploy the candidate. Independently verify its manifest/event hashes, diagnose or revise
   locally, then repeat under a new non-overwriting evidence identity.
4. If both pass, accept ADR 0025 through review, merge the exact green commit, publish its immutable
   amd64/arm64 image and deploy that digest through the capture release procedure. The old-image
   endurance and local provider runs do not substitute for ARM/provider evidence from this image.
5. Run the exact candidate image for a fresh 72-hour `capture-v1` endurance interval spanning the
   recurrent daily window. Require heartbeat and every PRICE lifecycle/frequency channel current,
   zero q-trad or SDK loss, bounded queue/projection lag, no unexplained gaps and a clean terminal
   shutdown. Only this final stage can admit `capture-v2` mapping qualification.

## Exit gate

Corrected `capture-v1` can qualify only after a representative endurance interval demonstrates:

- zero q-trad dropped records and zero Lightstreamer-reported lost updates;
- every required subscription has current lifecycle and frequency evidence;
- the independent heartbeat is acknowledged and fresh, without substituting for per-PRICE evidence;
- bounded event-loop, queue and persistence lag through representative volatility;
- forced invalid-token and disconnect recovery obtains a fresh session and fresh per-channel data;
- every retained gap is explained by bounded lifecycle/market evidence, or no gap occurs;
- clean shutdown and restart leave one terminal prior run and one current generation.

Until those facts are proven, `capture-v2`, paper runtime and any claim of long-term stream
reliability remain gated.
