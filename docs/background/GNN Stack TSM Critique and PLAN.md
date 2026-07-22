# Review and Implementation Plan: Multi-Asset Trading Decision, Confidence and Sizing Framework

## 1. Overall verdict

There is no major objection to the architecture’s direction.

The strongest parts are:

- separating local asset signals from cross-asset structural signals;
- producing probabilistic rather than merely point forecasts;
- calibrating forecasts using genuinely out-of-sample errors;
- separating forecasting, trade selection and portfolio construction;
- allowing non-traded instruments to contribute context;
- building the components as replaceable layers.

The final blueprint captures much of this: a local model, a graph-based residual model, an uncertainty wrapper, and a secondary trade-selection layer.

The main changes I recommend are:

1. Predict horizons aligned with actual trades, not only the next one-minute return.
2. Keep five different concepts separate rather than calling all of them confidence.
3. Treat conformal intervals as calibrated uncertainty information, not an automatic trade rule.
4. Make meta-labeling an optional estimator of post-cost opportunity quality, not the position-sizing engine itself.
5. Replace the proposed scalar sizing formula with portfolio-level constrained optimisation.
6. Introduce the complex graph-conformal and change-point components only after simpler versions show incremental value.

The resulting architecture remains recognisably the one in the document, but the decision layer becomes cleaner and much easier to validate.

---

# 2. Reference audit

## 2.1 Early unreferenced claims that have solid foundations

Several early claims are reasonable despite lacking citations.

**Bid–ask bounce and the use of mid-prices.** Roll’s classic model demonstrates how transaction costs induce negative serial dependence in observed price changes. Mid-prices do not eliminate all microstructure effects, but using them instead of alternating bid and ask observations is well motivated.

**Intraday volatility seasonality.** Andersen and Bollerslev documented strong periodicity in high-frequency FX and equity volatility and showed that failing to account for it obscures the underlying volatility dynamics. The document’s time-of-day normalisation therefore has a sound empirical basis, although the profile should be estimated separately by instrument and changing session conditions.

**Learned graph adjacency.** The formula based on two node-embedding matrices is recognisably derived from Graph WaveNet’s adaptive dependency matrix.

There is an important naming correction: with fixed learned embeddings $E_1$ and $E_2$, the resulting adjacency is **learned and directed, but not dynamically changing with each market observation**. It is recomputed during a forward call, but its inputs are model parameters rather than the current market state. The document describes it as learning “shifting” relationships, whereas the stated formula learns one persistent relationship matrix.

A genuinely state-dependent graph would need something such as:

$$
A_t=f_\theta(H_t,E_1,E_2)
$$

where $H_t$ contains the current node states, or a mixture of persistent structural adjacency and observation-dependent attention.

**Aleatoric and epistemic uncertainty.** The distinction between conditional observation noise and uncertainty about model parameters is well established. A heteroscedastic likelihood head estimates primarily the former; model ensembles or Bayesian approximations try to capture the latter.

## 2.2 The recent conformal references are real, but some conclusions are overstated

The strongest references in the document are the recent conformal papers:

- Adaptive Conformal Inference addresses online coverage under unknown distribution shift.
- CoRel uses relationships among correlated time series when constructing conformal intervals.
- CPTC integrates state/change-point prediction with online conformal calibration.

These support experimentation with adaptive and relational calibration. They do **not** establish that:

- exchangeability failure is necessarily “catastrophic” for this strategy;
- the GNN’s existing predictive graph is also the correct uncertainty graph;
- every session boundary is a statistical change point;
- intervals should automatically be inflated for exactly 15 minutes;
- relational conformal prediction will improve trading results rather than just interval coverage or width.

Those are sensible hypotheses to test, not conclusions from the cited papers. The document currently moves directly from the papers to specific financial implementation rules.

The phrase “mathematically guaranteed live prediction intervals” should also be removed. Adaptive conformal methods can target long-run coverage under specified procedures, but that is not equivalent to a conditional guarantee that a particular live return lies inside a particular interval. ACI, for example, targets coverage frequency over time under distribution shift.

## 2.3 Weak or mismatched references

The residual-ensemble argument is reasonable, but several cited sources in that section are unrelated to the claims being made: the reference list includes material on long-context language models, industrial triggering and budgeting software.

The later “SOTA” section similarly mixes papers with marketing articles and general trading websites.

The final meta-labeling section is particularly weakly sourced. Its references include an unrelated computer-security proceeding, blog explanations, a generic rare-defect focal-loss article and a brokerage-testing case study.

This does not invalidate residual learning, LightGBM, triple-barrier labels or calibrated sizing. It means they should be described as **candidate engineering choices**, not as established institutional standards.

---

# 3. The central design problem: “confidence” currently means five different things

The proposed system combines:

1. predicted Gaussian variance;
2. Monte Carlo dropout dispersion;
3. conformal interval width;
4. meta-labeler probability of success;
5. portfolio risk limits.

These are not interchangeable.

## 3.1 Forecast distribution

This describes possible future returns conditional on the model and current input:

$$
p(r_{i,h}\mid x_t)
$$

Useful outputs include:

- expected return;
- median;
- lower and upper quantiles;
- conditional scale;
- tail probabilities.

This is where aleatoric uncertainty belongs.

## 3.2 Model uncertainty

This measures disagreement about the forecast caused by limited data, model instability or unfamiliar inputs.

Monte Carlo dropout was originally justified as an approximate Bayesian method. It remains a usable diagnostic, but I would not make it a core production dependency. Deep ensembles have generally produced stronger practical uncertainty estimates, and a 2026 evaluation found substantial failure modes in Monte Carlo dropout’s representation of uncertainty.

A practical later implementation would train three to five versions using different initialisations, bootstrap or rolling training samples. Their disagreement becomes an epistemic diagnostic.

## 3.3 Calibration state

Conformal scores answer whether recent errors are compatible with the model’s nominal uncertainty.

This should produce values such as:

- recent empirical coverage;
- interval width;
- conformal scale adjustment;
- coverage deficit;
- calibration sample count;
- fallback level used.

It should not be collapsed into a generic confidence percentage.

## 3.4 Opportunity probability

A meta-model can estimate an economically defined outcome, for example:

$$
P(r^{\text{net}}_{i,h}>0\mid x_t,\text{forecast state})
$$

or:

$$
P(\text{profit barrier first}\mid x_t,\text{proposed trade})
$$

This is a probability about a specified event. It depends on the forecast, costs, horizon and proposed management rule.

## 3.5 Portfolio risk

Portfolio risk depends on positions, covariances, factor concentrations and current holdings:

$$
\operatorname{Var}(R_p)=w^\top\Sigma w
$$

Neither the forecast head’s $\sigma_i$ nor a conformal interval substitutes for $\Sigma$. The portfolio layer must account for the fact that several of the 24 instruments may represent substantially the same underlying risk.

These five objects should have different Python types and different evaluation metrics.

---

# 4. Forecast target and horizon

## 4.1 A next-minute target is too narrow for the intended decision

The document is framed around next-step returns. That is acceptable as an auxiliary prediction task, but it is a poor sole target for trades expected to last from several minutes to an hour or more.

A model can correctly predict the next minute and still be wrong about:

- the cumulative move over the holding period;
- the maximum adverse excursion;
- whether the move survives costs;
- which horizon currently contains the signal;
- whether an entry should be held, reduced or reversed.

The first implementation should directly predict a small horizon set, for example:

$$
h\in\{5,15,30,60\}\text{ minutes}
$$

For each asset and horizon, emit either:

- conditional return quantiles; or
- a parametric distribution such as Student-$t$; or
- both a mean and several quantiles.

A sensible output tensor is:

$$
[\text{batch},\text{asset},\text{horizon},\text{distribution parameters}]
$$

The next-minute forecast can remain a feature for the opportunity model, but should not implicitly define the trade’s complete lifecycle.

## 4.2 Distribution choice

Gaussian NLL is a useful baseline, not an inherently correct return model. Its predicted scale is a conditional error scale, not a direct measure of forecast truth.

I would compare:

1. Gaussian mean and scale;
2. Student-$t$ location, scale and constrained degrees of freedom;
3. direct quantile regression, such as 10%, 25%, 50%, 75% and 90%.

Quantiles are particularly convenient because the portfolio policy can directly consume downside and upside forecasts without assuming symmetry.

Evaluate these using proper forecast metrics:

- NLL for complete parametric distributions;
- pinball loss for quantiles;
- CRPS where convenient;
- PIT or quantile calibration;
- calibration and sharpness by asset, horizon, session and volatility state.

---

# 5. Conformal prediction’s proper role

The proposed rule trades only when the entire conformal interval exceeds costs. That is coherent, but it represents a specific, highly risk-averse max–min policy—not a generally optimal use of conformal prediction.

A nominal 95% interval is especially demanding for a weak, noisy intraday return signal. Requiring its lower bound to clear costs could result in very few trades without necessarily producing the best economic outcome.

I recommend three uses.

## 5.1 Forecast recalibration

Use normalised scores such as:

$$
s_{i,t,h}=
\frac{|r_{i,t,h}-\hat{\mu}_{i,t,h}|}
{\hat{\sigma}_{i,t,h}+\epsilon}
$$

to correct the model’s scale.

## 5.2 Reliability monitoring

Measure realised coverage and width by:

- asset;
- horizon;
- session;
- broad volatility bucket.

Persistent undercoverage should lower the permitted risk or trigger recalibration. It need not automatically shut down all trading.

## 5.3 Policy input

Expose calibrated lower quantiles and interval width to the opportunity and portfolio layers. Test several policies rather than embedding one permanently:

- expected net return;
- probability of positive net return;
- lower-quantile or chance-constrained return;
- expected return penalised by downside;
- full-interval-above-cost rule.

Begin with rolling, asset/horizon-specific adaptive conformal calibration and a pooled fallback. CoRel and CPTC should implement the same interface but remain experimental replacements.

Do not automatically reuse the forecasting GNN’s adjacency for conformal calibration. The assets whose **returns** help predict one another need not be the same assets whose **forecast errors** co-move.

---

# 6. Meta-labeling and triple barriers

## 6.1 Meta-labeling is useful but not mandatory

Separating direction generation from estimating whether the proposed trade is attractive after costs is sensible.

However, the primary forecast can already provide:

- expected net return;
- return quantiles;
- probability of clearing costs.

A meta-labeler is worthwhile only if its extra state—spread, liquidity, calibration failure, model disagreement, session state and similar variables—adds out-of-sample value beyond those outputs.

Use LightGBM as a strong tabular baseline, but do not assume that it is an industry-standard answer or that a “multi-task LightGBM” is necessary. Practical alternatives are:

- one pooled model with asset and horizon identifiers;
- separate models by broad asset class;
- separate models per asset where data is sufficient.

## 6.2 Triple-barrier labels are one option, not a requirement

The document says fixed-horizon returns cannot be used and proposes profit, stop and timeout barriers. That is too categorical.

The correct label depends on the actual strategy:

- Fixed holding period: predict post-cost return at that horizon.
- Profit/stop/timeout policy: barrier labels are directly relevant.
- Variable exit policy: a competing-risks or survival formulation may be cleaner.
- Portfolio rebalancing every minute: predict the utility of the next target position rather than a standalone trade outcome.

The proposed binary label also classifies timeout as failure. That conflates a small profit, a small loss and no meaningful movement.

A better barrier formulation is:

$$
Y\in\{\text{profit first},\text{stop first},\text{timeout}\}
$$

and optionally models time to event.

The $+2\sigma$, $-1.5\sigma$ and 30-minute settings are experiment parameters, not architectural constants.

## 6.3 Probability calibration is essential

A sigmoid output is not automatically a trustworthy probability. Neural networks and boosted trees may require explicit out-of-sample calibration.

Evaluate the meta-model using:

- log loss;
- Brier score;
- reliability plots;
- expected calibration error;
- economic value by predicted-probability bucket.

The document recommends focal loss or class-weighted BCE as the standard response to imbalance. That is not a good default when the actual probability is used downstream. Standard focal loss is not strictly proper: its raw score need not equal the class-posterior probability.

Start with ordinary log loss and post-hoc calibration. Use weighting or focal variants only when demonstrated necessary, followed by recalibration and prior correction.

## 6.4 Strict out-of-fold construction

Every meta-labeler training row must contain a primary forecast produced without training on that observation.

The correct sequence is:

1. fit the primary model on an earlier window;
2. produce out-of-fold forecasts;
3. calculate realised outcomes and calibration features;
4. train the meta-labeler on those stored forecasts;
5. evaluate it on a still later window.

Training the gatekeeper on in-sample primary predictions would teach it to interpret unrealistically clean model behaviour.

---

# 7. Position sizing

## 7.1 The proposed Kelly formula is not valid for these labels

The file proposes:

$$
W=\max(0,2P_{\text{success}}-1)
$$

and describes it as a simplified Kelly calculation.

The expression $2p-1$ is the Kelly fraction for an even-money binary gamble with outcomes $+1$ and $-1$. It is not correct when:

- the profit and loss barriers differ;
- transaction costs are present;
- outcomes vary continuously;
- multiple correlated positions exist;
- the probability is imperfectly calibrated.

For a binary bet that earns $b$ units per unit risked on success and loses one on failure, the unconstrained Kelly fraction is:

$$
f^*=\frac{bp-(1-p)}{b}
$$

Even that is not an adequate portfolio solution here. General Kelly implementations require the complete distribution of possible portfolio outcomes, and risk-constrained variants are materially more involved than a linear mapping from probability to size.

## 7.2 Recommended sizing architecture

The meta-labeler should adjust the expected alpha or permitted maximum, but the final weight should be selected jointly across all traded instruments.

Define a calibrated expected net return vector:

$$
a_t =
\operatorname{reliability}_t
\odot
\left(
\hat{\mu}_t-\widehat{\operatorname{cost}}_t
\right)
$$

Reliability should be bounded and should not multiply several duplicative uncertainty scores together.

Then solve a constrained problem such as:

$$
\max_w
\quad
a_t^\top w
-\frac{\lambda}{2}w^\top\Sigma_t w
-C_t(w-w_{t-1})
-\eta\lVert w-w_{t-1}\rVert_1
$$

subject to constraints including:

$$
|w_i|\leq w_{i,\max}
$$

$$
\lVert w\rVert_1\leq L_{\max}
$$

$$
w^\top\Sigma_t w\leq \sigma^2_{\text{target}}
$$

plus limits for:

- gross and net exposure;
- currency exposure;
- equity-index beta;
- geographic concentration;
- correlated instrument groups;
- session exposure;
- turnover;
- short positions;
- instrument availability.

The portfolio risk model should use a stabilised covariance estimate independent of the neural model’s predicted return scale.

## 7.3 No-trade regions

Because the optimiser sees current holdings, it can decline to rebalance when the improvement is smaller than the expected cost.

This is preferable to generating an independent long/short/pass decision for every instrument and then attempting to reconcile the resulting positions afterwards.

---

# 8. Regime and session handling

An independent regime layer is reasonable, but a hard HMM or GMM gate is not required.

The initial version should use a continuous reliability or risk multiplier learned from out-of-fold performance conditional on:

- volatility;
- spread;
- liquidity;
- session;
- event proximity;
- recent calibration performance;
- ensemble disagreement.

Reserve hard vetoes for clear operational conditions such as unavailable prices, closed underlying markets, stale inputs, excessive spreads or unsupported instrument states.

Similarly, start with one shared model containing precise market-session and time-zone features unless the APAC/Atlantic split has already demonstrated a substantial out-of-sample advantage. Fixed AEST clock boundaries also need to account for daylight-saving changes in Sydney, London, Europe and North America.

Separate session models may ultimately win, but they double much of the model-selection and calibration surface. A shared model or mixture-of-experts formulation is a cleaner baseline.

---

# 9. Traded instruments and context instruments

The graph should distinguish:

- **traded target nodes**, which have forecast heads and may receive portfolio weights;
- **context-only nodes**, which contribute information but can never receive positions;
- **global context variables**, which are broadcast rather than represented as equivalent tradable nodes.

Bitcoin is not inherently a non-sequitur. It is continuously traded and may contain useful information about global speculative risk appetite or weekend-to-Monday state. But it should initially be a context-only candidate and survive the same incremental-value test as any other instrument.

The relevant test is not whether Bitcoin correlates with the basket overall. It is whether adding its lagged state improves strictly out-of-sample:

- forecast scoring;
- calibration;
- trade selection;
- final net portfolio results.

The architecture should permit arbitrary context nodes through a mask:

```text
node_is_tradable: bool
node_has_target: bool
node_asset_class: category
```

Adding context nodes should not require creating output heads or artificial positions.

---

# 10. Recommended final architecture

```text
Primary forecasting layer
    ├── local asset model
    └── cross-asset residual model
                 │
                 ▼
Probabilistic forecast
    ├── expected returns by horizon
    ├── return quantiles / conditional scale
    └── optional ensemble disagreement
                 │
                 ▼
Calibration layer
    ├── adaptive conformal adjustment
    ├── empirical coverage state
    └── pooled fallback hierarchy
                 │
                 ▼
Opportunity layer
    ├── expected post-cost return
    ├── calibrated probability of positive payoff
    ├── downside estimate
    └── continuous reliability multiplier
                 │
                 ▼
Portfolio layer
    ├── covariance and factor risk
    ├── current holdings
    ├── costs and turnover
    └── constrained target-weight optimisation
                 │
                 ▼
Decision policy
    ├── target positions
    ├── hard operational vetoes
    └── recorded decision explanation
```

Recommended typed contracts:

```text
ForecastDistribution
CalibrationState
OpportunityEstimate
CostEstimate
PortfolioRiskState
TargetPortfolio
DecisionRecord
```

These contracts should be model-independent. The GNN, LSTM, TCN or Mamba implementation should not leak into the portfolio API.

---

# 11. Python implementation plan divided into agent tasks

## Agent 1 — Decision contracts and configuration

**Scope**

Define the interfaces connecting forecasts, calibration, opportunity assessment and portfolio construction.

**Deliverables**

- dataclasses or Pydantic models for all decision objects;
- asset and horizon identifiers;
- context/tradable masks;
- timestamp and model-version fields;
- configuration schemas for limits and policy parameters;
- serialisation tests.

**Acceptance conditions**

- every value has explicit units and horizon;
- forecasts cannot be confused with realised returns;
- all objects carry an `as_of` timestamp;
- no component imports a concrete forecasting-model implementation.

This agent should complete first.

---

## Agent 2 — Target and outcome definitions

**Scope**

Implement model-independent target generation for decision research.

**Deliverables**

- cumulative gross returns at 5, 15, 30 and 60 minutes;
- corresponding net returns after supplied cost estimates;
- maximum adverse and favourable excursion;
- optional three-outcome barrier labels;
- event time for barrier labels;
- overlap metadata needed for purging.

**Acceptance conditions**

- labels are derived using information strictly after the decision timestamp;
- boundary behaviour is deterministic;
- timeout is distinct from stop-loss;
- tests cover gaps, unavailable periods and simultaneous barrier ambiguity.

This work does not require committing to triple barriers as the production target.

---

## Agent 3 — Out-of-fold forecast store

**Scope**

Create the historical forecast artefact on which calibration and meta-models depend.

**Deliverables**

A versioned table keyed by:

```text
as_of
asset
horizon
training_window
model_version
forecast_mean
forecast_quantiles
forecast_scale
ensemble_member
realised_outcome
```

**Acceptance conditions**

- each forecast is produced by a model trained only on earlier permitted data;
- training, calibration and evaluation intervals are auditable;
- reruns produce identical keys and version metadata;
- impossible in-sample rows are rejected.

This is the most important leakage-control component.

---

## Agent 4 — Probabilistic forecast adapter

**Scope**

Standardise outputs from the local and graph models.

**Deliverables**

- additive residual combination;
- optional learned constrained blender;
- Gaussian adapter;
- Student-$t$ adapter;
- quantile adapter;
- per-horizon distribution interface;
- forecast-score calculations.

**Acceptance conditions**

- the local baseline’s residual training target is generated from out-of-fold baseline forecasts;
- quantiles are ordered or corrected for crossing;
- scale parameters remain positive;
- all output distributions pass simulation and scoring tests.

The initial implementation can use existing point forecasts while the probabilistic heads are developed.

---

## Agent 5 — Adaptive calibration

**Scope**

Implement simple conformal calibration before relational methods.

**Deliverables**

- rolling normalised residual calibrator;
- separate state by asset and horizon;
- optional session subdivision;
- pooled asset-class and global fallbacks;
- coverage and width monitoring;
- configurable decay or ACI update;
- sparse-sample handling.

**Acceptance conditions**

- calibration uses only errors observable by the current timestamp;
- coverage reports are available by asset, horizon and session;
- fallback choice is recorded with every prediction;
- larger recent errors cannot produce narrower intervals, all else equal.

CoRel and CPTC should be separate plug-ins, not embedded into this first implementation.

---

## Agent 6 — Opportunity and meta-labeling model

**Scope**

Estimate post-cost opportunity quality.

**First implementation**

Use a pooled LightGBM model with asset, horizon and direction features.

**Candidate inputs**

- expected gross and net return;
- lower and upper forecast quantiles;
- calibrated interval width;
- recent coverage deficit;
- forecast-model disagreement;
- spread and estimated cost;
- volatility state;
- session and event state;
- current position and proposed direction.

**Candidate outputs**

- calibrated $P(r^{net}>0)$;
- expected realised net payoff;
- optional profit/stop/timeout probabilities.

**Deliverables**

- out-of-fold training pipeline;
- log-loss and Brier-score evaluation;
- isotonic or Platt calibration fitted on later validation data;
- reliability plots;
- feature ablation report;
- probability-bucket economic results.

**Acceptance conditions**

- no raw in-sample primary predictions enter training;
- reported probabilities are post-calibration;
- the model must beat a simple forecast-and-cost baseline;
- class weighting and focal loss are off by default.

---

## Agent 7 — Portfolio risk model

**Scope**

Produce the independent risk inputs required for joint sizing.

**Deliverables**

- rolling covariance matrix;
- shrinkage or regularisation;
- volatility forecast;
- asset-to-factor exposure matrix;
- currency, equity, region and asset-class groupings;
- stress covariance or correlation scenarios;
- risk-contribution calculations.

**Acceptance conditions**

- covariance matrices are positive semidefinite;
- missing assets have deterministic fallback behaviour;
- duplicate or nearly identical instruments create visible concentration;
- factor and marginal risk contributions reconcile with total portfolio risk.

This agent can work in parallel with Agents 4–6.

---

## Agent 8 — Cost-aware portfolio optimiser

**Scope**

Convert expected net opportunities into target positions.

**Implementation**

Use CVXPY initially.

**Deliverables**

- single-period convex optimiser;
- linear and quadratic transaction-cost terms;
- turnover penalty;
- gross, net, per-asset and group constraints;
- target-volatility constraint;
- current-holdings input;
- no-trade tolerance;
- infeasibility fallback;
- decision attribution.

**Acceptance conditions**

- higher expected cost cannot increase an otherwise identical target;
- increasing covariance between two similar positions reduces combined exposure;
- all constraints hold after rounding;
- zero alpha produces flat or unchanged positions;
- solver failure generates a deterministic safe target rather than partial output.

Multi-period model-predictive-control optimisation can be considered after the single-period version is stable.

---

## Agent 9 — Decision policy and operational state machine

**Scope**

Apply hard operational constraints around the optimiser.

**States**

```text
NORMAL
REDUCED
NO_NEW_POSITIONS
FLATTEN
UNAVAILABLE
```

**Deliverables**

- explicit state-transition rules;
- asset-specific availability;
- stale-forecast handling;
- calibration degradation handling;
- event/session restrictions;
- maximum permitted risk multiplier;
- structured reason codes.

**Acceptance conditions**

- hard vetoes are separate from learned opportunity probabilities;
- every suppressed or reduced trade has a reason code;
- state transitions are reproducible from recorded inputs;
- a regime classifier cannot silently override limits.

---

## Agent 10 — Walk-forward evaluation framework

**Scope**

Evaluate the complete decision stack without model-selection contamination.

**Deliverables**

- nested chronological walk-forward procedure;
- purging and embargo for overlapping outcomes;
- component-level and portfolio-level metrics;
- complete trial registry;
- parameter and feature ablations;
- cost and latency sensitivity;
- session and regime breakdowns;
- Deflated Sharpe Ratio and Probability of Backtest Overfitting reports.

**Acceptance conditions**

- every reported portfolio result maps to one immutable experiment configuration;
- rejected experiments remain in the trial registry;
- all comparisons share identical evaluation intervals;
- no selection is based on the final holdout;
- gross and net results are always reported together.

---

## Agent 11 — Relational conformal experiment

**Scope**

Implement CoRel-inspired calibration behind the Agent 5 interface.

**Deliverables**

- error-dependence graph learned from out-of-fold residuals;
- comparison with the forecasting graph;
- relational quantile or conformal adjustment;
- coverage/width comparison;
- portfolio-value ablation.

**Promotion criterion**

It must improve either:

- interval width at equal coverage; or
- final decision utility at comparable risk,

over the simpler adaptive calibrator.

It should not be promoted merely because the architecture is newer.

---

## Agent 12 — Change-point and session experiment

**Scope**

Test whether session transitions benefit from explicit state prediction.

**Deliverables**

- change-point or state model;
- empirical distribution of forecast errors around session events;
- comparison of shared, split and mixture-of-experts models;
- learned interval adjustment;
- daylight-saving-aware session definitions.

**Promotion criterion**

The evidence must support both the existence of the error shift and the proposed response. Do not pre-impose a 15-minute widening period.

---

## Agent 13 — Context-instrument experiment

**Scope**

Evaluate additional non-traded nodes, including Bitcoin.

**Deliverables**

- context-only node support;
- individual and grouped context ablations;
- predictive and portfolio-level incremental-value tests;
- graph and feature-importance diagnostics;
- stability across subperiods.

**Acceptance conditions**

- context instruments can never receive target weights;
- timestamp availability is enforced;
- improvement must persist after accounting for the additional experiment count;
- Bitcoin is treated as one candidate rather than a required feature.

---

# 12. Recommended build sequence

## Stage A — Minimum viable decision system

Build:

1. multi-horizon forecast contracts;
2. out-of-fold forecast store;
3. simple forecast combination;
4. cost estimates;
5. covariance risk model;
6. constrained portfolio optimiser;
7. walk-forward evaluation.

Do not begin with a meta-labeler, Monte Carlo dropout, CoRel or CPTC. This establishes whether the primary forecasts contain economically useful information and whether the portfolio mapping exploits it properly.

## Stage B — Probabilistic forecast and calibration

Add:

1. quantile or Student-$t$ outputs;
2. rolling adaptive calibration;
3. calibration monitoring;
4. lower-tail-aware portfolio policies.

Compare them against Stage A rather than replacing it without an ablation.

## Stage C — Opportunity model

Add a calibrated LightGBM opportunity model trained exclusively from out-of-fold primary forecasts.

Test:

- fixed-horizon net-return targets;
- three-state barrier targets;
- direct expected-payoff regression.

Keep it only if it improves final out-of-sample portfolio utility after costs.

## Stage D — Epistemic uncertainty

Add a small deep ensemble and measure whether disagreement predicts future forecast degradation after the adaptive calibration information is already present.

## Stage E — Research components

Independently test:

- relational conformal calibration;
- explicit session/change-point models;
- context-only Bitcoin and other instruments;
- TCN or Mamba temporal replacements;
- multi-period portfolio optimisation.

This sequencing prevents every attractive research idea from entering the first model simultaneously.

---

# 13. Core ablation matrix

At minimum, maintain the following named configurations:

```text
A0  Point forecasts + simple threshold
A1  Point forecasts + portfolio optimiser
A2  Multi-horizon probabilistic forecasts + optimiser
A3  A2 + adaptive conformal calibration
A4  A3 + calibrated opportunity model
A5  A4 + deep-ensemble disagreement
A6  A4 + relational conformal calibration
A7  A4 + session/change-point model
A8  A4 + additional context nodes
```

Each component must justify itself against the immediately simpler configuration.

The graph model should also be compared with:

- local models only;
- pooled non-graph model;
- static learned graph;
- observation-dependent graph;
- shuffled or sparsified graph.

This is necessary because an adaptive graph can act mainly as an additional parameterisation rather than proving that economically meaningful cross-asset links were learned.

---

# 14. Recommended initial production rule

The first defensible decision rule is simpler than the document’s final proposal:

1. Produce calibrated multi-horizon return distributions.
2. Subtract estimated costs.
3. Optionally discount alpha using one bounded reliability estimate.
4. Supply expected net returns, covariance and costs to the portfolio optimiser.
5. Apply only explicit operational vetoes outside the optimiser.
6. Record every input, forecast, target and reason code.
7. Re-evaluate all calibration and performance measures through chronological walk-forward tests.

Meta-labeling should initially be an experimental enhancement. Conformal intervals should initially calibrate forecasts and measure reliability. Neither should directly determine position size without the portfolio layer.

## Final recommendation

Proceed with the architecture, but define its centre as:

> **probabilistic multi-horizon alpha forecasts feeding a calibrated, cost-aware, constrained portfolio optimiser**

rather than:

> **a directional neural forecast multiplied by several confidence scores and a pseudo-Kelly fraction**

The residual graph model, adaptive conformal methods, meta-labeler, deep ensemble, session experts and Bitcoin context can all fit around that centre. Their inclusion should be decided independently through out-of-fold ablations.
