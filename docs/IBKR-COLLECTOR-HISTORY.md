# IBKR collector and history proposal source

**Status:** SUPERSEDED AS NORMATIVE PLAN

This proposal is retained as review source material. The approved decisions, corrected sequencing and
complete implementation gates are now governed by `docs/IBKR_CAPTURE_IMPLEMENTATION_PLAN.md` and
ADR 0028. Where this proposal differs from those authorities, the normative plan and ADR control.

The core proposal is a **second independent capture source**, not an IBKR mode inside the existing IG
`capture-v4` collector.

Notes on timing for historical data:

* **One-minute history should be fast.** IBKR allows one-minute bars over durations up to 52 weeks, and supports `MIDPOINT`, `BID` and `ASK`. For twenty instruments, a 16-week MIDPOINT bootstrap may require only one request per instrument; your approximately nine-minute estimate is plausible, although it should be measured rather than treated as a guaranteed SLA. ([IBKR Campus US][1]) `BID` and `ASK` provide more information, however, and are preferred in general.
* **A week of historical one-second bars is much slower.** One-second requests are limited to 2,000 seconds each. A week therefore requires 303 requests per instrument, or 6,060 requests for twenty instruments for one data type. With the 60-small-bar-requests-per-ten-minute pacing rule, the absolute lower bound is about **16 hours 50 minutes**, before response latency; MIDPOINT, BID and ASK separately would multiply that workload. Bars of 30 seconds or less are also unavailable once older than six months. ([IBKR Campus US][1])
* **Live capture is the better one-second path.** Twenty streaming top-of-book subscriptions fit comfortably within the default 100 market-data lines. IBKR’s dedicated real-time bars are five-second bars, while tick-by-tick subscriptions are normally limited to approximately 5% of the account’s market-data-line allowance—about five simultaneous tick-by-tick instruments at the default allocation. ([IBKR Campus US][2])

So the recommended acquisition strategy is:

1. bootstrap R2 quickly with 16+ weeks of one-minute historical MIDPOINT bars;
2. add historical BID and ASK where useful;
3. collect live top-of-book updates for the full universe;
4. use historical one-second bars only for bounded investigations, not as the primary twenty-market ingestion path.

---

## 1. Fixed architectural decisions

### 1.1 A separate source, database and runtime

Create:

```text
capture-ibkr-v1
capture_source_id = "ibkr-paper-v1"
provider = "ibkr"
environment = "paper"
```

It should have:

* its own IB Gateway;
* its own q-trad ingest process;
* its own PostgreSQL event store;
* its own capture API;
* its own backups and restore verification;
* its own universe and deployment descriptor; and
* no shared stream-version history with IG.

The same q-trad application image can support both collectors, but their canonical stores must remain separate. The current canonical stream IDs are based on instrument and basis rather than provider, so writing both providers to the same event store would intermingle stream versions and quote-derived bar history.

This also matches the repository rule that new processes should exist only for demonstrated lifecycle or failure isolation. IB Gateway’s authentication, restart and market-data-session lifecycle provide that justification.

### 1.2 Preserve canonical instrument IDs

Reuse q-trad IDs such as:

```text
fx:aud-usd
index:us-500
commodity:spot-gold
```

Only the provider listing changes:

```text
ProviderListingId(
    provider="ibkr",
    environment="paper",
    external_id="<conid>"
)
```

IBKR recommends identifying exact contracts with `conId` and exchange, rather than relying on a symbol search at runtime. ([IBKR Campus US][1])

### 1.3 Keep IBKR and IG research conclusions separate

Introduce three explicit `MarketDataSourceClass` values, not new R2 evidence classes:

```text
IBKR_HISTORICAL_RESEARCH
IBKR_NATIVE_CAPTURE
IG_NATIVE_CAPTURE
```

R2 `EvidenceClass` remains the orthogonal implementation-versus-confirmatory dimension. Foundations
and downstream R2 artefacts bind both independently. An IBKR historical result can answer whether
R2's forecasting hypothesis works on IBKR-provided CFD or reference-price bars. It cannot substantiate
IG quote behaviour, IG spreads, IG slippage or IG paper execution.

External data may support training or hypothesis rejection but must retain venue, product, timestamp
and correction provenance and must not masquerade as IG CFD history.

Do not initially combine IBKR and IG rows in one R1 bundle. Run:

```text
R2-IBKR-HISTORICAL
R2-IBKR-NATIVE
R2-IG-NATIVE
```

as distinct experiments.

---

## 2. Proposed IBKR universe

Begin with twenty canonical concepts similar to the current IG universe:

### FX — 8

```text
AUD/USD
EUR/USD
USD/JPY
GBP/USD
USD/CHF
USD/CAD
NZD/USD
EUR/JPY
```

### Equity indices — 9

```text
Australia 200
US 500
US 30
US Tech 100
FTSE 100
Germany 40
Japan 225
EU Stocks 50
Hong Kong HS50
```

### Commodities — 3

```text
Gold
Silver
US Crude
```

That retains the most useful current exposures while leaving China A50, Taiwan and VIX out of the first release until exact IBKR contracts and data entitlements are proven. The present IG universe already uses these canonical FX, index and commodity concepts.

IBKR Australia currently offers index, crude-oil, metals and Forex CFDs, including named contracts such as `IBUS500`, `IBUS30`, `IBUST100` and `IBUSOIL`. Exact availability still depends on the account, affiliate, permissions and the contract returned by `reqContractDetails`; the repository should not infer mappings from those marketing symbols. ([Interactive Brokers Australia Pty. Ltd.][3])

---

## 3. Stage IBKR.A — account, entitlement and capability probe

This must precede implementation of the production host.

Add a bounded command:

```text
qtrad instruments review
    --provider ibkr
    --environment paper
    --catalogue config/capture-ibkr-v1-candidates.toml
    --output evidence/ibkr-review.json
```

For every candidate concept, request and retain:

```text
conId
symbol
localSymbol
secType
exchange
currency
tradingClass
multiplier
minTick
marketRuleIds
validExchanges
longName
underConId
timeZoneId
tradingHours
liquidHours
```

Then probe, without ingesting:

* current market-data type: live, delayed or unavailable;
* bid, ask, bid size and ask size availability;
* MIDPOINT one-minute historical support;
* BID and ASK historical support;
* one-second historical support;
* earliest available timestamp;
* returned timezone behaviour;
* regular-hours and all-hours behaviour;
* account entitlement errors; and
* observed request completion time.

Most non-FX products require Level 1 market-data subscriptions, an IBKR Pro account and the relevant API permissions. Live subscriptions can be shared with a paper account, but that shared data cannot be consumed simultaneously by the live and paper usernames. ([IBKR Campus US][2])

**Exit gate:** at least twenty exact contracts are identified, or the release explicitly records a smaller accepted universe and quarantines every unmatched concept.

---

## 4. Stage IBKR.B — provider adapter

Add:

```text
src/qtrad/adapters/ibkr/
    __init__.py
    client.py
    contracts.py
    market_data.py
    historical.py
    pacing.py
    errors.py
```

Use the existing provider-neutral port:

```python
class MarketDataAdapter:
    connect()
    disconnect()
    discover_listings()
    review_listings()
    subscribe()
    records()
    backfill()
    health()
```

The port, `MarketDataRecord`, `ProviderListing` and `BackfillRequest` already provide most of the required boundary.

### Client implementation

Prefer the **official direct TWS Python API**, contained entirely inside the adapter. IBKR describes `ib_async` as the maintained successor to `ib_insync`, but does not endorse it and recommends the direct API whenever possible. ([IBKR Campus US][4])

Bridge its callback thread into a bounded asyncio queue, similarly to the existing IG callback adapter. No IBKR contract, ticker or callback type may escape `adapters/ibkr`.

### No order capability

The adapter must expose no order methods and import no q-trad order port.

The q-trad process should know only:

```text
gateway host
gateway port
client ID
environment
market-data type requirement
```

IBKR usernames, passwords and 2FA secrets remain in the Gateway login boundary, not q-trad settings or logs.

### Live quote normalisation

Use streaming Level 1/watchlist data for all twenty contracts:

```text
bid
ask
bid_size
ask_size
provider timestamp where available
local receive timestamp
market data type
request ID
contract conId
```

Coalesce side updates conservatively into `MarketQuote`:

* emit one-sided quotes when only one side changed;
* retain separate bid and ask timestamps;
* never invent a side;
* reject crossed markets;
* identify delayed/frozen data explicitly;
* retain update-only raw callbacks; and
* record missing or invalid values as bounded error records.

The existing ingestion service can then persist raw messages, canonical quotes, gaps and quote-derived one-minute bars without provider-specific changes.

Do not manufacture a quote every second by blindly carrying forward the last price. Store actual updates. A one-second research grid can later select the latest causally available state with explicit age and missingness, consistent with q-trad’s no-forward-filled-executable-price rule.

---

## 5. Stage IBKR.C — historical acquisition

Add a separate immutable planning contract rather than forcing IBKR into IG’s 10,000-point allowance machinery:

```text
qtrad-ibkr-historical-plan-v1
```

Each planned request records:

```text
plan ID
capture source ID
universe hash
contract-evidence ID
instrument ID
conId and exchange
bar size
whatToShow
useRTH
start and end
IBKR duration string
expected chunk count
pacing class
timezone policy
application/image identity
```

Commands:

```text
qtrad historical ibkr plan
qtrad historical ibkr register
qtrad historical ibkr execute
qtrad historical ibkr verify
```

### Acquisition order

#### C1 — R2 bootstrap

Request:

```text
barSize = "1 min"
whatToShow = "MIDPOINT"
useRTH = 0
formatDate = 2
duration = 16–26 weeks
```

for all accepted instruments.

IBKR supports MIDPOINT historical bars for CFDs and allows one-minute requests over long durations. Historical datasets must nevertheless be frozen exactly as returned because IBKR notes that historical data may be filtered, compressed, adjusted and may differ when requested again later. ([IBKR Campus US][1])

#### C2 — optional spread evidence

Request separate:

```text
whatToShow = "BID"
whatToShow = "ASK"
```

Start with the six confirmatory instruments, then expand to the full universe if the returned semantics and coverage are useful.

Historical BID and ASK bars do not prove that their extrema were contemporaneous. Spread features remain ineligible until the repository validates alignment against live top-of-book capture.

#### C3 — bounded one-second history

Start with:

* six confirmatory instruments;
* MIDPOINT only;
* one representative active day;
* exact 2,000-second chunks.

Expand only when those data materially answer a feature or microstructure question.

Do not make a week of one-second bars for all twenty instruments an R2 prerequisite.

### Persistence

Add:

```python
BarProvenance.IBKR_HISTORICAL
```

Each bar retains:

```text
conId
request ID
request-completion time
whatToShow
useRTH
bar-size contract
source timezone
provider environment
plan identity
```

Repeated downloads never overwrite earlier data. A changed bar becomes either:

* a later revision with explicit request lineage; or
* a new immutable historical dataset.

---

## 6. Stage IBKR.D — historical research foundation

This requires an explicit change to the current R1 observation path.

At present, the observation builder reads only quote-derived bars and verification requires:

```text
provenance = QUOTE_DERIVED
availability_basis = persisted_at
```

Historical IBKR bars must not be relabelled as quote-derived observations.

Add a parallel builder:

```text
qtrad research observations build-provider-history
```

with a contract such as:

```text
qtrad-provider-historical-observations-v1
```

It declares:

```text
provider = IBKR
provenance = IBKR_HISTORICAL
historical availability policy
correction/revision policy
request manifests
contract mapping identities
session/schedule evidence
bar bases included
```

The provider-history contract exposes `ProviderHistoricalObservation.available_at` under a versioned
`ProviderHistoricalAvailabilityPolicy`, for example:

```text
BAR_END_PLUS_DECLARED_PROVIDER_DELAY
```

A versioned foundation availability selector authenticates this field. The policy, delay, request time
and correction assumptions participate in semantic identity; assumed availability is never written
into native `received_at` or `persisted_at`. This source class may drive a chronological external-
provider R2 experiment with an independent implementation or confirmatory `EvidenceClass`, but its
report must identify historical availability and revision behaviour as assumptions rather than
measured live delivery.

---

## 7. Stage IBKR.E — capture host

Use a dedicated OCI ARM VM or an otherwise isolated host. IB Gateway now has an official Linux ARM64 build, so the existing OCI ARM approach is viable. ([Interactive Brokers][5])

Suggested stack:

```text
ib-gateway
qtrad-ingest
postgres
qtrad-read-only-api
backup
restore-verification
private operator GUI
```

IB Gateway must remain accessible only on a private container network. Operator GUI access should be through an authenticated private tunnel, not a public VNC port.

IBKR officially requires a GUI login rather than a fully headless Gateway session. Gateway can auto-restart daily, but credentials must periodically be entered again, normally after the weekly authentication cycle. ([Interactive Brokers][6])

Version 1 should therefore:

* support operator-assisted login;
* use Gateway auto-restart;
* alert when authentication is required;
* fail closed rather than spin indefinitely;
* avoid automated credential or 2FA injection;
* pin the Gateway installer and q-trad image identities; and
* make Gateway upgrades explicit deployments.

### Health state

Require:

```text
socket connected
next-valid-ID received
IB server time received
market-data farms healthy
historical-data farm available
expected contracts subscribed
marketDataType == LIVE
first bid/ask update received
last update sufficiently recent
queue depth bounded
callback drops == 0
pacing violations == 0
connection generation current
```

Handle IBKR connectivity codes explicitly:

```text
1100  connectivity lost
1101  restored, subscriptions lost
1102  restored, subscriptions maintained
1300  socket port changed
```

A `1101` event must cause exact subscription reconstruction; `1102` must not duplicate subscriptions. ([IBKR Campus US][7])

---

## 8. Stage IBKR.F — provider-neutral runtime composition

Generalise the currently IG-specific CLI:

```text
qtrad ingest --provider ig --environment demo
qtrad ingest --provider ibkr --environment paper
```

The current parser and `_ingest()` composition hard-code IG demo, its adapter, producer and broker environment.

Add a provider factory:

```text
src/qtrad/runtime/market_data_provider.py
```

responsible for:

```text
settings validation
adapter construction
provider/environment identity
producer name and version
health-policy selection
universe synchronisation strategy
```

`BrokerEnvironment` already contains `IBKR_PAPER` and `IBKR_LIVE`, so no new execution-mode concept is needed.

Settings additions:

```text
QTRAD_MARKET_DATA_PROVIDER=ibkr
QTRAD_IBKR_HOST=ib-gateway
QTRAD_IBKR_PORT=<paper API port>
QTRAD_IBKR_CLIENT_ID=<dedicated ID>
QTRAD_IBKR_REQUIRED_DATA_TYPE=LIVE
```

Do not add account credentials to q-trad settings.

---

## 9. Stage IBKR.G — qualification

### G1 — fixture qualification

Prove:

* contract ambiguity fails closed;
* conId mappings are deterministic;
* no order method is reachable;
* raw callbacks preserve connection-generation and local monotonic arrival order;
* replaying the same identity-bearing callback sequence is deterministic and idempotent, while
  payload-equal callbacks with different identities remain distinct;
* one-sided and crossed quotes behave correctly;
* delayed/frozen data cannot be marked healthy;
* timestamps normalise to UTC;
* planned request coverage is contiguous and non-overlapping after clipping;
* returned interval keys are ordered and unique, overlaps reconcile deterministically, active expected
  absences receive gap dispositions, inactive intervals remain absent and no interval is forward-filled;
* one-second planning honours the 2,000-second limit;
* pacing survives restarts;
* reconnect codes rebuild subscriptions correctly; and
* refetched historical bars cannot overwrite prior evidence.

### G2 — account-gated smoke test

For each accepted contract:

* resolve the exact mapping;
* receive current bid and ask;
* retrieve a bounded one-minute MIDPOINT interval;
* retrieve bounded BID and ASK intervals;
* verify market-data type;
* record request latency and row count; and
* compare the returned schedule with observed activity.

### G3 — live qualification

Run progressively:

```text
2 instruments
6 confirmatory instruments
full 20-instrument universe
```

Require at the full-universe gate:

* every channel subscribed and updated;
* no callback drops;
* no unexplained gaps;
* restart and reconnect recovery proven;
* backup and restore verification passed;
* exact source/universe/image identities retained;
* at least one complete multi-region trading cycle observed; and
* a complete weekly reauthentication boundary proves expiry detection, fail-closed unavailability,
  operator alerting, a new recovery generation, exact subscription restoration and gap accounting.

Until the weekly boundary is observed, the Gateway lifecycle remains unqualified and capture cannot
be declared complete.

### G4 — historical qualification

Produce:

* 16+ weeks of one-minute MIDPOINT bars;
* explicit active-session evidence;
* six or more qualifying targets;
* three market groups;
* per-instrument and per-block coverage;
* immutable request and result manifests; and
* successful deterministic export verification.

---

## 10. Stage IBKR.H — R2 experiment sequence

### H1 — fast historical decision

Run the full R2 baseline stack on:

```text
R2-IBKR-HISTORICAL
```

Use:

* 15-minute primary target;
* one-minute MIDPOINT inputs;
* local Ridge;
* pooled local Ridge;
* pooled cross-asset Ridge;
* six or more targets across FX, indices and commodities;
* three two-week OOF folds;
* four-week holdout; and
* the existing stability and concentration gates.

This is the fastest answer to:

> Is there enough simple cross-market forecasting information to justify continuing?

A credible negative result should stop unnecessary one-second expansion.

### H2 — live-native validation

As live quote-derived history accumulates, run:

```text
R2-IBKR-NATIVE
```

This validates whether the historical-bar result transfers to actual observed IBKR top-of-book data.

### H3 — cross-provider transfer

Only after H1 and H2:

```text
train: IBKR historical/native
holdout: untouched IG-native future history
```

This is a separate augmentation experiment. It must not silently replace the still-pending IG-native experiment.

---

## 11. Recommended pull-request sequence

1. **ADR and contracts**
   Independent source decision, source/evidence dimensions, candidate universe and Gateway operational boundary.

2. **IBKR capability probe**
   Direct API client, contract review, entitlement report and exact mapping evidence.

3. **Historical acquisition**
   Immutable planner/executor, pacing, `IBKR_HISTORICAL` provenance and one-minute bootstrap.

4. **Live adapter**
   Top-of-book normalisation, health, reconnect behaviour and provider-neutral runtime composition.

5. **IBKR host**
   ARM Gateway image, private GUI boundary, Compose stack, deployment descriptor, backups and runbook.

6. **Historical R1-equivalent path**
   Provider-historical observation contract, availability policy, exports and verification.

7. **R2 integration**
   IBKR historical readiness, experiment configuration and separate report identity.

8. **Native qualification**
   Full-universe live observation, loss/lag evidence and first IBKR-native bundle.

---

## Completion definition

The addition is **software-complete** when the IBKR adapter, historical planner, live ingestion, independent verification and host lifecycle pass fixtures without needing a qualifying dataset.

The **IBKR historical bootstrap is complete** when the repository has an immutable, verified 16+ week one-minute MIDPOINT bundle that can run the R2 historical experiment.

The **IBKR capture host is complete** only when the reviewed universe streams into an independent
canonical store with truthful health, reconnect recovery, backups and an operator-authenticated
Gateway lifecycle proven across a complete weekly reauthentication boundary.

The **research objective is complete** when R2 produces a positive, negative or inconclusive IBKR historical result, followed where justified by an IBKR live-native comparison—without making an unsupported IG execution claim.

Recommended normative file:

```text
docs/IBKR_CAPTURE_IMPLEMENTATION_PLAN.md
```

and accompanying architectural decision:

```text
docs/adr/00xx-independent-ibkr-capture-source.md
```

[1]: https://ibkrcampus.com/campus/ibkr-api-page/twsapi-doc/ "https://ibkrcampus.com/campus/ibkr-api-page/twsapi-doc/"
[2]: https://ibkrcampus.com/campus/ibkr-api-page/market-data-subscriptions/ "https://ibkrcampus.com/campus/ibkr-api-page/market-data-subscriptions/"
[3]: https://www.interactivebrokers.com.au/en/trading/products-cfds.php "https://www.interactivebrokers.com.au/en/trading/products-cfds.php"
[4]: https://ibkrcampus.com/docs/tws-api/doc/third-party-api-platforms/non-standard-tws-api-languages-and-packages/ib-insync-and-ib-async "https://ibkrcampus.com/docs/tws-api/doc/third-party-api-platforms/non-standard-tws-api-languages-and-packages/ib-insync-and-ib-async"
[5]: https://www.interactivebrokers.com/en/trading/ibgateway-latest.php "https://www.interactivebrokers.com/en/trading/ibgateway-latest.php"
[6]: https://www.interactivebrokers.com/docs/tws-api/doc/download-tws-or-ib-gateway/download-tws-or-ib-gateway "https://www.interactivebrokers.com/docs/tws-api/doc/download-tws-or-ib-gateway/download-tws-or-ib-gateway"
[7]: https://ibkrcampus.com/campus/ibkr-api-page/twsapi-doc/?utm_source=chatgpt.com "TWS API Documentation | IBKR API | IBKR Campus"
