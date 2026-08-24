# R3 product-economics readiness inventory

**Job:** R3-E0  
**Evidence cut-off:** 2026-08-24  
**Authority:** preparatory read-only inventory; R3 remains not started  
**Reporting currency:** AUD

## Result

No current IG or IBKR row is `ECONOMICS_READY`.

The tracked current IG configuration identifies 23 canonical instruments, currencies and selected
epics. The ignored local review inputs used during this inventory supplied a mechanically reconciled
planning summary of minimum quantity, contract/lot size and pip/value fields for those rows; those
copied fields are not independently verifiable at this Git head and are not R3 product-economics
authority. The tracked R0 report independently supports the broad conclusion that all 23 reviewed
listing records had `price_increment = null` and lacked an authoritative session calendar. No row
has a quantity increment or source-aligned commission, financing or non-AUD conversion contract.
Native bid/ask evidence exists for all 23, but the bounded `capture-v4` observation is a deployment
smoke rather than representative-session cost evidence. Twenty-two are potentially tradable;
`index:volatility` remains context-only and `PAPER_INELIGIBLE`.

At inventory time an ignored local IBKR historical plan identified 20 contract fingerprints and
currencies. The copied fingerprints are planning summaries, not independently reproducible retained
authority. They had `multiplier = null` and did not contain minimum quantities, quantity increments
or minimum ticks. B5 construction code requires an authenticated non-null minimum tick and constructs
a listing with configured minimum deal size `1`, but the exact numeric B5 ticks and retained B5
listing artefact are not present in the evidence available at this reviewed head. The tracked B5
authority proves fresh LIVE bid/ask capture for an exact-20 native universe; it does not expose the
row binding or supply per-instrument spread distributions, market-data timing, commission, financing,
conversion or impact economics.

The first subset suitable for R3 **implementation evidence** is the single native IG
`index:australia-200` listing. Its tracked proof binds the selected epic, embedded listing metadata,
native bid/ask paper fills, AUD identity conversion and explicit configured tick/session/latency/
slippage assumptions. It is not source-aligned economic evidence and cannot support a post-cost
conclusion without the additional facts listed below.

## Scope and evidence classes

This inventory used only repository files and existing local artefacts. It did not contact
IG, IBKR, either collector, a collector database, or a broker endpoint. Historical IBKR MIDPOINT and
SCHEDULE evidence remains provenance-distinct from native top-of-book evidence and is never used as
IG spread or fill evidence.

Evidence tags used below are:

- **PM-R — retained product metadata:** an exact provider review/listing or authenticated contract
  field whose source bytes or governed retained authority are independently available to a reviewer.
  Admissibility and freshness remain field-specific.
- **PM-S — product-metadata planning summary:** a field copied from an identified but non-retained
  local input. It is useful for planning and reconciliation but cannot satisfy an R3
  product-economics contract.
- **NM — observed native market evidence:** retained source-native quotes or callbacks.
- **CA — configured assumption:** a research or runtime value, not a provider economic fact.
- **UA — unsupported assumption:** no qualifying evidence; it must remain explicit and cannot be
  treated as zero or inferred from another source.

The following evidence sources are used by short code in the instrument tables:

| Code | Exact source and identity | Use |
|---|---|---|
| IG-V2 | ignored local `tmp/capture-v2-review-v2.json`, review `aff32264bfc9dade67bf1df06cf689d55b1b3f5b60550f28332da3826dae0dc9`; `tmp/capture-v2-selections-v2.toml` | 19 copied IG selections and listing-economics rows; **PM-S** only |
| IG-HS | ignored local `tmp/hang-seng-review.json`, review `f7aa58a401e3ac2bf8b5beb74ff00d2f0122871e0790894d2207c252eb04284e`; `tmp/hang-seng-selection.toml` | copied HS50 selection and listing-economics row; **PM-S** only |
| IG-AP | ignored local `tmp/capture-v4-corrected-review.json`, review `ed17cf86d566f742c0be3e0f003fa24ffc0bbb7a31642c71a386ec3e3782cf13`; `tmp/capture-v4-apac-selection.toml` | copied China A50, Taiwan and VIX rows; **PM-S** only |
| IG-CFG | tracked `config/capture-v4.toml`; `config/capture-v4-deployment.toml`, universe hash `eca6649cfd2477204d9a6d5970596657ad0d94b0a25916f8b26b9c5f0c606078` | current 23 canonical IDs, currencies and exact preferred epics |
| IG-R0 | tracked `docs/R0_DATA_READINESS.md`, measured 2026-07-22 15:35 UTC against live OCI `capture-v4`; abbreviated hash in that report resolves to IG-CFG `eca6649cfd2477204d9a6d5970596657ad0d94b0a25916f8b26b9c5f0c606078` | retained authority for broad native field availability, null-tick and bounded bid/ask/bar findings |
| IG-PROOF | tracked `config/research-proof-v1.toml`, source manifest `5289530e6b5d946c626593f74eda8d14774d1454774fff666c9c313a9946565d` | **PM-R** embedded Australia 200 metadata and **CA** paper assumptions, for implementation evidence only |
| IB-P | ignored local `tmp/ibkr-run-20260806T035300Z/plan.json`, plan `555b2acb3f730f908f362fbfdae7fdc90dc46f5320272ed8ea4899a1787d6bac`, selection `788bd8304781bc8aff0614dc6622f84a934e85e9bc9ffa002bfc66e7f46f1f88` | 20 copied historical contract fingerprints; **PM-S** only, not native cost evidence |
| IB-C | ignored local `tmp/ibkr-run-20260806T035300Z/evidence/stage5-canary-20260806T035300Z.json`, evidence `68c9a42bddfd5a660ce7614aec1bb1b9b1977d27e4491c3fefb7fb9aecde70a1` | copied planning summary of 12 historical MIDPOINT/SCHEDULE cases; not retained authority |
| IB-B5 | tracked `docs/STATUS.md` B5 authority: canonical artefact `efb6f465221659cb0b1c65d6e0df12ac01d20a9227d07e606e8febf78152ed24`, qualification file `87c4860dbc97b7e73e1849ed58ba528b1b630cdd13207393fec32ebfb1eb9218`, verifier `dbca7ba916fa2c1a97fecc2dd1ef71f73621ddf87cbe6313ca7f416b41949a67` | retained authority-level exact-20 LIVE bid/ask qualification; row artefact not available here |
| IB-CODE | tracked `src/qtrad/runtime/ibkr_b5.py::_expected_listing` | configured listing-ID/minimum-size policy and requirement for authenticated non-null minimum tick |

The ignored `tmp/` inputs are absent from the reviewed Git head. Recording their hashes prevents
silent local substitution but does not make the copied values or reconciliations independently
verifiable. Every value sourced only from IG-V2, IG-HS, IG-AP, IB-P or IB-C is therefore **PM-S**:
a non-authoritative planning summary that must be reacquired from an accepted retained authority
before it can populate or satisfy an R3 product-economics contract.

## Source-wide cost evidence

These findings apply to every row for the named source unless a row-specific exception follows.

| Field | IG | IBKR |
|---|---|---|
| Product metadata | IG-CFG retains selected IDs/currencies; IG-R0 retains the broad null-tick/missing-session finding. Per-row minimum quantity, contract/lot and pip/value fields are **PM-S**, except the Australia 200 fields embedded in IG-PROOF (**PM-R** for implementation-bound use) | Per-row IB-P fingerprints are **PM-S**. Native B5 code requires a non-null authenticated tick, configures minimum deal size `1`, and leaves economics empty; the retained row artefact is unavailable |
| Observed spread | **NM available:** IG-R0 has 23 healthy current quotes and BID/ASK/MID bars. **Insufficient:** bounded smoke interval, not representative by session, size or decision time | **NM available at authority level:** IB-B5 has fresh post-reconnect LIVE bid/ask for 20/20. Exact per-row observations/distributions are unavailable here. IB-C MIDPOINT is not spread evidence |
| Latency | Native provider/source/receive timing and paper execution policy are separate. No representative native market-data timing study was retained. IG-PROOF supplies **CA** delay/slippage scenarios, not observed fills. Paper readiness requires a reviewed, versioned delay policy and first qualifying healthy executable-side quote after that delay; real decision-to-broker-fill latency is **UA** and out of scope | Callback counts, zero-drop qualification and historical request completion do not establish market-data or broker execution latency. A reviewed, versioned paper delay policy may support paper readiness without pretending it is observed fill latency; real broker-fill latency remains **UA** and out of scope |
| Commission | **UA:** no schedule | **UA:** no account/product schedule |
| Financing | **UA:** no schedule, cut-off, day-count or multi-day rule | **UA:** no schedule, cut-off, day-count or multi-day rule |
| Sessions | **UA:** listing metadata has no authoritative calendar. IG-PROOF configures weekdays 10:00–16:00 Australia/Sydney with an empty holiday list for Australia 200 only | Copied IB-C rows reported historical SCHEDULE callbacks for EUR/USD, Australia 200 and spot gold over 1D/1W/2W/4W. They are **PM-S**, not a current all-contract calendar |
| Quote to AUD | AUD identity is exact. Same-source canonical FX pairs define mathematical candidate paths for USD, EUR, GBP, JPY, CAD and CHF, but no causal rate-selection/staleness contract exists. No selected HKD conversion pair exists | Same position as IG within the IBKR 20-contract universe. No cross-provider substitution is permitted |
| Impact | `UNSUPPORTED_BLOCKING`: no supported model or evidenced size-validity cap; top-of-book size meaning is not qualified trade volume | `UNSUPPORTED_BLOCKING`: no supported model or evidenced size-validity cap |
| Existing paper cost | IG-PROOF measures spread plus configured adverse tick slippage through executable bid/ask sides. Production paper code has no commission, financing or impact component | No retained source-aligned paper-economics configuration was found |

The existing paper ledger's `execution_cost` is the difference between gross-mid and bid/ask-plus-
slippage P&L. It must not be described as commission, financing or market impact.

### Impact disposition

An R3 cost state must use exactly one explicit impact disposition:

- `SUPPORTED_MODEL`: a versioned, source/product-specific quantity-dependent model with evidence,
  units, calibration interval and stated validity range.
- `CAPPED_NO_IMPACT_RANGE`: a reviewed no-impact approximation that is valid only up to an immutable
  quantity ceiling, with source/product rationale and qualifying size evidence. The evaluator must
  block or mark ineligible every proposed change above the ceiling; this is not a measured-impact
  claim.
- `UNSUPPORTED_BLOCKING`: neither an accepted model nor an evidenced cap exists. Impact cannot be
  silently set to zero, and a source-aligned economic conclusion is blocked.

All 43 planning rows are currently `UNSUPPORTED_BLOCKING`. The Australia 200 proof may still exercise
implementation mechanics under **CA**, but it cannot promote that assumption into source-aligned
economics.

## AUD conversion paths

These are mathematical paths through already selected **same-source** canonical FX concepts, not
configured or qualified conversion evidence.

| Listing currency | Candidate path | Current disposition |
|---|---|---|
| AUD | identity rate `1` | available when listing and reporting currency are both AUD |
| USD | `1 / AUDUSD` | `MISSING_CONVERSION` until causal side, timestamp, health and staleness policy is versioned |
| EUR | `EURUSD / AUDUSD` | `MISSING_CONVERSION` |
| GBP | `GBPUSD / AUDUSD` | `MISSING_CONVERSION` |
| JPY | `1 / USDJPY / AUDUSD` | `MISSING_CONVERSION` |
| CAD | `1 / USDCAD / AUDUSD` | `MISSING_CONVERSION` |
| CHF | `1 / USDCHF / AUDUSD` | `MISSING_CONVERSION` |
| HKD | no selected same-source path | `MISSING_CONVERSION`; acquire and review an exact native path or keep ineligible |

## Readiness profiles

Instrument rows reference one of these profiles. Each profile is the row's current readiness state
and exact missing-field set; no abbreviated profile hides a favourable exception.

For every profile, paper latency readiness means: timestamp semantics sufficient to preserve
provider/source, receive and decision ordering and quote health; a reviewed/versioned configured delay
plus frozen sensitivity or calibration basis; and selection of the first qualifying healthy
executable-side observation after that delay. Observed native market-data timing is recorded where
available but is not broker-fill evidence. Actual decision-to-broker-fill latency is
`UNSUPPORTED`, outside the no-order architecture and not required for paper readiness.

Every profile also has impact disposition `UNSUPPORTED_BLOCKING`: no accepted
`SUPPORTED_MODEL` or evidenced `CAPPED_NO_IMPACT_RANGE` exists.

### IG-AUD

States: `MISSING_PRICE_INCREMENT`, `MISSING_QUANTITY_RULE`, `MISSING_COMMISSION`,
`MISSING_FINANCING`, `SPREAD_EVIDENCE_INSUFFICIENT`,
`SESSION_EVIDENCE_INSUFFICIENT`.

Exact missing facts: authenticated numeric price increment; provider-authoritative quantity step and
unit rule; account/product commission schedule; financing basis/rates/cut-offs/day count; authoritative
session/time-zone/holiday evidence; representative native spread evidence for the intended decision
windows and quantity; validated timestamp semantics; and a reviewed/versioned paper latency policy.

### IG-FX (non-AUD listing currency)

All `IG-AUD` states and missing facts, plus `MISSING_CONVERSION`: an immutable causal same-source
AUD conversion policy, executable side, timestamp, staleness/health rule and retained rate evidence.

### IG-VIX

State: `PAPER_INELIGIBLE`, plus the `IG-AUD` missing-economics states. The current authority makes
VIX context-only. Economics completion alone does not promote its paper role.

### IB-AUD-CANARY

States: `MISSING_PRICE_INCREMENT`, `MISSING_QUANTITY_RULE`, `MISSING_COMMISSION`,
`MISSING_FINANCING`, `SPREAD_EVIDENCE_INSUFFICIENT`,
`SESSION_EVIDENCE_INSUFFICIENT`.

Exact missing facts: the exact authenticated B5 numeric tick; provider-authoritative minimum quantity,
quantity increment and unit semantics (the configured `1` is not enough); multiplier/value per price
unit; commission; financing; a current authoritative session calendar; locally retained representative
native spreads; validated timestamp semantics; and a reviewed/versioned paper latency policy. The
copied historical schedule canary is planning context only.

### IB-FX-CANARY (non-AUD listing currency)

All `IB-AUD-CANARY` states and missing facts, plus `MISSING_CONVERSION` and its exact causal
same-source evidence contract.

### IB-FX (non-AUD listing currency)

All `IB-FX-CANARY` states and missing facts. Unlike the canary planning rows, no per-instrument
SCHEDULE sample was found in the local input.

## IG selected-listing planning inventory

All rows describe the reviewed `capture-v4` selection. Exact selected listing IDs and currencies
are independently re-checkable in IG-CFG, and IG-R0 independently records the broad null-tick finding.
The copied per-row minimum quantity, contract/lot and pip/value fields are **PM-S**, not R3 authority,
except that Australia 200 metadata is also embedded in tracked IG-PROOF. Quantity increment is
unavailable for every row and tick is explicitly `null`.

| Canonical instrument | Tracked selected listing / copied product summary / source | Currency | Minimum quantity | Contract / lot | Pip meaning; value | AUD path | Profile |
|---|---|---:|---:|---:|---|---|---|
| `commodity:spot-gold` | `ig:demo:CS.D.CFDGOLD.CFDGC.IP`; ROLLING_CFD; mv `00a52575e0793843`; IG-V2 | USD | 0.1 | 100 / 100.0 | 1 $/Troy Ounce; 100.00 USD | USD→AUD | IG-FX |
| `commodity:spot-silver` | `ig:demo:CS.D.CFDSILVER.CFDSI.IP`; ROLLING_CFD; mv `f309ca61afdd83d2`; IG-V2 | USD | 0.5 | 50 / 50.0 | 1 Cents/Troy Ounce; 50.00 USD | USD→AUD | IG-FX |
| `commodity:us-crude` | `ig:demo:CC.D.CL.UNC.IP`; ROLLING_CFD; mv `3cdedcf9115be3c3`; IG-V2 | USD | 0.0001 | 10 / 10.0 | 1 cent per barrel; 10.00 USD | USD→AUD | IG-FX |
| `fx:aud-usd` | `ig:demo:CS.D.AUDUSD.CFD.IP`; SPOT_FX; mv `1b7c60f3588d2ca1`; IG-V2 | USD | 1.0 | 100000 / 10.0 | 0.0001 USD/AUD; 10.00 USD | USD→AUD | IG-FX |
| `fx:eur-jpy` | `ig:demo:CS.D.EURJPY.CFD.IP`; SPOT_FX; mv `796a42a50ad7512d`; IG-V2 | JPY | 0.1 | 100000 / 1000.0 | 0.01 JPY/EUR; 1000.00 JPY | JPY→USD→AUD | IG-FX |
| `fx:eur-usd` | `ig:demo:CS.D.EURUSD.CFD.IP`; SPOT_FX; mv `358b2873dcb1ebcb`; IG-V2 | USD | 1.0 | 100000 / 10.0 | 0.0001 USD/EUR; 10.00 USD | USD→AUD | IG-FX |
| `fx:gbp-usd` | `ig:demo:CS.D.GBPUSD.CFD.IP`; SPOT_FX; mv `f76768e678f25854`; IG-V2 | USD | 0.04 | 100000 / 10.0 | 0.0001 USD/GBP; 10.00 USD | USD→AUD | IG-FX |
| `fx:nzd-usd` | `ig:demo:CS.D.NZDUSD.CFD.IP`; SPOT_FX; mv `bc308c6bf00d5c8a`; IG-V2 | USD | 0.1 | 100000 / 10.0 | 0.0001 USD/NZD; 10.00 USD | USD→AUD | IG-FX |
| `fx:usd-cad` | `ig:demo:CS.D.USDCAD.CFD.IP`; SPOT_FX; mv `f07363fa04e3d91f`; IG-V2 | CAD | 0.1 | 100000 / 10.0 | 0.0001 CAD/USD; 10.00 CAD | CAD→USD→AUD | IG-FX |
| `fx:usd-chf` | `ig:demo:CS.D.USDCHF.CFD.IP`; SPOT_FX; mv `157f7b135b4e9ca5`; IG-V2 | CHF | 0.1 | 100000 / 10.0 | 0.0001 CHF/USD; 10.00 CHF | CHF→USD→AUD | IG-FX |
| `fx:usd-jpy` | `ig:demo:CS.D.USDJPY.CFD.IP`; SPOT_FX; mv `5a76511ca2ee9a85`; IG-V2 | JPY | 0.2 | 100000 / 1000.0 | 0.01 JPY/USD; 1000.00 JPY | JPY→USD→AUD | IG-FX |
| `index:australia-200` | `ig:demo:IX.D.ASX.IFD.IP`; ROLLING_CFD; mv `0454fc612d2ed4b3`; IG-V2 | AUD | 1.0 | 25 / 25.0 | 1 Index Point; 25.00 AUD | identity | IG-AUD |
| `index:china-a50` | `ig:demo:IX.D.XINHUA.IFM.IP`; ROLLING_CFD; mv `2d993524d98fbb23`; IG-AP | USD | 1.0 | 0.2 / 0.2 | 1 Index Point; 0.20 USD | USD→AUD | IG-FX |
| `index:eu-stocks-50` | `ig:demo:IX.D.STXE.IFD.IP`; ROLLING_CFD; mv `476491713df1db32`; IG-V2 | EUR | 0.5 | 10 / 10.0 | 1 Index Point; 10.00 EUR | EUR→USD→AUD | IG-FX |
| `index:ftse-100` | `ig:demo:IX.D.FTSE.CFD.IP`; ROLLING_CFD; mv `f21f96cead72d372`; IG-V2 | GBP | 0.5 | 10 / 10.0 | 1 Index Point; 10.00 GBP | GBP→USD→AUD | IG-FX |
| `index:germany-40` | `ig:demo:IX.D.DAX.IFD.IP`; ROLLING_CFD; mv `652ab3220db1c7a8`; IG-V2 | EUR | 0.1 | 25 / 25.0 | 1 Index Point; 25.00 EUR | EUR→USD→AUD | IG-FX |
| `index:hong-kong-hs50` | `ig:demo:IX.D.HANGSENG.IFM.IP`; ROLLING_CFD; mv `e66e70184df2e9eb`; IG-HS | HKD | 0.5 | 10 / 10.0 | 1 Index Point; 10.00 HKD | unavailable | IG-FX |
| `index:japan-225` | `ig:demo:IX.D.NIKKEI.IFA.IP`; ROLLING_CFD; mv `488f0411b54837e8`; IG-V2 | AUD | 0.25 | 1 / 1.0 | 1 Index Point; 1.00 AUD | identity | IG-AUD |
| `index:taiwan` | `ig:demo:IX.D.TAIWAN.IFM.IP`; ROLLING_CFD; mv `bfc3e74f785e224a`; IG-AP | USD | 2.0 | 10 / 10.0 | 1 Index Point; 10.00 USD | USD→AUD | IG-FX |
| `index:us-500` | `ig:demo:IX.D.SPTRD.IFD.IP`; ROLLING_CFD; mv `ded476b700c6eab4`; IG-V2 | USD | 1.0 | 250 / 250.0 | 1 Index Point; 250.00 USD | USD→AUD | IG-FX |
| `index:us-tech-100` | `ig:demo:IX.D.NASDAQ.IFD.IP`; ROLLING_CFD; mv `3779f2d57c40c389`; IG-V2 | USD | 0.2 | 100 / 100.0 | 1 Index Point; 100.00 USD | USD→AUD | IG-FX |
| `index:volatility` | `ig:demo:CC.D.VIX.UMA.IP`; ROLLING_CFD; mv `fda98efb49069dcc`; IG-AP | AUD | 1.0 | 1 / 1.0 | 1 Index Point; 1.00 AUD | identity | IG-VIX |
| `index:wall-street` | `ig:demo:IX.D.DOW.IFD.IP`; ROLLING_CFD; mv `4b3cfa21f9980188`; IG-V2 | USD | 0.2 | 10 / 10.0 | 1 Index Point; 10.00 USD | USD→AUD | IG-FX |

### Australia 200 configured exception

IG-PROOF configures `quantity=1`, `price_increment=1`,
`value_per_price_unit=25 AUD`, AUD-to-AUD rate `1`, weekdays 10:00–16:00
Australia/Sydney with no holidays, and three 1/2/5-second latency plus 1/2/3-increment adverse-
slippage scenarios. These are **CA**. The listing's authenticated `price_increment` remains null,
the empty holiday list is not an authoritative session calendar, and no commission or financing
component exists. The row therefore remains `IG-AUD`, not `ECONOMICS_READY`.

## IBKR historical-contract planning inventory

The fingerprint columns below were copied from IB-P during the local inventory. Because IB-P is absent
from the reviewed Git head, they are **PM-S**: planning summaries that cannot authenticate a current
or historical product-economics contract. They must not be silently asserted to be the current B5
contract binding. At inventory time every copied fingerprint had `contract_month`,
`primary_exchange` and `multiplier` null.

Tracked IB-CODE configures canonical native listing IDs as
`ibkr:IBKR_PAPER:<canonical-name>`, minimum deal size `1`, an empty economics mapping, and requires
an authenticated non-null minimum tick. Accordingly every planning row below has:

- minimum quantity: `1` **CA**, with provider-authoritative minimum/step/unit unavailable;
- quantity increment: unavailable;
- multiplier/value per point: unavailable;
- exact numeric minimum tick: unavailable in retained evidence at this head.

| Canonical instrument / configured native listing | Copied local historical fingerprint (**PM-S**): conId; secType/exchange; symbol, local symbol, trading class; underlying conId | Currency | AUD path | Profile |
|---|---|---:|---|---|
| `commodity:spot-gold` / `ibkr:IBKR_PAPER:spot-gold` | 457068913; CFD/SMART; XAUUSD, XAUUSD, XAUUSD; 58430358 | USD | USD→AUD | IB-FX-CANARY |
| `commodity:spot-silver` / `ibkr:IBKR_PAPER:spot-silver` | 457068916; CFD/SMART; XAGUSD, XAGUSD, XAGUSD; 58430361 | USD | USD→AUD | IB-FX |
| `commodity:us-crude` / `ibkr:IBKR_PAPER:us-crude` | 738357708; CFD/SMART; IBUSOIL, IBUSOIL, IBUSOIL; 737877813 | USD | USD→AUD | IB-FX |
| `fx:aud-usd` / `ibkr:IBKR_PAPER:aud-usd` | 14433401; CASH/IDEALPRO; AUD, AUD.USD, AUD.USD; null | USD | USD→AUD | IB-FX |
| `fx:eur-jpy` / `ibkr:IBKR_PAPER:eur-jpy` | 14321016; CASH/IDEALPRO; EUR, EUR.JPY, EUR.JPY; null | JPY | JPY→USD→AUD | IB-FX |
| `fx:eur-usd` / `ibkr:IBKR_PAPER:eur-usd` | 12087792; CASH/IDEALPRO; EUR, EUR.USD, EUR.USD; null | USD | USD→AUD | IB-FX-CANARY |
| `fx:gbp-usd` / `ibkr:IBKR_PAPER:gbp-usd` | 12087797; CASH/IDEALPRO; GBP, GBP.USD, GBP.USD; null | USD | USD→AUD | IB-FX |
| `fx:nzd-usd` / `ibkr:IBKR_PAPER:nzd-usd` | 39453441; CASH/IDEALPRO; NZD, NZD.USD, NZD.USD; null | USD | USD→AUD | IB-FX |
| `fx:usd-cad` / `ibkr:IBKR_PAPER:usd-cad` | 15016062; CASH/IDEALPRO; USD, USD.CAD, USD.CAD; null | CAD | CAD→USD→AUD | IB-FX |
| `fx:usd-chf` / `ibkr:IBKR_PAPER:usd-chf` | 12087820; CASH/IDEALPRO; USD, USD.CHF, USD.CHF; null | CHF | CHF→USD→AUD | IB-FX |
| `fx:usd-jpy` / `ibkr:IBKR_PAPER:usd-jpy` | 15016059; CASH/IDEALPRO; USD, USD.JPY, USD.JPY; null | JPY | JPY→USD→AUD | IB-FX |
| `index:australia-200` / `ibkr:IBKR_PAPER:australia-200` | 111987484; CFD/SMART; IBAU200, IBAU200, IBAU200; 111987392 | AUD | identity | IB-AUD-CANARY |
| `index:eu-stocks-50` / `ibkr:IBKR_PAPER:eu-stocks-50` | 111987407; CFD/SMART; IBEU50, IBEU50, IBEU50; 111987342 | EUR | EUR→USD→AUD | IB-FX |
| `index:ftse-100` / `ibkr:IBKR_PAPER:ftse-100` | 111987412; CFD/SMART; IBGB100, IBGB100, IBGB100; 111987344 | GBP | GBP→USD→AUD | IB-FX |
| `index:germany-40` / `ibkr:IBKR_PAPER:germany-40` | 111987422; CFD/SMART; IBDE40, IBDE40, IBDE40; 111987349 | EUR | EUR→USD→AUD | IB-FX |
| `index:hong-kong-hs50` / `ibkr:IBKR_PAPER:hong-kong-hs50` | 111987478; CFD/SMART; IBHK50, IBHK50, IBHK50; 111987384 | HKD | unavailable | IB-FX |
| `index:japan-225` / `ibkr:IBKR_PAPER:japan-225` | 111987469; CFD/SMART; IBJP225, IBJP225, IBJP225; 111987372 | JPY | JPY→USD→AUD | IB-FX |
| `index:us-500` / `ibkr:IBKR_PAPER:us-500` | 111767871; CFD/SMART; IBUS500, IBUS500, IBUS500; 111743379 | USD | USD→AUD | IB-FX |
| `index:us-tech-100` / `ibkr:IBKR_PAPER:us-tech-100` | 111767885; CFD/SMART; IBUST100, IBUST100, IBUST100; 111754060 | USD | USD→AUD | IB-FX |
| `index:wall-street` / `ibkr:IBKR_PAPER:wall-street` | 111767879; CFD/SMART; IBUS30, IBUS30, IBUS30; 111746713 | USD | USD→AUD | IB-FX |

### IBKR schedule and spread evidence boundaries

At inventory time IB-C reconciled to twelve successful cases: EUR/USD, Australia 200 and spot gold
at each of 1D, 1W, 2W and 4W. Every copied case had one MIDPOINT request and one SCHEDULE request; the
4W schedule counts were respectively 20, 40 and 20 sessions. Because IB-C is absent from the reviewed
head, this is non-authoritative planning context. Even with retained source bytes it would prove only
bounded historical request/schedule availability, not contemporaneous spread, current trading hours,
native market-data timing, broker execution latency or product costs.

IB-B5 records 24,056 callbacks with zero failed, dropped or reconciliation-loss callbacks and fresh
post-reconnect LIVE bid/ask evidence for 20/20. Because the exact row artefact was not locally
available, this inventory records native spread **availability** but cannot compute or authenticate a
per-instrument spread statistic. All IBKR rows therefore remain
`SPREAD_EVIDENCE_INSUFFICIENT`.

## Explicit exclusions

- `index:korea-200`: no eligible IG demo listing and no selected current source listing;
  `PAPER_INELIGIBLE`.
- `crypto:bitcoin-usd`: all reviewed IG listings were unavailable and it remains quarantined;
  `PAPER_INELIGIBLE`.
- `index:volatility`: included above because it is captured, but its current role is context-only.
- IBKR historical MIDPOINT extrema are not contemporaneous BID/ASK spread evidence.
- Another provider's name, tick, multiplier, session, commission, financing or conversion fact is
  never used to fill a missing source field.

## First implementation-evidence subset and remaining conclusion gate

The first bounded subset is:

| Source | Instrument | Why it can exercise R3 code now | Evidential ceiling |
|---|---|---|---|
| IG demo | `index:australia-200` / `ig:demo:IX.D.ASX.IFD.IP` | tracked selection; embedded proof metadata; AUD identity conversion; native bid/ask availability; existing causal paper-fill and ledger path; explicit configured tick/session/latency/slippage scenarios | implementation evidence only; not source-aligned cost evidence or an effectiveness claim |

Use that row only with labels preserving **PM-R**, **NM** and **CA**. In particular,
`price_increment=1`, the ASX cash profile and the latency/slippage grid remain configured proof
inputs. They must not be copied into an R3 product-economics contract as broker facts.

A source-aligned economic conclusion for even this one row additionally requires:

1. Exact provider-authenticated minimum tick/price increment for the selected epic.
2. Exact minimum quantity, quantity increment and unit/rounding rule; minimum quantity alone is not
   a step rule.
3. The account- and product-specific commission schedule, including an explicit supported zero if
   the schedule truly charges zero.
4. The overnight financing schedule: basis/rate source, cut-off, day count, weekend/holiday and
   multi-day treatment. A strictly enforced intraday close-out may avoid realised financing, but it
   does not justify inventing a zero schedule.
5. An authoritative session calendar for the rolling CFD, including time zone, holidays, partial
   days and any out-of-hours distinction.
6. Representative source-native bid/ask spread evidence across the intended decision windows and
   tested quantity, with gaps and unhealthy intervals excluded explicitly.
7. Timestamp semantics sufficient to preserve provider/source, receive and decision ordering and
   quote health, plus a reviewed/versioned paper latency policy: configured delay, frozen sensitivity
   or calibration basis, and the first qualifying healthy executable-side observation after that
   delay. Observed native market-data timing may inform the policy but is not an observed broker-fill
   distribution. Actual decision-to-broker-fill latency remains unsupported/out of scope and is not
   required for paper readiness.
8. One explicit impact disposition: a `SUPPORTED_MODEL` with validity range, or an evidenced
   `CAPPED_NO_IMPACT_RANGE` with an enforced quantity ceiling. Otherwise
   `UNSUPPORTED_BLOCKING` remains and no source-aligned economic conclusion may be made.
9. Immutable binding of those facts to the exact listing metadata version, source, environment and
   evidence interval.

AUD conversion is identity for this row, so no FX rate is required. Every non-AUD extension also
requires the causal same-source conversion evidence contract described above. HS50 additionally
requires a reviewed native HKD-to-AUD path; it cannot borrow one from the other provider.

## Reconciliation

The following reconciliation was performed in memory at inventory time against the identified ignored
local inputs. Because those inputs are absent from the reviewed Git head, these checks are planning QA,
not independently reproducible exact-head validation or retained product-economics authority:

- IG-CFG has 23 instruments. The local selection inputs reconciled as 19 IG-V2 + 1 IG-HS + 3 IG-AP =
  23, with one review candidate per selected listing and exact agreement with IG-CFG
  `preferred_epic` values. The copied rows had non-null minimum quantity, contract/lot and pip/value
  fields; IG-R0 independently retains the authoritative broad finding that all 23 ticks were null.
- The current tracked role count is 22 potentially tradable + 1 VIX context-only = 23. Korea 200 and
  Bitcoin remain outside the selected release.
- `config/capture-ibkr-v1-candidates.toml` has 20 canonical offline concepts and no provider mappings.
  The local IB-P input reconciled to 20 unique eligible IDs, 20 unique conIds and 280 requests.
- The local IB-C input reconciled to 12/12 successful cases = 3 copied fingerprints × 4 durations,
  with one MIDPOINT and one SCHEDULE request per case.
- Total planning rows: 23 IG + 20 IBKR = 43. Ready rows: 0; impact disposition for every row:
  `UNSUPPORTED_BLOCKING`.
- No `src/` file changed, no extraction code or retained source extract was added, and no provider,
  collector or collector-database call was made.
