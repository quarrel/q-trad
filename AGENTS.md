# q-trad agent instructions

## Purpose and authority

q-trad exists to determine, as efficiently and honestly as possible, whether short-horizon
multi-asset forecasts can produce useful paper portfolio outcomes after realistic costs and joint
risk constraints. Negative results are useful evidence.

q-trad currently has one real operator and one research programme. Its internal research artefacts,
CLIs and Python APIs are not public interfaces and have no external compatibility obligation unless
an active document names a concrete retained result or operational dependency that requires one.
Design for the system that exists, not hypothetical future users.

Read the minimum authority needed for the task:

1. Always read `AGENTS.md` and the relevant active section of `PLAN.md`.
2. Read `docs/STATUS.md` when current operational or research state affects the work.
3. Read `docs/TRADING_RESEARCH.md` only for target, feature, forecast, evaluation, risk, portfolio or
   paper work.
4. Read the relevant part of `docs/ARCHITECTURE.md` only when changing a system boundary or
   implemented/intended flow.
5. Read accepted ADRs and task-specific runbooks only when touching their decision or operation.

`docs/archive/` is historical evidence, not routine context. Consult it only to reconstruct an
incident or decision, verify retained evidence or handle a compatibility boundary that still
affects current work. Keep active documents concise and archive completed chronology.

When an active implementation plan exists for the task, execute it rather than re-deriving a more
elaborate architecture. File lists and function names in plans are normally soft guidance; scientific,
evidence, safety and authority boundaries are hard constraints.

## Current phase

The current milestone is **R3 — cost and portfolio baseline**:

> retained source-specific forecasts and outcomes → explicit cost/risk states → horizon positions →
> global netting and constrained offline paper portfolio → later continuous shadow paper.

R0 and R1 are complete. The source-specific `R2-IBKR-HISTORICAL` experiment is also complete with
`VALID_CONSUMED_RESULT`: local Ridge versus zero return was negative, pooled local Ridge versus local
Ridge was positive, pooled beyond zero was inconclusive because that question was not frozen, and
pooled cross-asset Ridge was rejected at OOF. The result is limited to historical IBKR midpoint
research under the declared provider-history availability policy.

The Stage 6 → Stage 7 → Stage 8 → R1/R2 evidence-handoff simplification programme and the first
decision-grade R2 run are complete. Each transformation is proved once and that proof is reused
cheaply. R3 is next but has not started; begin it only under a new milestone authority that preserves
the completed R2 evidence and does not reinterpret its small pooled-versus-local improvement as
executable or post-cost alpha.

The reviewed `capture-v4` IG demo collector and the independent IBKR paper market-data collector are
operational data sources. Historical IBKR evidence is provenance-distinct from native IG evidence and
cannot substantiate native IG execution conclusions.

The old single-instrument strategy-ranking report is retained framework-proof evidence. It is not the
intended model/portfolio architecture and its negative cost-aware result is not an effectiveness claim.

R3 may implement explicit cost estimates, shrinkage risk, horizon positions, global netting,
constrained target portfolios and internal paper accounting. It also runs the active R3 plan’s
mandatory lightweight historical-exploratory lane, including one bounded tiny-configuration graph/GNN
feasibility check. That check is non-decision-grade and does not replace the full residual structural
graph experiment in R4. The integrated offline MVP remains a later milestone. Continuous live shadow
integration begins only after the offline MVP. It must not add a broker-order port, real-capital path
or production trading endpoint.

When experimental learning and operational hardening compete, prefer the shortest trustworthy
experiment unless missing hardening could make a result materially false, irreproducible, unsafe or
cause loss of required evidence.

## Experimental proportionality

q-trad is an experimental paper-trading research system whose immediate purpose is to obtain
trustworthy evidence quickly enough to prove or disprove the research idea. It is not being prepared
for real-capital trading or third-party distribution.

Apply rigour where an error could invalidate the current experiment, create false confidence, corrupt
retained evidence or cross a safety boundary. Do not import downstream evaluation,
operational-hardening, public-API, multi-tenant or production requirements into an earlier stage unless
their absence makes that stage's output dishonest or unusable.

Keep these distinctions explicit:

- artefact validity is not the same as evidential sufficiency;
- implementation evidence is not a decision-grade result;
- paper correctness is not production readiness;
- execution provenance is not semantic identity;
- verification is not promotion;
- a later consumer owns requirements that affect only its interpretation; and
- reversible prototype code remains simple until an observed need justifies generalisation or
  hardening.

Review objections must identify the concrete failure mode, the stage that owns it and how it could
mislead the current research decision. If they cannot, treat the objection as a follow-up or reject it
as premature complexity.

## Efficient evidence and identity

### Governing rule

**Prove a transformation once; authenticate that proof thereafter.**

For an ordinary evidence boundary:

1. authenticate the immediate parent's accepted receipt or promotion;
2. perform the current transformation once;
3. publish the current immutable artefact after bounded structural checks;
4. independently replay the current transformation once;
5. issue a create-only verification receipt; and
6. let ordinary descendants authenticate that receipt instead of replaying the transformation again.

A child proves the claims introduced by its own boundary. It does not recursively re-prove its
parent, grandparent or complete ancestry.

Deep audit remains available when a verifier is revoked, a defect may have affected an established
claim, or the operator explicitly requests a fresh audit. Deep audit is exceptional work, not the
ordinary construction, authentication or promotion path.

### Identity classes

Use these concepts deliberately and do not mix them into one convenient hash:

- **semantic identity** — what the data, configuration or result means under its contract;
- **closure identity** — the exact physical representation and declared child bytes of this artefact;
- **verification identity** — a create-only receipt proving that a named verifier completed a named
  check set against an exact artefact and immediate-parent authority; and
- **promotion identity** — explicit authority to use already-verified evidence at a consequential
  scientific boundary.

Execution provenance such as Git commit, application image, Python version or library versions is a
separate class. Include provenance in semantic identity only when the value changes the scientific
meaning or numerical policy being claimed. A documentation change, collector-health change or
unrelated q-trad commit must not silently become a new experiment.

Paths, creation timestamps, Parquet layout, JSON formatting, whole-application image identity and
physical child SHA-256 values are not semantic identity merely because they are easy to hash.

When changing an identity-bearing contract, classify every field as one of:

- semantic;
- closure/physical;
- provenance;
- verifier; or
- promotion/authority.

Record that classification in the PR description for non-trivial identity changes.

### Authentication and hashing

A manifest declares the expected closure and expected child byte identities. Ordinary authentication
must authenticate the manifest, exact expected tree and applicable receipt/promotion, but it need not
re-hash or decode every unused descendant byte on every call.

Hash and validate a child when the current operation actually consumes that child. A consumer that
uses every child will naturally authenticate every child once. A consumer that selects 14 of 120
parts should not hash or decode the other 106 merely to prove work it does not use.

Do not copy parent closures into child artefacts merely to make the child self-contained. q-trad has
no current requirement for independently portable evidence bundles. Bind the parent's semantic,
closure and verification/promotion identities instead.

### Verification receipts

A reusable verification receipt should be small, create-only and boundary-specific. It normally binds:

- artefact contract/version;
- semantic identity;
- closure identity and manifest identity;
- immediate-parent semantic and verification/promotion identities;
- verifier contract/version;
- explicit completed-check set;
- claim-scoped verifier identity where needed; and
- receipt identity.

Do not create signing infrastructure, PKI, transparency logs or a generic trust service. The relevant
threat model protects against implementation defects, accidental mutation, corruption, stale or
mismatched inputs, unsupported claims, look-ahead, holdout leakage and false provenance. It is not
required to resist the sole operator deliberately forging both evidence and verification records to
mislead themselves.

### Promotion

Promotion grants authority to already-verified evidence. It is not another semantic verifier.

A promotion should authenticate the exact verification receipt, confirm the required evidence/readiness
class and verifier acceptance, record explicit operator authorisation, and write a create-only
promotion. It must not repeat a deep parent or ancestry replay merely because the boundary is
consequential.

Holdout opening and consumption remain separate irreversible scientific authorities and are not
weakened by this rule.

### Avoid generic evidence frameworks

Prefer explicit stage-specific functions and small frozen values over a generic artefact/receipt
framework, registry or service hierarchy. Allow local duplication until concrete repeated uses show a
stable invariant and the abstraction reduces total code and cognitive burden.

An abstraction that merely moves the same complexity behind generic names is not a simplification.

## Product model

- A **target** defines one asset's realised return over a declared decision time and horizon, its
  availability time and dependency interval.
- A **point forecast** records expected return, return unit, feature/training cut-offs and immutable
  model/experiment/fold identity.
- An **out-of-fold forecast artefact** contains forecasts made without training on their targets and
  can be consumed without loading model code.
- A **cost estimate** describes expected physical trading costs separately from gross alpha.
- A **portfolio risk state** describes ordered joint covariance and configured group/currency
  exposures independently of forecast uncertainty.
- A **horizon sleeve** preserves asset/horizon intent, virtual position and model attribution.
- A **target portfolio** nets horizon intents, applies global constraints and records adjustment
  reason codes.
- A **paper evaluator** pairs decisions with subsequent executable-side evidence and independently
  reconciles gross performance, costs and net P&L.
- An **experiment evaluator** compares forecast, economic and portfolio outcomes in locked time
  order and retains failed/rejected configurations.

The offline MVP has no runtime strategy selector, learned regime gate or automatic model-promotion
lifecycle. Models and configurations remain experiments until held-out evidence supports retention.

## Research validity

Correctness is required where an error could create false confidence:

- no look-ahead in features, transformations, training, residuals, warm-up, calibration, risk,
  selection, target windows or outcome pairing;
- explicit source, receive, feature cut-off, decision, training cut-off and target-availability times
  in UTC;
- dependency-derived purging/embargo for overlapping target, feature and update windows;
- immutable dataset, experiment, fold, model and configuration identity;
- executable bid/ask evidence rather than midpoint fills where economic execution is being claimed;
- explicit spread, latency, adverse slippage, commission, financing and unsupported assumptions;
- `Decimal` for prices, quantities, currency conversion and money; explicit tested conversion to
  numerical model/risk arrays;
- known product economics, sessions and conversion for paper eligibility;
- visible gaps, dropped callbacks, stale inputs, unavailable assets and excluded intervals;
- no forward-filled executable prices and no silent coercion of missing markets into aligned panels;
- external history retains venue/product provenance and never masquerades as native IG fill evidence;
- top-of-book size changes are not called trade volume or CVD without a validated volume source;
- retained forecasts and outcomes for every compared configuration;
- independent P&L arithmetic, simple controls and component-aware ablations; and
- locked time-ordered out-of-sample evaluation before any effectiveness claim.

Rank correlation is one forecast input, not profitability. Forecast uncertainty, expected return,
portfolio covariance and operational validity remain separate concepts.

Do not simplify away controls that protect a concrete scientific failure mode. In particular retain:

- source-class separation;
- causal availability and target maturity;
- chronological folds, purging and embargo;
- holdout exclusion from feature/model/calibration/selection decisions;
- outcome-blind G1/G2 interfaces;
- immutable selection freeze;
- marker-first `OPENED` before holdout outcome access;
- irreversible `CONSUMED` terminal state; and
- exact-byte verification of the evidence actually consumed.

## Architecture and implementation

- Keep one modular Python application and image. Add a process only for demonstrated lifecycle or
  failure isolation.
- Dependency direction is `domain ← ports ← application ← adapters/runtime/API`.
- Domain code must not import frameworks, model libraries, provider libraries, environment
  configuration or filesystem code.
- Convert provider values at adapter boundaries and prevent provider identifiers from becoming
  canonical identity.
- Use frozen domain values, injected clocks and synchronous deterministic transformations.
- Keep `asyncio` at I/O/orchestration boundaries.
- Prefer functions and composition over pass-through service layers.
- Allow local duplication until at least two concrete uses demonstrate a stable shared invariant.
- Do not adopt archived package/class trees. Extend existing boundaries only when the active milestone
  exercises them.
- Do not add Redis, Kafka, Celery, TimescaleDB, React, Kubernetes, another datastore or another
  top-level runtime product without measured need and an ADR.
- Do not scaffold probabilistic forecasts, conformal calibration, session experts, dynamic graphs,
  context nodes, ensembles or multi-period optimisation before an active experiment uses them.
- Choose solver and numerical/model dependencies at milestone entry using version-specific
  documentation; no research suggestion alone makes a library an architectural requirement.
- Delete obsolete code when its replacement becomes authoritative. Git history is the archive for
  retired implementation, not the active runtime.

## Experimental compatibility, migration and evidence

Before the first decision-grade result, internal research schemas, events, CLIs and Python APIs may
change incompatibly. Default to the smallest clean current contract.

Compatibility requires a named current reason. Examples that can justify temporary compatibility are:

- a retained expensive artefact that has not yet been migrated;
- an active operational process that cannot be cut over in the same change; or
- a decision-grade result that explicitly cites the older contract.

The following are not sufficient reasons:

- an old fixture exists;
- an old test used the API;
- a schema once shipped on `main`;
- a hypothetical future consumer might need it; or
- deleting it feels risky despite no identified caller.

When compatibility is temporarily required:

1. name the exact retained evidence or operation requiring it;
2. keep the bridge outside the normal construction path where practical;
3. do not maintain dual writers;
4. define the migration/deletion trigger; and
5. delete the bridge in the same programme once migration succeeds.

A version bump does not imply that the old parser must survive. A one-time migration command is not a
permanent product feature.

Classify state before changing it:

- development databases, projections and failed local experiments are disposable unless retained by
  an experiment record;
- market data, manifests, forecasts and configurations cited by a retained result are research
  evidence;
- material collector failures may be retained as incident evidence; and
- the collectors' raw and canonical history is operational evidence until a reviewed retention or
  replacement decision says otherwise.

Never overwrite or selectively edit immutable research evidence in place. Migration creates new
artefacts, independently verifies them and records the relationship to the old evidence. Old evidence
may remain archived even after active code no longer reads its contract.

Never rewrite or selectively delete the running collectors' raw or canonical history. This does not
require future research schemas to read every experimental epoch.

## External I/O and collector safety

- Broker adapters are market-data-only: IG uses demo data and IBKR uses the paper environment. No
  production trading endpoint, broker-order port or order submission operation may exist.
- Treat broker, stream and database connections as explicit lifecycles with bounded readiness,
  recovery and shutdown.
- Readiness requires per-channel evidence; a connected socket or aggregate activity is insufficient.
  Quiet prices alone are not transport failure.
- Tag callbacks with a connection generation and ignore superseded work.
- Bound queues and make loss, lag and recovery exhaustion visible. An unbounded collector ends only
  as explicit `STOPPED` or truthful `FAILED`.
- Collector observation is read-only by default. Publication, deployment, provider experiments,
  evidence writes and cloud changes require the current runbook and explicit authority.
- For an authorised IG capture deployment, use `ops/capture/deploy.sh` as the sole normal operator
  entry point. It owns CI verification, backup, release installation, readiness checkpoints,
  activation, bounded observation, rollback and sanitised evidence. Do not manually decompose the
  release into ad-hoc SSH, `sleep`, `curl` and `jq` polling when the orchestrator is available.
- Use the documented `ops/ibkr/` orchestration for IBKR collector deployment, bounded capture,
  qualification and restore work; do not reconstruct those Docker/systemd paths ad hoc.
- Start one bounded orchestrator command and wait for its result. Do not emit progress updates for
  internal polling intervals or repeatedly inspect tiny remote steps. Inspect individual services
  only after the orchestrator fails or when diagnosing a recorded deployment incident.
- Never infer an instrument mapping, promote a quarantined product or change a reviewed universe
  without its explicit selection/publication authority.
- Never expose credentials, account identifiers, rendered secret-bearing configuration or session
  tokens in logs, fixtures, events, tools or version control.

## Working method

Before changing code, read the active `PLAN.md` milestone, relevant implemented contracts and tests,
plus `docs/TRADING_RESEARCH.md` for research/paper work. Confirm the change advances the active
research programme or protects a required correctness boundary.

For evidence/identity work, begin by writing down:

- the current boundary's immediate parent authority;
- the claims introduced by the current boundary;
- which work is transformation, independent verification, authentication and promotion;
- which bytes the ordinary consumer actually needs;
- the semantic/closure/provenance/verifier/promotion field classification; and
- the obsolete path that will be deleted when the new path lands.

Do not solve an efficiency problem by adding a cache in front of unnecessary work. First remove the
unnecessary work.

Before the first retained-scale execution of a new computationally significant pathway, run a
representative micro-sample through the exact production CLI and persistence path. Use a very small
slice of correctly shaped input, such as the first few bars, while preserving the real contracts,
source class, lineage, authority handoffs, schemas, boundary conditions and output verification. The
micro-run must exercise every planned deliverable and state transition; a shortcut fixture that skips
the code under test does not satisfy this gate. Treat the result as implementation evidence only, not
scientific evidence. Do not start a multi-hour or 10+ minute retained-data run until this micro-run
succeeds end to end; any failure blocks the larger run until the owning mechanism and regression are
fixed. A micro-run is representative only for the dimensions it actually exercises. Before
retained-scale launch, inventory every variable-length or nested output and its immediate persistence
and decoder limits. For cardinality, byte size, nesting depth, partition count, closure shape,
transaction boundaries and resource use, either exercise a retained-equivalent bound or use a bounded
construction or read-only projection from already-authorised inputs with explicit margin. Record the
projection and every untested assumption. A small row slice alone is insufficient.

### Retained-scale rerun escalation

A failed retained-scale or 10+ minute run changes the operating mode. Preserve its truthful state and
do not immediately rerun the next patch. Before another retained attempt:

1. perform a read-only look-ahead failure audit from the failing boundary through the next durable
   authority, not only the named child or stack frame that failed;
2. inventory every sibling output and variable-length or nested collection produced by the same path,
   every per-file, part, aggregate, memory and transaction limit, every create-only destination and
   orphan/symlink rule, and every downstream decoder, authenticator, verifier and promotion consumer;
3. use retained metadata and counts without outcome access, or bounded synthetic constructions with
   the same identity shape, to project each scale-sensitive boundary with explicit margin;
4. fix the owning mechanism, add regressions at the real trust and persistence boundary, and run the
   exact production CLI through every already-authorised planned deliverable and reversible or
   fail-closed state transition; a validation or rerun never grants provider, promotion, holdout,
   irreversible or other special-state authority; and
5. record the audit, projections, remaining assumptions, exact destinations and downstream stop
   conditions in the orchestrator-owned progress tracker named by the active plan, or another named
   durable execution record when no tracker exists, before authorising the rerun.

For this gate, the stage is the launched CLI path and every artefact it can emit before the next
durable authority; item 2 is its finite audit inventory. Search that inventory for the same failure
class. A fix for one oversized or malformed child is incomplete when a sibling emitted by the same
mechanism can fail next. A passing small fixture does not override contrary retained-scale evidence.

If two retained attempts in that stage fail before reaching the next durable authority, a third
attempt is blocked until a fresh independent review audits the finite inventory and its immediate
downstream gate against every applicable scale-sensitive dimension listed above, not merely the
failures already observed. Do not raise limits, shrink required evidence or weaken identity to make a
rerun pass. If an immutable artefact published successfully, reuse it for verifier or downstream
remediation; do not rebuild it merely because a later gate failed unless a demonstrated defect
invalidates it and the active authority explicitly requires a create-only replacement.

For a retained-scale command, or any command that may run or hang for 10+ minutes, establish a
delegable observation contract before launch: exact command and code identity, start time,
process/session identity, sanitised durable output or another reliable way to recover the final exit
result, resource/stop limits and the authority for
any follow-up action. Do not launch without it. When agent delegation is available, assign passive
monitoring to `gpt-5.6-luna` at `medium` reasoning, or the cheapest capable equivalent; otherwise use a
supported wait mechanism. The primary reasoning agent may inspect only on monitor notification,
expected completion or a defined resource/stop threshold, not by periodic polling of a silent healthy
process. A monitor may report liveness, resources and exit; it must not mutate the process or artefacts,
infer completion from activity, or exercise downstream authority.

### Agent delegation and programme state

When delegation is available and authorised:

- The orchestrator exclusively owns the progress record, task sequencing, branch/worktree lifecycle,
  publication, merges and irreversible or special-state actions.
- Give implementers bounded ownership, exact paths, known context, constraints and expected
  validation. Tell them other agents may be working concurrently and not to revert unrelated changes.
- Independent reviewers inspect an exact candidate head without modifying it. Any commit, amend,
  rebase or force-push invalidates exact-head review and any identity-sensitive validation.
- Use `gpt-5.6-luna` at `medium` reasoning, or the cheapest capable equivalent, for passive long-running monitoring.
- Batch independent reads and tool calls. Give delegated agents the facts already known rather than
  paying for rediscovery. Summarise closed phases in a concise durable record.
- Base heavy-task concurrency on measured peak memory, disk and CPU use, not merely the number of
  available agent slots.
- For a multi-stage programme, keep a durable progress record containing the exact base/head, agent
  ownership and status, completed validation and review, long-running process identities, artefact
  identities, failures, remaining authority gates and an exact resume sequence.
- If agents repeatedly fail to respond or hit the same infrastructure timeout, stop retrying,
  preserve the checkpoint and report the infrastructure blocker.

When performance is part of the change, prefer deterministic work-count evidence over fragile wall
clock assertions. Useful counts include:

- semantic verifier invocations;
- parent semantic replay invocations;
- files and bytes hashed;
- Parquet parts and rows decoded;
- feature recomputations; and
- model fits.

An ordinary downstream operation should normally have **zero parent semantic replay invocations**.

Run checks in proportion to the change:

- focused tests and static checks while iterating;
- `ops/dev/verify.sh` for the complete clean PostgreSQL, formatting, lint, typing and test gate at a
  milestone, schema or release boundary; do not substitute raw `uv run pytest`, which skips PostgreSQL
  integration unless a guarded `QTRAD_TEST_DATABASE_URL` has already been provisioned;
- the complete gate runs non-PostgreSQL tests concurrently with xdist and PostgreSQL tests serially.
  Non-PostgreSQL tests must be process-isolated and order-independent. Any test using
  `QTRAD_TEST_DATABASE_URL` or the shared test database must carry `pytest.mark.postgres`; do not
  parallelise that lane without per-worker database isolation. Use `ops/dev/verify.sh` rather than
  reproducing its selections manually;
- credential-gated or endurance checks only when their behaviour is under review; and
- for elapsed timing in development or operational shell runs, use Bash's `time` keyword or epoch
  timestamp arithmetic. The slim runtime image does not install `/usr/bin/time`.

For a PR that changes an evidence boundary, the PR description should state:

- the old redundant/incorrect work;
- the new immediate-parent handoff;
- semantic identity impact;
- physical/closure identity impact;
- retained evidence or migration impact;
- compatibility retained, if any, and its deletion trigger;
- work-count regression evidence; and
- exact validation run.

Update active documents only when their current claims change. Add or amend an ADR for a durable
architectural decision, not ordinary implementation detail or a reversible model experiment.

Stop rather than guess when an instrument mapping, timestamp, price basis, product economics,
currency conversion or historical-product equivalence is ambiguous; when a change could reach a
live broker endpoint; or when credentials/evidence could be exposed.

Use Python 3.13, `uv`, en-GB text, Ruff and strict typing in domain, ports and application code.
Unexpected required-field and computation failures propagate with context rather than becoming
plausible defaults.

## GitHub

- You have the `gh` CLI tool installed and authenticated locally via your shell context.
- To view actions, check PRs, or merge queues, run the standard CLI commands via your native shell
  execution tool (for example `gh pr list`, `gh run view`, `gh pr merge --auto`).

The GitHub fine-grained PAT used here cannot be granted Checks API access. This is an expected platform
limitation, not a missing permission or merge blocker. Verify CI through GitHub Actions workflow runs
for the exact commit instead of repeatedly raising unavailable check-run access.

GitHub Actions testing is temporarily paused. The `verify` job covers formatting, linting, typing and
shell checks only; run `ops/dev/verify.sh` locally for test evidence, and never treat a green GitHub
workflow as proof that tests passed.

## Contradiction and exception rule

Treat merged ADRs, scientific invariants, immutable evidence, holdout boundaries and preservation of
explicitly retained expensive work as hard constraints.

Treat suggested file lists, function locations, implementation tactics and PR sequencing details as
soft constraints unless expressly marked hard.

Compatibility is not a hard constraint merely because an older implementation exists. It becomes a
hard constraint only when an active authority identifies the retained evidence or operation that
requires it.

When a soft constraint prevents the smallest correct implementation, make the narrowest necessary
exception without requesting approval. Record the exception and rationale in the PR description.

Before claiming that an exception has no persistence or invalidation impact, trace all transitive:

- semantic identities;
- closure identities and physical byte hashes;
- provenance identities;
- verifier identities and receipts;
- promotion/authority identities;
- checkpoint identities;
- cache keys; and
- downstream authority boundaries.

Stop and request a decision only when the exception would:

- change scientific or readiness policy;
- weaken a holdout or causal-safety boundary;
- invalidate or discard retained evidence without a reviewed migration/equivalence path;
- require provider calls or reacquisition;
- access or affect holdout state;
- create an irreversible external state;
- reach a live/production trading endpoint; or
- materially enlarge the agreed PR boundary.

Never resolve a contradiction by silently weakening correctness merely to obey a tactical file
boundary. Equally, never preserve unnecessary complexity merely to avoid deleting an obsolete path.
