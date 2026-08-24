# R3 product-economics readiness inventory

**Job:** R3-E0  
**Evidence cut-off:** 2026-08-24  
**Authority:** preparatory read-only inventory; R3 remains not started  
**Reporting currency:** AUD

## Result

No current IG or IBKR row is `ECONOMICS_READY`.

The current IG release has exact reviewed listings and useful product metadata for 23 instruments.
All 23 listing records have product type, currency, minimum quantity, contract/lot size and
pip/value information, but all 23 have `price_increment = null`, none records a quantity
increment, non-AUD rows lack a source-aligned conversion contract, and no row has a
source-aligned commission, financing or authoritative-session contract. Native bid/ask evidence
exists for all 23, but the bounded `capture-v4` observation is a deployment smoke rather than representative-session cost evidence. Twenty-two are potentially
tradable; `index:volatility` remains context-only and `PAPER_INELIGIBLE`.

The locally retained IBKR historical selection identifies 20 exact contracts and currencies. Its
fingerprints have `multiplier = null` and do not contain minimum quantities, quantity increments or
minimum ticks. The B5 construction code requires an authenticated non-null minimum tick and constructs
a listing with configured minimum deal size `1`, but the exact numeric B5 tick values and retained B5
listing artefact are not present in the local evidence consumed by this inventory. The B5 authority
does prove fresh LIVE bid/ask capture for the same-sized 20-contract native universe; it does not
supply per-instrument spread distributions, latency, commission, financing, conversion or impact
economics.

The first subset suitable for R3 **implementation evidence** is the single native IG
`index:australia-200` listing. Its existing framework proof already joins exact listing metadata,
native bid/ask paper fills, AUD identity conversion and explicit configured tick/session/latency/
slippage assumptions. It is not source-aligned economic evidence and cannot support a post-cost
conclusion without the additional facts listed below.

## Scope and evidence classes

This inventory used only repository files and existing local retained artefacts. It did not contact
IG, IBKR, either collector, a collector database, or a broker endpoint. Historical IBKR MIDPOINT and
SCHEDULE evidence remains provenance-distinct from native top-of-book evidence and is never used as
IG spread or fill evidence.

Evidence tags used below are:

- **PM — product metadata:** an exact provider review/listing or authenticated contract field.
- **NM — observed native market evidence:** retained source-native quotes or callbacks.
- **CA — configured assumption:** a research or runtime value, not a provider economic fact.
- **UA — unsupported assumption:** no qualifying evidence; it must remain explicit and cannot be
  treated as zero or inferred from another source.

The following evidence sources are used by short code in the instrument tables:

| Code | Exact source and identity | Use |
|---|---|---|
| IG-V2 | `tmp/capture-v2-review-v2.json`, review `aff32264bfc9dade67bf1df06cf689d55b1b3f5b60550f28332da3826dae0dc9`; `tmp/capture-v2-selections-v2.toml` | 19 exact IG selections and listing economics |
| IG-HS | `tmp/hang-seng-review.json`, review `f7aa58a401e3ac2bf8b5beb74ff00d2f0122871e0790894d2207c252eb04284e`; `tmp/hang-seng-selection.toml` | exact HS50 selection and listing economics |
| IG-AP | `tmp/capture-v4-corrected-review.json`, review `ed17cf86d566f742c0be3e0f003fa24ffc0bbb7a31642c71a386ec3e3782cf13`; `tmp/capture-v4-apac-selection.toml` | exact China A50, Taiwan and VIX selections |
| IG-CFG | `config/capture-v4.toml`; `config/capture-v4-deployment.toml`, universe hash `eca6649cfd2477204d9a6d5970596657ad0d94b0a25916f8b26b9c5f0c606078` | current 23 canonical IDs, currencies and exact preferred epics |
| IG-R0 | `docs/R0_DATA_READINESS.md`, measured 2026-07-22 15:35 UTC against live OCI `capture-v4`; abbreviated hash in that report resolves to IG-CFG `eca6649cfd2477204d9a6d5970596657ad0d94b0a25916f8b26b9c5f0c606078` | native field availability and bounded bid/ask/bar coverage |
| IG-PROOF | `config/research-proof-v1.toml`, source manifest `5289530e6b5d946c626593f74eda8d14774d1454774fff666c9c313a9946565d` | Australia 200 configured paper assumptions only |
| IB-P | `tmp/ibkr-run-20260806T035300Z/plan.json`, plan `555b2acb3f730f908f362fbfdae7fdc90dc46f5320272ed8ea4899a1787d6bac`, selection `788bd8304781bc8aff0614dc6622f84a934e85e9bc9ffa002bfc66e7f46f1f88` | 20 exact historical contract fingerprints; not native cost evidence |
| IB-C | `tmp/ibkr-run-20260806T035300Z/evidence/stage5-canary-20260806T035300Z.json`, evidence `68c9a42bddfd5a660ce7614aec1bb1b9b1977d27e4491c3fefb7fb9aecde70a1` | 12 successful historical MIDPOINT/SCHEDULE cases for three contracts |
| IB-B5 | `docs/STATUS.md` B5 authority: canonical artefact `efb6f465221659cb0b1c65d6e0df12ac01d20a9227d07e606e8febf78152ed24`, qualification file `87c4860dbc97b7e73e1849ed58ba528b1b630cdd13207393fec32ebfb1eb9218`, verifier `dbca7ba916fa2c1a97fecc2dd1ef71f73621ddf87cbe6313ca7f416b41949a67` | native exact-20 LIVE bid/ask qualification at authority level; row artefact not locally consumed |
| IB-CODE | `src/qtrad/runtime/ibkr_b5.py::_expected_listing` | configured listing-ID/minimum-size policy and requirement for authenticated non-null minimum tick |

The ignored `tmp/` files are local evidence inputs, not durable tracked authorities. Their identities
are recorded here so a later inventory cannot silently substitute different bytes.

## Source-wide cost evidence

These findings apply to every row for the named source unless a row-specific exception follows.

| Field | IG | IBKR |
|---|---|---|
| Product metadata | **PM:** exact selected listing; product type, currency, minimum quantity, contract/lot size and pip/value fields present | **PM:** exact local historical contract fingerprint. Native B5 code requires a non-null authenticated tick; configured minimum deal size is `1`; economics mapping is empty |
| Observed spread | **NM available:** IG-R0 has 23 healthy current quotes and BID/ASK/MID bars. **Insufficient:** bounded smoke interval, not representative by session, size or decision time | **NM available at authority level:** IB-B5 has fresh post-reconnect LIVE bid/ask for 20/20. Exact per-row observations/distributions were not locally consumed. IB-C MIDPOINT is not spread evidence |
| Latency | **UA:** no validated provider-event-to-receive or decision-to-fill distribution. IG-PROOF only configures 1 s/1 tick, 2 s/2 ticks and 5 s/3 ticks scenarios | **UA:** callback counts, zero-drop qualification and historical request completion do not establish quote or execution latency |
| Commission | **UA:** no schedule | **UA:** no account/product schedule |
| Financing | **UA:** no schedule, cut-off, day-count or multi-day rule | **UA:** no schedule, cut-off, day-count or multi-day rule |
| Sessions | **UA:** listing metadata has no authoritative calendar. IG-PROOF configures weekdays 10:00–16:00 Australia/Sydney with an empty holiday list for Australia 200 only | **Partial PM:** IB-C returned historical SCHEDULE callbacks for EUR/USD, Australia 200 and spot gold over 1D/1W/2W/4W. This is not a current all-contract calendar |
| Quote to AUD | AUD identity is exact. Same-source canonical FX pairs define mathematical candidate paths for USD, EUR, GBP, JPY, CAD and CHF, but no causal rate-selection/staleness contract exists. No selected HKD conversion pair exists | Same position as IG within the IBKR 20-contract universe. No cross-provider substitution is permitted |
| Impact | **UA:** no validated trade-volume or fill-response evidence; top-of-book size is not executed volume or CVD | **UA:** no validated trade-volume or fill-response evidence |
| Existing paper cost | IG-PROOF measures spread plus configured adverse tick slippage through executable bid/ask sides. Production paper code has no commission, financing or impact component | No retained source-aligned paper-economics configuration was found |

The existing paper ledger's `execution_cost` is the difference between gross-mid and bid/ask-plus-
slippage P&L. It must not be described as commission, financing or market impact.

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

### IG-AUD

States: `MISSING_PRICE_INCREMENT`, `MISSING_QUANTITY_RULE`, `MISSING_COMMISSION`,
`MISSING_FINANCING`, `SPREAD_EVIDENCE_INSUFFICIENT`,
`SESSION_EVIDENCE_INSUFFICIENT`.

Exact missing facts: authenticated numeric price increment; provider-authoritative quantity step and
unit rule; account/product commission schedule; financing basis/rates/cut-offs/day count; authoritative
session/time-zone/holiday evidence; representative native spread distribution for the intended
decision windows and quantity; validated timestamp semantics and latency distribution. Impact remains
`UNSUPPORTED` and must not be treated as zero.

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
unit; commission; financing; a current authoritative session calendar; local retained representative
native spreads; validated timestamp semantics and latency distribution. The historical schedule
canary is partial evidence only. Impact remains `UNSUPPORTED`.

### IB-FX-CANARY (non-AUD listing currency)

All `IB-AUD-CANARY` states and missing facts, plus `MISSING_CONVERSION` and its exact causal
same-source evidence contract.

### IB-FX (non-AUD listing currency)

All `IB-FX-CANARY` states and missing facts. Unlike the canary profile, no retained per-instrument
SCHEDULE sample was found.

## IG exact-listing inventory

All rows are from a reviewed `capture-v4` selection. Quantity increment is unavailable for every
row and tick is explicitly `null`. Minimum quantity, contract/lot, pip meaning and pip value are
**PM**, not configured assumptions.

| Canonical instrument | Exact listing / product / source | Currency | Minimum quantity | Contract / lot | Pip meaning; value | AUD path | Profile |
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

## IBKR exact-contract inventory

The contract identity columns below come from IB-P. They are exact for that local historical
selection. They must not be silently asserted to be the current B5 contract binding: the current B5
release artefact needed to authenticate that equivalence was not locally consumed. For all rows,
`contract_month`, `primary_exchange` and `multiplier` are null in IB-P.

IB-CODE configures canonical native listing IDs as
`ibkr:IBKR_PAPER:<canonical-name>`, minimum deal size `1`, an empty economics mapping, and requires
an authenticated non-null minimum tick. Accordingly every row below has:

- minimum quantity: `1` **CA**, with provider-authoritative minimum/step/unit unavailable;
- quantity increment: unavailable;
- multiplier/value per point: unavailable;
- exact numeric minimum tick: unavailable in the locally consumed artefacts.

| Canonical instrument / configured native listing | Exact local contract fingerprint: conId; secType/exchange; symbol, local symbol, trading class; underlying conId | Currency | AUD path | Profile |
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

IB-C reconciles to twelve successful cases: EUR/USD, Australia 200 and spot gold at each of 1D, 1W,
2W and 4W. Every case has one successful MIDPOINT request and one successful SCHEDULE request. The 4W
schedule counts were respectively 20, 40 and 20 sessions. This proves bounded historical request and
schedule availability for those three exact fingerprints. It does not establish contemporaneous
spread, current trading hours, execution latency or product costs.

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
| IG demo | `index:australia-200` / `ig:demo:IX.D.ASX.IFD.IP` | exact metadata; minimum quantity 1; 25 AUD per index point; AUD identity conversion; native bid/ask availability; existing causal paper-fill and ledger path; explicit configured tick/session/latency/slippage scenarios | implementation evidence only; not source-aligned cost evidence or an effectiveness claim |

Use that row only with labels preserving **PM**, **NM** and **CA**. In particular,
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
7. Validated provider-event, receive, decision and fill timestamp semantics plus an observed latency
   distribution; configured delay scenarios remain sensitivities.
8. A versioned impact disposition. Without validated volume/fill-response evidence, impact must stay
   `UNSUPPORTED` and no market-impact claim may be made.
9. Immutable binding of those facts to the exact listing metadata version, source, environment and
   evidence interval.

AUD conversion is identity for this row, so no FX rate is required. Every non-AUD extension also
requires the causal same-source conversion evidence contract described above. HS50 additionally
requires a reviewed native HKD-to-AUD path; it cannot borrow one from the other provider.

## Reconciliation

The inventory was mechanically reconciled in memory without adding production or durable extraction
code:

- IG-CFG has 23 instruments. Selection counts are 19 IG-V2 + 1 IG-HS + 3 IG-AP = 23.
  Every selected listing joins exactly once to its review candidate and exactly matches the
  `preferred_epic` in IG-CFG. All 23 have non-null minimum quantity, contract/lot and pip/value
  fields; all 23 have null price increment and no quantity-increment field.
- The current role count is 22 potentially tradable + 1 VIX context-only = 23. Korea 200 and Bitcoin
  remain outside the selected release.
- `config/capture-ibkr-v1-candidates.toml` has 20 canonical offline concepts and no provider
  mappings. IB-P has 20 unique eligible canonical IDs, 20 unique conIds and 280 request
  specifications.
- IB-C has 12/12 successful cases = 3 exact instruments × 4 durations, with exactly one MIDPOINT and
  one SCHEDULE request per case.
- Total inventoried selected source rows: 23 IG + 20 IBKR = 43. Ready rows: 0.
- No `src/` file changed, no extraction code was retained, and no provider or collector call was
  made.
