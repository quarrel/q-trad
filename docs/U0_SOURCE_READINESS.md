# U0 source readiness

Status: **DATA_LIMITED — documentation feasibility complete; no empirical panel qualified.**
Review date: 2026-09-09. Authority: `U_LANE_IMPLEMENTATION_PLAN.md` sections 3–5 and 12.
Work class: `IMPLEMENTATION_ONLY`; empirical inputs: `NONE_APPROVED`.

Recommend **Databento GLBX.MDP3 for the next bounded metadata/quote decision**, provisionally,
not purchase or acquisition. Its indexed public documentation identifies contract-level futures,
instrument definitions and daily OHLCV, making it the best-supported route examined for a small
multi-group pilot. The next action is to obtain permission for the metadata/quote envelope below.
No daily sample was acquired. The companion U1/U2 fixture work is software evidence only.

## Three-source comparison

Evidence labels: **DOC** is a statement found in indexed vendor documentation via Context7 on the
review date, not measured coverage; **PLAN** is an observation already recorded in the approved plan;
**UNRESOLVED** means neither verified nor assumed. Website availability is not dataset availability.

| Candidate | Evidence established | Fit and material limitation | Decision |
| --- | --- | --- | --- |
| Databento, GLBX.MDP3 | DOC [D1]: CME, CBOT, NYMEX and COMEX futures/options coverage advertised from June 2010. DOC [D2]: daily OHLCV uses UTC dates and electronic-session data; official settlement can differ. DOC [D3–D5]: parent/continuous symbology and instrument definitions are documented. | Proposed contract-level pilot; options are excluded by U scope. June 2010 is a dataset-level advertised start, not twelve verified family histories. No verified spot/cross-FX, European/APAC or delisted-family coverage for this pilot. Raw contracts and session alignment must be qualified before scoring. | Provisional first choice for metadata and quote, conditional on licence, affordable numerical quote and usable development-only extraction. |
| CME DataMine / continuous price series | PLAN section 13 cites active/front continuous conventions and separately timed daily information [C1–C3]. Direct attempts at DataMine, continuous-series, daily-bulletin and one contract-specification page returned HTTP 403 in this environment. | Candidate for exchange-origin daily/settlement history. Dataset-level fields, historical legs, actual breadth, acquisition packaging, pricing and retention rights remain UNRESOLVED. A continuous-series convention alone does not establish a scored return stream. | Backup documentation/quote route if Databento history, session semantics or cost fail; not a verified substitute. |
| Nasdaq Data Link CHRIS | Both attempted CHRIS documentation addresses returned HTTP 404 [N1–N2]; the full-futures landing address returned an application shell [N3]. | Candidate continuous-futures catalogue could not be qualified. Current existence/access, field basis, history, adjustment/roll rules, pricing and licence are UNRESOLVED. No assertion of free access, breadth or usable returns follows from the legacy name. | Do not select until current documentation and terms can be established; no further source expansion in this tranche. |

The choice prioritises documented contract identity and a small feedback path. It is not a claim that
Databento is cheapest, that CME is inaccessible to the operator, or that CHRIS has ceased to exist.
No account, API key, paid service or market-data endpoint was used. Context7 returned public
example snippets, including illustrative rows; no example values were retained as market observations,
used to tune fixtures or inspected as U empirical inputs. Documentation examples were not executed.

## Representative family catalogue

This is a **twelve-family proposed discovery shortlist**, not twelve verified source mappings or a
usable market panel. Canonical IDs and economic groups below are local research taxonomy. Product
names, root hints, intended venues and currencies are **proposals to verify**, not assertions of
historical eligibility or contract economics. A root hint must never be passed directly to an
acquisition tool as an approved product list. ES/NQ parent syntax is documented in [D3]; that does
not authenticate a mapping to any original anchor or establish historical coverage.

| Canonical family ID | Proposed economic group / exposure | Proposed region | Candidate root hint | Venue to verify | Quote/settlement currency to verify |
| --- | --- | --- | --- | --- | --- |
| U_EQ_US_LARGE | Equity index / US large-cap | US | ES; documented `ES.FUT` syntax [D3] | CME | USD |
| U_EQ_US_TECH | Equity index / US technology-heavy index | US | NQ; documented `NQ.FUT` syntax [D3] | CME | USD |
| U_RATE_US_10Y | Rates / US ten-year Treasury | US | UNRESOLVED | UNRESOLVED | UNRESOLVED, including quote-unit conversion |
| U_RATE_US_LONG | Rates / US long Treasury | US | UNRESOLVED | UNRESOLVED | UNRESOLVED, including quote-unit conversion |
| U_ENERGY_OIL_US | Energy / US crude oil | US | UNRESOLVED | UNRESOLVED | UNRESOLVED, including price unit |
| U_ENERGY_GAS_US | Energy / US natural gas | US | UNRESOLVED | UNRESOLVED | UNRESOLVED, including price unit |
| U_METAL_GOLD | Metals / gold | Global exposure, proposed US venue | UNRESOLVED | UNRESOLVED | UNRESOLVED, including price unit |
| U_METAL_SILVER | Metals / silver | Global exposure, proposed US venue | UNRESOLVED | UNRESOLVED | UNRESOLVED, including price unit |
| U_AGRI_CORN | Agriculture / corn | US | UNRESOLVED | UNRESOLVED | UNRESOLVED, including price-unit conversion |
| U_AGRI_SOY | Agriculture / soybeans | US | UNRESOLVED | UNRESOLVED | UNRESOLVED, including price-unit conversion |
| U_FX_EUR_USD | FX / euro–US dollar | Europe/US | UNRESOLVED | UNRESOLVED | UNRESOLVED, including quote orientation |
| U_FX_JPY_USD | FX / yen–US dollar | Japan/US | UNRESOLVED | UNRESOLVED | UNRESOLVED, including quote orientation |

The following entries apply **to every row**, and are part of each family's catalogue record. No
blank field means approval or zero. This shared record avoids repeating identical unknowns twelve times.

| Required field | Present value for each family |
| --- | --- |
| Source / exact relationship | Proposed `DISCOVERY_EXTERNAL:Databento:GLBX.MDP3`; exact dated raw-symbol/contract mapping UNRESOLVED. No source version or acquired snapshot exists. |
| Session / timezone | UNRESOLVED; vendor UTC-day aggregation is documented [D2], but is not a verified venue session calendar, DST rule or historical trading schedule. |
| Listing / inception / delisting / successor | UNRESOLVED, including historical family membership; no common inception is fabricated. |
| Bar basis | Proposed vendor trade OHLCV daily bars; DOC [D2] distinguishes these from official settlement. Actual per-contract field semantics remain unqualified. |
| Coverage / missingness | UNMEASURED. DOC dataset start June 2010 [D1] does not establish any row's first/last usable session, missingness or sufficient preceding history. |
| Adjustment / roll | No adjustment or roll policy accepted. Prefer raw dated legs; continuous examples [D4] are documentation only. No synthetic splice return is eligible for scoring. |
| Publication / corrections | Historical availability of daily fields, corrections, settlements and open interest UNRESOLVED. UTC bar label is not a publication timestamp. |
| Volume / open interest | Daily OHLCV volume is documented [D2]; family coverage and exact volume interpretation unqualified. Settlement/cleared-volume/open-interest retrieval is documented [D6], but field timing and historical presence are unqualified. FX futures volume cannot stand in for spot-FX market volume. |
| Tick / multiplier / lot / expiry / delivery | UNRESOLVED. Definitions can supply tick size and expiration [D5]; no per-contract values have been acquired or validated. No invented multiplier, minimum lot, cash settlement or physical-delivery exemption. |
| Eventual execution | Proposed separately qualified small futures product or the original native anchor product, where justified. Broker availability UNVERIFIED; no account qualification done. Mini/micro/full-size versions remain one discovery family with distinct unresolved economics. |
| Original twenty anchor mapping | UNRESOLVED for every row. Proposed futures proxies are not original CFD/index/FX observation streams; no anchor/control equivalence asserted. |
| Daily descriptors | POTENTIALLY SUITABLE after approved development data, mapping, availability and domain checks; presently unavailable empirically. |
| Daily H-DAY scoring | UNAVAILABLE until same-contract entry/exit, session/holiday/weekend/delivery rules, unit economics, lagged risk scale and cost scenarios are qualified. |
| Intraday H-SESSION / H-EVENT | UNAVAILABLE; no canary acquisition authorised. Daily bars do not resolve within-bar ordering, handoff timing or historical event availability. |

Twelve proposed families are well below the 80–150 discovery aim. Rates and agriculture broaden the
proposed pilot beyond equity/FX variants, but Europe/APAC venues, short rates, soft commodities,
livestock, industrial metals and direct major/cross-FX remain absent or unresolved. The pilot is
US-venue-heavy and cannot support a global-universe conclusion. No liquidity screen has been measured.
The smallest **empirically usable** panel is currently zero; twelve is the proposed acquisition
shortlist, not a claim that all twelve will pass. Do not force cohort sizes or matching to fit it.

## Conditions for a usable vertical slice

Use raw dated contracts and retain source date, session date, UTC timestamp basis, publication rule,
contract identity, price basis and adjustment provenance. Vendor UTC daily OHLCV cannot be silently
relabelled as an exchange-session open/close. Where the required entry/exit cannot be resolved from
that bar, H-DAY scoring stays unavailable; settlement-only descriptors can remain separately qualified.
Acquiring more granular bars to solve this is a new bounded acquisition decision, not implied here.

Choose a deterministic past-only roll rule only after contract expiry/delivery constraints are known.
Score same-contract price changes and charge explicit roll transactions; never score a cross-contract
price jump or use future volume to pick the historical contract. Current back-adjusted levels are not
historically known values. Unknown leg semantics restrict continuous series to qualified descriptors.
Keep non-positive prices, domain-invalid logarithms, acquisition gaps and delivery exclusions explicit.

Publication timing is unresolved, so no numerical lag is presently certified. Before empirical use,
document a source-supported conservative availability rule per field and disclose it as an assumption
where exact historical publication is missing. If no defensible bound is available, exclude that field
from causal selection/scoring. Final settlement, revised volume and open interest are not assumed
available at session close. Daily OHLCV does not establish executable fills, spreads, stress liquidity,
intraday stops, impact capacity or real-capital suitability.

Historical membership is also unresolved. Even after acquisition, a current-family shortlist permits
only survivor-conditioned exploratory findings until listing, disappearance and successor intervals
are established. Missing post-selection members cannot disappear from the comparison denominator.

## Exact missing decision and proposed next envelope

**No numerical quote is available.** Public documentation describes cost-estimate and unit-price
methods [D7]; these are not a quote for this request or permission to call them. No licence, retention,
derived-output sharing right, account entitlement or spend approval has been established. Neither a
free trial nor a zero-cost daily tariff is assumed.

The concrete next action is operator approval of a **metadata/quote-only enquiry**, with no market
payload acquisition. Proposed bounds, all awaiting approval:

- Provider/dataset: Databento `GLBX.MDP3`; identify the licensed product/version and approved account
  context without exposing credentials. Resolve this twelve-family shortlist to exact supported
  contracts using metadata; drop unresolved families rather than guess. Exclude options and spreads.
- Scope: endpoint/schema availability, dated definitions/mappings, per-family non-value coverage,
  session/bar/publication documentation, licence/retention terms and a numerical quote. Metadata must
  exclude prices, volumes, open interest, returns and any embedded example outcome payload.
- Cost enquiries: documented `metadata.get_cost` and `metadata.list_unit_prices` [D7], only within the
  specifically approved account/API context. Suggested limit: 20 cumulative requests, 5 MB returned
  metadata, USD 0 charges; sequential requests at no more than one per second. Stop if billed metadata,
  market values, broader access or a larger request budget would be needed. These are proposed caps,
  not granted authority or claims about provider limits.
- Requested quote scope: raw-contract daily OHLCV plus necessary dated definitions and calendar/field
  metadata, at most twelve families, development-only dates. Prefer history from June 2010 if actually
  available; exact start is determined per family from metadata. No minute/tick/book download.
- Freeze the endpoint quarter from non-value availability metadata before any return inspection. If
  the latest supported complete endpoint is 2026-Q2, reserve 2024-Q3–2026-Q2 and request development
  only before 2024-07-01. This is a conditional example, not an established endpoint or authorised
  date range. Earlier endpoints require their own fixed eight-quarter reserve.
- Destination: an operator-named metadata-only location under the U output root. A later acquisition
  must name its exact retained destination, version/snapshot, cumulative USD/request/byte limits,
  pacing and stop conditions. The plan's 20 GB daily-lab planning target is not a purchase byte cap.
- Terms decision: confirm personal/internal exploratory use, raw retention duration and backups,
  contract metadata retention, permitted derived findings, publication restrictions and any exchange
  entitlement charges. Raw licensed data stays out of Git. Do not accept terms or contact a vendor
  on the operator's behalf without the necessary authority.

After this enquiry, return the exact quote (currency, total including mandatory fees, scope and
validity period) and terms for a separately bounded acquisition decision. An unavailable quote is a
specific blocker, not a zero-price estimate. If terms or session semantics cannot support the pilot,
stop that route and consider the CME backup; do not silently acquire a fourth source.

Before data-dependent work, freeze the catalogue snapshot/development dates and establish plan
section 5 isolation through actual filesystem, shell, connector and provider access. Prefer leaving
reserve data unacquired and exposing only development extracts. A loader filter or read-only folder
is insufficient. If the vendor requires a mixed archive, a separately authorised external splitter
must expose development-only data and non-value partition metadata. Existing R2/R4 exposure to parts
of 2026 must be disclosed; this does not open the U reserve. No isolation claim is made by this audit.

## Evidence references and validation

Direct public-page checks and indexed documentation checks occurred on 2026-09-09. Context7's
indexed excerpts have no authenticated vendor snapshot/version here; DOC claims concern what those
excerpts document, and account-specific capabilities still require qualification. Only public
HTML/documentation was requested. No source download API, account route, local empirical payload,
reserve, collector or retained research artefact was accessed or changed.

- [D1] [Databento GLBX.MDP3](https://databento.com/docs/venues-and-datasets/glbx-mdp3): indexed
  documentation states the four venues and June 2010 advertised history.
- [D2] [Databento OHLCV](https://databento.com/docs/schemas-and-data-formats/ohlcv): indexed daily UTC
  aggregation/settlement distinction; direct page and `.md` variant returned an application shell.
- [D3] [Historical request examples](https://databento.com/docs/examples/basics-historical/requesting):
  indexed `ES.FUT`/`NQ.FUT` parent syntax, not evidence of twelve family mappings.
- [D4] [Continuous symbology](https://databento.com/docs/examples/symbology/continuous): indexed
  `.v.0` example; no endorsement of its roll rule for U. Direct standards/continuous-contracts page
  returned an application shell.
- [D5] [Instrument definitions](https://databento.com/docs/examples/futures/futures-introduction/using-instrument-definitions-to-get-tick-size-expiration-and-matching-algorithm):
  indexed tick/expiration metadata capability, not validated economics.
- [D6] [Settlement and open interest](https://databento.com/docs/examples/futures/retrieving-oi-and-settlement-prices):
  indexed field-retrieval example; values in examples are not U empirical observations.
- [D7] [Historical API reference](https://databento.com/docs/api-reference-historical): indexed USD
  cost-estimate and USD/GB unit-price methods. Direct pricing page returned a shell; no quote obtained.
- [C1] [CME continuous price series](https://www.cmegroup.com/market-data/cme-group-continuous-price-series.html):
  plan section 13 observation; current direct request HTTP 403.
- [C2] [CME daily bulletin](https://www.cmegroup.com/market-data/daily-bulletin.html): plan section 13
  observation; current direct request HTTP 403. Related plan reference:
  [volume/open interest](https://www.cmegroup.com/market-data/volume-open-interest.html), not re-fetched.
- [C3] [CME DataMine](https://www.cmegroup.com/market-data/datamine-historical-data.html): HTTP 403.
  [Ten-year Treasury specifications](https://www.cmegroup.com/markets/interest-rates/us-treasury/10-year-us-treasury-note.contractSpecs.html)
  also returned HTTP 403; no economics inferred.
- [N1] [Nasdaq CHRIS documentation](https://data.nasdaq.com/databases/CHRIS/documentation): HTTP 404.
- [N2] [Legacy CHRIS documentation](https://www.quandl.com/data/CHRIS/documentation): HTTP 404.
- [N3] [Nasdaq full-futures landing page](https://data.nasdaq.com/tools/full-futures): application shell.

`U0-SOURCE`: PASS for this documentation deliverable: source claims have explicit DOC/PLAN references,
proposed catalogue entries and unverified economics are labelled, no acquired sample or measured
coverage claimed. `U0-SCOPE`: PASS: three candidates, public documentation only, `NONE_APPROVED`,
no market acquisition, spending, account access, reserve access, collector changes or evidence
promotion. These checks do not pass empirical readiness. Independent review belongs to the combined
tranche. No executable code changed; documentation scope and whitespace checks suffice locally.
