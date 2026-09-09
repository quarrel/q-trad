# U-lane MAP execution observations

Status: first tranche accepted after one review repair cycle. This records observed costs and interventions; it does not change MAP authority or agent roles.

## Scope and measurements

The operator approved the plan on 2026-09-09. Working base was `cff6cd272b5605d9c64fed05fe75f9ad217638c1`, activation `922da41`, first candidate `2322f2c`, and independently accepted candidate `61c02c2`. Public documentation and synthetic fixtures were authorised; empirical inputs remained `NONE_APPROVED`. Delivery is local, with no push or merge.

| Measure | Observed result and limit |
| --- | --- |
| Bootstrap to accepted-review checkpoint | 09:18:58–09:47:26 UTC: 28 min 28 s. Preliminary authority reading preceded bootstrap; documentation closeout followed this checkpoint. |
| Agent structure | Root orchestrator, two item owners, one reducer child and one independent reviewer: five agents in total. No extra planner or orchestrator. |
| Review convergence | One combined initial review, two accepted findings, one owner repair cycle, same reviewer resumed to PASS. |
| Initial review duration | Approximately 2½ min, reviewer estimate. Owner starts were not instrumented, so exact owner durations are unavailable. |
| Final focused tests | 23 passed in 10.82 s; the final four selector cases were rechecked in 8.40 s after a test-only repair. Ruff, formatting and strict Pyright passed. |
| Accepted representative fixture | 6.39 s CPU, 197.5 MiB peak RSS, 14.58 MB outputs; 24 families, eight quarters, two probes, 100 draws. |
| Computation reuse | One decode, 24 preparations, 384 market/vintage/probe outcomes, zero fits. Cohorts and friction scenarios reuse outcomes. |
| Token usage | Not measured. Tool-output volume and handover count are qualitative observations, not token totals or monetary costs. |

## Problems and interventions

| Observation | Effect and response |
| --- | --- |
| Overlarge authority/source reads recurred in root, owner and reviewer work. | Truncated responses required narrower repeat reads. JSON-wrapped text and large raw HTML increased avoidable output. Print text blocks, use explicit budgets and separate independent slices when a combined response will exceed them. |
| Root combined `paths` with per-file `sections` during closeout. | The tool returned full-file content and truncated it; a single-file ranged read recovered the needed anchors. Use `sections` with one `path`, as documented. One listing call also omitted required `patterns` and needed correction. |
| Public-source documentation access was unreliable. | CME returned HTTP 403, CHRIS HTTP 404, and other pages were JavaScript shells. U0 recovered through one Context7 library resolution and three queries, retaining indexed-document limitations and unknowns. No data was acquired. |
| No empirical inputs, budget or effective reserve isolation were designated. | The parent recorded `NONE_APPROVED` and continued authorised fixture/source work. The handoff provides a concrete metadata/quote envelope, without claiming a loader guard establishes access isolation. |
| Missing risk scales/signals became successful flat zero observations. | Independent review reproduced the defect despite passing tests. Repair preserves unavailable-session reasons, invalidates affected scoring, separates payoff coverage and retains fixed entry scales for active positions. |
| Initial planted test bypassed the selector pipeline. | Independent review required actual descriptors→selection→all controls→shared scoring checks. Four deterministic no-edge/planted cases now cover both probes; their one-session support is explicitly not statistical false-positive calibration. |
| Initial formatting/type issues and one invalid causal-test mutation. | Owners repaired them locally. The test had moved future publication earlier; it now delays publication only. Remediation also repaired one strict arithmetic type issue. No scientific control was relaxed. |
| Mandatory verbatim inter-agent announcements repeated long receipts in user commentary. | This added visible and contextual duplication. Any change requires adjusting the governing announcement instruction; MAP documentation alone cannot override it. Prefer shorter receipts meanwhile. |

## Coordination assessment

`/root/u0` delivered source feasibility and remained available without polling. `/root/u12` delivered the laboratory and owned repairs; its `/root/u12/reducers` child completed a bounded mathematical module with a stable interface. `/root/tranche_review` reviewed both candidates independently and did not edit them. The parent owned programme documents, exact commits, acceptance and delivery. All implementation/review work is complete.

Shared-checkout ownership kept source documentation and laboratory mutations separate. No merge conflict or duplicate independent review occurred. The reviewer carried forward unchanged U0 and reducer conclusions and did not rerun routine passing checks. Healthy work used event-driven waits; status was not repeatedly requested. The representative run was repeated after payoff semantics changed because its earlier outcome evidence no longer established the repaired behaviour.

The source owner finished before the laboratory. This supports the practical value of the chosen separation, but exact speed-up cannot be claimed without a sequential baseline or complete owner timings. Four spawned agents were sufficient; more decomposition was not needed for this tranche.

## Changes worth trying next time

1. Put a dispatch timestamp and a small set of acceptance-to-test obligations in each packet. For scientific invariants, require a behavioural counterexample, not merely a passing test count.
2. Fix read-budget discipline and teach the single-file `sections` distinction before adding more orchestration machinery. The same recoverable tool mistake across roles was the clearest avoidable token cost.
3. Keep one coherent independent review and reuse the same owner/reviewer for repairs. This cycle found real defects and converged without another planning or review layer.
4. Capture available token/usage telemetry at dispatch and closeout if quantitative token efficiency is required. Do not reconstruct exact token totals from tool-call counts or elapsed time.

Named synthetic attempts `fixture-001` through `fixture-003`, their append-only register and ignored programme state under `tmp/agents/u-lane-20260909/` remain useful for reproducibility and this requested audit. They are implementation evidence, not empirical results; no broad temporary-file deletion was performed. The remaining operator decision and empirical boundaries are in [the tranche handoff](U_LANE_FIRST_TRANCHE.md).
