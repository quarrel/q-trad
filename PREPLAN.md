# q-trad product direction

## Purpose

q-trad is an experimental framework for testing whether short-horizon, factor-style strategies can
produce useful results after realistic costs. Its centre of gravity is holding periods from minutes
to hours.

The intended operating loop is:

> market data → many comparable strategy forecasts → continuous shadow paper outcomes → effectiveness
> scores → market-state-aware ranking and selection → constrained allocation → monitoring, demotion
> and retirement.

The framework must make negative results cheap and trustworthy. A strategy that fails is useful
evidence; infrastructure that delays the result without protecting its validity is not.

## Intended system

### Market universe

The seven `capture-v1` instruments proved the ingestion path. They are not the intended research
  limit. The next target is a reviewed set of approximately 20 liquid IG demo FX, equity-index,
  commodity and crypto markets. `config/capture-v2-candidates.toml` is the current candidate
  catalogue; mappings and product economics remain subject to fail-closed provider review.

Strategies declare their applicability: one instrument, an asset-class subset, a cross-section or a
basket. A bad or ambiguous listing can be quarantined without preventing research on the rest of the
universe.

### Strategy population

Many strategies may run continuously against the same eligible live events. Most remain `SHADOW`:
they receive no deployable allocation but retain forecasts, hypothetical fills, virtual positions
and cost-aware P&L. This supplies comparable forward evidence and prevents only selected strategies
from surviving in the record.

Strategies will mainly represent factors or simple variants appropriate to minutes-to-hours
horizons: momentum, reversal, session effects, volatility, cross-market relationships and later
conditional combinations. The initial moving-average, breakout and similar examples exist only to
prove the framework.

### Evaluation, market state and selection

A strategy evaluator joins each causal forecast to its later defined outcome. It calculates
versioned measures such as Rank IC, paper P&L after modelled costs, drawdown, turnover, sample size
and stability.

A market-state model records contemporaneously observable conditions such as volatility,
correlation, liquidity or trend state. A strategy selector uses only information available at the
selection time to rank or admit strategies, potentially conditional on that state. The allocation
engine then sizes selected strategies under fixed limits.

Predictive quality, selection and allocation are separate decisions. Rank IC is not itself a
profitability measure, and regime labels are models rather than objective truth.

### Lifecycle

A future bounded lifecycle is:

> `CANDIDATE → SHADOW → ELIGIBLE → SELECTED → PAUSED → RETIRED`

The framework proof need only record comparable scores and one transparent selection decision.
Automated pruning, sophisticated factor timing and real-capital promotion belong to later experiments
after their evidence rules are defined.

## Delivery stages

### 1. Research-framework proof — current

- continue reliable capture while expanding towards the reviewed 20-market universe;
- run a few simple strategies through deterministic replay and live shadow paper accounting;
- retain comparable forecasts and causal outcomes;
- calculate a basic versioned effectiveness ranking;
- show a simple market-state annotation without granting it control;
- produce one reproducible experiment report.

### 2. Experimental strategy research

- acquire sufficient representative history for time-ordered inference;
- grow a controlled strategy population and trial registry;
- compare factor families, horizons, costs and simple benchmarks;
- test whether regime-conditioned selection beats unconditional selection;
- introduce evidence-based pause and retirement rules;
- keep all capital hypothetical or paper allocated.

### 3. Live-data paper operation

- run surviving candidates continuously over meaningful forward windows;
- monitor score decay, cost sensitivity, selection stability and paper-model limitations;
- harden unattended recovery only as demanded by these experiments.

### 4. External execution — separately authorised future work

Broker-demo orders, supervised live canaries and real allocation require new plans and safety gates.
Nothing in the current architecture authorises an external order route.

## Framework-proof slice

The smallest useful slice is:

> completed market data → several forecasts → fixed sizing and limits → subsequent bid/ask paper
> fills → per-strategy position/P&L → realised outcomes → deterministic scores and rank report.

Use direct functions and domain values where possible. Introduce a separate sleeve, selector,
allocation or execution service only when its present behaviour differs from a simple policy.

Minimum acceptance:

- selected and unselected strategies see the same eligible observations;
- forecasts declare instrument set, horizon, strength/target and configuration version;
- warm-up and future observations cannot leak into forecasts, regime labels or selection;
- fills use later executable sides with explicit latency, spread and adverse slippage;
- unhealthy intervals are excluded visibly, not repaired into plausible data;
- replay reproduces forecasts, fills, outcomes, scores and rank order;
- independent arithmetic reproduces paper P&L;
- selection remains subordinate to fixed limits;
- no external order capability exists.

## Decisions to make when evidence requires them

- the exact 20-market approved universe and per-strategy applicability;
- whether initial IC is cross-sectional, time-series or both;
- forecast horizons, rolling windows, overlapping-observation treatment and sample minima;
- initial market-state features and whether conditioning adds out-of-sample value;
- pause/retirement evidence and reconsideration rules;
- decision-grade historical source and licence;
- thresholds that justify continued investment or stop the project.

These are experiment specifications, not reasons to build general-purpose infrastructure in
advance.

## Explicit non-goals

- live orders or broker-neutral execution completeness;
- automatic real-capital promotion;
- sophisticated regime inference, covariance optimisation or machine learning before baselines;
- exchange queue simulation from top-of-book data;
- multi-user, multi-account or high-availability operation;
- broad asset-class support beyond a reviewed research need;
- indefinite backward compatibility for disposable experimental state;
- production-style evidence ceremony that does not protect a research conclusion or current data.
