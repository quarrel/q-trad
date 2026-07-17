# ADR 0025: heartbeat evidence and maintained Lightstreamer client qualification

- **Status:** Proposed
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

`trading-ig` 0.0.24 pins `lightstreamer-client-lib==1.0.3`. Lightstreamer has superseded that release;
its current Python 2.2.2 client is documented as compatible with code from 2.2.1, and the 2.1.0
changelog specifically records improved high-update-rate performance. ADR 0010 kept 1.0.3 until
compatibility could be proven.

## Proposed decision

For the next candidate release:

- add one `TRADE:HB.U.HEARTBEAT.IP`/`HEARTBEAT` MERGE subscription on the existing single
  Lightstreamer connection, without a data-adapter override;
- require heartbeat subscription acknowledgement and one fresh heartbeat for readiness;
- retain heartbeat receive time, bounded value, event count, subscription lifecycle, applied
  frequency and loss/error evidence separately from per-PRICE freshness;
- never append heartbeat updates to raw market capture or canonical quote history and never claim
  that heartbeat continuity proves an individual PRICE update was emitted;
- marshal changed-field normalisation and renewal invalidation through the same event-loop boundary;
- use uv's reviewed transitive override to lock `lightstreamer-client-lib==2.2.2`, while retaining
  `trading-ig` only for IG REST access; and
- qualify the exact dependency lock and application image against IG demo before deployment. A
  successful import/API test or synthetic load result is necessary but not provider compatibility;
  and
- run the reviewed seven-PRICE/seven-CHART:TICK/heartbeat contrast only after the collector
  measurement and a verified operator-approved stop. Require one connection, all 15 channels
  data-ready, bounded non-overwriting callback/lifecycle evidence, zero loss and verified teardown;
  and
- on a separate single-connection run under the same stop gate, terminate the real client and inject
  a fixed invalid local REST token. Require fresh per-channel data after automatic disconnect
  recovery and after exactly one bounded REST reauthentication/replay, followed by verified cleanup.

The heartbeat consumes one additional subscription: `capture-v1` uses eight, and the proposed
seven-PRICE/seven-CHART contrast uses fifteen, both below IG's documented default limit of 40.

## Consequences

Continued heartbeat with one stale PRICE item becomes strong whole-connection continuity evidence;
loss of both brackets the silence at the transport or provider-session boundary. Neither outcome
alone establishes what an unobserved provider update contained.

The dependency override intentionally disagrees with `trading-ig`'s exact transitive pin, so q-trad's
locked uv environment and immutable image are the supported runtime. The override must be removed
if the upstream package adopts a compatible maintained client. Promotion remains blocked until a
single-connection provider experiment proves connection, all subscription acknowledgements, fresh
data, reconnect, clean shutdown and no unexplained loss.

Primary references:

- [IG Java sample heartbeat subscription](https://github.com/IG-Group/ig-webapi-java-sample/blob/c9d8bcb6b3dede7657f5689c5f4d76a910494eb5/ig-webapi-java-sample-console/src/main/java/com/iggroup/webapi/samples/Application.java)
- [IG JavaScript sample connection-verification heartbeat](https://github.com/IG-Group/ig-webapi-js-ember-sample/blob/479c6e07d0b1673b4eca391c9956ab04e4156d2f/app/components/heart-beat.js)
- [Lightstreamer Python client changelog](https://github.com/Lightstreamer/Lightstreamer-lib-client-haxe/blob/ffc256805539ae7d83ae82f375abf16bfd3ab9fc/CHANGELOG-Python.md)
