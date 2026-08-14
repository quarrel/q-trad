# Agent implementation plan: source-specific `R2-IBKR-HISTORICAL` representative integration

**Status:** Implemented and archived on 2026-08-14. The real-F2 promotion-attestation gate and current
evidence state now live in `docs/IBKR-HISTORICAL-ACQUISITION.md`, `docs/R2_IMPLEMENTATION_PLAN.md`,
`docs/ARCHITECTURE.md`, `PLAN.md` and `docs/STATUS.md`; no active authority depends on this file.

## Objective

This plan implemented the source-specific representative-integration path that allows authenticated
IBKR Stage 8 historical foundation evidence to exercise the existing R2.A–R2.F1 pipeline and produce
an independently replayable **R2.H software-verification bundle**.

An ordinary accepted Stage 8 verification receipt may support explicitly implementation-only work.
It is not confirmatory authority: real IBKR F2 additionally requires the qualifying,
immutable-runtime confirmatory-promotion attestation defined by ADR 0029 and S8.4.

---

## Scope boundary

### In scope

* A fixed representative-integration profile for `IBKR_HISTORICAL_RESEARCH`.
* Building an R2 experiment configuration from a verified Stage 8 IBKR foundation.
* Running the existing L0/L1/P0/P1 R2 pipeline against an IBKR historical foundation.
* Source-specific OOF and software-verification bundles.
* Complete synthetic/fixture integration proving the path before real Stage 8 evidence exists.
* CLI support and independent replay.
* Mutation, source-separation and holdout-isolation tests.
* Documentation updates recording implementation status.

### Out of scope

* Stage 6 execution, monitoring, recovery or result publication.
* Building Stage 7 or Stage 8 evidence from incomplete acquisition state.
* Changing the Stage 6 request plan or fixed six contracts.
* Confirmatory OOF execution.
* Real holdout feature materialisation or holdout evaluation.
* Model or feature selection based on partial Stage 6 data.
* Combining IBKR historical and IG-native observations in one foundation.
* Claims about IG-native products or execution.
* R3 portfolio or cost work.
* Gateway, IBC or deployment changes.

Do not read or interpret partial Stage 6 outcomes for experiment-design decisions.

---

## Fixed representative profile

Introduce a typed profile with a stable identifier such as:

```text
IBKR_HISTORICAL_V1
```

It must require exactly these six eligible targets:

```text
FX
fx:aud-usd
fx:eur-usd

INDEX
index:australia-200
index:us-500

COMMODITY
commodity:spot-gold
commodity:us-crude
```

Required semantics:

```text
market_data_source_class = IBKR_HISTORICAL_RESEARCH
evidence_class           = IMPLEMENTATION_EVIDENCE_ONLY
primary horizon          = 15 minutes
confirmatory targets     = the same fixed six
market groups            = FX, INDEX, COMMODITY
group cardinality        = exactly two targets per group
```

Required feature ladder:

```text
L0 = LOCAL_RETURNS + TIME_AVAILABILITY

L1 = LOCAL_RETURNS
   + TIME_AVAILABILITY
   + LOCAL_VOLATILITY_RANGE

P0 = LOCAL_RETURNS
   + TIME_AVAILABILITY
   + LOCAL_VOLATILITY_RANGE

P1 = LOCAL_RETURNS
   + TIME_AVAILABILITY
   + LOCAL_VOLATILITY_RANGE
   + POOLED_CROSS_ASSET
```

Required feature-family eligibility:

```text
LOCAL_RETURNS          = ELIGIBLE
TIME_AVAILABILITY      = ELIGIBLE
LOCAL_VOLATILITY_RANGE = ELIGIBLE
POOLED_CROSS_ASSET     = ELIGIBLE
SPREAD                 = NOT_ELIGIBLE
QUOTE_IMBALANCE        = NOT_ELIGIBLE
```

IBKR historical midpoint bars do not supply native executable spread or validated quote-size evidence. Do not synthesize those fields or weaken their existing gates.

Retain the existing numerical and selection policies unless the current fixed representative specification already declares otherwise:

```text
alpha grid                  = 0.01, 0.1, 1.0, 10.0
Ridge solver                = lsqr
Ridge tolerance             = 1e-8
Ridge maximum iterations    = 10_000
pooled weighting            = EQUAL_INSTRUMENT_TOTAL_WEIGHT_MEAN_ONE
minimum training rows       = 100
minimum inner validation    = 20
minimum outer validation    = 20
```

---

## Architectural requirements

### 1. Preserve the existing IG representative path

The current representative orchestration is explicitly capture-v4/IG-specific and hard-codes:

* `IG_NATIVE_CAPTURE`;
* the capture-v4 universe;
* the fixed capture-v4 target set; and
* IG-specific representative validation.

Do not replace or loosen it.

Refactor to a source-discriminated structure, for example:

```python
class RepresentativeIntegrationProfile(Enum):
    IG_CAPTURE_V4 = "IG_CAPTURE_V4"
    IBKR_HISTORICAL_V1 = "IBKR_HISTORICAL_V1"
```

Then use separate validators:

```python
_validate_representative_capture_v4(...)
_validate_representative_ibkr_historical_v1(...)
```

A generic dispatcher may select the validator, but each profile must retain strict independent invariants.

Do not implement one permissive validator containing optional source-dependent checks.

---

### 2. Build configuration from authenticated Stage 8 evidence

The existing builder derives identity-bearing configuration fields rather than requiring operators to
copy semantic IDs manually. After S8.3, ordinary setup must authenticate the exact Stage 8 foundation
and accepted receipt without deep replay. After S8.4, real F2 must also authenticate the separate
confirmatory-promotion attestation; an ordinary receipt remains sufficient only for an explicitly
implementation-only path.

Existing CLI shape:

```text
qtrad research baselines experiment-build \
  --foundation <verified-stage8-foundation-manifest> \
  --profile ibkr-historical-v1 \
  --output <new-experiment.json>
```

Use the Stage 8 authentication boundary as it lands. Do not add an alternate foundation parser or let
an ordinary verification receipt masquerade as confirmatory authority.

The builder must derive and bind:

* foundation bundle ID;
* observation dataset ID;
* foundation configuration ID;
* panel dataset ID;
* target dataset ID;
* fold dataset ID;
* application identity;
* image identity;
* exact ordered universe;
* instrument roles;
* configured horizons;
* holdout range;
* market-data source class.

It must reject before writing output when:

* the foundation is not independently verified;
* the foundation source is not `IBKR_HISTORICAL_RESEARCH`;
* any expected child identity differs;
* the fixed six are missing or substituted;
* group assignments differ;
* the primary horizon differs;
* unsupported feature families are marked eligible;
* holdout or fold semantics are inconsistent;
* the output already exists or contains unsafe path components.

Output must remain bounded, canonical and create-only.

---

### 3. Bind the representative profile into execution evidence

The representative profile must be authenticated by the OOF execution boundary.

The current OOF run descriptor should either:

* be versioned to include `representative_profile`; or
* be replaced with a new descriptor contract that explicitly carries it.

Do not silently add a field to an existing immutable v1 contract.

The descriptor should bind at least:

```text
representative_profile
foundation_bundle_id
experiment_configuration_id
market_data_source_class
evidence_class
feature-set names and identities
application identity
Python identity
NumPy identity
scikit-learn identity
holdout range
holdout_excluded = true
```

Independent verification must reconstruct the expected fixed profile from these bindings and the verified experiment/foundation.

---

### 4. Make R2.H software verification source-specific

The current software-verification path expects an IBKR-historical synthetic child and an IG-native representative child.  That is useful implementation evidence but cannot represent an independently completed `R2-IBKR-HISTORICAL` integration.

Do not weaken the existing v1 semantics.

Either:

1. introduce `qtrad-r2-software-verification-v2`; or
2. introduce a separate source-specific software-verification contract.

The new contract must explicitly bind:

```text
market_data_source_class
representative_profile
synthetic OOF bundle
representative OOF bundle
synthetic selection
representative selection
application identity
Python identity
NumPy identity
scikit-learn identity
representative integration disposition
research disposition
```

For this profile, require:

```text
market_data_source_class      = IBKR_HISTORICAL_RESEARCH
representative_profile        = IBKR_HISTORICAL_V1
representative integration    = READY
evidence disposition          = IMPLEMENTATION_EVIDENCE_ONLY
research disposition          = RESEARCH_EVIDENCE_PENDING
```

The verifier must reject:

* mixed-source children;
* IG representative children inside an IBKR bundle;
* confirmatory children inside an implementation-only bundle;
* different experiment IDs;
* different foundation IDs;
* mismatched profile IDs;
* mismatched runtime identities;
* altered child bytes;
* orphan or additional children;
* unsafe paths;
* holdout-containing OOF features.

The top-level bundle should remain thin and reference independently verified children. The existing bundle architecture is explicitly intended to preserve that separation.

---

## End-to-end representative flow

The completed path should support:

```text
verified Stage 8 IBKR foundation
        ↓
IBKR_HISTORICAL_V1 experiment config
        ↓
L0/L1/P0/P1 raw OOF features
        ↓
local and pooled preprocessing selections
        ↓
local Ridge, pooled-local Ridge and pooled-context Ridge fits
        ↓
OOF forecasts and explicit coverage
        ↓
evaluation and selection-mechanics evidence
        ↓
source-specific OOF bundle
        ↓
source-specific R2.H software-verification bundle
        ↓
independent file-only replay
```

All OOF feature generation must exclude the locked holdout. Existing feature contracts already require holdout exclusion; preserve that invariant.

No real holdout feature child is part of this task.

---

## Synthetic Stage 8 integration fixture

Create a complete synthetic IBKR-historical foundation fixture that exercises the actual source-specific path.

It must include:

* the fixed six instruments;
* two instruments in each of three groups;
* midpoint OHLC observations;
* provider-history availability semantics;
* source-active sessions;
* uneven listing start times;
* at least one genuine inactive-market interval;
* at least one known gap;
* at least one missing target;
* at least one source correction/revision;
* enough rows for local and pooled fitting;
* three chronological outer folds;
* a disposable holdout interval that remains excluded from OOF features;
* deterministic variation sufficient to avoid every model becoming degenerate;
* at least one feature-unavailable opportunity;
* explicit coverage dispositions;
* both successful and rejected model configurations where useful.

The fixture must pass through the same Stage 8 verifier and the same R2 loaders as a future real foundation. Do not construct in-memory objects that bypass file-boundary verification.

Use intentionally disposable implementation evidence:

```text
evidence_class = IMPLEMENTATION_EVIDENCE_ONLY
```

No effectiveness result should be asserted from this fixture.

---

## CLI work

Extend or add CLI operations sufficient to perform:

```text
experiment-build
features build / verify
OOF build / verify
selection freeze / verify
software bundle build / verify
```

Prefer extending the existing commands rather than adding parallel duplicate implementations.

Requirements:

* source/profile selection must be explicit or deterministically inferred from an authenticated experiment;
* every write must be create-only;
* all inputs must be independently loaded from files;
* provider or database access must not be required;
* no command may materialise real holdout features;
* output should report semantic IDs and source/evidence classes;
* error messages should identify source/profile mismatches clearly.

---

## Tests

### Profile validation

Add tests proving acceptance of the exact fixed profile and rejection of:

* one missing target;
* one substituted target;
* an additional target;
* duplicate group membership;
* fewer than two instruments in one group;
* an IG-native foundation;
* a native-IBKR foundation;
* an incorrect horizon;
* spread marked eligible;
* quote imbalance marked eligible;
* a changed alpha grid;
* a changed pooled weighting policy;
* a changed fixed numerical policy.

### Source separation

Prove that:

* the IG capture-v4 representative path still passes unchanged;
* an IG foundation cannot enter the IBKR profile;
* an IBKR foundation cannot enter the IG profile;
* OOF children from different source classes cannot share one bundle;
* a software bundle cannot mix source classes;
* source-class tampering changes or invalidates semantic identity.

### Holdout isolation

Prove that:

* no OOF feature row falls inside the holdout;
* selection evidence contains no holdout outcomes;
* software-bundle construction rejects a holdout-containing feature child;
* fixture holdout mechanics are clearly marked disposable;
* no command invoked by this path materialises real holdout features.

### Independent replay

Add mutation tests covering:

* foundation identity;
* experiment identity;
* representative profile;
* market-data source class;
* evidence class;
* feature child;
* preprocessing child;
* fit child;
* forecast child;
* coverage child;
* evaluation child;
* selection child;
* application/image identity;
* bundle child path;
* orphan child;
* additional child;
* changed child bytes with unchanged declared digest.

### CLI round trip

Exercise the complete file-based sequence:

```text
fixture Stage 8 foundation
→ experiment build
→ L0/L1/P0/P1 features
→ OOF build
→ OOF verify
→ selection freeze
→ selection verify
→ software build
→ software verify
```

The final verification must reconstruct and authenticate the complete closure without trusting builder objects.

### Existing behavior

Run all existing IG representative and synthetic R2 tests unchanged. A new IBKR path must not alter existing bundle IDs or accepted v1 semantics.

---

## Documentation

Update:

```text
PLAN.md
docs/STATUS.md
```

Record:

* the source-specific IBKR representative-integration implementation;
* that synthetic/file-only integration is complete;
* that real representative execution remains dependent on verified Stage 7/8 evidence;
* that confirmatory OOF and holdout remain unexecuted;
* that no IG-native conclusion is supported.

Do not mark R2.F2, R2.G1, R2.G2 or final research completion as complete.

Amendment 2 already authorises source-specific provider-history experiments, so do not rewrite the normative R2 requirements unless implementation reveals a genuine incompatible semantic change.

---

## Delivery sequencing

### PR implementation phase

Complete now, independently of the ongoing Stage 6 capture:

1. typed profile and validators;
2. foundation-bound experiment builder;
3. descriptor/source binding;
4. source-specific OOF orchestration;
5. source-specific software bundle;
6. complete synthetic Stage 8 integration fixture;
7. CLI round trip;
8. mutation and source-separation tests;
9. documentation.

### Real evidence phase

After the revised Stage 7/8 handoff is complete:

1. authenticate the retained Stage 6 closure and accepted Stage 7 receipt;
2. authenticate the published Stage 8 foundation and accepted verification receipt;
3. retain a nonqualifying foundation without downstream confirmatory work; or, if readiness qualifies
   and the operation is separately authorised, create and authenticate the confirmatory-promotion
   attestation before real F2;
4. run the `IBKR_HISTORICAL_V1` experiment builder under the applicable evidence class;
5. produce implementation-only L0/L1/P0/P1 feature evidence where explicitly intended;
6. run representative OOF integration only under its authorised boundary;
7. independently verify the OOF bundle;
8. build and verify the source-specific R2.H software bundle; and
9. retain the resulting readiness disposition.

The real evidence run should be a separate evidence/operations PR or retained execution record, not
mixed into a software implementation PR.

---

## Acceptance criteria

The PR is complete when:

* the exact fixed six-instrument IBKR profile is implemented;
* the existing IG representative path is unchanged;
* an authenticated Stage 8 foundation can deterministically produce an IBKR experiment configuration;
* the full L0/L1/P0/P1 implementation-only OOF path works from authenticated fixture files;
* the resulting OOF bundle is explicitly `IBKR_HISTORICAL_RESEARCH`;
* the resulting software bundle is source-specific and independently replayable;
* mixed-source and holdout-contaminated closures fail closed;
* no real holdout is materialised;
* no Stage 6 provider or database access occurs;
* an ordinary Stage 8 receipt cannot authorise real F2, which requires the S8.4 confirmatory promotion;
* all focused tests pass;
* `ops/dev/verify.sh` passes;
* exact-head CI passes; and
* documentation makes no research-effectiveness claim.
