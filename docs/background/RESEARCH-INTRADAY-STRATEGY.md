# Intraday strategy research dossier

**Status:** initial deep survey; living research document  
**As at:** 2026-07-04  
**Scope:** public research relevant to liquid FX and equity-index exposures held for
seconds to hours  
**Purpose:** guide future research; not an implementation plan, investment recommendation
or profitability claim

## 1. Executive view

Public research supports a more restrained conclusion than either “intraday markets are
efficient” or “a good indicator is enough”.

There are persistent, interpretable intraday regularities in liquid markets. The best
documented include:

- momentum between separated parts of a trading session;
- reversal after some overnight, opening or order-flow shocks;
- strong time-of-day structure in volatility, volume, spread and information arrival;
- scheduled-event and cross-market lead-lag effects;
- short-horizon volatility persistence and commonality.

Some papers turn these regularities into attractive backtests. Much less public evidence
shows that the exact strategy survived independent replication, realistic bid/ask
execution, model selection, capacity constraints and a meaningful forward or live period.
That gap is the central fact of this survey.

The most promising near-term research direction for q-trad is therefore not a large
end-to-end prediction model. It is a disciplined comparison of simple signal families,
using the data foundation's bid, ask, midpoint, provenance, gap and replay semantics, with
volatility- and liquidity-aware risk controls. Complexity should be admitted only when it
beats those baselines under locked, time-ordered validation.

### Current judgements

| Question | Initial judgement | Confidence |
|---|---|---|
| Is intraday momentum real? | A recurring empirical regularity, especially across separated session windows and on high-information or high-volatility days. Tradability is product- and cost-specific. | Medium-high |
| Is intraday mean reversion real? | Yes as a conditional phenomenon after liquidity shocks, overreaction and asynchronous information transfer; unsafe as an unconditional rule. | Medium |
| Are “regimes” useful? | Observable market state is useful for diagnosis and risk scaling. Evidence for hard switching among signal strategies is much weaker. | Medium-high |
| Is ML now the default? | No. ML is increasingly useful for volatility, nonlinear interactions and representation learning, but simple linear, regularised and tree baselines remain difficult to beat economically. | High |
| Are foundation models or RL state of the art for deployable alpha? | They are active research frontiers, not established intraday alpha engines. | High |
| Where is public success reported? | Mostly in academic/preprint backtests and non-audited practitioner records. Strategy-specific, independently verified live records are rare. | High |
| What deserves priority? | Validation, costs, product/session semantics, simple momentum/reversion/event baselines, then state-aware sizing. | High |

## 2. Scope and definitions

### Included

- FX spot, currency futures and FX CFDs where product differences are explicit.
- Equity-index futures, broad index ETFs and index CFDs.
- Holding periods from roughly one second to one trading day, with the centre of gravity
  at one minute to several hours.
- Directional return signals, relative-value signals, volatility forecasts used for risk,
  event filters, execution-aware decisions and market-state observations.
- Public peer-reviewed research, working papers, preprints, regulator evidence, public
  code/replication and clearly labelled practitioner reports.

### Excluded

- Microsecond latency arbitrage, co-location races and queue-position market making.
- Options strategies as a primary target. Options and 0DTE data remain relevant as
  possible state variables for index trading.
- Daily-to-monthly carry, value and trend strategies except as context or as an overnight
  input to an intraday decision.
- Unverifiable screenshots, testimonials and marketing claims as evidence of success.
- Any assumption that an ETF, future, spot pair and CFD are interchangeable.

### Important product distinctions

| Product | Research implications |
|---|---|
| Spot FX | Decentralised venue, no single complete tape, 24-hour week, provider-specific spread and liquidity. |
| Currency future | Centralised trades and book, expiry and roll, exchange session and margin. |
| Index future | Nearly continuous session, centralised liquidity, basis/roll and distinct regular-trading-hours behaviour. |
| ETF | Cash-session opening/closing auctions, creation/redemption, distributions, borrow and short-sale details. |
| CFD | Broker-derived price, provider spread, financing, possible dealing intervention and no portable consolidated order flow. |

A result is transferable only after translating its economic mechanism, trading hours,
price basis, costs and execution path.

## 3. Evidence protocol

### 3.1 Evidence dimensions

Do not collapse evidence into one star rating. Record five dimensions:

1. **Publication:** peer reviewed, institutional working paper, preprint or practitioner.
2. **Validation:** in-sample, single holdout, rolling/expanding walk-forward, purged
   cross-validation or genuinely untouched replication period.
3. **Economic realism:** commissions, observed or conservative spread, slippage,
   financing, roll, borrow, latency and capacity.
4. **Reproducibility:** rules, data lineage, code and independent replication.
5. **Deployment:** backtest, simulated forward, broker paper, small live or audited live.

### 3.2 Minimum admissibility for a q-trad candidate

A candidate may enter the experiment backlog only if it has:

- a causal or behavioural mechanism that can be stated without indicator folklore;
- features available before the decision timestamp;
- exact session, timezone, DST and holiday rules;
- an executable bid/ask interpretation;
- a declared parameter and model trial count;
- a simple null or baseline;
- a falsification condition;
- enough observations after event overlap and serial dependence are considered.

### 3.3 Validation standard

The preferred funnel is:

1. lock the hypothesis and primary metric;
2. build a naive, no-skill and simple economic baseline;
3. use expanding or rolling time-ordered splits;
4. purge overlapping label horizons and embargo adjacent observations where necessary;
5. calculate results at bid/ask with a latency/slippage stress grid;
6. preserve all attempted variants for multiple-testing accounting;
7. examine parameter surfaces, not only the optimum;
8. report subperiod, instrument, session and market-state stability;
9. use block/bootstrap uncertainty suitable for dependent returns;
10. reserve a final untouched period, then run shadow forward observation.

The Deflated Sharpe Ratio and Probability of Backtest Overfitting are useful diagnostics,
not magic certification. A 2024 controlled comparison found combinatorial purged
cross-validation reduced overfitting relative to ordinary approaches [S02], while the
original DSR work addresses selection bias and non-normal returns [S01]. Neither repairs
wrong timestamps, incomplete costs, a biased universe or a hidden research history.

### 3.4 Required outcome reporting

At minimum:

- gross and net return;
- turnover and number of decisions/trades;
- mean and median return per trade in spread units;
- annualised volatility, Sharpe and Sortino, with uncertainty;
- maximum drawdown, time under water and worst day;
- expected shortfall and gap/event losses;
- long/short and instrument attribution;
- exposure by session and state;
- rejected, missed and stale-data decisions;
- sensitivity to one- and two-spread adverse execution;
- performance with one-bar decision and execution delay;
- minimum track record length and trial-adjusted significance.

Accuracy, AUC, MSE or QLIKE alone is not trading evidence.

## 4. Research landscape

### 4.1 Intraday momentum and breakout

#### What is supported

The strongest broad result is not “the last bar predicts the next bar”. It is that a
return in one meaningful session window can predict a later, separated window. A large
peer-reviewed futures study covers equity indices, bonds, commodities and currencies over
decades and reports widespread open-to-close intraday momentum, with hedging demand as a
candidate mechanism [S05]. Related work finds the effect in VIX futures and conditions it
on option-market-maker gamma and market participation [S06].

The state dependence matters:

- effects tend to strengthen with volatility, jumps, volume or information arrival;
- the opening or overnight component can matter more than arbitrary recent bars;
- late-session flows may reflect rebalancing and hedging rather than the same mechanism as
  morning information;
- trading hours must correspond to the market's economic session.

Recent public SPY work reports substantial net backtest performance from an abnormal-move
filter and trailing stop [S07]. Opening-range-breakout research reports strong results in
news-active US equities [S08]. These are interesting, precisely specified candidates, but
they remain working-paper evidence with author-controlled model selection and no
independently audited live strategy record.

#### What should be tested

- First 15/30/60-minute return predicting the last 15/30/60 minutes.
- Overnight-to-open and open-to-later-session continuation separately.
- Breakout relative to a predeclared session range, not a continuously optimised lookback.
- Volatility-normalised breakout magnitude.
- Continuation conditional on realised volatility, volume proxy, spread, gap and scheduled
  event.
- Time exit versus trailing exit; the exit often contributes more degrees of freedom than
  the entry.

#### Main failure modes

- selecting the best opening range after seeing the full sample;
- entering at a breakout price that was not actually executable;
- treating a bar high/low as an ordered tick path;
- letting leveraged ETF compounding masquerade as signal alpha;
- ignoring false-breakout spread and slippage;
- transferring a cash-session effect to a nearly 24-hour future or CFD;
- relying on a small number of crisis or trend days for most profits.

**Research priority:** P1.

### 4.2 Mean reversion, reversal and liquidity provision

#### What is supported

Short-horizon reversal can arise from temporary price pressure, liquidity provision,
inventory adjustment and asynchronous market opening. It is therefore most plausible
after a defined shock and while liquidity remains available, not whenever an oscillator
is “overbought”.

Research documents overnight-to-intraday reversal across asset classes [S09], residual
intraday reversal after factor adjustment [S10], and intraday/overnight lead-lag across
non-overlapping markets [S11]. FX evidence is mixed and market-specific: RUB/USD research
links intraday momentum, rather than reversal, to liquidity providers avoiding overnight
risk and stresses that explicit trading hours matter [S12].

Mean reversion has a negatively skewed structural risk: frequent small gains can be
overwhelmed when a liquidity shock becomes information and the price does not return.
This makes state, event and stop design part of the hypothesis rather than an afterthought.

#### What should be tested

- Deviation from a session anchor or rolling fair-price estimate, scaled by ex-ante
  volatility and spread.
- Reversal following an opening gap, extreme bar or cross-market dislocation.
- Residual rather than raw-price reversal, using contemporaneously available index/FX
  factors.
- Separate “liquidity shock” and “information shock” proxies.
- Entry inhibition around scheduled macro events and during spread expansion.
- Time-to-reversion survival analysis instead of only binary next-bar direction.

#### Main failure modes

- an anchor calculated with later volume or prices;
- averaging down or martingale sizing;
- fixed stops/targets whose apparent edge is bar-path ambiguity;
- treating a persistent trend as a larger deviation and increasing risk;
- omitting the adverse-selection cost of providing liquidity;
- using midpoint fills during a spread or volatility shock.

**Research priority:** P1, but only with hard risk and event controls.

### 4.3 Intraday seasonality and session structure

FX volatility, trading activity, spread and return distributions vary strongly with the
Asian, London and New York sessions and their overlaps. Research using electronic-broker
data established pronounced time-of-day activity patterns [S13]. Later multi-currency
work reports domestic-session depreciation and US-session appreciation patterns related
to realised volatility [S14].

For indices, the open, cash/futures overlap, scheduled US releases and close are
structurally distinct. A “minute of day” feature is not merely a statistical seasonal:
it represents changing participants, auction mechanisms, information and hedging demand.

Seasonality is likely more valuable as:

- a volatility/spread baseline;
- a minimum-edge threshold;
- a session-specific strategy parameter fixed in advance;
- an exposure and risk schedule;
- a control variable preventing spurious signal discovery.

Raw average return by clock bucket is fragile because a few events, timezone mistakes or
changes in daylight-saving alignment can dominate it.

**Research priority:** P0 as a control; P1 as a tightly specified signal.

### 4.4 Scheduled events and news

Scheduled macro releases alter both volatility and the way information is incorporated.
FX work finds that private information in trades continues to affect efficient prices
after public announcements, consistent with heterogeneous interpretation [S15]. Research
on retail EUR/USD positioning finds no strong pre-announcement adjustment and contrary
post-surprise trading in aggregate retail flow [S16].

There are four different candidate strategies:

1. predict the announcement surprise;
2. react to the released surprise faster than the market;
3. trade delayed interpretation/continuation;
4. avoid or reduce risk because execution distributions become hostile.

Only the third and fourth are plausible for q-trad without specialised low-latency news
infrastructure. Event time must be sourced independently and revisions must not overwrite
the value first released.

Potential events include CPI, labour data, central-bank decisions/press conferences,
PMIs, GDP and market-specific opens/closes. Each currency pair has two economies and USD
releases can affect every instrument in the initial universe.

**Research priority:** P1 for risk avoidance; P2 for delayed reaction.

### 4.5 Cross-market lead-lag and relative value

Non-overlapping market hours and asynchronous price discovery create more defensible
lead-lag hypotheses than generic indicator combinations. Recent work studies thresholded
overnight comovement between SPY and FXI [S11], while 2026 Nikkei futures research reports
both opening reversal and late-session continuation related to the prior US session
[S17].

For q-trad, candidate relations include:

- US index movement into the next Australia/UK cash session;
- Australia 200 and AUD/USD around Australian information;
- FTSE 100 and GBP/USD around UK releases;
- US 500 and USD pairs around US releases;
- equity-index common shocks versus currency-specific residuals.

Correlation is not a signal by itself. The research question is whether the leader's move
contains information not yet incorporated in the follower after accounting for overlap,
spread and common news.

**Research priority:** P2.

### 4.6 Order flow, liquidity and market microstructure

Order-book and order-flow models can forecast very short-horizon price changes, but recent
research gives an important warning: high forecasting accuracy need not yield actionable
signals after execution [S18]. A systematic 2024 analysis documents short-term
order-book-driven predictability [S19], while FX research supports continued information
in order flow around announcements [S15].

This area straddles the excluded HFT boundary. It remains in scope only when:

- the feature is aggregated over seconds or minutes;
- the decision does not depend on queue position or sub-millisecond latency;
- the forecast horizon survives a realistic provider and application delay;
- full bid/ask execution is modelled;
- the data source is portable to the intended product.

IG CFD top-of-book updates cannot reproduce central-limit-order-book imbalance. Futures
book research may still identify state variables, but it cannot be silently transplanted
to a CFD stream.

Interesting non-HFT features include quote intensity, spread change, signed price-change
imbalance, duration between updates, short-window realised volatility and disagreement
between price movement and activity. They should initially be tested as filters and risk
features, not standalone direction forecasts.

**Research priority:** P2 with current data; revisit centralised depth data later.

### 4.7 Volatility forecasting and volatility timing

Volatility is more forecastable than return direction and is directly useful without
claiming directional alpha. Recent work includes:

- graph models using spot volatility, co-volatility and volatility-of-volatility [S20];
- high-frequency stochastic-volatility models for E-mini S&P 500 futures [S21];
- duration-based estimators that capture immediate announcement effects [S22];
- simple multiple-equation intraday models that outperform LightGBM and LSTM in a 2026
  study [S23].

The literature does not say that complexity always wins. A 2024 ten-index comparison
finds no general nonlinear-ML advantage over linear volatility models and reports better
economic value from simpler specifications at some horizons [S24]. Another study finds
short-run ML predictability without economic gains over the benchmark [S25].

Volatility forecasts can improve:

- position sizing;
- entry thresholds measured in expected movement rather than points;
- spread/cost hurdle estimates;
- stop and maximum-holding-time design;
- portfolio gross exposure;
- event and kill-switch logic.

They should be judged on calibration, QLIKE and downstream risk outcomes, not only MSE.

**Research priority:** P1 and likely the best first use of ML.

### 4.8 Classical ML, deep learning and ensembles

The direction of travel is away from “predict the next close with an LSTM” and toward:

- cross-instrument training;
- probabilistic forecasts and uncertainty;
- multi-horizon outputs;
- representation learning;
- cost-aware labels and objectives;
- online monitoring for distribution drift;
- using ML as a filter, forecaster or sizing component inside an explicit strategy.

Recent systematic reviews still identify noise, overfitting and interpretability as
central obstacles [S26]. Research design choices can dominate the named model [S27].
Pooling/winsorising forecast ensembles is partly motivated by the catastrophic errors of
otherwise strong ML models [S28].

For tabular intraday features and q-trad's initially small universe, regularised linear
models and gradient-boosted trees are stronger baselines than a deep network. A deep
model has to justify its data appetite, retraining policy and additional selection space.

**Research priority:** P1 for regularised/tree baselines; P2 for deep models.

### 4.9 Reinforcement learning

RL is attractive because trading is sequential and actions affect exposure and costs.
It also expands the opportunity to overfit through environment design, reward shaping,
state construction, simulator assumptions, seeds and hyperparameters.

Recent work demonstrates that RL can recover an analytical solution in a controlled
commodity model with costs [S29]. That is evidence that RL can optimise a well-specified
control problem, not evidence that it discovers robust alpha. Intraday studies reporting
profitability often use one synthetic execution environment and a limited cost model.

RL becomes worth testing only when:

- the predictive signal is already validated;
- action constraints and risk limits are explicit;
- the simulator has passed fill/replay calibration;
- simple threshold/control policies are credible baselines;
- many seeds and environment perturbations are reported;
- evaluation is on untouched chronological periods.

The plausible future use is execution or constrained sizing, not first-line signal
discovery.

**Research priority:** P3.

### 4.10 Time-series foundation models

Foundation models are coming into favour rapidly. They promise transferable
representations, zero/few-shot forecasting and shared learning across instruments and
frequencies [S30]. Finance-specific evaluation is still immature. Recent benchmarks are
already mixed: some conference research reports improved mock-trading results after
financial fine-tuning [S31], while 2026 evaluations find gains over random walk small and
sparse [S32].

Risks are especially acute in finance:

- unknown financial data in pretraining and benchmark contamination;
- target scaling and distribution shift;
- point-forecast objectives disconnected from net return;
- enormous implicit model-selection histories;
- unstable sign of tiny return forecasts;
- compute and operational complexity without a durable edge.

Use them first for volatility, anomaly detection, feature embeddings or transfer-learning
experiments. Do not make zero-shot return direction a core strategy assumption.

**Research priority:** P3, monitored because the field is moving quickly.

## 5. Are regimes useful?

### 5.1 The word hides several different ideas

A regime can mean:

- a descriptive volatility bucket;
- a trend/range label;
- a liquidity or spread state;
- a scheduled-event state;
- a latent statistical distribution;
- a structural break;
- a state in which a particular strategy happens to have done well.

The last definition is circular unless the state is formed without the strategy's future
returns. A 2026 review finds substantial definitional fragmentation across regime research
and weak convergence on validation [S33].

### 5.2 Where market state is genuinely useful

1. **Measurement:** compare like with like and reveal that aggregate performance comes
   from a narrow environment.
2. **Risk scaling:** reduce exposure when volatility, spread, uncertainty or correlation
   rises.
3. **Eligibility:** do not run a liquidity-provision/mean-reversion hypothesis during a
   scheduled information shock.
4. **Monitoring:** identify distribution drift or a break in intraday volatility shape.
5. **Research stratification:** test whether a mechanism behaves as predicted.

Recent jump-model research finds out-of-sample downside-risk benefits from reducing
equity exposure in unfavourable states, with costs and trading delays included [S34].
Online change-point methods are also increasingly applied to order flow and financial
systems [S35].

### 5.3 Where regimes are most dangerous

- fitting states on the full sample;
- using smoothed HMM state probabilities that incorporate future observations;
- naming clusters “bull”, “bear” or “range” after seeing their returns;
- choosing the state count and features from strategy P&L;
- hard switching at noisy boundaries;
- training a different high-dimensional strategy in each small state;
- assuming a latent state has stable economic meaning across refits;
- ignoring transition latency and extra turnover.

### 5.4 Recommended comparison

Every regime-aware experiment should compare:

| Level | Model | Permitted initial use |
|---|---|---|
| R0 | No state | Required baseline |
| R1 | Time/session and scheduled-event flags | Control, eligibility |
| R2 | Rolling ex-ante volatility and spread buckets | Annotation, risk scaling |
| R3 | Continuous trend, volatility, liquidity and uncertainty scores | Soft weighting |
| R4 | Online change-point probability | Monitoring, possible de-risking |
| R5 | Filtered HMM/jump/cluster state | Research challenger |
| R6 | Hard strategy routing by latent state | Deferred until all lower levels fail |

Evaluate four uses separately: annotation, filter, risk sizing and strategy switching. A
state model that improves a confusion matrix but not net risk-adjusted outcomes has not
earned control authority.

### 5.5 Working conclusion

“Regime” is interesting when it means a versioned, real-time market-state observation
with uncertainty and a precise purpose. It is mostly irrelevant—or harmful—when it is a
retrospective story attached to clusters. For q-trad, the first market-state model should
annotate and scale risk. Hard switching should remain a challenger that must beat a
continuous no-state/simple-state baseline.

## 6. Risk management and adaptation

Risk management cannot rescue negative expectancy, but it determines whether a small
edge is observable and survivable.

### 6.1 Volatility-normalised exposure

Size exposure inversely to a robust ex-ante volatility estimate, subject to:

- minimum and maximum volatility floors/caps;
- a hard leverage/notional cap;
- a limit on how quickly size can rise after volatility falls;
- immediate or faster de-risking after a volatility shock;
- separate estimates by instrument and session;
- portfolio correlation and shared USD/index exposure.

Volatility targeting may partly embed trend exposure in equities because falling markets
and rising volatility coincide. Recent work argues much of its apparent equity alpha is
explained by that interaction and does not generalise the same way to currencies,
commodities or fixed income [S36]. Treat it as risk control, not free alpha.

### 6.2 Stops and exits

| Control | Useful when | Principal danger |
|---|---|---|
| Price stop | Invalidating level is economically meaningful | Noise-triggered adverse selection and bar ambiguity |
| Volatility stop | Expected movement varies materially | Expands risk during high volatility unless size shrinks first |
| Trailing stop | Strategy seeks positively skewed continuation | Adds path-dependent parameters and can truncate noisy trends |
| Time stop | Hypothesis has a defined information horizon | Can crystallise a temporary loss just before expected reversion |
| Signal exit | Signal is calibrated and changes slowly enough | Churn around zero and repeated spread payment |
| Daily loss stop | Limits operational/behavioural runaway | Creates path dependency and can switch off before recovery |

Stops must be simulated from ordered quotes or conservative intrabar assumptions. A bar's
high and low do not reveal which occurred first.

### 6.3 Portfolio and concentration controls

The seven-instrument universe contains hidden common exposures:

- EUR/USD, GBP/USD, AUD/USD and USD/JPY all carry USD risk with different sign;
- Australia 200, FTSE 100 and US 500 share global equity risk;
- macro events can move both groups together;
- volatility correlations rise during stress.

Research should report gross, net, currency-factor and global-equity-factor exposure.
Limits should act before strategy-level stops.

### 6.4 Liquidity and cost adaptation

Required controls include:

- minimum quoted spread and freshness quality;
- entry hurdle as a multiple of round-trip expected cost;
- size reduction or rejection during spread expansion;
- no midpoint assumption for executable outcomes;
- maximum participation/capacity assumptions for futures/ETFs;
- CFD financing for any position that can cross the provider's cut-off;
- rollover and expiry handling for futures comparisons.

FX research using full electronic books shows capacity falls as trade size and frequency
increase and recommends sweep-to-fill cost modelling [S37].

### 6.5 Tail and operational controls

- maximum loss per decision, instrument, correlated group and day;
- expected-shortfall and stress-scenario limits;
- stale-data, gap, spread and clock-quality halts;
- cool-down after repeated rejects or exceptional losses;
- no automatic increase in size to recover drawdown;
- strategy health based on behaviour drift as well as P&L;
- explicit UNKNOWN market state that reduces, never increases, authority.

Full Kelly sizing is inappropriate for an uncertain, selected and non-stationary edge.
Even fractional Kelly requires a credible distribution estimate and hard caps; fixed
volatility/loss budgets are the more defensible baseline.

## 7. What is coming into and falling out of favour?

These classifications describe public research direction and evidential momentum, not
industry secrets.

### 7.1 Coming into favour

| Theme | Why it is rising | Assessment |
|---|---|---|
| Intraday volatility and vol-of-vol forecasting | Better data; direct use in sizing, options and risk | Substantive |
| Cross-asset/multivariate models | Shared shocks and transfer across instruments | Substantive if leakage is controlled |
| Online change-point and drift monitoring | Static models fail under distribution change | Substantive for monitoring |
| Probabilistic forecasts and uncertainty | Decisions need confidence and tails, not only point estimates | Substantive |
| Cost-aware/end-to-end evaluation | Recognition that predictive accuracy is not economic value | Essential |
| Interpretable state-aware risk | Easier to validate than wholesale strategy switching | Promising |
| Time-series foundation models | Transfer learning and AI research momentum | Early and partly hype-driven |
| 0DTE/implied-volatility state variables | Rapid growth in same-day index-option activity | Interesting, data-heavy |
| Open benchmarks and reproducibility | Reaction to incomparable model claims | Underdeveloped but valuable |

### 7.2 Losing favour or requiring a higher burden of proof

| Theme | Why |
|---|---|
| Standalone technical indicators | Most are transformations of the same price history, with large hidden selection space and weak mechanism. |
| One train/test split | Unstable under non-stationarity and repeated research use of the holdout. |
| Forecast accuracy as the result | Small statistical gains often disappear after thresholding, turnover and costs. |
| LSTM as the automatic default | Linear, regularised, boosted and newer sequence models frequently match or beat it with less complexity. |
| Hard, retrospectively labelled regimes | Boundary noise, look-ahead and state-name storytelling. |
| Midpoint-only strategy backtests | Particularly misleading at short horizons and during the states a strategy wants to exploit. |
| Optimising stop/target grids | Creates a large, poorly reported trial count and bar-path artefacts. |
| Sharpe without trial history | Selection bias and non-normality can dominate the reported number. |

RL and deep learning are not “falling out of favour” in publication volume. What is
falling out of favour among careful research is accepting their raw backtests without
strong baselines, costs and chronological validation.

### 7.3 Important areas being ignored

- Exact bid/ask and intrabar execution reconstruction.
- Negative results and the complete number of attempted specifications.
- Strategy capacity at realistic size.
- DST, holidays and changing exchange/session definitions.
- CFD broker-price and financing differences from exchange products.
- Data-vendor revisions, survivorship and symbol/contract mapping.
- The difference between filtered and smoothed latent states.
- Strategy behaviour drift before P&L drift becomes statistically visible.
- Portfolio overlap among independently attractive signals.
- Parameter-surface stability rather than a single optimum.
- Whether a risk overlay reduces genuine tail risk or merely hides losses by reducing
  average exposure.
- Independent replication of popular public intraday papers.

These neglected areas are likely to have higher research value than inventing another
indicator.

## 8. Publicly reported success

### 8.1 What is visible

Publicly visible success falls into four groups:

1. **Peer-reviewed empirical effects.** Intraday momentum, reversal, session structure and
   order-flow predictability exist in multiple datasets. This is the strongest evidence
   for phenomena, not necessarily for a deployable implementation.
2. **Working-paper strategy backtests.** SPY intraday momentum and opening-range work
   report attractive cost-adjusted results [S07, S08]. Rules are more transparent than
   most commercial offerings, but independent replication and live evidence are absent.
3. **Institutional aggregate profits.** Proprietary firms and funds may disclose business
   performance, but not enough strategy/horizon detail to attribute it to the researched
   effect. It is not usable validation.
4. **Public practitioner records.** Some services publish broker or signal records. These
   often have short histories, changing strategy versions, unclear capital/fees or
   conflicts of interest. They are leads, not high-grade evidence.

### 8.2 Counter-evidence

Actual-account studies show that most individual day traders lose after costs, although
institutions and a small minority may profit. KOSPI 200 futures research finds substantial
individual losses and profits among domestic money managers and foreign institutions
[S38]. ASIC reported that 68% of Australian retail CFD investors lost money in the 2024
financial year, with costs materially contributing [S39].

These observations do not prove that systematic intraday strategies cannot work. They do
show that opportunity is not the same as accessible, persistent net profit and that CFD
cost/leverage deserves special caution.

### 8.3 Answer to “where are people reporting success?”

The clearest public reports in the seconds-to-hours range are currently:

- systematic intraday momentum in futures and SPY;
- opening-range breakout in selected high-activity equities/ETFs;
- conditional reversal and cross-market lead-lag;
- volatility timing and volatility-informed sizing;
- short-horizon order-book forecasting.

Nearly all strategy-specific evidence stops at author-run historical simulation.
Transparent, independently verified, multi-year live evidence with stable rules is scarce.
Absence of public evidence is not evidence of no private success; it limits what this
research can responsibly conclude.

## 9. Relevance to q-trad

### 9.1 Advantages of the existing data foundation

q-trad already preserves several facts commonly lost in research datasets:

- provider event and receive times;
- separate bid, ask and midpoint bars;
- immutable canonical events and provenance;
- explicit gaps and health;
- deterministic replay;
- distinction between quote-derived and provider historical bars;
- canonical instrument IDs and provider listings.

This supports unusually good tests of timestamp leakage, spread sensitivity, late data,
data-source differences and replay determinism.

### 9.2 Current limitations

- One-minute bars are too coarse for the bottom of the requested seconds-to-hours range.
- IG top-of-book updates are not a centralised trade tape or depth book.
- The initial research export is far too short for strategy inference.
- Historical IG bars do not reconstruct ordered quote paths.
- CFDs do not directly reproduce ETF/futures fees, basis, roll or exchange liquidity.
- Macro calendars, volume, futures depth, options gamma and news are not present.

Framework and hypothesis work may begin with short data, but decision-grade strategy conclusions
require sufficient representative coverage across sessions, volatility conditions and events.

### 9.3 Suggested q-trad experiment order

#### P0 — make inference trustworthy

1. Build coverage over many months, preferably years from a licensed source if permitted.
2. Establish session, holiday and DST calendars.
3. Create bid/ask executable outcome conventions.
4. Define a trial registry and immutable experiment manifest.
5. Add cost, delay and bar-path stress harnesses.
6. Establish no-signal, random-timing and simple persistence/reversal baselines.

#### P1 — high-information simple experiments

1. Time-of-day volatility, spread and quote-activity model.
2. Ex-ante intraday volatility forecast for sizing.
3. Opening-window to later-window momentum.
4. Volatility-normalised session breakout.
5. Shock-conditioned, time-limited mean reversion.
6. Scheduled-event avoidance and post-event continuation.
7. R0–R3 market-state comparison for annotation and sizing.

#### P2 — conditional extensions

1. Cross-index and FX/index lead-lag.
2. Residual reversal after common-factor removal.
3. Quote-duration/activity features.
4. Online change-point monitoring.
5. Gradient-boosted meta-filter against a locked rules-based signal.
6. External futures volume/depth or options-state data, subject to licensing and an ADR if
   the datastore/runtime boundary changes.

#### P3 — speculative

1. Latent-regime routing.
2. Deep multivariate sequence models.
3. Foundation-model embeddings or fine-tuning.
4. RL sizing or execution after simulator validation.

## 10. Candidate experiment cards

These are candidate research specifications. The current framework proof may implement simple
versions where they exercise the common forecast, shadow outcome and evaluation contracts; it must
not present the resulting short sample as an effectiveness claim.

### E01 — intraday volatility baseline

- **Question:** Can the next 15/30/60-minute realised volatility be forecast better than
  time-of-day seasonal, last-value and HAR-style baselines?
- **Inputs:** lagged bid/ask/midpoint returns, range, spread, update activity, session and
  event flags.
- **Models:** seasonal naive, linear/HAR, regularised linear, boosted tree.
- **Use:** sizing only.
- **Falsify:** no stable QLIKE improvement or no improvement in downstream risk after
  costs.
- **Priority:** P1.

### E02 — session-window momentum

- **Question:** Does an early-session move predict a non-overlapping later window?
- **Inputs:** fixed 15/30/60-minute session returns, ex-ante volatility, gap, spread and
  event state.
- **Baseline:** same unconditional exposure and sign-randomised exposure.
- **Execution:** next executable quote after the signal window; close at fixed window end.
- **Falsify:** effect disappears under one-spread-plus-one-bar delay or is confined to one
  crisis.
- **Priority:** P1.

### E03 — volatility-normalised breakout

- **Question:** Does a fixed session range breakout contain continuation beyond expected
  cost?
- **Variants:** pre-registered range length and one trailing versus one time exit.
- **Do not optimise:** dozens of range/stop/target combinations.
- **Falsify:** unstable parameter neighbourhood or net edge below two round-trip spreads.
- **Priority:** P1.

### E04 — shock-conditioned reversal

- **Question:** After an extreme volatility-normalised move without a scheduled event,
  does price partially revert within a fixed horizon?
- **Safety:** one entry, no averaging down, hard time and loss limits.
- **Stratify:** spread/liquidity, session, event and continuation trend score.
- **Falsify:** tail loss overwhelms mean gain or midpoint-only profitability.
- **Priority:** P1.

### E05 — event risk policy

- **Question:** Does flattening or reducing size around scheduled events improve expected
  shortfall after the opportunity cost of missed profitable moves?
- **Baseline:** unchanged exposure.
- **Outcomes:** gap/slippage, worst-tail return, recovery time and total net return.
- **Priority:** P1.

### E06 — soft market-state risk

- **Question:** Do continuous ex-ante volatility, trend, spread and uncertainty scores
  improve a locked signal through sizing or eligibility?
- **Compare:** R0, simple buckets, continuous scores and filtered HMM challenger.
- **Falsify:** benefit exists only with smoothed states or hard routing.
- **Priority:** P1/P2.

### E07 — cross-market lead-lag

- **Question:** Do completed-session index moves predict the next instrument's early or
  late cash-session return beyond common information?
- **Requirements:** precise overlapping hours, event controls and executable product map.
- **Falsify:** effect vanishes after synchronisation or is just shared USD/global-beta
  exposure.
- **Priority:** P2.

### E08 — ML meta-filter

- **Question:** Can a regularised/tree model decide when not to take a locked E02–E04
  signal?
- **Objective:** net utility or calibrated success probability, not raw return regression.
- **Baseline:** locked signal and logistic regression.
- **Falsify:** no nested walk-forward improvement after accounting for filter trials.
- **Priority:** P2.

## 11. Research backlog

### Highest-value unanswered questions

1. Does the broad futures intraday-momentum result persist in the exact seven q-trad
   instruments and IG quote stream after executable costs?
2. Which session definition is economically meaningful for each 24-hour FX pair and
   nearly continuous index CFD?
3. How much of public SPY/ORB performance survives an independent implementation and a
   locked post-publication sample?
4. Can volatility/state improve tail outcomes without serving as a retrospective P&L
   filter?
5. Are quote activity and spread useful beyond the one-minute bars, and at what minimum
   aggregation does provider latency cease to dominate?
6. What historical data licence permits a credible multi-year bid/ask replication?
7. How different are IG historical candles, quote-derived bars and exchange futures/ETF
   data around opens and events?
8. Does one cross-instrument model outperform per-instrument models because it learns
   stable structure, or because split construction leaks common timestamps?
9. What is the smallest shadow period that can detect expected degradation with useful
   power?
10. Can a no-regime or simple-state policy beat latent regime models after turnover and
    trial adjustment?

### Follow-up literature work

- Track citations and independent replications of [S07] and [S08].
- Review the full methods and appendices of [S05], especially cost construction and
  subperiod stability.
- Add exchange-specific research for ASX 200, FTSE 100 and their futures.
- Build a first-release macroeconomic calendar source review.
- Review stop-loss research by strategy mechanism rather than generic stop performance.
- Survey volatility-estimator robustness on bid/ask bars and sparse quote streams.
- Examine current 0DTE gamma research only as a possible US-index state input.
- Revisit foundation-model benchmarks every six months; the area is changing rapidly.
- Seek registered reports, failed replications and negative-result repositories.

## 12. Source ledger

The ledger emphasises sources used for conclusions above. “Trading evidence” describes
what the source demonstrates, not an endorsement.

| ID | Source | Date/status | Market/horizon | Trading evidence and caution |
|---|---|---|---|---|
| S01 | Bailey & López de Prado, [The Deflated Sharpe Ratio](https://papers.ssrn.com/sol3/papers.cfm?abstract_id=2460551) | 2014, journal | General | Selection-bias/non-normality diagnostic; depends on credible trial accounting. |
| S02 | Arian, Mobarekeh & Seco, [Backtest overfitting in the machine learning era](https://www.sciencedirect.com/science/article/pii/S0950705124011110) | 2024, peer reviewed | Synthetic financial evaluation | CPCV performed strongly in controlled comparisons; not a substitute for a final untouched period. |
| S03 | Bajgrowicz & Scaillet, [Technical trading revisited](https://www.sciencedirect.com/science/article/pii/S0304405X1200116X) | 2012, peer reviewed | Technical rules | False-discovery and persistence framework; foundational rather than recent. |
| S04 | [No pain, no gain](https://www.sciencedirect.com/science/article/pii/S0165176522001720) | 2022, peer reviewed | Trading rules | Omitting fees or liquidity costs creates false discoveries even with multiple-testing procedures. |
| S05 | Baltussen et al., [Hedging demand and market intraday momentum](https://www.sciencedirect.com/science/article/pii/S0304405X21001598) | 2021, Journal of Financial Economics | 62 futures; open/close windows | Broad, long-history intraday momentum and gamma-hedging mechanism; replication in target products still required. |
| S06 | Huang et al., [Intraday momentum in the VIX futures market](https://www.sciencedirect.com/science/article/pii/S0378426622003260) | 2023, peer reviewed | VIX futures; intraday | Conditional momentum and gamma evidence; specialised volatility product. |
| S07 | Zarattini, Aziz & Barbon, [Beat the Market](https://papers.ssrn.com/sol3/papers.cfm?abstract_id=4824172) | 2024, revised 2025 working paper | SPY; intraday | Large reported net backtest; transparent candidate, no independent/live confirmation. |
| S08 | Zarattini, Barbon & Aziz, [A Profitable Day Trading Strategy](https://papers.ssrn.com/sol3/papers.cfm?abstract_id=4729284) | 2024, revised 2025 working paper | US stocks; 5-minute ORB | Strong reported ORB results in stocks in play; selection, capacity and replication are central. |
| S09 | Liu et al., [Overnight-Intraday Reversal Everywhere](https://papers.ssrn.com/sol3/papers.cfm?abstract_id=2730304) | Working paper | Multiple asset classes | Broad reversal result; older working-paper evidence and implementation costs need review. |
| S10 | Brogaard, Han & Kim, [Intraday Residual Reversal](https://papers.ssrn.com/sol3/papers.cfm?abstract_id=4731947) | 2024 working paper | US equities; intraday | Factor-residual reversal; data and shorting differ from index products. |
| S11 | Jung, Lee & Leung, [Threshold Overnight Comovement](https://papers.ssrn.com/sol3/papers.cfm?abstract_id=4946188) | 2024 working paper | SPY/FXI; intraday/overnight | Lead-lag framework for asynchronous markets; limited pair and trading evidence. |
| S12 | Elaut, Frömmel & Lampaert, [Intraday momentum in FX markets](https://www.sciencedirect.com/science/article/pii/S1386418116300313) | 2018, peer reviewed | RUB/USD transactions | Explicit-session momentum linked to liquidity demand; not automatically applicable to major 24-hour pairs. |
| S13 | Ito & Hashimoto, [Intra-Day Seasonality in FX](https://www.nber.org/papers/w12413) | 2006, NBER/published | EUR/USD, USD/JPY transactions | Foundational time-of-day activity, volatility and spread evidence; old market structure. |
| S14 | [Intraday patterns in FX returns and realised volatility](https://www.sciencedirect.com/science/article/pii/S1544612317305251) | 2018, peer reviewed | 16 currencies, 2010–2015 | Session return patterns linked to volatility; needs current replication. |
| S15 | [Informativeness of trades around macro announcements in FX](https://www.sciencedirect.com/science/article/pii/S1042443122000245) | 2022, peer reviewed | FX order flow; intraday | Post-announcement interpretation remains in trades; specialised transaction data. |
| S16 | Kaourma et al., [News and Intraday Retail Investor Order Flow](https://papers.ssrn.com/sol3/papers.cfm?abstract_id=3796753) | 2021, revised 2024 working paper | EUR/USD retail positions | Retail response around news; proprietary aggregate data and no direct strategy proof. |
| S17 | [Prior S&P 500 returns and Nikkei 225 futures](https://www.sciencedirect.com/science/article/pii/S3050700626000204) | 2026, peer reviewed open access | Nikkei futures; intraday | Reports reversal and momentum across asynchronous sessions; new, no shared data. |
| S18 | Briola, Bartolucci & Aste, [Deep limit order book forecasting: a microstructural guide](https://eprints.lse.ac.uk/128950/) | 2025, peer reviewed/open code | NASDAQ LOB; very short horizon | Strong forecasts need not be actionable; centralised-book setting. |
| S19 | Lucchese, Pakkanen & Veraart, [Short-term predictability in order-book markets](https://www.sciencedirect.com/science/article/pii/S0169207024000062) | 2024, peer reviewed | Order books; high frequency | Large-scale multi-horizon predictability study; much of horizon may be below q-trad scope. |
| S20 | Brini & Toscano, [SpotV2Net](https://www.sciencedirect.com/science/article/pii/S0169207024001080) | 2025, peer reviewed | DJIA components; intraday volatility | Graph/vol-of-vol forecast gains; not direct direction or economic-value evidence. |
| S21 | Watanabe & Nakajima, [High-frequency realised stochastic volatility](https://www.sciencedirect.com/science/article/pii/S0927539824000938) | 2024, peer reviewed | E-mini S&P 500; 5-minute | Improved volatility forecasting with diurnal structure. |
| S22 | Li et al., [Decoupling interday and intraday volatility](https://papers.ssrn.com/sol3/papers.cfm?abstract_id=4850075) | 2024, revised 2025 working paper | SPY; intraday | Duration-based estimation captures FOMC impact; method and data burden. |
| S23 | Clements & Preve, [Modelling and forecasting intraday spot volatility](https://www.sciencedirect.com/science/article/pii/S0169207025001189) | 2026, peer reviewed/open access | US stocks; intraday | Simple multi-equation model beats LightGBM/LSTM in this setting. |
| S24 | Branco, Rubesam & Zevallos, [Does anything beat linear models?](https://www.sciencedirect.com/science/article/pii/S0927539824000598) | 2024, peer reviewed | Ten indices; realised volatility | No general nonlinear-ML dominance; simpler economic performance can be better. |
| S25 | Díaz, Hansen & Cabrera, [Machine-learning stock-market volatility](https://www.sciencedirect.com/science/article/pii/S1057521924002187) | 2024, peer reviewed | S&P 500 volatility | Short-run ML gains but no economic gain over benchmark. |
| S26 | [Deep learning for algorithmic trading: systematic review](https://www.sciencedirect.com/science/article/pii/S2590005625000177) | 2025, systematic review | Broad | Maps rapid growth; highlights noise, overfitting and interpretability. |
| S27 | Lalwani, Meshram & Jindal, [Empirical Asset Pricing via ML: Research Design Choices](https://papers.ssrn.com/sol3/papers.cfm?abstract_id=4837337) | 2024, journal/working-paper page | Equity prediction | Research-design sensitivity; not intraday-specific. |
| S28 | [Pooling and winsorising ML forecasts](https://www.sciencedirect.com/science/article/pii/S0927539824000732) | 2024, peer reviewed | Stock returns | Documents severe forecast failures across several popular ML models. |
| S29 | Giorgi et al., [An RL algorithm for trading commodities](https://onlinelibrary.wiley.com/doi/full/10.1002/asmb.2825) | 2024, peer reviewed | Commodity control model | RL approximates known optimum with costs; controlled proof, not discovered live alpha. |
| S30 | Lu, [Time-Series Foundation Models in Finance](https://papers.ssrn.com/sol3/Delivery.cfm/5570099.pdf?abstractid=5570099) | 2025 survey/preprint | Broad finance | Useful taxonomy and risk-aware evaluation proposal; fast-moving field. |
| S31 | [Financial Fine-Tuning a Large Time Series Model](https://doi.org/10.1109/CIFER64978.2025.10975735) | 2025 conference | Multiple markets | Reports mock-trading gains after TimesFM fine-tuning; requires independent replication. |
| S32 | [Pretrained TSFMs for Financial Return Forecasting](https://arxiv.org/abs/2606.27100) | 2026 preprint | Five US equities; returns | Finds gains over random walk small and sparse; very recent preprint. |
| S33 | Jalil, Jabbar & Fayyaz, [What Are Market Regimes?](https://papers.ssrn.com/sol3/Delivery.cfm/6493762.pdf?abstractid=6493762) | 2026 systematic-review preprint | 98 regime papers | Valuable critique of definitions and validation; not yet peer reviewed. |
| S34 | Shu, Yu & Mulvey, [Downside Risk Reduction Using Regime-Switching Signals](https://papers.ssrn.com/sol3/Delivery.cfm/SSRN_ID4719989_code5886757.pdf?abstractid=4719989) | 2024, peer reviewed | Major equity indices | Out-of-sample jump-model risk reduction with costs/delay; daily rather than intraday control. |
| S35 | Tsaknaki, Lillo & Mazzarisi, [Online learning of order flow and market impact](https://ricerca.sns.it/handle/11384/132122) | 2024, peer reviewed/open version | Order flow; online | Bayesian online change points for real-time prediction; microstructure-heavy. |
| S36 | Hood & Raughtigan, [Volatility Targeting Is Trendy](https://papers.ssrn.com/sol3/Delivery.cfm/SSRN_ID4811459_code1675552.pdf?abstractid=4773781) | 2024, revised 2025 working paper | Global futures | Equity volatility-management alpha largely overlaps trend; warns against universal interpretation. |
| S37 | Melvin, Pan & Wikstrom, [Retaining Alpha](https://www.ifo.de/en/cesifo/publications/2020/working-paper/retaining-alpha-effect-trade-size-and-rebalancing-frequency-fx) | 2020 working paper | Major FX electronic books | Sweep-to-fill costs show size/frequency capacity effects. |
| S38 | Ryu, [The profitability of day trading](https://www.tandfonline.com/doi/abs/10.1080/10293523.2012.11082543) | 2012/2015, peer reviewed | KOSPI 200 futures | Individuals lose; some institutional groups profit. Describes participants, not algorithms. |
| S39 | ASIC, [2026 CFD sector review](https://www.asic.gov.au/about-asic/news-centre/find-a-media-release/2026-releases/26-004mr-asic-secures-nearly-40-million-in-refunds-to-investors-and-drives-change-after-cfd-sector-falls-short/) | 2026 regulator evidence | Australian retail CFDs | 68% lost in FY2024; aggregate client outcome, not systematic-strategy evidence. |
| S40 | [Forecasting realised volatility: commonality and ML](https://papers.ssrn.com/sol3/papers.cfm?abstract_id=4022147) | 2022, published 2024 | Intraday volatility | Cross-asset commonality and ML; supports multivariate volatility research. |

## 13. Search log and known corpus limits

### Initial search

**Run:** 2026-07-04  
**Primary emphasis:** work published or materially revised from 2024 to 2026  
**Foundational exceptions:** older market-microstructure, seasonality, validation and
participant-outcome research needed to interpret recent claims

Query families combined asset, horizon, method and evidence terms, including:

- `intraday momentum` or `intraday reversal` with FX, currency futures, equity-index
  futures, ETF and CFD;
- opening range, session, overnight, lead-lag, scheduled announcement and order flow;
- volatility forecasting, volatility timing, volatility-of-volatility and adaptive risk;
- market regime, market state, HMM, jump model, online change point and structural break;
- algorithmic trading with machine learning, deep learning, reinforcement learning and
  time-series foundation models;
- transaction costs, spread, slippage, capacity, walk-forward, purged validation,
  multiple testing, DSR and PBO;
- live, audited, forward, paper and public track record;
- regulator studies of retail FX/CFD and futures day-trading outcomes.

Sources were sought through journal/publisher pages, NBER, SSRN, arXiv, university
repositories, conference proceedings and regulators. Search-result snippets, social
posts and commercial pages were used to discover claims but not to establish the central
judgements unless they appear explicitly as low-grade public-report evidence.

### Known limits

- This is a structured initial survey, not a PRISMA systematic review or bibliometric
  census.
- Some publisher pages expose only abstracts without full appendices, so cost and
  validation assessments must be revisited before replication.
- Publication bias is severe: failed strategies and internal institutional research are
  rarely public.
- Publicly reported institutional profit usually cannot be mapped to one signal,
  instrument or holding horizon.
- The recent 2025–2026 literature includes unreviewed preprints with no citation or
  replication history.
- The corpus is stronger for US equity/volatility markets than for Australia 200 and FTSE
  100 intraday strategies.
- Centralised futures and ETF evidence is richer than CFD evidence; this is a genuine
  transfer problem, not a reason to pretend the products are equivalent.
- English-language and searchable online sources dominate.

The next corpus expansion should target independent replications, negative results,
exchange-specific index research and exact executable-cost appendices rather than merely
adding more model papers.

## 14. Maintenance rules

- Record the date each search is rerun.
- Prefer the latest paper revision but preserve the originally assessed version.
- Add a source only with a one-sentence relevance and one-sentence limitation.
- Mark corrections, retractions and failed replications prominently.
- Separate new evidence from changed judgement in the revision log.
- Reassess fast-moving ML, foundation-model, 0DTE and market-structure sections every six
  months.
- Turn a research priority into an implementation work package only when the active plan identifies
  the hypothesis, minimum data, baseline, falsification condition and result needed for the next
  viability decision.

## 15. Revision log

| Date | Change |
|---|---|
| 2026-07-04 | Initial recency-weighted survey, evidence protocol, regime assessment, risk review, source ledger and prioritised experiment backlog. |
