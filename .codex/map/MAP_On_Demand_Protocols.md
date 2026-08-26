# Multi-Agent Programme orchestrator — on-demand protocols

## Version 0.9 (25 August 2026)

Read only a section triggered by MAP_Orchestrator.md or explicitly referenced in a delegated packet.

These procedures elaborate the kernel. They do not grant authority, expand scope, or turn planning advice into requirements.

---

## BOOTSTRAP_COMPILE

Use before the first repository or evidence mutation.

### Establish authority and baseline

Establish only what the task needs:

    explicit operator task and requested completion boundary
    governing repository instructions and applicable external authority
    optional planning artifact and its provenance
    repository root and current checkout/head
    upstream/default branch and remote identities
    tracked and untracked local state
    relevant branches, worktrees, PRs, reviews, and CI
    required tools, credentials, services, and environment constraints
    validation semantics and likely expensive checks
    retained MAP state relevant to this task

Do not assume origin/main, a clean checkout, an available remote, permission to discard local state, or permission to merge. The standard delivery path is a GitHub PR, but exact repository and branch state must be observed.

Protect existing operator-owned state. If required baseline or authority cannot be established, block only the affected mutation.

### Interpret the task and optional plan

Read the complete operator-supplied plan before compiling from it.

Create a compact source map when useful:

    source_ref | provenance | classification | concise interpretation | revalidation

Allowed MAP classifications are:

    BINDING
    OBSERVED
    ADVISORY
    DECISION_REQUIRED

Verify every BINDING entry against its cited source. A label cannot create authority.

Never treat an unclassified plan as binding in full. Determine material statements from provenance, wording, governing authority, and explicit operator instruction. If the operator required exact plan adherence, record that authority and any conflict with higher constraints. If required meaning remains materially ambiguous, ask or block dependent work.

Revalidate observations only when they affect a live decision. Adopt, revise, defer, or reject advisory material based on evidence. Resolve decision-required material before dependent mutation unless the decision belongs to the operator.

### Choose scale, uncertainty and task shape

Choose DIRECT, DELEGATED, or PROGRAMME using task coupling, ownership conflicts, independent judgment, genuine parallelism, validation/risk, controlled operations, duration/recovery and coordination/token cost.

Classify independently:

    specification_certainty: CLEAR | UNCLEAR
    implementation_uncertainty: LOW | MEDIUM | HIGH
    task_shape: SIMPLE | COMPOSITE

A delivery item is COMPOSITE when several trust boundaries, immediate consumers, retained/create-only operations, causal/identity semantics, accounting joins, numerical failure policies, resource limits or independently verifiable changes prevent one small discriminating packet.

Prefer DIRECT unless delegation has concrete value. Use multiple implementers only for a clear contract with real implementation uncertainty and discriminating evidence. Resolve or escalate unclear meaning; decompose composite work before broad candidate search.

### Compile the live execution model

For DIRECT work, a concise internal checklist is enough.

For DELEGATED work, define one complete owner packet.

For PROGRAMME work, record a compact manifest:

    work_item:
        id | logical lineage | purpose | authority/context refs |
        task shape | specification/implementation uncertainty |
        deps/start/merge gates | custody | generation/candidate budgets |
        expected outputs | mutation scope | acceptance matrix |
        immediate downstream gate | invariants/capsules |
        checks | state/blocker

    ownership:
        surface | mutation owner | consumers | conflict risk

    invariant:
        id | owner | contract | discharge condition

When a plan is substantial, maintain a proportional coverage map:

    source_ref | classification | execution disposition | execution ref | rationale/evidence

The live model records execution decisions; it neither replaces source authority nor promotes non-binding plan material.

Audit once for omitted requirements, unsupported classifications, hidden unresolved decisions, incorrect dependencies, overlapping ownership, lost safety or compatibility constraints, and non-discriminating validation.

Begin the first useful action immediately when unblocked.

---

## LOCAL_CUSTODY

Use before delegating a bounded implementation/review work item to `map_item_owner`.

### Preconditions

Establish:

    exact item and stable logical_item_id
    task_shape and specification/implementation uncertainty
    exact permitted base SHA and safe working root
    binding authority and separately classified context refs
    satisfied gates/dependencies and owned/prohibited surfaces
    expected outputs, acceptance matrix, immediate downstream gate, invariants/capsules
    mandatory checks and optional exploration
    explicit generation_budget and total candidate_budget
    environment mutation and Git/PR delivery contract
    local decision rights and escalation boundary
    resolved protocols_path and applicable sections
    receipt paths

Do not delegate unresolved cross-item ownership, authority conflicts, protected-operation authorization, or binding task meaning.

### Spawn

Spawn one primary `map_item_owner` with the smallest context needed. Prefer no inherited conversation when exact references and runtime state are sufficient.

Set `protocols_path` to the absolute resolved value of `PROTOCOLS`, not the bare symbolic name. Include only protocol section names expected by this custody unit. These references provide procedure and do not grant authority.

Do not separately spawn programme-visible `map_implementer` and `map_reviewer` children for the same ordinary item. The owner contains that relationship.

### While delegated

Treat the owner subtree as a local context boundary. Do not request routine progress, diffs, logs, findings, or child summaries.

Continue independent work. When genuinely idle, use event-aware waiting or one suitably long wait. Reconcile states after a wait or material event; do not stack short polls.

React only to:

    READY_FOR_ACCEPTANCE
    NO_CHANGE
    DECOMPOSE_REQUIRED
    ESCALATE
    BLOCKED

Resolve only the programme-level decision. DECOMPOSE_REQUIRED terminates the current logical search lineage when evidence shows a composite/non-discriminating unit: preserve exact state, use the failure clusters and look-ahead evidence to split it, and do not reset its budget through a new owner or name. ESCALATE terminates a CLEAR, SIMPLE, indivisible lineage whose budget is exhausted: preserve the same evidence and require an explicit decision to change model/approach/boundary, abandon it, or seek operator direction. Return a changed boundary to the same owner only when the original lineage budget remains live.

### Acceptance

READY_FOR_ACCEPTANCE is evidence, not programme acceptance. The orchestrator still verifies exact identity, scope, required outputs, validation, findings, review requirements, PR state, and cross-item gates.

---

## LONGRUN

Use for a predictably substantial elapsed-time command, CI run, build, migration, data operation, or any job that would otherwise invite repeated model polling.

Keep task-local jobs beneath their owner. The job owner retains all launch, retry, cancellation, repair, acceptance, and follow-up authority.

### Choose the observation mode

Prefer one blocking invocation when the current actor can wait safely and the operation is expected to complete without repeated model turns. Do not create another agent merely to wrap a naturally blocking tool call.

Delegate observation to the `map_passive_monitor` custom agent when the wait is predictably long, multi-hour, concurrent with useful owner work, detached or external, or otherwise likely to consume repeated coordinating or implementation-model turns. Use it only when the monitor can access a trustworthy read-only observation mechanism.

`map_passive_monitor` is a leaf role pinned in its project agent definition to `gpt-5.6-luna` with `medium` reasoning. Do not restate or override its model in the packet. Its lower-cost configuration is part of the role boundary, not authority to perform work.

### Observation packet

Before starting or delegating observation, record as applicable:

    job:
    owner:
    exact_sha_and_working_root:
    exact_command_or_operation:
    authority_and_capsule_refs:
    observation_mechanism:
    permitted_read_only_observations:
    success_criterion:
    expected_phases_and_approximate_duration:
    durable_terminal_evidence:
    thresholds_and_material_anomalies:
    wait_strategy_and_observation_boundary:
    permitted_retries:
    output_and_receipt_refs:
    prohibited_actions:
    receipt_format:

When spawning `map_passive_monitor`, send this packet without programme narrative. Omit only fields that truly do not apply. The packet grants observation access only; it never grants authority to start, stop, cancel, retry, repair, mutate, publish, accept, merge, clean up, or perform a follow-up action.

Prefer:

    event or service completion
    one suitably long session or process wait
    detached process with a durable completion marker
    GitHub checks or equivalent terminal-state mechanism
    coarse status polling only when no safe wait or event mechanism exists

A PID alone is not durable identity. Never send or store credentials. Do not repeatedly read growing logs merely to establish liveness. Partial output, activity, elapsed time, or a successful intermediate phase is not completion.

A wait timeout is an observation boundary, not evidence of job failure and not authority to change the operation. The owner decides whether to continue observing or take an authorized action.

The observer returns exactly one compact receipt:

    job=<id>
    status=PASS|FAIL|BLOCKED|LOST|ANOMALY|MONITORING_UNRELIABLE|CHECKPOINT|OBSERVATION_BOUNDARY
    terminal=<true|false>
    evidence=<durable exact refs>
    observed_at=<timestamp or event identity>
    detail=<minimum useful classification>
    next_action_authority=<owner>

The owner reconciles the receipt against live state before acting. A source-changing failure returns to its implementation owner; neither a passive monitor nor another job custodian silently becomes an implementer.

---

## EXPENSIVE_EXECUTION

Use before the first retained-scale or otherwise expensive execution, and after a failed expensive execution before authorizing a rerun. An execution is expensive when its elapsed time, compute, storage, external cost, retained output, operational consequence, or recovery burden makes blind failure or replay materially costly.

Use LONGRUN for observation mechanics. This protocol adds the evidence and authorization gates for launching and repeating expensive work.

### Preflight before the first expensive run

Before launch:

1. Resolve exact operational values from authoritative inputs; never infer paths, identities, mappings, universes or commands from abbreviations or conversational summaries.
2. Run correctly shaped authorized inputs through the exact production entry point and every reversible/fail-closed gate up to mutation, including schemas, persistence, planned outputs, state transitions and verifier.
3. Inventory variable-length/nested outputs, file/part/decoder limits, memory/disk, transactions, create-only destinations and other scale-sensitive edges.
4. Project retained counts, bytes, partitions, peak memory/disk, elapsed time and safe concurrency from authorized metadata, bounded constructions or representative measurements with explicit margins.
5. Trace every output and sibling through immediate consumers, authenticators, verifiers, publication gates and other boundaries that could reject an otherwise successful producer.
6. Record untested assumptions, exact source/destination identities, stop conditions and evidence needed to retire each uncertainty.

A successful sample proves only the dimensions it exercised. It does not override contrary retained-scale evidence or discharge an untested scale boundary.

Do not launch until the preflight establishes a credible path within authority and resource limits, or the operator explicitly resolves the documented uncertainty.

### Observation contract

Before launch, record:

    run ID and owner
    exact code, configuration, input, and destination identities
    exact command or operation
    start time and process or session identity
    expected phases and approximate duration
    durable sanitized output and recovery path for the final exit result
    memory, disk, time, cost, and other stop thresholds
    required terminal markers, final artifacts, exit status, and downstream verification
    permitted retries and retry destination policy
    authority for every follow-up external, publication, promotion, deletion, or irreversible action

Prefer passive event or session waits. A wait timeout is an observation boundary, not evidence of failure. Inspect only after a reported state change, expected completion, a crossed threshold, material anomaly, or process exit. CPU activity, elapsed time, partial files, checkpoints, staging state, or growing logs do not establish completion.

Require the declared terminal marker, final artifact, exit result, and verification evidence before reporting success. Monitoring and successful validation do not grant follow-up authority.

### Failure and rerun discipline

A failed expensive run changes the operating mode. Preserve its truthful state and do not immediately replay the next patch.

Before another attempt:

1. Classify the failure and identify the mechanism that owns the violated invariant.
2. Audit forward from the failure through the next durable boundary, including sibling outputs, aggregate limits, decoders, authenticators, verifiers, and publication gates.
3. Search the finite affected path for other instances of the same failure class.
4. Correct the owning mechanism and add a regression at the real boundary.
5. Exercise the exact production path through all reversible and fail-closed transitions before approving another retained run.
6. Use a fresh create-only destination. Never delete, overwrite, or reuse a partial retained destination merely to make a retry convenient.
7. Reuse valid immutable upstream artifacts when a downstream gate failed; do not replay expensive successful work without evidence that it was invalid.
8. Update the scale projection, observation contract, residual assumptions, and stop conditions from the failure evidence.

After two retained attempts fail before the same durable boundary, require fresh independent review before authorizing a third. Do not repeatedly replay an identical infrastructure timeout or spin indefinitely; preserve a resumable checkpoint and report the smallest blocker.

### Evidence and discharge

For retained evidence, record semantic identity, physical closure, verification status, and execution provenance separately. Classify disposable development outputs explicitly so cleanup cannot be confused with retained evidence.

Discharge the protocol only when the run has a truthful terminal classification, required evidence is durable, downstream gates are resolved or explicitly deferred, retry authority is no longer live, and remaining cleanup or external actions retain their own authority gates.

---

## CONTROLLED_OPERATIONS

Use before a protected, destructive, irreversible, production, migration, retained-evidence, security-sensitive, compatibility-deletion or otherwise consequential operation. A task or plan does not grant separately protected authority.

Progress through explicit states:

    AUTHORITY_DEFINED
    -> INPUTS_RESOLVED
    -> PREFLIGHT_EXECUTED
    -> CAPSULE_REVIEWED
    -> EXECUTION_AUTHORISED
    -> EXECUTED

Record each transition durably. Validation and capsule construction do not grant execution authority.

The machine-readable capsule binds:

    capsule ID and state
    authority references and designated owner/executor
    exact argv array, cwd, code/config identity
    exact input paths and semantic/closure/authority identities
    exact destination and create-only/overwrite rule
    allowed and forbidden reads and mutations
    exact permitted tool read/search scopes, required globs/exclusions, and output bounds
    resource, time and stop limits
    executed preflight receipt against the actual authorized inputs
    independent capsule-review identity and findings
    success/terminal evidence
    failure, rollback, orphan and partial-destination semantics
    retry count, destination policy and retry authority
    deletion or discharge trigger

Operational values come from authoritative files or observed state, never an abbreviation, naming convention or conversational summary. The preflight must exercise actual authorized inputs through every reversible gate, including cardinality, subset/order semantics, representation limits and resource projections. A synthetic fixture discharges only properties it actually represents.

For a read-restricted operation, tools whose default scope may extend beyond the working root must receive an explicit absolute scope and required exclusions; omitted or fallback scope is prohibited. Instructions and capsule fields are audit controls, not a confidentiality sandbox. When prohibited inputs must be technically unreadable, run the task in an environment that cannot access them.

Only after exact inputs are resolved, preflight has executed, the capsule is independently reviewed and applicable authority explicitly moves it to EXECUTION_AUTHORISED may the designated executor run the exact argv. The executor cannot fill blanks, widen scope or reinterpret the capsule.

On failure preserve authority and useful evidence; do not reuse an ambiguous partial destination, perform downstream deletion, invent replacement identity/path/mapping/equivalence, or retry without the recorded authority. Keep the capsule active until deliberately discharged.

---

## GITHUB_PR

Use before branch push, PR creation/update, review publication, or merge.

All candidate-specific conclusions bind to an exact commit SHA.

### Branch, push, and PR

Before acting establish:

    repository and remote identity
    permitted base and current candidate SHA
    branch ownership
    push authorization and destination
    target branch
    existing PR identity, if any
    required checks and review policy
    merge authorization boundary

Never force-push, overwrite another owner's branch, or rewrite published history without explicit authority.

Prefer one deterministic sequence:

    verify candidate and branch state
    -> push
    -> create or update PR
    -> verify resulting PR head and target
    -> record receipt

Keep the PR title and description concise and grounded in actual tracked changes and controlling task references. Do not paste plans, internal transcripts, sensitive state, large logs, or broad diffs.

Pass Markdown titles, bodies, comments and reviews as structured API data or through a safely created body file. Never interpolate such content into shell source: backticks and command substitutions remain executable even when the content was JSON-encoded for insertion into a command string.

An open, non-draft, mergeable, approved, or green PR is not acceptance by itself.

### Review publication

Bind the verdict to the exact reviewed SHA. Immediately before publication, recheck that the PR head equals that SHA. If the head cannot be established, retain and return the local verdict but do not publish it.

Publish one complete verdict through the repository's normal review mechanism. If GitHub prevents formal self-review because author and reviewer identity are the same, publish the structured verdict once as a top-level PR comment instead. Do not publish both.

A delegated owner or reviewer may publish only when its packet permits it and it independently reviewed the exact head without modifying candidate content. Review publication is not merge authorization.

### Head movement

If the PR head changes after candidate-specific validation or review:

- invalidate affected conclusions;
- identify the delta and changed assumptions;
- rerun affected checks;
- obtain a fresh exact-head verdict where required;
- do not merge under stale authorization.

### Merge

The orchestrator authorizes one exact accepted candidate SHA only when the operator request or governing workflow grants merge authority.

Immediately before merge verify:

    PR identity
    observed head equals authorized candidate SHA
    required checks and reviews are satisfied
    target branch is correct
    prescribed merge strategy
    repository merge policy
    no newly material blocker

Abort on head movement or unmet gates.

Record:

    PR identity
    authorized candidate SHA
    resulting merge identity
    resulting remote default-branch identity
    post-merge checks required

Do not infer that deleting the source branch, closing related issues, deploying, or performing cleanup is authorized merely because the PR merged.

---

## CONTEXT_ROTATION

Use when a long PROGRAMME run can discard closed conversational state at a semantic transition. Do not rotate a small task merely because this protocol exists, and do not rotate on a fixed timer or turn count.

State roles are:

    bootstrap.json — immutable run identity, initial authority and state-file locations
    events.jsonl — append-only material transitions/incidents
    manifest.json — sole current programme projection
    coverage.json — plan/source coverage when required

Do not maintain a second mutable snapshot. If a human status view is later needed, derive it from the manifest and bind its manifest identity/event sequence so staleness is detectable.

Good rotation points include:

- after bootstrap and initial delegation;
- after a merge wave or closed dependency tranche;
- after abandoning a candidate lineage;
- immediately before a controlled operation;
- after an incident or authority change; and
- at operator handoff or continuation.

Before compacting, update durable state and retain only exact current default-branch identity, active item/owner/job IDs and states, candidate/PR heads, live authority/capsule identities, blockers, receipt/finding refs and the next legal transitions. Exclude descendant transcripts, routine logs and closed candidate histories.

A continuation reads the kernel, immutable bootstrap, current manifest and exact sources required for the next decision. Before semantic action it revalidates Git, GitHub, owner, job and controlled-operation state. Durable state is recovery evidence, not authority merely because it was written.

---

## CLEANUP

Use for ambiguous or non-routine cleanup, destructive deletion, unexpected untracked content, or uncertain continued need.

Routine removal of an exact known-clean completed disposable worktree does not require this section, but its identity, cleanliness, process use, and continued need must still be known.

Before destructive cleanup establish:

    exact target
    purpose and mutation owner
    tracked cleanliness
    unexpected untracked content
    corresponding branch, PR, and task state
    active processes or agents
    evidence, recovery, review, and audit need

Delete only exact state known to be completed and no longer required.

Never:

- use a broad unresolved path;
- force deletion merely to eliminate ambiguity;
- delete useful failure evidence;
- delete unresolved or operator-blocked work;
- delete an active agent's worktree or process state;
- delete state required for acceptance, review, merge, post-merge verification, recovery, or a controlled operation.

Use recoverable cleanup where practical and report material removals.
