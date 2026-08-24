# Multi-Agent Programme orchestrator — on-demand protocols

## Version 0.7 (24 August 2026)

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

### Choose scale

Choose DIRECT, DELEGATED, or PROGRAMME using:

    task coupling and implementation ambiguity
    mutation-surface conflicts
    need for independent judgment
    opportunity for genuinely parallel progress
    validation cost and risk
    controlled operations
    expected duration and recovery needs
    coordination and token cost

Prefer DIRECT unless delegation has a concrete benefit. Do not spawn a planner or owner merely to satisfy a process shape.

### Compile the live execution model

For DIRECT work, a concise internal checklist is enough.

For DELEGATED work, define one complete owner packet.

For PROGRAMME work, record a compact manifest:

    work_item:
        id | purpose | authority refs | context refs |
        deps/start/merge gates | custody |
        expected outputs | mutation scope |
        mandatory checks | invariants/capsules |
        state/blocker

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

    exact work item and permitted base SHA
    isolated or otherwise safe working root
    binding authority references
    separately classified decision-support references
    satisfied start gates and dependencies
    owned and prohibited mutation surfaces
    expected outputs and active invariants/capsules
    mandatory checks and optional exploration
    environment mutation boundary
    Git/branch/push/PR delivery contract
    local decision rights
    escalation boundary
    protocols_path and applicable protocol_sections
    receipt paths

Do not delegate unresolved cross-item ownership, authority conflicts, protected-operation authorization, or binding task meaning.

### Spawn

Spawn one primary `map_item_owner` with the smallest context needed. Prefer no inherited conversation when exact references and runtime state are sufficient.

Set `protocols_path` to the absolute resolved value of `PROTOCOLS`, not the bare symbolic name. Include only protocol section names expected by this custody unit. These references provide procedure and do not grant authority.

Do not separately spawn programme-visible `map_implementer` and `map_reviewer` children for the same ordinary item. The owner contains that relationship.

### While delegated

Treat the owner subtree as a local context boundary. Do not request routine progress, diffs, logs, findings, or child summaries.

Continue independent work. When genuinely idle, use event-aware waiting or one suitably long wait. Reconcile states after a wait or material event; do not stack short polls.

React to:

    READY_FOR_ACCEPTANCE
    NO_CHANGE
    ESCALATE
    BLOCKED

Resolve only the escalated programme decision. Return the minimum changed boundary to the same owner when practical.

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

1. Run a small representative sample through the exact production entry point, persistence path, schemas, state transitions, and verifier. The sample must exercise every planned output type. If safe down-scaling cannot represent a material property, record that evidence gap and establish another credible gate.
2. Inventory variable-length and nested outputs, file or part limits, decoders, memory and disk boundaries, transaction boundaries, create-only destinations, and other scale-sensitive edges.
3. Project retained-scale counts, bytes, partitions, peak memory, peak disk, elapsed time, and safe concurrency from authorized metadata, bounded constructions, or representative measurements. State assumptions and explicit safety margins.
4. Trace each output through its immediate downstream consumers, authenticators, verifiers, publication gates, and other boundaries that could reject an otherwise successful producer.
5. Record untested assumptions, exact source and destination identities, stop conditions, and the evidence required to retire each material uncertainty.
6. Base concurrency on measured or credibly bounded peak resources. Do not launch the maximum number of heavy jobs merely because parallel agent or worker slots are available.

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

Use before a protected, destructive, irreversible, production, migration, retained-evidence, security-sensitive, compatibility-deletion, or otherwise consequential operation.

A task or plan requirement does not grant authority for a separately protected operation unless applicable governing authority allows it.

The orchestrator defines a capsule:

    capsule ID
    authority references
    exact source identity
    exact destination identity
    operation and prohibited operations
    immutability or overwrite rule
    preflight
    success evidence
    failure and rollback semantics
    deletion trigger

Do not fill unresolved values with plausible defaults.

Before mutation establish exact authority, prerequisites, identities, non-overwrite rules, failure boundary, and required evidence. Only then may the designated owner execute the operation. The executor may not widen or reinterpret the capsule.

On failure:

- preserve existing authority and useful failure evidence;
- do not reuse an ambiguous partial destination;
- do not perform downstream deletion;
- do not invent replacement identity, path, mapping, or equivalence;
- stop when fresh operator authority or specialist judgment is required.

Keep the capsule active until deliberately discharged.

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

An open, non-draft, mergeable, approved, or green PR is not acceptance by itself.

### Review publication

Bind the verdict to the exact reviewed SHA. Immediately before publication, recheck the PR head when practical.

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

Use only when a long PROGRAMME run must continue from compact durable state. Do not rotate a small task merely because this protocol exists.

Write bootstrap.json atomically with:

    run/state version
    exact observed default-branch identity
    snapshot/events/manifest/coverage paths
    active item IDs and states
    runnable queue
    delegated owner task identities
    candidate and PR heads
    blockers and escalations
    ownership conflicts
    active invariants/capsules
    long-job identities
    validation fingerprint
    exact receipt/finding refs needed next

Do not include descendant transcripts, credentials, or copied logs.

A fresh continuation reads only the kernel, bootstrap.json, and exact sources required for the next decision. Before semantic action it revalidates current Git, GitHub, owner, job, and controlled-operation state.

Durable state is evidence and recovery material, not authority merely because it was written down.

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
