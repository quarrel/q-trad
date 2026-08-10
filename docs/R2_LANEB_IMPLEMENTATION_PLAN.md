Lane B has reached its full reviewed native-capture universe. B1–B5 are implemented and merged, the twenty-contract `capture-ibkr-v1` release is deployed on the independent IBKR runtime, and its bounded qualification passed on 2026-08-10. This plan records the achieved boundary and the remaining lifecycle evidence.

The implemented pieces are:

* the continuous Level-1 adapter and its provider-neutral ingest composition;
* `IbkrSession` generation, subscription, farm-state, recovery and resubscription ownership;
* generation/arrival-aware canonical persistence and truthful health/reconciliation;
* hardened deployment, maintenance-stop, bounded-capture, backup, qualification and restore interfaces
  under `ops/ibkr`; and
* a distinct IBKR runtime/store with no fabricated sides, trade-volume semantics or broker-order surface.

The next native-capture gate is the complete weekly Gateway reauthentication lifecycle, not another collector architecture or universe expansion.

# Lane B — IBKR native collector

## Objective

Operate and progressively qualify:

```text
MarketDataSourceClass.IBKR_NATIVE_CAPTURE
capture-ibkr-v1
capture_source_id = "ibkr-paper-v1"
```

using the q-trad ingest/canonical-event infrastructure, official direct TWS API, `IbkrSession`,
PostgreSQL storage, read-only health API and the independent `ops/ibkr` host runtime.

The first useful outcome was deliberately **not “capture complete.”** It was a trustworthy native IBKR collector preserving every callback needed for later replay. Exact-two B3, fixed-six B4 and full-universe B5 qualified on 2026-08-10. A complete weekly reauthentication boundary and R2-IBKR-NATIVE remain later gates.

---

# Hard no-touch boundary

This work should branch from current `main` and remain independent of Lane A and PR #67.

Do **not** modify:

* `src/qtrad/application/r2_holdout.py`
* `src/qtrad/domain/r2_holdout.py`
* `src/qtrad/runtime/r2_holdout.py`
* R2.G1/G2 selection, holdout or evaluation contracts
* Stage 7/8 provider-history/foundation construction except for a genuinely shared bug
* historical acquisition plans/results or the currently running evidence
* any real historical evidence files
* IG collector behaviour
* order/trading interfaces
* `STATUS.md` to claim deployment or qualification before it actually occurs.

Also do not make the native collector depend on completion of Stage 8 or PR #67.

The live source and historical source are separate:

```text
IBKR_HISTORICAL_RESEARCH != IBKR_NATIVE_CAPTURE
```

That separation is already a durable repository decision.

---

# PR B1 — Continuous IBKR Level-1 adapter

This should be the first agent task.

## Scope

Add the missing continuous adapter under something like:

```text
src/qtrad/adapters/ibkr/market_data.py
```

It should implement the existing provider-neutral `MarketDataAdapter` boundary rather than introduce another ingestion architecture.

Reuse the same official TWS API transport/session machinery used by capability and historical acquisition.

### Required subscription

Use `reqMktData` for exact, already-reviewed contracts.

The native initial subscription needs top-of-book information sufficient to retain:

```text
bid price
ask price
bid size
ask size
```

plus the provider callback metadata necessary to understand it.

Do not introduce tick-by-tick or depth yet.

Do not request generic ticks without a specific use.

### Callback identity

Every accepted callback must retain at least:

```text
connection generation
local monotonic callback sequence
IBKR request/subscription ID
canonical instrument ID
exact contract/conId identity
callback type
IBKR tick type
local received_at
raw provider value
```

The architecture explicitly requires generation-authenticated local arrival order and treats payload-equal callbacks with different callback identities as distinct evidence.

If the current `MarketDataRecord` port cannot represent generation/arrival sequence cleanly, make the **smallest provider-neutral port amendment necessary** rather than hiding those identities in a deduplication hash.

I would prefer first-class optional source chronology fields, e.g. conceptually:

```python
source_generation: int | None
source_sequence: int | None
```

with IG remaining unaffected.

### Critical semantic rule

Do **not** turn each callback into a fabricated complete quote.

IBKR may provide:

```text
bid changes
ask changes
size changes
```

as separate callbacks.

A raw callback saying “bid changed” must remain a bid callback.

It may update adapter-local state used to emit a derived quote snapshot, but the raw canonical evidence must preserve exactly what arrived.

No:

```text
bid callback
→ carry old ask forward
→ pretend provider emitted bid+ask simultaneously
```

without an explicit derived/coalesced boundary.

The architecture already requires one-sided states to be representable and forbids inventing sides.

### Subscription identity

Build desired `IbkrSubscription`s from the exact selected contracts and register them with the existing `IbkrSession`.

Do not rediscover or fuzzy-match contracts during ingestion.

The subscription mapping should be immutable for a running capture generation:

```text
canonical instrument
→ selected IBKR contract fingerprint / conId
→ request ID
```

Unknown callbacks/request IDs fail visibly.

### Generation handling

Every API connection:

```text
begin_connection()
→ new generation
```

Callbacks from superseded generations are dropped as stale operational callbacks but increment/report the appropriate health evidence.

Use `IbkrSession.accept_callback()` rather than implementing another generation mechanism.

### 1100 / 1101 / 1102 / 1300

Reuse `IbkrSession` semantics exactly.

Especially:

**1101 — connectivity restored, data lost**

```text
one recovery epoch
active subscriptions cleared
exact desired subscriptions resubmitted once
```

No duplicate resubscription storm.

**1102 — restored, data maintained**

Revalidate server time; don't blindly recreate subscriptions.

**1300**

Treat as socket-generation replacement/restart boundary.

### Market-data type

Require the intended account-visible real-time paper market-data type.

Delayed/frozen data must not silently pass as healthy native capture.

If IBKR reports another market-data type, preserve it as evidence and mark the affected subscription/source unhealthy or ineligible according to the frozen policy.

---

## B1 tests

This PR should be predominantly fixture tests and need no Gateway.

At minimum:

1. handshake → server time → exact subscriptions;
2. BID callback produces correct raw/canonical evidence;
3. ASK callback;
4. BID_SIZE / ASK_SIZE;
5. payload-equal callbacks with different sequence IDs remain separate;
6. callbacks retain exact generation and monotonically increasing local sequence;
7. old-generation callbacks are rejected;
8. unknown request ID is visible/fail-closed;
9. one-sided quote state does not invent the other side;
10. crossed bid/ask produces an explicit disposition rather than correction;
11. invalid/non-finite/non-positive prices are rejected or visibly classified;
12. sizes are not described as trade volume;
13. 1100 degradation;
14. 1101 causes exactly one complete resubscription epoch;
15. repeated 1101 doesn't duplicate subscriptions;
16. 1102 revalidates without unnecessary subscription duplication;
17. 1300 establishes a new generation;
18. disconnected farm makes health degraded;
19. quiet/inactive farm isn't confused with infrastructure loss where the existing reducer says otherwise;
20. no order method/import/command becomes reachable.

### B1 exit

A fixture adapter can feed exact Level-1 IBKR callback sequences through the existing `MarketDataAdapter` abstraction with deterministic replay and no semantic fabrication.

**No deployment in this PR.**

---

# PR B2 — Compose IBKR into the existing ingest runtime

Once B1 merges, wire it into the normal ingestion application.

This is where I would be particularly strict about **not creating `ibkr_ingest.py` as a second application architecture**.

The durable architecture says one codebase/application image and provider-neutral composition; only provider lifecycle differences belong in the adapter/runtime boundary.

## Runtime shape

Conceptually:

```text
qtrad ingest
    |
    +-- provider=ig
    |      ↓
    |   IG MarketDataAdapter
    |
    +-- provider=ibkr
           ↓
        IBKR MarketDataAdapter
```

Both flow into the same application-level raw/canonical persistence services.

The stores are physically independent because their deployments point to different PostgreSQL instances.

### Configuration

Add provider-specific settings only where necessary:

```text
QTRAD_PROVIDER=ibkr
QTRAD_ENVIRONMENT=paper

IBKR_API_HOST
IBKR_API_PORT
IBKR_CLIENT_ID
IBKR_EXPECTED_GATEWAY_VERSION
IBKR_EXPECTED_API_VERSION
IBKR_MARKET_DATA_TYPE
IBKR_UNIVERSE_PATH
```

No:

```text
username
password
2FA seed
trading account authority
```

inside q-trad.

Gateway authentication remains operator/IBC-owned.

### Capture identity

Make sure the normal capture identity surfaces:

```text
source_id = ibkr-paper-v1
universe = capture-ibkr-v1
configuration_hash = <immutable universe/config hash>
```

The existing capture-feed identity already supports provider-neutral source/universe/configuration identities.

### Storage

Use the normal raw/canonical persistence path.

Do not write IBKR native events into the IG database.

The IBKR deployment points the same storage implementation at its own independent PostgreSQL database.

### Event ordering

The persisted raw callback must allow deterministic reconstruction of:

```text
generation
then local callback arrival sequence
```

Do not infer chronology from provider timestamps.

Where provider timestamps exist, retain them separately.

### Derived quote state

If the current ingest path requires a `MarketQuote`, implement an explicit IBKR quote-state reducer:

```text
raw callback stream
        ↓
per-subscription current side state
        ↓
derived MarketQuote where semantically valid
```

It needs separate timestamps for the side observations if the canonical quote contract supports them.

If it doesn't, **fail closed rather than pretending the two sides are contemporaneous**.

This is important because later spread/quote-imbalance work depends on precisely this distinction.

### Bars

Do not add native IBKR bar subscriptions in this lane unless the existing canonical minute-bar projector naturally derives bars from valid quote events.

The source of truth for native evidence should initially be Level-1 callbacks.

---

# PR B2 health contract

Health should expose enough information for an operator to tell the difference between:

```text
socket connected
Gateway authenticated
handshake complete
server-time validated
market-data farm healthy
correct market-data type
desired subscriptions = N
active subscriptions = N
first quote received
recent quote received
callback queue healthy
persistence queue healthy
drops = 0
current generation
```

The existing `IbkrSessionSnapshot` already exposes generation, farms, desired/active subscription counts and reason codes.

Adapter health should add per-subscription delivery freshness.

A connection with:

```text
20 / 20 subscribed
0 / 20 ever updated
```

is **not** capture-ready.

But distinguish:

```text
known closed/inactive market
```

from:

```text
expected active market with stale data
```

rather than requiring continuous ticks overnight.

### Health reason codes

Prefer stable codes over prose. For example:

```text
IBKR_SOCKET_UNAVAILABLE
IBKR_HANDSHAKE_PENDING
IBKR_SERVER_TIME_UNVERIFIED
IBKR_MARKET_DATA_FARM_DOWN
IBKR_WRONG_MARKET_DATA_TYPE
IBKR_SUBSCRIPTIONS_INCOMPLETE
IBKR_NO_FIRST_QUOTE
IBKR_ACTIVE_MARKET_STALE
IBKR_CALLBACK_QUEUE_PRESSURE
IBKR_CALLBACK_DROP
IBKR_PERSISTENCE_LAG
IBKR_OPERATOR_AUTH_REQUIRED
```

Don't make current text/error strings semantic identities.

---

## B2 tests

Add an integration fixture:

```text
fake IBKR callback stream
→ real IBKR adapter
→ real application ingest service
→ disposable PostgreSQL
→ raw records
→ canonical events
→ health/read-only API
```

Prove:

* raw callback count reconciles;
* canonical stream versions are deterministic;
* source identity is IBKR, never IG;
* callback arrival order survives persistence;
* no silent callback loss;
* restart creates a new generation;
* duplicate payloads don't collapse when callback identities differ;
* replay gives identical canonical results;
* subscription recovery doesn't duplicate stream history;
* queue/drop counters propagate to health;
* IG tests remain unchanged.

Run the PostgreSQL/full project gate.

---

# PR B3 — `capture-ibkr-v1` immutable universe and two-instrument release

Only after B1+B2 are merged.

This should be operationally narrow.

## Initial universe

I would use exactly two of the already-qualified six:

```text
AUD/USD       conId 14433401
Australia 200 conId 111987484
```

Why these two?

They are already part of the fixed historical representative set, and together give one FX and one equity-index product. The exact IDs are already frozen in the historical/contract work.

This is a **capture qualification subset**, not a new research-selection decision.

### Immutable universe artifact

Create `capture-ibkr-v1` using the same broad catalogue/operator-selection philosophy as IG:

```text
canonical instrument
exact reviewed IBKR listing
contract fingerprint
capture role
expected market-data type
```

No runtime fuzzy lookup.

### Host

Reuse the existing IBKR host and `ops/ibkr` scaffolding.

The historical CPU/Parquet work has moved away from the machine, making it a sensible continuous collector host.

The host already has deployment, ingest-env, healthcheck, backup and restore-verification assets.

Do not rebuild the host.

### Services

Target shape:

```text
IB Gateway + IBC
        |
        | localhost/private TWS socket
        v
qtrad ingest --provider ibkr
        |
        v
independent PostgreSQL
        |
        +--> read-only qtrad API
        |
        +--> backup / restore verification
```

No public TWS socket.

No order ports.

No credential automation beyond what is already separately approved for IBC/operator login.

---

# First live rollout gate — 2 instruments

Do this as a separately authorised operational step after B3 code review.

Before calling the two-instrument collector healthy require:

```text
exact deployed image identity
exact capture-ibkr-v1 config hash
Gateway/API versions match
paper environment
expected client ID
server time verified
market data type correct

2 desired subscriptions
2 active subscriptions

both instruments have:
    first valid price-side callback
    recent valid callback while their market is active

callback drops = 0
queue overflow = 0
unknown request callbacks = 0 or explicitly reconciled
persistence caught up
no unexplained generation churn
```

Observe through an actual active period for both product types.

Do **not** wait a week before expanding to six if the two-instrument path is behaving correctly.

---

# PR B4 / operational step — six target instruments

The fixed six promoted and qualified on 2026-08-10:

```text
AUD/USD
EUR/USD
Australia 200
US 500
spot gold
US crude
```

These are already the R2 historical representative targets; this avoids inventing another core subset while Lane A is moving. The historical plan binds their exact conIds.

Require:

```text
6 desired
6 active
6 first-update evidence
active-session freshness
zero unexplained drops
reconnect test
```

At this point we have started the clock on precisely the eventual `R2-IBKR-NATIVE` target set.

This is the highest-value near-term outcome of Lane B.

## Qualification evidence software boundary

The B3-to-B4 transition uses separate read-only snapshot and verification steps. For B4, each step
restores both the qualification-bound B3 parent archive and the current B4 archive into simultaneous,
independently owned disposable databases through the hardened dual-restore wrapper. The workflow binds
each archive SHA-256, source database, restored database, migration head and completion in create-only
evidence. The builder retains raw/canonical chronology, durable counters, current health, run identity and
controlled reconnect evidence in a create-only artifact. A separate verifier repeats both hash-checked
restores, re-queries the retained evidence and compares it with the snapshot before it can mint the
runtime-only qualification capability. A named clone or caller-supplied restore URL is insufficient. The
qualifying collector run is stopped cleanly before backup; its final counters and last healthy
post-reconnect snapshot are retained in immutable terminal run detail.

The artifact hash proves content identity only. A file-only check, API response, fake store,
closed-market connection or operator assertion cannot grant qualification. Final-image B3 session
`1dfa5a7a-fee2-40b2-8900-13359a0977e9` received and persisted 1,010 callbacks with zero failed, dropped or reconciliation-loss callbacks and passed independent restored replay. B4 session `c014d0e5-1f71-42fe-b99a-007390c83ede` received and persisted 3,636 callbacks with the same zero-loss boundary; generation 1 to 2 retained fresh LIVE bid and ask; and two independent dual-restore replays minted `B4_EXACT_SIX`. B5 session `971facc4-cab4-413a-a29a-27c7f7ac89e1` then received and persisted 24,056 callbacks with zero loss, crossed generation 1 to 2 with fresh LIVE bid/ask evidence for all twenty contracts, and passed snapshot plus independent three-restore replay to mint `B5_FULL_UNIVERSE`.

---

# PR B5 / completed operational step — full accepted IBKR capture universe

Completed on 2026-08-10 after clean six-target collection: the collector expanded to the full reviewed accepted universe.

The original qualification contains twenty selected mappings across FX, indices and commodities.

The promotion must prove:

```text
exact subscription count
every configured contract active or explicitly unavailable
no capacity regression
no callback loss
bounded queues
reasonable persistence lag
```

If one product becomes problematic, quarantine it visibly rather than hiding unavailability or weakening the qualified useful set.

---

# Qualification work that should run after capture starts

These are **evidence accumulation tasks**, not prerequisites for starting capture.

## Reconnect qualification

Inject/test:

```text
socket reconnect
1100
1101
1102
1300
market-data farm disconnect/reconnect
Gateway daily restart
```

Verify exact subscription reconstruction and explicit unavailable intervals.

## Backup/restore

The host already has the scaffolding. Exercise it against the native capture database once real events exist.

Verify:

```text
backup identity/checksum
restore into disposable DB
migration state
raw count
canonical count
source identity
stream/version integrity
```

Run restore at a time that does not interfere with ingestion.

## Weekly Gateway lifecycle

This is deliberately a **later qualification gate**.

The normative plan says capture isn't formally complete until a full weekly reauthentication boundary has been observed:

```text
authentication expires
→ collector fails closed
→ operator alert
→ manual login
→ new connection generation
→ subscriptions reconstructed exactly
→ unavailable interval explicitly accounted
```

Until then report:

```text
GATEWAY_WEEKLY_LIFECYCLE_UNQUALIFIED
```

but continue collecting useful native history.

---

# Things the agent should explicitly not build

This lane should **not** grow into:

* tick-by-tick history;
* market depth/order book;
* trades;
* order entry;
* positions/account data;
* automated Gateway credentials;
* source mixing;
* IBKR-native R1/R2 construction;
* BID/ASK historical backfill;
* quote-imbalance feature implementation;
* CVD or trade-volume semantics;
* another PostgreSQL schema just because the provider is IBKR;
* another web API;
* another ingestion application.

The current architecture explicitly says to reuse the provider-neutral ingestion services rather than create a second application architecture.

---

# Status-aware execution order

Completed through 2026-08-10:

```text
1. Audited the continuous-ingest seam and IBKR session/capability adapter.
2. Implemented and merged B1 continuous Level-1 capture and recovery chronology.
3. Implemented and merged B2 composition, canonical persistence, health and PostgreSQL coverage.
4. Implemented and merged B3 immutable exact-two release/deployment wiring.
5. Added independently replayable qualification evidence and hardened operational interfaces.
6. With explicit operational authority, deployed exact two on the independent IBKR runtime.
7. Qualified the controlled active-session reconnect, zero-loss, backup and restore boundary.
```

Completed on 2026-08-10:

```text
8. Refreshed and independently reverified exact-two B3 on the final main image.
9. Promoted through B4 exact-six and repeated the controlled reconnect, zero-loss and dual-restore boundary.
10. Promoted through B5 full universe and repeated the controlled reconnect, zero-loss and three-restore boundary.
```

Next:

```text
11. With separate authority: complete weekly Gateway reauthentication qualification.
```

---
