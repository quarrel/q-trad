# ADR 0025: heartbeat evidence on IG's supported Lightstreamer client

- **Status:** Accepted
- **Date:** 2026-07-17

## Context

The failed cloud candidate retained 70 intervals with no raw callback for one required PRICE item
while other items on the same connection continued. Historical bars moved in every interval, but
neither aggregate price activity nor transport `CONNECTED` status proves that one subscription was
delivering.

IG's official Java and JavaScript samples subscribe to `TRADE:HB.U.HEARTBEAT.IP` in MERGE mode with
the `HEARTBEAT` field. The JavaScript sample describes it as verification of the connection. It is
an application data item, distinct from Lightstreamer's protocol-level keepalive and recovery; the
available primary sources do not support treating the subscription as a requirement that renews or
prevents expiry of the IG session.

`trading-ig` 0.0.24 pins `lightstreamer-client-lib==1.0.3`. Lightstreamer's IG-specific download
matrix reports that IG deploys Server 7.3.3 and identifies Python client 1.0.3 as the matching SDK.
Lightstreamer's general compatibility promise is asymmetric: newer servers support older clients,
but newer clients can require a later server. Compatibility of Python 2.x with IG's deployed server
therefore cannot be inferred from source-API compatibility or a synthetic test. ADR 0010 keeps 1.0.3
until provider compatibility is established independently.

The Python changelog makes the incompatibility explicit: 2.0.0 and every later 2.x release require
Server 7.4.0, and 2.0.0 is not source-compatible with 1.x. Version 2.1.0 separately records improved
high-update-rate performance. That improvement is relevant evidence for a source-level audit, but it
does not authorise the 2.x package against IG or prove that one isolated change can safely be moved to
1.0.3.

## Decision

For the next candidate release:

- add one `TRADE:HB.U.HEARTBEAT.IP`/`HEARTBEAT` MERGE subscription on the existing single
  Lightstreamer connection, without a data-adapter override;
- require heartbeat subscription acknowledgement and one fresh heartbeat for readiness;
- retain heartbeat receive time, bounded value, event count, subscription lifecycle, applied
  frequency and loss/error evidence separately from per-PRICE freshness;
- invalidate heartbeat readiness on every transport degradation and require a new heartbeat on the
  recovered transport, independently of the retained staleness/watchdog grace window;
- use heartbeat staleness, explicit transport lifecycle failures and the library-recovery watchdog
  as whole-connection reconnect evidence. Quote recency is separate per-instrument telemetry and
  cannot by itself degrade or reset a connection while heartbeat is fresh: a quiet or closed market
  is not transport-failure evidence;
- require transport connection, current heartbeat, every required PRICE subscription acknowledgement
  and at least one callback from every required PRICE channel for operational readiness. The callback
  may carry a closed, partial or unchanged market snapshot; strategy-level quote usability and age
  remain separate. Missing heartbeat, transport, subscription acknowledgement or channel callback
  remains a startup failure;
- never append heartbeat updates to raw market capture or canonical quote history and never claim
  that heartbeat continuity proves an individual PRICE update was emitted;
- marshal changed-field normalisation and renewal invalidation through the same event-loop boundary;
- retain `trading-ig`'s exact `lightstreamer-client-lib==1.0.3` pin and q-trad's narrow reviewed
  WebSocket-disposal correction; do not include a Python 2.x override in the heartbeat release; and
- qualify the exact supported dependency lock and application image against IG demo before
  deployment. Any later Python-client upgrade is a separate compatibility experiment and ADR change,
  not part of heartbeat qualification; and
- run the reviewed seven-PRICE/seven-CHART:TICK/heartbeat contrast only after the collector
  measurement and a verified operator-approved stop. Require one connection, all 15 channels
  data-ready, bounded non-overwriting callback/lifecycle evidence, zero loss and verified teardown;
  and
- on a separate single-connection run under the same stop gate, terminate the real client and inject
  a fixed invalid local REST token. Require fresh per-channel data after automatic disconnect
  recovery and after exactly one bounded REST reauthentication/replay, followed by verified cleanup;
  and
- independently revalidate each non-overwriting experiment manifest. For callback evidence, confine
  its event stream beside the manifest and recompute the parsed record count and uncompressed hash.

The heartbeat consumes one additional subscription: `capture-v1` uses eight, and the proposed
seven-PRICE/seven-CHART contrast uses fifteen, both below IG's documented default limit of 40.

## Consequences

Continued heartbeat with one stale PRICE item becomes strong whole-connection continuity evidence;
loss of both brackets the silence at the transport or provider-session boundary. Neither outcome
alone establishes what an unobserved provider update contained.

This separation also permits closed-market endurance evidence: a healthy heartbeat can keep the
connection under observation without repeated reconnects caused solely by unchanged prices. It does
not prove an individual item remains productive after its initial callback or repair an isolated
item. Continue pairing heartbeat continuity and subscription/loss evidence with gap projections and
quota-bounded historical-API corroboration until the provider path earns operational confidence.

The heartbeat candidate retains IG's published Python-client match and isolates the behavioural
change under test. This forgoes fixes in later client releases; the existing narrow disposal repair
remains reviewable and version-guarded. A controlled weekend endurance deployment is permitted after
local lifecycle gates and the closed-market heartbeat diagnostic pass. Full `capture-v1`
qualification remains blocked until active-market evidence proves connection, all subscription
acknowledgements, fresh per-channel data, reconnect, clean shutdown and no unexplained loss.

Separately compare the tagged 1.0.3 and 2.1.0 sources to identify the exact performance change and
its prerequisites. Any proposed backport needs its own narrow patch, attribution/licence review,
high-rate and renewal-under-load regression evidence, and provider recovery qualification on 1.0.3;
do not copy the 2.x implementation wholesale or fold that work into heartbeat qualification.

Primary references:

- [IG Java sample heartbeat subscription](https://github.com/IG-Group/ig-webapi-java-sample/blob/c9d8bcb6b3dede7657f5689c5f4d76a910494eb5/ig-webapi-java-sample-console/src/main/java/com/iggroup/webapi/samples/Application.java)
- [IG JavaScript sample connection-verification heartbeat](https://github.com/IG-Group/ig-webapi-js-ember-sample/blob/479c6e07d0b1673b4eca391c9956ab04e4156d2f/app/components/heart-beat.js)
- [IG-specific Lightstreamer server/client compatibility matrix](https://www.lightstreamer.com/share/ig/)
- [Lightstreamer Python client changelog](https://github.com/Lightstreamer/Lightstreamer-lib-client-haxe/blob/ffc256805539ae7d83ae82f375abf16bfd3ab9fc/CHANGELOG-Python.md)
