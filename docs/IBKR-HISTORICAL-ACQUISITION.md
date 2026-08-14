# IBKR Historical Acquisition — Staged Implementation Plan

## 1. Objective

Produce the first independently verified IBKR provider-history foundation without registering or running the `R2-IBKR-HISTORICAL` experiment and without consuming its holdout.

The work will:

* freeze the accepted canonical-to-IBKR contract mappings;
* prove the runtime identity used for acquisition;
* generate an immutable historical request plan;
* execute that plan through a restart-safe and pacing-aware state machine;
* publish independently authenticated request and aggregate results;
* convert verified results into provider-history observations using an explicit declared-availability policy;
* build and replay an IBKR-specific foundation;
* report whether the fixed six-target, three-group R2 entry gate is satisfied.

The initial acquisition range remains:

```text
[2026-02-01T00:00:00Z, 2026-08-02T00:00:00Z)
```

The initial historical request remains:

```text
bar size:       1 minute
whatToShow:     MIDPOINT
bar payload:    full midpoint OHLC
use RTH:        false
format date:    epoch
keep up to date:false
session data:   SCHEDULE
```

`MIDPOINT` names the IBKR price basis; it does not mean a close-only observation. Every accepted bar
retains its midpoint open, high, low and close. This is deliberately the first full-universe bootstrap
because it provides the smallest broadly supported, internally consistent request set for the fastest
trustworthy R2 forecasting test while native quote history accumulates. It does not declare midpoint
OHLC sufficient for every later volatility, spread, cost or execution question.

Historical availability is explicitly **declared rather than measured**:

```text
available_at = interval_end + PT5M
policy       = BAR_END_PLUS_DECLARED_PROVIDER_DELAY
```

IBKR’s current API documentation confirms epoch-formatted historical bars, `SCHEDULE` delivery through `historicalSchedule`, historical-data subscription requirements and historical pacing constraints.

## 2. Development principles

### 2.1 One trust boundary per pull request

No pull request should simultaneously introduce:

* a new immutable artifact contract;
* a new database execution state machine;
* a new provider transport;
* and a new downstream research interpretation.

Each stage below must be independently reviewable and releasable.

### 2.2 Builders and verifiers are separate paths

Every immutable artifact must have:

* a typed domain model;
* deterministic semantic identity;
* canonical serialization;
* create-only publication;
* an independent loader and verifier;
* mutation tests proving that important fields and child files are authenticated.

A verifier must not rely on in-memory objects supplied by the builder.

### 2.3 Operational state is not research evidence

PostgreSQL tables for plans, attempts, callbacks, pacing and recovery are operational state.

Immutable plan, request-result and aggregate-result manifests are the research evidence generated from that state.

The database may be resumed and repaired operationally. Published evidence may not be overwritten.

### 2.4 No plan mutation during execution

Once a historical plan is registered:

* request boundaries cannot be split or extended;
* contracts cannot be substituted;
* request parameters cannot be changed;
* successful request results cannot be replaced;
* failed requests remain part of the result.

A materially different acquisition strategy requires a new plan identity.

Retries of the same planned request are permitted only under the plan’s fixed retry policy.

### 2.5 No model-driven acquisition decisions

Contracts, acquisition ranges, chunk profiles and the six confirmatory candidates must be frozen without reference to model performance.

Operational canaries may determine safe request sizing. They must not inspect or optimise for predictive results.

---

# 3. Evidence model

## 3.1 Contract selection

Add create-only:

```text
qtrad-ibkr-contract-selection-v1
```

It binds:

* the completed capability review and its SHA-256;
* the source catalogue and probe specification;
* the API identity used for the capability review;
* the operator and freeze time;
* exactly one decision for each canonical instrument;
* the selected IBKR contract fingerprint;
* mapping acceptance separately from acquisition eligibility.

Decisions are:

```text
ACCEPTED_EXACT_CONTRACT
QUARANTINED
REJECTED
```

The initial selection contains the previously reviewed 20 unique mappings:

```text
FX
AUD/USD              14433401
EUR/USD              12087792
USD/JPY              15016059
GBP/USD              12087797
USD/CHF              12087820
USD/CAD               15016062
NZD/USD              39453441
EUR/JPY               14321016

Indices
Australia 200        111987484
US 500               111767871
Wall Street          111767879
US Tech 100          111767885
FTSE 100             111987412
Germany 40           111987422
Japan 225            111987469
EU Stocks 50         111987407
Hong Kong HS50       111987478

Commodities
Spot gold            457068913
Spot silver          457068916
US crude              738357708
```

### Canonical contract fingerprint

Do not freeze every descriptive field returned by IBKR as an identity field.

Define a typed, product-aware `IbkrContractFingerprint` containing identity-relevant fields such as:

* `conId`;
* security type;
* currency;
* exchange or routing destination;
* primary exchange where applicable;
* local symbol;
* trading class;
* multiplier where applicable;
* underlying `conId` where applicable;
* expiry or contract month where applicable.

Optional fields must be represented explicitly as absent rather than omitted unpredictably.

The complete original capability response remains authenticated through the capability-review digest. Reauthentication compares the canonical fingerprint.

A changed descriptive field that is not part of the fingerprint may be recorded as metadata drift without invalidating the mapping. A changed identity field produces immutable:

```text
CONTRACT_IDENTITY_CHANGED
```

and blocks acquisition for that contract.

## 3.2 Runtime lock

Add:

```text
qtrad-ibkr-acquisition-runtime-v1
```

This records the exact acquisition environment:

* official IB Gateway version and archive SHA-256;
* official Python API version and archive SHA-256;
* IBC version and archive SHA-256;
* q-trad commit;
* q-trad image digest;
* Python and relevant library versions;
* Gateway configuration identity;
* paper-account environment;
* API host, port and client-ID policy without secrets.

The proposed 10.49/10.49/3.24.1 stack may be used if its archives and compatibility are independently verified. It should be frozen by this artifact rather than embedded as an unchangeable assumption in application logic.

IBKR currently recommends using compatible current Stable or Latest Gateway/TWS and API releases.

## 3.3 Historical plan

Add:

```text
qtrad-ibkr-historical-plan-v1
```

The plan is a thin immutable collection of exact request identities.

It binds:

* contract-selection identity;
* runtime-lock identity;
* provider and paper environment;
* acquisition range;
* request-profile identity;
* planner application identity;
* all exact request definitions.

Each planned request contains:

* canonical instrument;
* complete contract fingerprint;
* request kind: `MIDPOINT_BARS` or `SCHEDULE`;
* exact UTC half-open interval;
* IBKR `endDateTime`;
* duration string;
* bar size;
* `whatToShow`;
* `useRTH`;
* `formatDate`;
* `keepUpToDate`;
* deterministic request identity.

The verifier reconstructs every request and proves:

* the expected contracts are present exactly once;
* bar intervals cover the range contiguously;
* accepted intervals neither overlap nor leave planner-created gaps;
* schedule coverage spans the required range;
* all parameters match the frozen request profile.

## 3.4 Request results

Add:

```text
qtrad-ibkr-historical-request-result-v2
```

One result represents one planned request, with separate durable operational and independently derived evidence outcomes.

It authenticates:

* plan and planned-request identities;
* all attempt identities and their reconstructed transitions;
* the first independently valid terminal attempt;
* generation, callback ownership and callback sequence;
* raw callback and completion-marker closure;
* accepted normalized rows or sessions;
* independently recomputed completion-marker counts;
* operational request/attempt disposition;
* evidence disposition;
* error classification;
* retry history;
* acquisition timing.

The operational terminal disposition describes the durable execution state. The evidence disposition describes what the independently replayed callback closure proves. A request can therefore have `request_status = SUCCEEDED` and `terminal_disposition = SUCCEEDED` while its evidence disposition is `NO_DATA_RETURNED`, `SESSION_EVIDENCE_UNAVAILABLE`, `INVALID_CALLBACK_EVIDENCE` or `CONFLICTING_CALLBACK_EVIDENCE`.

Possible evidence dispositions include:

```text
SUCCEEDED
NO_DATA_RETURNED
CONTRACT_IDENTITY_CHANGED
ENTITLEMENT_UNAVAILABLE
INVALID_REQUEST
RETRY_LIMIT_EXHAUSTED
PROVIDER_REJECTED
SESSION_EVIDENCE_UNAVAILABLE
INCOMPLETE_RESPONSE
INVALID_CALLBACK_EVIDENCE
CONFLICTING_CALLBACK_EVIDENCE
```

A successful operational result is accepted only after the verifier reconstructs the callback ownership, completion boundary, marker counts, error callbacks and normalized output. Earlier incomplete or superseded attempts remain authenticated evidence but do not contribute rows. A malformed raw closure fails verification rather than being repaired by a summary field.

## 3.5 Aggregate result

Add:

```text
qtrad-ibkr-historical-result-v2
```

It references:

* one historical plan;
* exactly one terminal result per planned request;
* the runtime lock;
* aggregate coverage and entitlement summaries;
* no unreferenced request-result children.

The aggregate verifier independently recomputes:

* plan completeness;
* request-result identity and evidence dispositions;
* returned interval coverage;
* active-session coverage;
* planned, terminal, operational-success, evidence-success, no-data and failure counts;
* rows and sessions across every planned chunk for an instrument;
* missing and conflicting rows;
* entitlement and failure dispositions;
* the set of contracts eligible for provider-history construction.

## 3.6 Provider-history observations

Add:

```text
qtrad-provider-historical-observations-v1
qtrad-provider-history-availability-selector-v1
```

Each observation contains:

* source class `IBKR_HISTORICAL_RESEARCH`;
* provider `ibkr`;
* environment `paper`;
* canonical instrument;
* exact contract-selection identity;
* interval start and end;
* MIDPOINT OHLC;
* request and result lineage;
* acquisition attempt and completion times;
* declared `available_at`;
* declared availability policy;
* correction policy;
* provider-schedule evidence;
* gap disposition.

It must not contain fabricated:

```text
received_at
persisted_at
```

The correction policy is:

```text
FROZEN_FIRST_SUCCESSFUL_RESPONSE_NO_REFETCH_MERGE
```

---

# 4. Staged development

## Stage 0 — Threat model and invariant matrix

**Form:** documentation-only pull request.

**Status:** Complete — the accepted threat model and invariant matrix are recorded in
[ADR 0029](adr/0029-ibkr-historical-acquisition-evidence-boundaries.md).

Write a short ADR defining:

* trusted and untrusted inputs;
* what each digest proves;
* what is mutable operational state;
* what becomes immutable evidence;
* which failures are retryable;
* what constitutes request success;
* what “independent replay” means at each layer;
* which absence claims are measured, provider-declared or unknown.

Create an invariant matrix covering:

```text
contract identity
runtime identity
plan identity
attempt identity
callback ownership
terminal result selection
file closure
availability semantics
session semantics
foundation lineage
```

### Exit criteria

* Every later artifact has a named trust boundary.
* The distinction between operational recovery and evidence immutability is explicit.
* No production code is added.

---

## Stage 1 — Contract selection and runtime-lock contracts

**Form:** domain and artifact-contract pull request.

Implement:

* typed contract fingerprints;
* contract-selection builder and verifier;
* runtime-lock builder and verifier;
* exact duplicate, substitution and missing-instrument rejection;
* create-only persistence;
* symlink, path-escape, unknown-field and overwrite rejection.

Add the contract-selection CLI:

```text
qtrad instruments select --provider ibkr \
  --capability-review <path> \
  --catalogue <canonical-catalogue-path> \
  --probe-spec <canonical-probe-spec-path> \
  --selection <operator-authored-path> \
  --frozen-by <operator> \
  --output <path>
```

Add a runtime-lock inspection command:

```text
qtrad historical ibkr runtime-lock \
  --gateway-archive <path> \
  --api-archive <path> \
  --ibc-archive <path> \
  --output <path>
```

This stage performs no socket connections and no acquisition.

### Exit criteria

* The 20 decisions reconstruct exactly from the capability review.
* Identity-relevant contract mutations fail verification.
* Non-identity descriptive drift can be represented separately.
* Runtime archives and application identity are authenticated.
* All tests run without IB Gateway.

---

## Stage 2 — Deterministic historical planner

**Form:** pure planning pull request.

Implement the typed request profile and deterministic planner.

The request profile includes:

* permitted bar and schedule duration strings;
* maximum in-flight requests;
* request timeout;
* retry count;
* pacing policy;
* duplicate-request protection;
* product-specific request duration where justified.

Do **not** initially fix the production plan to four-week bar requests. The planner accepts a frozen request profile as input.

Add:

```text
qtrad historical ibkr plan \
  --contract-selection <authenticated-selection-path> \
  --operator-selection <operator-selection-path> \
  --capability-review <capability-review-path> \
  --catalogue <canonical-catalogue-path> \
  --probe-spec <canonical-probe-spec-path> \
  --runtime-lock <authenticated-runtime-lock-path> \
  --gateway-archive <gateway-archive-path> \
  --api-archive <api-archive-path> \
  --ibc-archive <ibc-archive-path> \
  --expected-gateway-sha256 <gateway-sha256> \
  --expected-api-sha256 <api-sha256> \
  --expected-ibc-sha256 <ibc-sha256> \
  --expected-runtime-qtrad-commit <clean-application-commit> \
  --expected-runtime-image-digest <immutable-image-digest> \
  --expected-gateway-version <gateway-version> \
  --expected-api-version <api-version> \
  --expected-ibc-version <ibc-version> \
  --expected-api-host <gateway-host> \
  --expected-api-port <gateway-port> \
  --expected-client-id-policy <client-id-policy> \
  --request-profile <authenticated-request-profile-path> \
  --canary-evidence <authenticated-canary-evidence-path> \
  --profile-frozen-by <profile-operator> \
  --profile-frozen-at <profile-freeze-time> \
  --planner-image-digest <immutable-image-digest> \
  --start 2026-02-01T00:00:00Z \
  --end 2026-08-02T00:00:00Z \
  --output <plan-output-path>

qtrad historical ibkr plan-verify \
  --plan <plan-path> \
  --contract-selection <authenticated-selection-path> \
  --operator-selection <operator-selection-path> \
  --capability-review <capability-review-path> \
  --catalogue <canonical-catalogue-path> \
  --probe-spec <canonical-probe-spec-path> \
  --runtime-lock <authenticated-runtime-lock-path> \
  --gateway-archive <gateway-archive-path> \
  --api-archive <api-archive-path> \
  --ibc-archive <ibc-archive-path> \
  --expected-gateway-sha256 <gateway-sha256> \
  --expected-api-sha256 <api-sha256> \
  --expected-ibc-sha256 <ibc-sha256> \
  --expected-runtime-qtrad-commit <clean-application-commit> \
  --expected-runtime-image-digest <immutable-image-digest> \
  --expected-gateway-version <gateway-version> \
  --expected-api-version <api-version> \
  --expected-ibc-version <ibc-version> \
  --expected-api-host <gateway-host> \
  --expected-api-port <gateway-port> \
  --expected-client-id-policy <client-id-policy> \
  --request-profile <authenticated-request-profile-path> \
  --canary-evidence <authenticated-canary-evidence-path> \
  --profile-frozen-by <profile-operator> \
  --profile-frozen-at <profile-freeze-time> \
  --planner-image-digest <immutable-image-digest> \
  --start 2026-02-01T00:00:00Z \
  --end 2026-08-02T00:00:00Z
```

The planner is entirely independent of PostgreSQL and IB Gateway.

### Exit criteria

* Property tests prove exact half-open coverage.
* DST and calendar boundaries do not change UTC request ownership.
* Request identities are deterministic.
* Reordering source mappings does not change plan identity.
* Invalid profiles and unsafe request durations fail before execution.

---

## Stage 3 — Durable execution state machine

**Form:** database and application-state pull request.

**Status:** Implemented and verified with a fake historical-data port and disposable PostgreSQL state; no real IBKR client or provider data is involved.

Implement transport-independent execution using a fake historical-data port.

PostgreSQL stores:

* registered immutable plan bytes and identity;
* registered planned requests;
* pacing reservations bound to the frozen request profile and pacing policy;
* attempt starts;
* attempt terminal states;
* callback records namespaced by connection session ID, provider request ID and generation;
* completion markers;
* publication status.

Required properties:

* registration is idempotent only for byte-identical plan content;
* an attempt is persisted before provider I/O;
* callbacks are append-only;
* every callback carries connection session ID, provider request ID, connection generation and monotonic sequence;
* stale session or generation callbacks cannot enter a successful closure;
* selected-attempt publication is bound to plan and request identity and terminal disposition;
* a disconnect invalidates unfinished attempts;
* a completed successful planned request is never rerun;
* terminal failures do not block unrelated requests;
* crash recovery derives work from durable state rather than process memory.

The executor passes the frozen request-profile pacing policy to the durable ledger, which retains reservations for the maximum supported cooldown across pacing profiles. It uses a conservative internal rate below provider limits; IBKR documents identical-request, per-contract burst and 60-request-per-ten-minute historical pacing constraints.

### Exit criteria

* State-machine tests cover every transition.
* Crash injection is tested before request send, during callbacks, after completion and before publication.
* Restart resumes only unfinished or retryable requests.
* The stage has no dependency on the real IBKR client.

---

## Stage 4 — Result publication and independent verification

**Form:** immutable evidence pull request.

**Status:** Implemented and covered by deterministic file-only fixtures; no real IBKR client or provider data is involved.

Implement request-result and aggregate-result builders from database state.

Normalize historical bars as follows:

* provider timestamp is treated as bar start;
* one-minute end is derived deterministically;
* epoch values are normalized to UTC;
* accepted rows are clipped to the planned half-open interval;
* callback values are preserved;
* OHLC is serialized canonically;
* provider volume, WAP and count fields are retained without assigning unsupported meaning;
* rows are strictly ordered and uniquely keyed;
* invalid OHLC and conflicting duplicates produce explicit terminal evidence dispositions; malformed closure evidence fails verification;
* an eligible completion marker must copy the matching completion callback's transport identity, payload, eligibility and receive time, and its receive time must be no later than the attempt finalization time;
* completion callbacks and completion markers form an exact one-to-one closure, including ineligible completions; a SUCCEEDED attempt requires exactly one eligible marker;
* aggregate activity declarations are reconciled against all in-range structured sessions independent of callback order;
* provider request transport identities (connection_session_id, connection_generation, provider_request_id) are unique across all attempts;
* callbacks from incomplete or superseded generations remain evidence but cannot become accepted rows.

For provider midpoint callbacks returned outside an owning adjacent request, the planned half-open interval determines ownership: those callbacks remain raw boundary evidence and are not accepted rows. Completion ranges may span a request boundary, but must overlap the request and contain every retained in-range bar.

Treat `SCHEDULE` as **provider-declared session evidence**, not proof that quotes must exist throughout every session.

If schedule acquisition fails:

```text
session state = UNKNOWN
```

The interval must not be classified as inactive.

Schedule sessions are clipped to the planned half-open interval before activity is classified; contradictory overlapping in-range declarations produce explicit conflicting evidence.

Add:

```text
qtrad historical ibkr result-build \
  --plan <path> \
  --output <directory>

qtrad historical ibkr verify \
  --result <manifest>
```

### Exit criteria

* The verifier starts from files and independently reconstructs attempt, callback and marker closures.
* Every planned request has exactly one terminal result with separate operational and evidence dispositions.
* Missing, altered, additional and orphaned children fail.
* Reordered callback input produces the same result.
* Marker counts, callback eligibility, error-before-completion, attempt outcome consistency and first-terminal selection are independently recomputed; completion callbacks and markers are one-to-one, and published attempt, callback, completion-marker, and provider transport identities are unique across the aggregate;
* an invalidated attempt may have a terminal time with no terminal disposition and may precede a later successful retry.
* Conflicting or invalid provider evidence is never accepted as normalized output.
* The PostgreSQL snapshot is repeatable-read and bounded before callback materialization.
* Publication is staged, verified and create-only; the verified request-to-result mapping is committed atomically.

---

## Stage 5 — IBKR adapter, runtime deployment and request-profile canary

**Form:** provider-adapter pull request followed by an operational deployment.

**Software status:** The direct official TWS historical adapter, contract reauthentication,
generation-fenced MIDPOINT/SCHEDULE callback normalization, sanitized error evidence, bounded
cancellation/timeout handling, immutable canary evidence and file-only canary/profile operations are
implemented. Host deployment, account-gated reauthentication and the recorded request-profile canary
remain operational exit work; this implementation does not claim a qualified host or historical data.

Extend the shared IBKR session engine with:

* contract-detail reauthentication;
* historical bar requests;
* historical schedule requests;
* callback correlation;
* completion handling;
* error-code classification;
* generation invalidation;
* bounded cancellation and timeout handling.

Raw provider messages are not exported. Persist:

* numeric provider error code;
* normalized internal classification;
* a strictly sanitized diagnostic where safe;
* digest of the original provider message if required for identity.

### Host deployment

On `q-trad-2`:

1. Back up the existing Gateway installation, probe image and capability evidence.
2. Install the exact archives named in the runtime lock.
3. Build the matching q-trad image.
4. Verify effective localhost-only API access (TrustedIPs plus firewall) and paper-account identity.
5. Prove server time and current-generation handshake.
6. Reauthenticate all 20 contract fingerprints.
7. Run PostgreSQL migrations.
8. Verify writable evidence storage.

### Request-profile canary

Before freezing the full plan, run canaries on one representative contract from each product group:

```text
FX
index
commodity
```

The index representative must be an account-reviewed `CFD` independently verified to support `MIDPOINT`. This Stage 5 boundary is CFD-only: IBKR represents ETFs with `STK`, so an ETF requires a separate authenticated product model. Native `IND` contracts are excluded from this midpoint-only stage; `TRADES` bars are a different price basis and require a separately declared acquisition contract.

Test increasingly large request durations, for example:

```text
1 D
1 W
2 W
4 W
```

Stop increasing a product’s duration when requests:

* exceed the fixed timeout;
* trigger throttling or disconnect;
* return an operationally excessive closure;
* fail deterministic result verification;
* show inconsistent schedule behaviour.

Use several adjacent, non-identical intervals rather than immediately repeating an identical request.

Freeze the largest conservatively reliable duration per product class into:

```text
qtrad-ibkr-historical-request-profile-v1
```

Four-week chunks may be selected if demonstrated reliable. They are not assumed in advance.

### Supported canary execution

On the authorised paper-account host, after the runtime lock and contract selection have been
independently verified, run `ops/ibkr/verify-host.sh` first. Set
`QTRAD_IMAGE_DIGEST` to the exact immutable IBKR image reference verified by that command, then
set `QTRAD_IBKR_CLIENT_ID` to the ID reserved for continuous native capture and set
`QTRAD_IBKR_HISTORICAL_CLIENT_ID` to a different positive ID. Account probes, canaries and Stage 6
execution use only the historical ID and share one PostgreSQL advisory lock, so only one of those
operations can use it at a time. Execute the canary from that same image:

~~~bash
export QTRAD_IMAGE_DIGEST="${QTRAD_IBKR_IMAGE:?set the verified qtrad-ibkr@sha256 image reference}"
ops/ibkr/verify-host.sh
docker run --rm --network host \
  --env-file /srv/qtrad/ibkr/runtime.env \
  --env QTRAD_IMAGE_DIGEST="$QTRAD_IMAGE_DIGEST" \
  --volume /srv/qtrad/ibkr:/srv/qtrad/ibkr \
  "$QTRAD_IMAGE_DIGEST" \
  qtrad historical ibkr canary-run \
    --runtime-lock /srv/qtrad/ibkr/runtime-lock.json \
    --contract-selection /srv/qtrad/ibkr/contract-selection.json \
    --fx-representative-id fx:eur-usd \
    --index-representative-id index:australia-200 \
    --commodity-representative-id commodity:spot-gold \
    --anchor-end 2026-08-05T00:00:00Z \
    --output /srv/qtrad/ibkr/evidence/canary-2026-08-05.json \
    --execute-account-canary
~~~

The output is create-only qtrad-ibkr-historical-canary-v1 evidence. Full acquisition cannot begin
until the resulting evidence independently verifies; this command does not deploy the Gateway,
freeze a request profile, register a plan or start full acquisition. The required operational order
is: merge this change; publish the resulting main-commit qtrad-ibkr image; freeze the runtime lock
using that image digest; verify the host; then run the canary from that exact image.

### Exit criteria

* Contract reauthentication succeeds or produces immutable mismatch evidence.
* One-day MIDPOINT OHLC and `SCHEDULE` results independently verify.
* The request profile is based on recorded canary evidence.
* No full 20-contract plan has yet been registered.

---

## Stage 6 — Register and execute the full acquisition

**Form:** operational run, not a new feature pull request.

**Software status:** Immutable plan registration, durable execution, result publication and independent
verification are implemented on `main`. The account-gated operational run of the full acquisition and
its provider evidence remain pending; this implementation does not claim a completed historical run.

Generate the final immutable plan using:

* the frozen contract selection;
* the frozen runtime lock;
* the frozen request profile;
* the exact 26-week range.

The total request count is derived from that profile. It is not hardcoded as 160 unless the verified profile actually produces 140 bar requests and 20 schedule requests.

The execution command loads the immutable registered plan from PostgreSQL. It requires the frozen profile file separately because the registered plan binds the profile by identity but does not duplicate its policy fields.

Registration replays the complete lower-artifact closure and confirms the reconstructed plan before its first database write. Execution repeats that replay, verifies the runtime and deployment identity against the registered plan and frozen profile, and rejects any mismatch before provider construction or socket I/O. It also verifies the exact PostgreSQL request closure, canonical request columns, attempt state, selected-attempt relationship and terminal evidence.

Every newly opened provider connection reauthenticates the exact unique contract-fingerprint set for the plan. Historical requests start only after all current-generation reauthentication results are MATCH.
Commands:

```text
qtrad historical ibkr plan ...
qtrad historical ibkr plan-verify --plan <path>
qtrad historical ibkr register --plan <path> --confirm-plan-hash <sha256>
qtrad historical ibkr execute --plan-id <sha256> --request-profile <path>
qtrad historical ibkr result-build --plan <path> --output <directory>
qtrad historical ibkr verify --result <manifest>
```

Execution policy:

* reserve pacing before socket I/O;
* persist attempt before send;
* default to small bounded concurrency;
* use a 60-second request timeout unless the frozen profile specifies otherwise;
* permit at most five transient attempts;
* use full-jitter reconnect delay;
* never retry terminal contract or entitlement failures automatically;
* continue unrelated requests after individual failures.

A partial or entitlement-limited run is still valid immutable evidence.

After later permission changes, create a new plan referencing the original contract selection and prior result. Do not amend the original run.

### Exit criteria

* Every planned request has a verified terminal result.
* PostgreSQL state and immutable result manifests reconcile exactly.
* All failures are explicit and attributable.
* The aggregate result verifies independently.

---

## Stage 7 — Provider-history observation construction

A complete Stage 7 semantic verification must issue create-only reusable evidence bound to the exact
provider-history manifest, closure, immediate source identities and accepted verifier contract.
Ordinary descendants authenticate that receipt and the exact unchanged closure without replaying
Stage 6. Complete replay is reserved for confirmatory promotion, explicit deep audit or verifier
revocation.

**Form:** research-input pull request.

**Software status:** The completed rollout published and independently verified the v2 closure once,
preserving the 3,376,258-row semantic dataset while reducing 2,948 daily physical parts to 120 monthly
parts. Normal Stage 7/8 use is v2-only. The v1 writer, repacker, row decoder, deep verifier and new Stage
8/promotion routes are retired; retained v1 evidence supports only cheap exact-tree/receipt
authentication. Historical v1 deep audit remains available through exact commit
`f0e882bbcd19aabbefb1add2d87a03daae7670e8`.

Current v2 verification and v1/v2 authentication commands:

```text
qtrad research observations verify-provider-history \
  --manifest <v2-path> \
  --receipt-output <new-file>

qtrad research observations authenticate-provider-history \
  --manifest <path> \
  --receipt <path>
```

During the completed publication, the retired builder consumed only independently verified aggregate
results and wrote Stage 7 create-only. Bounded structural checks guarded atomic publication before one
independent semantic verification and create-only receipt persistence. Semantic-verification or
receipt-write failure retained the immutable closure as unclaimed. Current v2 verification preserves
those audit semantics; receipt files remain outside both the provider-history and embedded Stage 6
closures.

The availability selector union becomes:

```text
NATIVE_MEASURED_AVAILABILITY
PROVIDER_HISTORY_DECLARED_DELAY
```

Native observations continue using measured receipt or persistence timestamps.

Provider-history observations use:

```text
available_at = interval_end + declared_delay
```

The selector must authenticate:

* source class;
* result identity;
* declared delay;
* policy identity;
* recomputed availability;
* absence of native timestamps.

Unknown selectors and mixed native/provider inputs fail closed.

### Exit criteria

* Changing `PT5M` changes all dependent identities.
* Provider-history data cannot masquerade as measured native availability.
* Observation rows replay from request-result children.
* No model or foundation logic is introduced in this stage.

---

## Stage 8 — Source-specific foundation and readiness

Every structurally valid Stage 8 foundation is retained whether readiness is qualifying or
nonqualifying. Readiness gates downstream authority, not publication. Stage 8 independently
verifies its own transformation once from authenticated immediate Stage 7 evidence and issues a
reusable receipt; ordinary consumers authenticate that receipt rather than repeat data-scale replay.
Complete cumulative Stage 6-to-Stage 8 replay occurs only at confirmatory promotion, explicit deep
audit or verifier revocation.

**Form:** foundation integration pull request.

**Software status:** The source-specific builder, corrected per-block coverage policy and readiness
verifier are implemented on `main`. The retained foundation is `QUALIFYING_HISTORY_READY`; its
authenticated ordinary receipt grants `IMPLEMENTATION_EVIDENCE_ONLY`, and its separately authenticated
S8.4 promotion grants the confirmatory capability required before real IBKR F2. No real holdout result,
effectiveness claim or downstream R2 result has been created.

Extend foundation build and verification with mutually exclusive inputs:

```text
native observations
provider-history observations
```

Build the IBKR foundation over every successfully acquired accepted contract.

Predeclare the six confirmatory candidates:

```text
FX
AUD/USD
EUR/USD

Indices
Australia 200
US 500

Commodities
Spot gold
US crude
```

Predeclare the three groups:

```text
FX
indices
commodities
```

No candidate may be added or removed based on model outcomes.

The foundation verifier must replay:

* observations;
* provider-declared sessions;
* gaps;
* panels;
* targets;
* folds;
* common-support calculations;
* source and availability lineage.

Readiness may report:

```text
QUALIFYING_HISTORY_READY
INSUFFICIENT_HISTORY_FOR_MODEL_CONCLUSION
```

The latter must include exact causes such as:

```text
ENTITLEMENT_UNAVAILABLE
CONTRACT_IDENTITY_CHANGED
SESSION_EVIDENCE_UNAVAILABLE
INSUFFICIENT_COMMON_SUPPORT
INSUFFICIENT_BLOCK_COVERAGE
INSUFFICIENT_DURATION
INSUFFICIENT_ROWS
MISSING_CONFIRMATORY_TARGET
```

Provider gaps remain explicit evidence. Confirmatory readiness applies the frozen 90% R2
coverage threshold to persisted causal opportunities in each authenticated training,
validation, and holdout block; inactive opportunities are excluded, overlapping gaps are
counted once, and context-only gaps cannot block the fixed six candidates.

Stop before:

* R2 experiment registration;
* OOF construction;
* model selection;
* holdout access;
* effectiveness claims.

### Exit criteria

* The foundation independently replays from provider-history children.
* Readiness truthfully reports the six-target, three-group gate.
* No downstream R2 artifact has been created.

---

# 5. Test strategy

## Contract and planner tests

* Exact reconstruction of the 20 decisions.
* Duplicate `conId`, substitution and missing-instrument rejection.
* Product-aware fingerprint mutations.
* Deterministic request identity.
* Contiguous exact range coverage.
* DST, leap-day, weekend and session-boundary cases.
* Request-profile mutation and unknown-field rejection.

## Execution state-machine tests

* Registration races.
* Duplicate process execution.
* Crash before send.
* Crash after partial callback persistence.
* Disconnect before completion.
* Completion callback before final database transaction.
* Superseded generation callbacks.
* Retry-limit exhaustion.
* Permanent failure isolation.
* Pacing across restart.

## Provider protocol fixtures

Use genuine recorded callback shapes for:

* contract details;
* historical bars;
* historical schedule;
* historical completion;
* no-data response;
* entitlement failure;
* pacing rejection;
* connectivity loss;
* farm-status events;
* stale request IDs.

Fixtures must be sanitized and contain no account secrets.

## Evidence mutation tests

Mutate and rehash, where applicable:

* plan request parameters;
* contract fingerprint;
* runtime identity;
* callback values;
* callback sequence;
* accepted rows;
* session intervals;
* request disposition;
* availability delay;
* child references;
* closure files.

The verifier must reject semantic republishing, not merely stale hashes.

## End-to-end tests

* Fake transport through registration, execution, result publication and verification.
* Real Parquet or canonical persisted observation paths.
* Provider-history observation replay.
* Foundation replay.
* CLI round trips.
* `ops/dev/verify.sh` before every host deployment.

---

# 6. Operational completion

After the first full run:

1. Authenticate every Stage 6 request result and aggregate result.
2. Build or reuse the retained Stage 7 provider-history observations, perform complete semantic
   verification once, and persist the create-only Stage 7 receipt.
3. Run cheap Stage 8 preflight, then publish the source-specific foundation from authenticated Stage 7
   evidence and retain it whether readiness is qualifying or nonqualifying.
4. Independently verify the Stage 8 transformation once, persist its create-only receipt, and prove the
   ordinary authentication path performs no data-scale replay.
5. Record the readiness disposition. If it qualifies and confirmatory use is separately authorised,
   perform one cumulative replay from an immutable runtime and persist the confirmatory-promotion
   attestation; otherwise retain the valid nonqualifying foundation and stop.
6. Back up PostgreSQL and immutable artefacts; store raw market data outside Git.
7. Commit only sanitised identities, dispositions and documentation.
8. Update `PLAN.md`, `docs/STATUS.md`, `docs/ARCHITECTURE.md` and this runbook with the exact verified
   state.

The acquisition and verification system has two honest foundation outcomes:

```text
QUALIFYING_HISTORY_READY
```

or:

```text
INSUFFICIENT_HISTORY_FOR_MODEL_CONCLUSION
```

Both are valid retained publication and verification outcomes. Neither an ordinary verification
receipt nor qualifying readiness alone authorises real F2: that boundary additionally requires the
accepted confirmatory-promotion attestation. Implementation-only fixture work remains explicitly
separate.

The retained Stage 8 handoff is therefore:

```bash
uv run qtrad research foundation verify \
  --bundle <foundation.json> \
  --receipt-output <new-verification-receipt.json> \
  --replay-checkpoint-root <new-or-matching-verifier-replay-checkpoint-directory>
uv run qtrad research foundation authenticate \
  --bundle <foundation.json> \
  --receipt <verification-receipt.json>
```

The replay checkpoint is a verifier cache bound to the exact published foundation. It must be a
new directory or one previously created by `foundation verify`; a retained `foundation build
--checkpoint-root` is a distinct construction cache and cannot be reused here. Once provider-history
verification completes, its accepted receipt is persisted before Stage 8 derivation, so an interrupted
run on the same verifier identity resumes without repeating that semantic replay. Verified Stage 8
parts are likewise retained as they are compared.

Pass that same receipt as `--foundation-receipt` to implementation-only IBKR historical
commands. Ordinary authentication rehashes the exact provider/foundation child closure; it does not
replay Stage 7 rows or Stage 8 derivation and cannot be used as confirmatory authority.

For a qualifying foundation, create the separate S8.4 authority once from the authorised detached
runtime, then authenticate it cheaply:

```bash
uv run qtrad research foundation promote-confirmatory \
  --bundle <foundation.json> \
  --receipt <verification-receipt.json> \
  --authorized-by <operator> \
  --authorized-at <UTC-minute> \
  --authorization-reference <approval-reference> \
  --output <new-confirmatory-promotion.json>
uv run qtrad research foundation authenticate-promotion \
  --bundle <foundation.json> \
  --receipt <verification-receipt.json> \
  --promotion <confirmatory-promotion.json>
```

Confirmatory IBKR experiment, feature, readiness and OOF commands require both
`--foundation-receipt` and `--foundation-promotion`. The staged OOF retains both so real F2 replay
can authenticate promotion before any G2 authority is constructed.

# 7. Explicitly deferred work

The following remain outside this plan:

* historical BID or ASK acquisition;
* BID_ASK acquisition;
* one-second history;
* live IBKR capture;
* refetch merging or historical corrections;
* automatic contract substitution;
* adaptive mutation of a registered plan;
* R2 experiment registration;
* OOF model selection;
* confirmatory holdout execution;
* any effectiveness claim.

## Accepted amendment history — Verification reuse and Stage 8 publication

- **Status:** Accepted
- **Date:** 2026-08-11
- **Approval:** PR #108
- **Decision:** `docs/adr/0029-ibkr-historical-acquisition-evidence-boundaries.md`, Amendment 1

The accepted amendment is integrated into Stage 7, Stage 8 and operational completion above. It
separates artefact validity, execution provenance, scientific readiness and confirmatory authority;
uses reusable claim-scoped verification receipts for ordinary descendants; retains valid foundations
regardless of readiness; and reserves cumulative replay for confirmatory promotion, explicit audit or
verifier revocation. No Stage 6 or Stage 7 immutable evidence was invalidated.
