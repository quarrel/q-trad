# Multi-Agent Programme orchestrator — on-demand protocols

## Version 0.10 (27 August 2026)

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
    relevant branches, working roots, delivery artifacts, reviews, and CI when applicable
    required tools, credentials, services, and environment constraints
    validation semantics and likely expensive checks
    retained MAP state relevant to this task

Do not assume a particular default branch, a clean checkout, an available remote, permission to discard local state, a delivery platform, or permission to integrate. Observe the repository's actual delivery and branch model.

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

### Choose scale and custody boundaries

Choose DIRECT, DELEGATED, or PROGRAMME using task coupling, ownership conflicts, independent judgment, genuine parallelism, validation risk, duration/recovery, and coordination cost.

Prefer DIRECT unless delegation has concrete value. For DELEGATED work, assign one coherent custody unit to one owner. For PROGRAMME work, separate units only where real dependencies, mutation ownership, independently testable outcomes, or trust boundaries justify it. Resolve unclear binding meaning rather than delegating it as implementation uncertainty.

### Compile the live execution model

For DIRECT work, a concise internal checklist is enough.

For DELEGATED work, define one complete owner packet.

For PROGRAMME work, record a compact manifest:

    work_item:
        id | objective | authority/context refs | base/working root |
        deps/start/integration gates | custody |
        expected outcome | mutation scope | mandatory checks |
        material conditional boundaries | state/blocker

    ownership:
        surface | mutation owner | consumers | conflict risk

    invariant:
        id | owner | contract | discharge condition

When a plan is substantial, maintain a proportional coverage map:

    source_ref | classification | execution disposition | execution ref | rationale/evidence

The live model records execution decisions; it neither replaces source authority nor promotes non-binding plan material.

Audit once for omitted requirements, unsupported classifications, hidden unresolved decisions, incorrect dependencies, overlapping ownership, lost project-owned constraints, and non-discriminating validation.

Begin the first useful action immediately when unblocked.

---

## LOCAL_CUSTODY

Use before delegating a bounded implementation/review work item to `map_item_owner`.

### Preconditions

Compile the kernel's delegated work-item packet from verified current state. Confirm that its base and working root are available, its authority and mutation boundaries are consistent, applicable start gates are satisfied, mandatory checks discriminate the expected outcome, and delivery, local decision rights, escalation conditions, and any material resource limit are explicit.

Do not delegate unresolved cross-item ownership, authority conflicts, separately protected authority, or binding task meaning. Add conditional packet fields only when materially applicable.

### Spawn

Spawn one primary `map_item_owner` with the smallest context needed. Prefer no inherited conversation when exact references and runtime state are sufficient.

Set `protocols_path` to the absolute resolved value of `PROTOCOLS`, not the bare symbolic name. Include only protocol section names expected by this custody unit. These references provide procedure and do not grant authority.

Do not separately spawn programme-visible `map_implementer` and `map_reviewer` children for the same ordinary item. The owner contains that relationship.

### While delegated

Treat the owner subtree as a local context boundary. Do not request routine progress, diffs, logs, findings, or child summaries.

Continue independent work. When genuinely idle, use event-aware waiting or one suitably long wait. Reconcile states after a wait or material event; do not stack short polls.

Reconcile terminal states:

    READY_FOR_ACCEPTANCE
    NO_CHANGE
    DECOMPOSE_REQUIRED
    ESCALATE
    BLOCKED

Also respond to a material overrun, resource anomaly or agreed observation boundary under the
kernel's elapsed-progress guidance. Request only the evidence needed for that decision; a terminal
receipt is not a prerequisite for intervention.

Resolve only the programme-level decision. DECOMPOSE_REQUIRED is appropriate when evidence exposes genuinely separable units or non-discriminating acceptance evidence. ESCALATE is appropriate when progress requires missing authority, an architectural or scope decision, or an operator-defined budget change. Preserve exact state and do not relaunch unchanged work under a new owner or name.

### Acceptance

READY_FOR_ACCEPTANCE is evidence, not programme acceptance. The orchestrator still verifies exact identity, mutation scope, expected outcome, validation, findings, review requirements, requested delivery state, and cross-item gates.

---

## LONGRUN

Use for a predictably substantial elapsed-time command, build, test, analysis, external job, or any operation that would otherwise invite repeated model polling.

Keep task-local jobs beneath their owner. The job owner retains all launch, retry, cancellation, repair, acceptance, and follow-up authority.

Apply the kernel's `Validation cost and elapsed progress` guidance when setting observation
boundaries and deciding whether to keep waiting. Observers retain observation-only authority.

### Choose the observation mode

Prefer one blocking invocation when the current actor can wait safely and the operation is expected to complete without repeated model turns. Do not create another agent merely to wrap a naturally blocking tool call.

Delegate observation to the `map_passive_monitor` custom agent when the wait is predictably long, multi-hour, concurrent with useful owner work, detached or external, or otherwise likely to consume repeated coordinating or implementation-model turns. Use it only when the monitor can access a trustworthy read-only observation mechanism.

`map_passive_monitor` is a leaf role pinned in its project agent definition to `gpt-5.6-luna` with `medium` reasoning. Do not restate or override its model in the packet. Its lower-cost configuration is part of the role boundary, not authority to perform work.

### Observation packet

Before starting or delegating observation, record as applicable:

    job:
    owner:
    exact_operation_context:
    exact_command_or_operation:
    observation_mechanism:
    success_criterion:
    durable_terminal_evidence:
    relevant_resource_cost_or_stop_thresholds:
    observation_boundary:
    retry_authority:
    prohibited_actions:

When spawning `map_passive_monitor`, send this packet without programme narrative. Include only fields needed for trustworthy observation. The packet grants observation access only; it never grants authority to start, stop, cancel, retry, repair, mutate, publish, accept, integrate, clean up, or perform a follow-up action.

Prefer:

    event or service completion
    one suitably long session or process wait
    detached process with a durable completion marker
    delivery-platform checks or an equivalent terminal-state mechanism
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

## GITHUB_PR — optional GitHub delivery adapter

Use only when the operator and governing repository selected GitHub and before an authorized branch push, pull-request creation/update, review publication, or merge.

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
- after an integration wave or closed dependency tranche;
- after abandoning a candidate lineage;
- before a separately protected or irreversible operation when project authority requires a handoff;
- after an incident or authority change; and
- at operator handoff or continuation.

Before compacting, update durable state and retain only exact current repository identity, active item/owner/job IDs and states, candidate/delivery identities, live authority refs, blockers, receipt/finding refs and the next legal transitions. Exclude descendant transcripts, routine logs and closed candidate histories.

A continuation reads the kernel, immutable bootstrap, current manifest and exact sources required for the next decision. Before semantic action it revalidates repository, delivery, owner, job, and externally observed state. Durable state is recovery evidence, not authority merely because it was written.

---

## CLEANUP

Use for ambiguous or non-routine cleanup, destructive deletion, unexpected untracked content, or uncertain continued need.

Routine removal of an exact known-clean completed disposable working root through the project's supported mechanism does not require this section, but its identity, cleanliness, process use, and continued need must still be known.

Before destructive cleanup establish:

    exact target
    purpose and mutation owner
    tracked cleanliness
    unexpected untracked content
    associated branch, delivery artifact, and task state when applicable
    active processes or agents
    evidence, recovery, review, and audit need

Delete only exact state known to be completed and no longer required.

Never:

- use a broad unresolved path;
- force deletion merely to eliminate ambiguity;
- delete useful failure evidence;
- delete unresolved or operator-blocked work;
- delete an active agent's working root or process state;
- delete state required for acceptance, review, integration, post-integration verification, recovery, or an authorized external operation.

Use recoverable cleanup where practical and report material removals.
