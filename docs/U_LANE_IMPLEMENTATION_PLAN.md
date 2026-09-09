# U-lane implementation plan — universe, regime and opportunity discovery

- **Status:** DRAFT FOR OPERATOR REVIEW. The operator accepted the scope defaults; this detailed plan still requires approval before execution.
- **Planning base:** `5d358c9bad5648175e8742fa3d3b3bcc737ea0b8`.
- **Review reconciliation:** current `main` at `cb0146d1310935cab06b73bdb09756cc02a7513a`, including merged PR #189.
- **Immediate delivery:** U0 source/catalogue feasibility, U1 daily atlas and a first U2 daily selector laboratory.
- **Conditional delivery:** bounded U3 intraday canaries after exact acquisition permission and hypothesis specification.
- **Not authorised here:** historical-reserve evaluation, U4 prospective execution, collector changes, R5 integration or trading.
- **Research object:** market family × stated hypothesis × next evaluation period, not a permanently selected best twenty.

## 1. Authority, purpose and execution discretion

The operator requests a U-lane plan using the agreed defaults and values rapid, useful feedback, whether positive or negative. `AGENTS.md`, `docs/EVIDENCE_GOVERNANCE.md`, `docs/ENGINEERING.md` and `docs/DEVELOPMENT.md` remain governing instructions. `PLAN.md` and `docs/TRADING_RESEARCH.md` supply the programme context. MAP supplies orchestration procedure, not scientific authority.

**BINDING** below identifies the approved scope, necessary scientific boundaries and required deliverables. Proposed algorithms and work-item seams are **ADVISORY** until the orchestrator records the selected experiment specification. The orchestrator owns execution scale, decomposition, file placement, resource management and ordinary implementation decisions. No mandatory PR-per-stage or agent-per-stage structure is implied.

U asks:

> Using only information available before a selection date, can we choose markets for a named simple hypothesis whose next-period research results improve on fixed anchors and appropriate random controls?

Separate three questions:

1. **Researchability:** can this market be measured and eventually traded honestly?
2. **Opportunity:** does the stated hypothesis show future signal and sufficient movement relative to friction?
3. **Selection value:** does choosing markets prospectively improve the same hypothesis relative to controls?

High volatility, strong past returns or a visually attractive chart answers none of those questions alone. A profitable-looking selected cohort also does not establish selection value if the same rule works equally well on random markets.

The immediate objective is a working, inexpensive feedback loop. A source limitation, negative selector, useful regime description or well-supported decision not to acquire intraday data is a valid result. Do not build the complete U0–U4 architecture before producing the first table that changes a decision.

### Programme relationship — OBSERVED

R2 remains the source-specific historical baseline result. R4-P0 remains the qualified result of its exact frozen execution; R4-P1 established trainability but found no useful corrected temporal improvement, and its linear graph gate prevented further neural graph work. See `docs/R4_P0_FINDINGS.md` and `docs/R4_P1_FINDINGS.md`. No universal conclusion about market predictability follows.

PR #189 merged on 2026-09-09 as `cb0146d1310935cab06b73bdb09756cc02a7513a` and records the accepted P0/P1 programme position. Preserve its accepted conclusions; this plan does not duplicate or reopen that work. On activation, update only the programme pointers needed to identify U as a separately authorised research lane. The old offline-MVP deferral of opportunity models does not silently prohibit this operator-approved laboratory, nor does U introduce a runtime strategy selector into the MVP.

## 2. Adopted operating envelope — BINDING

| Dimension | Initial envelope |
| --- | --- |
| Markets | Global liquid futures and major/cross FX market families; include rates and broader commodities, not just equity-index/FX variants |
| Breadth | Aim for 80–150 underlying families in the catalogue; this is a discovery target, not a minimum acceptance count |
| Exclusions | Individual equities, options, crypto, multi-leg relative-value/calendar/curve strategies and deliberately illiquid niches |
| History | Prefer 15–20 years of usable daily history where available; do not require fifty years or fabricate a common start |
| Hypotheses | Session/market handoff; scheduled-event aftermath; regime-conditioned session-to-five-day continuation/reversal |
| Timing | Quarterly market selection using a primary trailing 12-calendar-month descriptor window; evaluate the next quarter |
| Holding risk | Separate intraday/session and one-to-five-trading-day probes; no weekend holding by default |
| Controls | Current twenty as fixed research anchors; selector cohorts; random and nuisance-matched random cohorts |
| Intraday pilot | Default 12 anchors, 12 selected and 12 matched controls for one hypothesis/vintage; deduplicate acquisition across overlapping membership |
| Data | Broad daily discovery may use an approved external source; later intraday/native evidence retains its actual source and product identity |
| Reserve | Final eight completed calendar quarters withheld from U development, with prior exposure disclosed; U4 needs separately authorised genuinely future evidence |
| Tail posture | No option selling, leverage-dependent rescue, assumed stress liquidity or unmodelled delivery exposure |
| Existing systems | Preserve collectors, R2/R3/R4 records and the frozen `R3_NATIVE_EXPERIMENT_PROTOCOL.md` |

The owner may start with a small representative panel, including the existing anchors where exact mappings permit, while broader history is resolved. Report missing asset classes and history instead of spending the entire first tranche filling the catalogue. Full-sized and pilot claims must remain distinct.

## 3. Work classes, permissions and boundaries — BINDING

```text
U0 catalogue, public documentation review and fixture implementation:
  experiment_class: IMPLEMENTATION_ONLY

U1–U3 analysis on explicitly designated historical development data:
  experiment_class: POST_HOC_HISTORICAL_EXPLORATORY
  evidence_state: ordinary development outputs; accepted findings retained as exploratory evidence
  refinement: newly acquired historical development data is not claimed to be previously authenticated R2/R4 evidence

Actual provider/data acquisition:
  experiment_class: OPERATIONAL_OR_EVIDENCE_MUTATION
  permission: only a separately approved, bounded acquisition instruction

U4 prospective or locked execution:
  experiment_class: DECISION_GRADE_LOCKED
  permission: separate protocol and explicit execution authority, not supplied by this plan
```

Permitted after plan approval: public documentation/catalogue research, fixture-based code, use of already authorised local development data, bounded exploratory batches, ordinary diagnosis/repair, compact results and acquisition proposals.

Prohibited: broker orders, account/collector mutation, live session interference, paid subscriptions or purchases without a numerical spend approval, unapproved provider calls, reserve outcome access, rewriting retained evidence, broad historical reacquisition, or claims of native execution from discovery bars.

**Data spending is not numerically authorised.** The accepted preference for a modest external source permits investigating and recommending one; it is not a blank cheque. U0 returns an exact quote and acquisition envelope where purchase or account access is needed. Public documentation browsing is not a market-data acquisition permission.

An acquisition instruction can cover a useful bounded batch rather than every request. It names provider/dataset/version and licence/retention terms, product list, date range, resolution/fields, endpoint/account context, cumulative monetary/request/byte limits, pacing, destination and stop conditions. Use supported read-only acquisition machinery when it fits; do not rebuild the historical ingest programme. A missing acquisition permission blocks dependent data work, not fixture implementation or analysis of already approved inputs.

Use an experiment-local provenance tag such as `DISCOVERY_EXTERNAL:<provider>:<dataset>` for an external discovery source. Do not label it `IBKR_HISTORICAL_RESEARCH`, add a production source enum or adapt every R1–R4 contract just to run this lab. Any later integration owns that contract decision.

U1–U3 do not opt into `CONSEQUENTIAL_RETAINED_EXECUTION` merely because runs take time or reports are retained. Do not add promotions, G0-style release gates, per-function receipts, separate verifiers for ordinary helpers, or a durable process-supervisor platform. Acquisition and a future reserve opening retain their own protected boundaries.

## 4. U0 — catalogue, source feasibility and one usable vertical slice

### Required outputs

Produce a compact catalogue and source recommendation sufficient to begin U1/U2. Compare at most three credible daily-source options initially. Include a representative daily sample only where its retrieval is already permitted. Otherwise prove the adapter on a fixture and return the exact missing access decision.

For each market family record:

- canonical family ID, economic group, region, currency, venue and known session/timezone;
- source symbols and exact underlying/contract relationship, with mapping evidence;
- listing/inception, delisting or successor information where available;
- bar basis: trade, midpoint, cash index, settlement or synthetic/continuous series;
- history coverage, missingness, adjustment/roll method and field publication timing;
- volume/open-interest availability and meaning; FX quote activity is not exchange volume;
- known tick, multiplier, lot-size and delivery/expiry constraints, with unresolved fields explicit;
- intended eventual execution product and whether broker availability is verified or only proposed;
- suitability for daily descriptors, daily hypothesis evaluation and intraday canaries separately.

A mini, micro, full-sized future and index/CFD referencing the same market are not four independent discovery families. Preserve their different execution economics. Never silently replace an original anchor CFD/index/FX product with a future and call it the same observation stream; mark any research proxy and later test source transfer.

### Source and point-in-time requirements

The source review must distinguish current coverage from historical investability. A catalogue of today's surviving markets can support a **survivor-conditioned exploratory result**, not a survivorship-free historical selection claim. Use known listing/delisting intervals; do not backfill future market existence. If historical membership cannot be established, disclose the restriction and do not pretend the source has passed prospective qualification.

For daily data retain at least source date, session date, timestamp basis, publication/availability rule, contract identity, price basis and adjustment provenance. Final settlements, revised event data, volume and open interest are not assumed available at the session close. Apply a documented conservative lag when exact historical availability is missing; flag the assumption. Do not infer release availability from the filename date.

For futures, distinguish a descriptor series from a tradable return series. A continuous price splice is not a roll-neutral strategy return. Use a deterministic past-only roll rule and exact same-contract price changes for scored outcomes, with roll transactions/costs explicit. Never use a current back-adjusted price level or a future-volume roll choice as though it were known historically. If leg/roll semantics are unavailable, use the series only for qualified descriptors and report economic scoring unavailable.

Non-positive futures prices must not silently disappear or enter logarithms. Use contract-price changes for P&L and a positive lagged risk scale for comparisons; percentage/log-return descriptors are unavailable where their domain is invalid. Test this explicitly. Acquisition gaps, delivery exclusions and structurally unavailable markets are not zero-return observations.

### First deliverable

Return `U0_SOURCE_READINESS.md` or equivalent with the recommended source, smallest usable panel, material limitations and one concrete next action. A shortfall from 80–150 families or 15–20 years is disclosed, not hidden and not an automatic instruction to build more infrastructure.

## 5. Historical chronology and reserve — BINDING

Before inspecting new market-return histories, record the catalogue snapshot, source endpoint metadata, development date ranges and reserved quarters. Use the latest eight completed calendar quarters supported by the selected dataset endpoint. At the planning date, the latest calendar-complete quarter is 2026-Q2: if the source endpoint is that quarter, the reserve is 2024-Q3 through 2026-Q2. Earlier coverage yields an earlier endpoint chosen from availability metadata, not performance. Do not slide the reserve after seeing results.

**Reserved for U is not globally untouched.** Existing R2/R4 research already exposed portions of 2026. Record that overlap and any other prior exposure. A later historical reserve evaluation is a guarded replication on disclosed evidence, not automatically a decision-grade result. U4 uses newly accumulating outcomes after a separate freeze.

**Access precondition before data-dependent agent work:** expose only the approved development inputs. U reserve payloads, raw bulk archives containing them and derived reserve copies must be inaccessible through the research agents' actual filesystem, shell, connector and provider tools, including those of the orchestrator and reviewer. Prefer leaving the reserve unacquired and supplying development-only extracts. Otherwise keep the reserve and unsplit archives outside agent-visible mounts or behind permissions the research identity cannot override; do not supply credentials or another tool route that bypasses that boundary. A separate folder, read-only mount, prompt prohibition or default-deny application loader alone is not isolation.

If acquisition delivers an inseparable archive, a separately authorised bounded mechanical splitter operates outside the research-access environment, routes rows by the frozen dates, and exposes only development extracts and non-value partition metadata. Its logs must not leak reserved values. Test the splitter on synthetic dates and payloads before use. Preserve the original archive under its existing retention authority; this plan does not authorise deleting or moving operator-owned evidence to establish isolation.

Record the chosen access arrangement and a bounded check from the actual research role showing that protected locations/routes are absent or denied, without reading real reserve values. Repeat that check only if the relevant mounts, permissions, tools or input exposure change. If effective isolation cannot be established, block data-dependent work; U0 public documentation and fixture work can still proceed. This is an input-access precondition, not a new holdout platform or receipt hierarchy.

Ordinary U loaders additionally read development dates only and reject an unauthorised reserve request before decoding. Keep that focused regression test as defence in depth, not as proof that direct agent access is impossible. No cryptographic vault or custom holdout lifecycle is needed.

Neither source ranking, eligibility-threshold calibration, clustering, synthetic-realism choices, whole-panel normalisation, exploratory plots nor the U1 atlas may use reserve values. The existing, already-exposed R2 interval may be described separately from its retained evidence and compared against **development-only** historical window distributions; that exception does not permit reading a new broad-universe reserve.

For every quarterly vintage:

1. Set a common UTC selection cutoff and use only fields available by it. Different exchange closes do not become simultaneous merely because they share a date.
2. Build the trailing 12-month descriptors and eligible family set at that cutoff; clusters, matching bins and any fitted transformations use past data only.
3. Record selected, anchor and control membership before scoring the next quarter.
4. Enter only at the first permitted observation after the cutoff and the specific signal's availability; no same-close fill from a close-derived signal.
5. Keep membership fixed for the quarter. Modelled activity/position rules may act within it, but cannot replace members using later performance.
6. Close positions before the declared weekend, delivery or quarter boundary; do not carry target windows into the reserve. Preserve outcomes from markets that subsequently disappear.

Past-only replay protects each individual selection, but repeated researcher adaptation can still overfit development quarters. Record all variants and label their apparent performance exploratory. Do not call a retrospectively chosen selector historically out of sample merely because its inputs were lagged.

## 6. U1 — daily regime atlas, not an alpha model

Build one reusable daily panel and a small descriptor table. Initial descriptor families are volatility/range, volatility-of-volatility, trend efficiency, lag dependence, gaps/drawdowns, liquidity/cost proxies and cross-market redundancy. Record exact formulas, lookbacks, valid domains and missing-data rules before producing the first comparative result; there is no automated descriptor search.

Use economic groups and past-only correlation clustering to expose duplicate risk. P1's contemporaneous graph findings motivate inspecting redundancy; they are not a universal adjacency or expected-return predictor for new markets.

Report rolling 16-week historical window descriptors on development data. Locate the original R2 interval using its exact retained boundaries, not a retrospectively selected 'better six months'. Distinguish original acquisition, OOF and terminal windows. Percentiles are descriptive: an unusual window cannot rescue a negative result, and a normal-looking window cannot prove absence of conditional signal.

Daily close/settlement-only data cannot identify overnight/session attribution or intraday lead-lag. Mark those dimensions unavailable until the required timestamps/open/intraday observations exist. The atlas does not authorise minute-data acquisition across the whole catalogue.

Deliver a compact atlas, coverage/exposure matrix and a brief answer to: which economic dimensions were absent from the original twenty, how unusual was the measured historical period, and which additional data would change a research decision? U2 can start on a representative usable subset without waiting for the atlas to become exhaustive.

## 7. U2 — hypothesis-specific selector laboratory

### 7.1 Stage the three hypotheses by required information

| Family | Minimum useful evidence | Initial test |
| --- | --- | --- |
| H-DAY: regime-conditioned continuation/reversal | Causal daily changes and valid entry/exit observations | First end-to-end selector experiment; next-session and up-to-five-day probes |
| H-SESSION: market/session handoff | Source-aligned sessions, opening/boundary intraday bars | One named handoff, fixed observation/entry/exit windows |
| H-EVENT: scheduled-event aftermath | Historically available scheduled timestamps plus post-event intraday evidence | One event class; react to an observed initial move, not an unobserved surprise |

Implement H-DAY first. For H-SESSION/H-EVENT, U2 may rank **researchability** from daily descriptors and prepare cohort requests. It cannot report directional efficacy until U3 supplies the relevant intraday evidence. Do not demand that a daily continuation score prove a session/event hypothesis before acquiring its data.

**ADVISORY starting recipes:** H-DAY continuation uses the sign of the preceding 20-session risk-normalised change with a maximum five-session holding period; reversal uses the opposite sign of the preceding five-session change with a one-session holding period. Treat these as two separately named probes, not a permission to flip the sign after results. For H-SESSION, start with a cash-open/futures handoff and a fixed post-open window. For H-EVENT, start with one scheduled macro or commodity release class and a fixed post-announcement observation window followed by a later entry. The owner selects exact feasible recipes before their first result; no current claim of alpha is implied.

### 7.2 One compact experiment specification before each feedback batch

Record in ordinary JSON/YAML:

```text
hypothesis and economic rationale;
source/data identities and exposure limitations;
development quarters and untouched-by-U reserve exclusions;
past-only family eligibility and minimum coverage;
selection score, lookbacks, economic/cluster constraints and tie handling;
cohort size, anchor mapping, random seeds and matching rule;
exact signal, decision/entry/exit, no-trade and holding rules;
return/risk units, roll treatment and conservative cost scenarios;
comparison support, reductions and diagnostic/advancement interpretation;
configuration count and batch resource budget.
```

This is a reproducibility record, not another G0 or per-fit approval gate. Save the specification before calculating that batch's performance. Preparation, fixture tests and source work may proceed in parallel; any data-derived choices must respect the recorded chronology.

### 7.3 Selection and controls

First apply researchability filters: minimum preceding history/coverage, usable prices, documented sessions/rolls, liquidity evidence and feasible cost/risk measurement. Set thresholds from product/data semantics or development-only inspection and disclose them. A missing cost estimate is `COST_UNRESOLVED`, not zero cost; the market may remain in descriptive work but not pass an economic gate.

Then apply a small hypothesis-specific score and redundancy constraints. Starting choices are trend efficiency for continuation and past lag-one reversal tendency for reversal, conditional on researchability. Range-to-cost and liquidity form a simple comparator selector. Do not optimise a large weighted indicator score or rank on future hypothesis P&L.

Maintain these cohort comparisons:

- the original twenty anchor families wherever mapped and historically eligible;
- the hypothesis-score selected cohort;
- a simple liquidity/range-to-cost cohort without the hypothesis-specific score;
- stratified-random cohorts from the same eligible universe;
- nuisance-matched random cohorts, matching relevant economic group, liquidity/cost and session/history bands.

Match only on past-observable nuisance variables, not on the mechanism score whose value is being tested. Report both broad and matched controls: tight matching can intentionally remove volatility/liquidity effects and answers a different question from total selector value. Freeze bins, matching distance, replacement policy and canonical tie handling before scoring; failed matching is disclosed rather than silently relaxed after results.

For cheap daily evaluation, default to 100 fixed random draws per vintage. Average all draws for the primary random comparison and show their distribution; never select the weakest control. Within a cohort sample without replacement. Between selected/anchor/random cohorts overlap is legitimate, is reported and reduces acquisition work; do not force disjointness by excluding selected names from the null universe. A seed is an experiment input, not a result-selection knob.

For the primary nuisance-matched comparison, each draw contains one distinct control assigned to each selected-market slot using the predeclared matching rule. Thus every draw has the same size as that vintage's selected cohort, and the equally weighted matched-slot mean equals the reported matched-cohort score. Retain these assignments for contribution accounting. Infeasible complete matching makes the comparison unavailable under the existing support rule; do not invent a match or change weights after outcomes.

Daily anchor reporting uses all available original anchors plus size-matched views where needed. The U3 twelve-anchor subset is chosen by a recorded economic-diversity/canonical rule before outcomes, not by which original instruments looked strongest. Insufficient cohort size or absent groups produces a smaller explicitly labelled pilot or an ineligible vintage, never a fabricated member.

### 7.4 Feedback, not a one-shot research freeze

Start with one continuation and one reversal selector/probe pair plus their controls. An initial feedback batch should contain no more than eight substantive selector × hypothesis configurations; control draws are not additional searched configurations. This is an orchestration default, not a lifetime scientific-attempt ceiling.

After a development batch, the orchestrator may authorise a small next batch within the agreed envelope when a stated observation motivates it. Retain the earlier results, explain the changed hypothesis, record the expanded search count and keep the reserve closed. Correctness repairs rerun only affected work under new output IDs, without preserving bad code as an authority. No new operator approval is needed for an ordinary reversible development iteration within the envelope; new sources/spending, reserve access, product expansion and prospective claims still require it.

Do not confuse iterative learning with pre-registration. Only an unchanged later slate can be evaluated in the separate reserve/prospective stage. There is no automatic 'best historical Sharpe' promotion.

## 8. Payoff, selection metrics and tail checks — BINDING

### 8.1 Economic unit and support

For each market/hypothesis maintain a chronological position path with at most one active directional position, unless a later explicit hypothesis says otherwise. Initial probes use long, flat or short intent; no pyramiding or increasing risk after losses. No-trade decisions remain in the denominator. Five-day horizons must not be counted as independent full-size daily trades when they overlap.

For cross-market research comparison, use **risk-normalised hypothetical payoff**, not an assumed capital return:

```text
v[m,t] = positive trailing daily P&L standard deviation for one underlying unit,
         estimated from the preceding 60 valid sessions available before entry
z_gross = signed exact-product P&L / v[m,t]
z_net_scenario = (signed P&L - scenario costs) / v[m,t]
```

Record the multiplier, quote currency and positive minimum-scale rule. Missing/near-zero risk scale makes an opportunity unavailable; it must not create extreme exposure. For multi-day holdings, allocate mark-to-market changes to their actual sessions with the entry scale held fixed. Count commission, spread/slippage, financing/carry and roll changes once at the relevant physical change. Cost scenarios remain modelled, not observed executions. Money/product arithmetic respects the repository's `Decimal` boundary; normalised research arrays may use tested floating-point calculations.

This is a comparison of unit-risk research opportunities, not a feasible AUD portfolio. Fractional sizing, collateral, margin and causal currency conversion must be disclosed as unevaluated until an economic/portfolio experiment covers them. Do not clip bad outcomes or claim that a stop order bounds gap losses.

### 8.2 Fixed reducers for the first laboratory

Let `S[m,v,h]` be the mean session-level scenario-net risk-normalised payoff for market `m`, vintage `v` and hypothesis `h`, on its predetermined observable schedule. Include zero for an eligible session on which the rule is flat/no-trade. Missing required outcomes are unavailable evidence, not flat positions. Also report gross score, per-trade expectancy, number of opportunities and exposure separately.

```text
cohort_score[v,h] = equal-weighted mean of S[m,v,h] over recorded cohort members
random_score[v,h] = mean cohort_score across all predeclared random draws
delta_control[v,h] = selected cohort_score - comparator cohort_score
overall_delta[h] = equal-weighted mean of paired vintage deltas
```

Do not pool all instrument rows or trades across quarters: that would give large/high-activity cohorts disproportionate weight. Report group-balanced sensitivity separately; it does not replace the primary reducer after results.

Within a market, all selector/control uses share one outcome/position calculation. Missing post-selection data cannot improve a cohort by dropping its difficult member and reweighting the survivors. Retain the selected membership and missing fraction; a vintage without the predeclared complete required comparison support is unscorable for the primary comparison. Report matched-complete sensitivity and the excluded-vintage account separately. Distinguish genuine post-listing termination with settled positions from unexplained data loss.

**Concentration must decompose the reported overall effect.** For the primary selected-minus-nuisance-matched comparison, let `V` be the paired scorable vintage set under the support rule, `K[v]` the recorded selected-cohort size, `D[v]` the number of predeclared matched draws, and `match[m,v,d]` the assigned control for selected market `m` in draw `d`. For each fixed hypothesis and cost scenario:

```text
d[m,v,h] = S[m,v,h] - mean_d S[match[m,v,d],v,h]
c[m,v,h] = d[m,v,h] / (len(V) * K[v])

C_market[m,h] = sum_v c[m,v,h]
C_quarter[v,h] = sum_m c[m,v,h] = delta_matched[v,h] / len(V)

sum_m C_market[m,h] = sum_v C_quarter[v,h] = overall_delta_matched[h]

P_market[m,h] = max(0, C_market[m,h])
P_quarter[v,h] = max(0, C_quarter[v,h])
best_market_share = max_m P_market[m,h] / sum_m P_market[m,h]
best_quarter_share = max_v P_quarter[v,h] / sum_v P_quarter[v,h]
```

The draw mean uses all `D[v]` draws. A non-selected market has zero contribution for that vintage; this is allocation bookkeeping, not permission to replace a missing outcome with zero. Sum signed weighted contributions before clipping, so losses offset gains for the same market and within the same quarter. The market attribution assigns each selected slot's uplift over its matched controls to that selected market; it is not the market's standalone P&L. Preserve selection frequency and variable cohort sizes. Mean contribution per appearance may be reported separately, but must not determine these concentration gates.

For either share, a zero positive sum is reported as no positive contribution, with share `1`. With no scorable vintage, report `INSUFFICIENT_EVIDENCE` rather than a numerical effect or share. Reconcile both signed contribution totals to the reported matched-control overall delta. These market/quarter concentration gates concern that same matched comparison, not a different anchor or random-control effect.

Required unequal-appearance fixture: across eight three-market cohorts, A has matched delta `+3` in every quarter and each quarter's two other, non-repeating markets has `+1`. The overall delta is `5/3`; A contributes `1`, so its positive share is `0.60`, and each quarter's share is `0.125`. The old appearance-mean share `3/19` (about `0.158`) must not pass as concentration. Also test unequal cohort sizes and signed cancellation before clipping.

Keep signed effects and economic-group results visible. Also report leave-one-market and leave-one-quarter effects where cheap. Descriptive uncertainty resamples whole quarters with cross-market dependence retained, not individual overlapping bars. Random-draw ranges and bootstrap intervals are not multiplicity-adjusted proof of alpha.

### 8.3 Decision meaning

The first daily result must answer separately whether the selector beats its controls and whether the selected hypothesis has positive scenario-net payoff. A selector can find 'less bad' markets without finding alpha. Positive selected payoff with no incremental control advantage supports investigating the broad hypothesis, not the selector.

**ADVISORY advancement rule to record before scoring:** positive mean and median selected-minus-matched-control quarterly deltas, positive deltas in a majority of at least eight evaluable development quarters spanning multiple regimes, positive selected scenario-net payoff at base and doubled variable friction, contributions across at least three economic groups, and no single market or quarter supplying more than half of positive incremental contribution under section 8.2's matched-control attribution. Failure deprioritises that candidate; fewer observations gives `INSUFFICIENT_EVIDENCE`, not a fabricated negative or positive.

Those thresholds are triage choices, not universal scientific constants. The owner may propose a better-supported rule before results, recording the reason. They do not block U3 acquisition whose explicit purpose is testing a session/event mechanism daily data cannot score. Such a canary needs its own stated information-value rationale, not a made-up daily alpha pass.

For every plausible candidate report worst session/quarter, drawdown, adverse gaps, expected shortfall, exposure/turnover, crisis or large-move dependence and sensitivity to wider spreads/late exits. Daily bars cannot prove an intraday stop fill, depth, impact capacity or an ability to exit a limit move. Mark those as unresolved; do not remove stress dates. Capacity suitability for a small operator is a later measured question, not an inference from high volume or small notional alone.

## 9. U3 — bounded intraday canaries

U3 is not 'download minute bars for 150 markets'. For the first canary choose one hypothesis, one selection vintage and at most the default three twelve-market cohorts, with no more than 36 distinct families after deduplication. Choose the vintage and cohort rules before viewing the candidate intraday outcomes. Use the smallest resolution that can answer the mechanism; five- or thirty-minute bars are acceptable where adequate, and one-minute bars require a timing reason.

A later batch may add a few non-overlapping vintages under its acquisition envelope, rather than multiplying all markets × years × hypotheses at once. Record historical exposure and acquisition feasibility before outcomes. Source availability, not attractive past performance, determines whether a vintage can be acquired; report selection bias if unavailable contracts exclude markets.

Each canary specification fixes exact products/contracts, sessions and DST handling, signal-observation window, next permitted entry, holding/exit/no-trade rule, costs, support and cohort reducers. Event work uses calendar times known before the event and only observations available after it; no final revised surprise series or same-bar high/low ordering. A scheduled-event calendar unavailable point in time is a disclosed limitation, not permission to reconstruct advance knowledge from the realised market move.

Reuse simple rules/linear probes first. No GNN/LSTM search, broad indicator sweep, R3 optimiser integration or new live service is part of U3. Inspect sample rows through signal, entry and exit before expanding acquisition. A properly explained negative first canary is complete feedback; there is no obligation to run the entire roadmap.

Deliver the cohort request, actual acquisition account, gross/scenario-net results, paired selector effects, stress/coverage limitations and either a concrete next cheap test or a stop recommendation. Native IG/IBKR capture remains unchanged.

## 10. U4 and historical-reserve evaluation — ROADMAP ONLY

This document authorises neither operation. If U2/U3 justify it, propose a compact separate protocol that fixes the selected hypothesis/selector slate, exact sources, eligible-universe rule, costs/risk units, folds, support, uncertainty/multiplicity treatment and thresholds before access.

Historical reserve evaluation uses the eight previously excluded quarters with all prior exposure disclosed. Once a quarter is evaluated, it cannot remain an unused reserve for a revised selector. A failure is retained; do not keep spending the reserve to tune the same idea.

Prospective U4 creates dated quarterly selections before the corresponding future outcomes. It needs an exact market-data acquisition/retention plan, no-trade and terminal-horizon rules, executable-evidence requirements and operator opening/execution authority. Source-transfer testing compares daily-discovery nominations with actual target-source observations; an external discovery series does not become IG/IBKR execution evidence by mapping its ticker.

Neither a U2/U3 recommendation nor a reserve result automatically activates the existing native protocol, changes collectors or authorises R5/R6. Preserve those separate decisions.

## 11. Engineering, evidence and delivery — BINDING OUTCOMES

### Keep the feedback path small

Use a new experiments namespace, a simple daily Parquet/Arrow panel, an ordinary configuration and one append-only run register. Retain source snapshot references/checksums, code commit, exposure/date partition, selector/hypothesis configuration, attempted variants, cohort selections, metrics and a concise findings document. Hash or authenticate consumed inputs once at the useful boundary; do not replay R2/R4 or rebuild their receipt systems.

One panel should serve descriptors, selectors and control cohorts. Compute market/hypothesis/vintage outcomes once; random cohorts change aggregation, not model fits or acquisition. Cache only measured repeated work. Keep raw licensed data and large outputs out of Git. Do not create a new database, service, scheduler, generic source framework or durable solver interface for this lab.

For scale intuition, 150 families × 20 years × roughly 260 sessions is about 780,000 family-session rows before contract-level expansion; that is a planning estimate, not observed coverage. Start CPU-first. Profile actual loading, joins, roll construction and repeated scoring before considering GPU work.

The orchestrator sets a modest first-batch resource envelope from a measured representative slice. Preferred defaults are at most 20 GB of new daily-lab output and a first selector batch designed for minutes rather than overnight execution; these are planning targets, not a promise or a hard-coded runtime kill switch. Intraday acquisition has its separate byte/cost cap. If producing one feedback table requires a new performance-remediation programme, reduce the batch or return a specific feasibility limitation before expanding infrastructure. Do not treat a small run as proof of unmeasured full-scale cost.

Use MAP's current LONGRUN rules for actual long jobs. Resource sampling is not a reason for repeated model wake-ups. Ordinary failures remain same-lineage diagnosis/repair; preserve the old run ID and report material changes. Existing operator limits and protected evidence boundaries still apply.

### Validation that can change the answer

Focused tests must discriminate:

- listing/delisting and as-of source availability, including delayed settlements/open interest;
- changing future rows cannot change earlier eligibility, rankings, clusters or cohorts;
- reserve/raw-archive inputs are absent or denied through the actual research role's tools, and loaders separately reject reserve requests before decoding;
- no future roll selection, fabricated roll return or logarithm of a non-positive price;
- signal precedes entry; no overlapping full-size five-day trades; weekend/delivery exits are explicit;
- zero/no-trade, known-cost and planted-selector fixtures recover expected results;
- controls use the same opportunity calculator, and overlap/deduplication does not change scores;
- unequal instrument/quarter row counts cannot silently change the declared reductions;
- unequal market appearances/cohort sizes and signed cancellation preserve section 8.2's contribution reconciliation and market/quarter concentration;
- unavailable outcomes, failed matching and negative trials remain visible;
- output sizes/work counts demonstrate reuse rather than repeated scans/fits.

A synthetic no-edge case should show no systematic selector advantage across the declared random draws; a planted past-state/future-opportunity case should expose the planted advantage without future access. These are software checks, not empirical alpha selection.

Use focused tests, formatting, Ruff and namespace-local strict typing. Do not run the full application suite for every experimental batch or helper. The complete gate remains required when changing active application/schema/shared dependency policy or at a separately governing release boundary under `docs/DEVELOPMENT.md`. One independent review of each meaningful delivered tranche checks causal selection, metrics, search accounting and conclusions; the reviewer must not have modified the candidate being reviewed and need not replay every trial. No mandatory per-function handovers.

### Suggested deliverables — ADVISORY PATHS

```text
experiments/universe_discovery/
tests/experiments/universe_discovery/
docs/U0_SOURCE_READINESS.md
docs/U_LANE_FINDINGS.md
```

Within one named ignored output root retain `catalogue`, `source_notes`, `data_split`, `experiment_specs`, `selection_register`, `run_register`, compact metrics and reports. Use repository-conforming agent scratch paths; keep research evidence separate from disposable scratch. These are content needs, not mandatory packages or independent evidence contracts.

## 12. Agent-ready launch and completion

### Activation record — BINDING CONTENT

At explicit operator activation, record the approved plan commit/revision and approval reference, working code base, permitted local input identities and date ranges, the section 5 access arrangement, and the required delivery endpoint (for example, branch/PR or local committed handoff). Use the existing task/run record; do not create another planning layer, G0 or receipt contract. If no empirical inputs are yet approved, record `NONE_APPROVED` and begin only U0 public documentation and fixture work. Add concrete input/acquisition permissions when granted rather than allowing MAP to infer them. The unspecified numerical acquisition/spend budget remains a U0 operator decision.

### Suggested execution seams — ADVISORY

**U0 owner:** inspect available local sources and public catalogue/documentation, build the minimal family/source inventory, supply a source/permission decision and a usable sample or fixture. Do not purchase data or touch collectors.

**U1/U2 owner:** implement the daily panel, reserve exclusion, past-only descriptors, cohort controls and one complete H-DAY experiment. Work on fixtures in parallel with U0; empirical work depends on approved inputs and the section 5 access precondition. Publish the first useful table before expanding universe coverage or adding another mechanism.

**U3 owner:** only when a canary is chosen and its acquisition is authorised, implement the hypothesis-specific intraday view and evaluate the recorded selected/control cohorts. Do not build U4.

**Synthesis:** consume the compact records, distinguish data feasibility, selector value and hypothesis value, and identify what would change the next decision. The orchestrator may combine implementation and synthesis or omit a separate synthesis role when a single owner is more efficient.

**Independent reviewer:** review the exact delivered candidate and findings under section 11. This reviewer must not have modified that candidate. Combining implementation/synthesis does not remove the independent-review requirement, but does not require separate reviewers for every stage or helper.

Immediate acceptance of the first tranche requires:

1. A truthful catalogue/source readiness result, including point-in-time and cost limitations.
2. The development/reserve split and effective research-access boundary recorded before data-dependent agent work, with known exposure disclosed.
3. A useful daily atlas on actual approved data, or a precise data blocker with fixture code rather than invented observations.
4. At least one complete past-only selector/hypothesis comparison with all controls on approved data, or its explicit unavailable prerequisite.
5. Complete variant/failure accounting and the declared metric/tail/coverage summaries.
6. Focused checks and independent review appropriate to the delivered result.
7. A concrete next action: revise a development hypothesis, acquire a bounded canary, propose reserve/prospective authority, or stop that line.

Use simple findings categories such as `DATA_LIMITED`, `DESCRIPTIVE_ONLY`, `NO_SELECTION_VALUE`, `HYPOTHESIS_WITHOUT_SELECTION_VALUE`, `CANDIDATE_FOR_INTRADAY_TEST` and `CANDIDATE_FOR_SEPARATE_PROSPECTIVE_PROTOCOL`. They are recommendations, not promotions. A blocked source audit is a completed feasibility task, not a completed empirical experiment.

At acceptance update only the programme documents whose current claims changed. Do not merge automatically. Preserve PR #189's merged R4 closure, update only the pointers needed for U activation/current state, and preserve all prior source-specific conclusions.

## 13. References and feasibility notes

### Repository and supplied evidence

- `AGENTS.md`; `docs/EVIDENCE_GOVERNANCE.md`; `docs/ENGINEERING.md`; `docs/DEVELOPMENT.md`.
- `PLAN.md`; `docs/TRADING_RESEARCH.md`; `docs/R4_P0_FINDINGS.md`; `docs/R4_P1_FINDINGS.md`.
- `docs/R2_HISTORICAL_EXPLORATORY_FINDINGS.md` and the supplied R2 scientific report: context for the original narrow universe/horizon, not authority for new source conclusions.
- `.codex/map/MAP_Orchestrator.md` and the current LONGRUN procedure when MAP is used.
- `docs/IBKR-HISTORICAL-ACQUISITION.md` and `ops/ibkr/README.md` only for an acquisition/operation actually authorised under those interfaces.

### External documentation checked 2026-09-09 — OBSERVED, NOT SOURCE SELECTION

IBKR's published historical-data limitations include expired futures older than two years after expiry and securities no longer trading. Do not assume the existing broker API supplies a survivor-free twenty-year contract archive. U0 must establish actual product coverage and access rather than extrapolating from the current twenty.

`https://ibkrcampus.com/docs/web-api/v1/endpoints/market-data/unavailable-historical-data`

CME documents different active/front continuous-series conventions and separately timed preliminary/final daily information. These are concrete reasons to inspect roll, price basis and availability semantics. They are not a recommendation to purchase CME data or a claim that one continuous series is execution-ready.

`https://www.cmegroup.com/market-data/cme-group-continuous-price-series.html`

`https://www.cmegroup.com/market-data/volume-open-interest.html`

`https://www.cmegroup.com/market-data/daily-bulletin.html`
