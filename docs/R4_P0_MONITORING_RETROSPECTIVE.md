# R4-P0 monitoring cadence retrospective

Date: 2026-09-08. Scope: actual owner, terminal-monitor and root tool calls during 6–7 September, after the current-policy restart. No scientific workload was rerun or altered.

## Assessment

Programme custody and convergence improved: one owner retained the lineage, completed delegates were reconciled, affected evidence was reused, and independently accepted negative results reached a complete repository gate. Resource failure, reporting defects and scientific exceptions were recorded separately. This was not defect-free: receipt preparation, control comparison and misleading monitor narratives required corrections. The root also failed to notice excessive descendant model wakeups while correctly using long event waits itself.

## What the tool records show

The audit counted `response_item` tool-call records dated before 2026-09-08 in the three retained session files below. It did not count copied transcript text or programme events as additional calls.

| Actor | Observed calls during 6–7 September |
| --- | --- |
| Item owner | 1,140 explicit `wait_agent(timeout_ms=60000)` calls; 1,012 returned timeout, 128 returned an event. No `wait_agent(30000)` calls. |
| Terminal passive monitor | 1,383 `functions.wait` continuations explicitly requesting 30,000 ms. Some inner `write_stdin` calls requested 300,000 ms, while outer cell continuations still yielded every 30 seconds. |
| Root | 217 `wait_agent` calls, all requesting 3,600,000 ms. Events could return early. |

Counts are tool invocations, not a calculation of model-token spend or proof every short wait was unnecessary. However, the 1,012 unchanged owner timeouts and repeated short outer monitor yields establish substantial avoidable model coordination. A short session read after an actual completion event can be appropriate; this audit does not classify every `write_stdin` as misuse.

Retained raw session evidence (not Codex Memories):

- Owner: `/home/vscode/.codex/sessions/2026/09/06/rollout-2026-09-06T08-54-37-01a075ed-3f86-7022-8faa-7a7b48d7a6da.jsonl`.
- Terminal monitor: `/home/vscode/.codex/sessions/2026/09/06/rollout-2026-09-06T19-55-15-01a0784a-1378-7b50-8323-0440b2258a73.jsonl`.
- Root: `/home/vscode/.codex/sessions/2026/09/06/rollout-2026-09-06T08-50-16-01a075e9-4324-71a0-bd75-a1610b2e4450.jsonl`.

Examples: owner lines 11556 and 11560 request successive one-minute waits at 2026-09-07T14:51:04.611Z and 14:52:08.246Z. Monitor line 53 requests a 30-second outer continuation; line 1313 requests a five-minute inner process wait without an explicit longer outer execution yield. These are separate from OS resource sampling.

## Cause and limits of attribution

1. Both agent session metadata records contain generic guidance to avoid blocking waits longer than 60 seconds and provide updates within 60 seconds. The item-owner role separately said to use an event-aware or suitably long wait and never stack short polls. The observed owner calls explicitly chose 60 seconds; this was not merely the tool's omitted/default 30-second timeout. The competing generic cadence is a supported contributing explanation, not access to the model's private reasoning.
2. The monitor sometimes lengthened the inner process wait but retained 30-second outer execution-cell continuations. That moved the polling cost to another tool layer rather than eliminating model wakeups.
3. LONGRUN and the monitor role said a wait timeout was an observation boundary. Without distinguishing a tool-yield timeout from an agreed job review boundary, this could reinforce the short-cadence behaviour despite the existing prohibition on polling for liveness.
4. Delegating observation did not remove owner wakeups: the owner continued short waits alongside the monitor. Root did not catch this resource inefficiency during the run.

The owner acknowledged short-timeout boundaries were allowed to become model observation points. This report establishes the calls and instruction overlap; it does not prove a single exclusive internal cause.

## Keep autonomous resource sampling

The ordinary supervisors correctly sampled resources without requiring model turns. Retained `longrun/r4-terminal-runtime-current-policy/final-metrics-control-exception/run.py` used `time.sleep(30)` in its loop (163 observations over 4,860.825 seconds); `mixed-journal/run.py` used `child.wait(timeout=30)` (882 observations over 26,462.052 seconds). Both live beneath `/workspace/tmp/MAP_orchestrator/r4-p0-20260828-ddd66f9/`.

Those intervals enforced the authorised resource boundary and are not the defect. Do not slow resource sampling or remove safety alarms to save model turns.

## Correction

Updated the existing owners of this behaviour, rather than adding another monitoring agent or scheduler:

- `.codex/agents/map_item_owner.toml`: explicit long interruptible agent waits, currently up to one hour for healthy multi-hour work, shortened for an earlier agreed review. Events/user input return early. No duplicate owner polling of a delegated monitor or liveness reads after an unchanged timeout.
- `.codex/agents/map_passive_monitor.toml`: choose durations at both inner process and outer execution-cell layers; do not use short no-output continuations or sleep-command loops as heartbeats.
- `.codex/map/MAP_On_Demand_Protocols.md` LONGRUN: distinguish OS sampling, tool limits and model observation boundaries. A tool timeout alone is not a declared checkpoint. Preserve genuine alarms, unreliable-monitor reports, terminal events and scheduled cost review.

Respect actual platform limits and higher-priority constraints; surface a concrete limitation if a supported interruptible wait cannot be used. This changes repository agent guidance, not the tool platform's defaults or an already spawned agent's injected role text. Future agents must load the revised roles; existing agents need the correction explicitly supplied in their current task. There is no claim that a long-run behavioural replay has proved compliance.

Validation: both edited role files parse as TOML and `git diff --check` passes. The change contains no supervisor, scientific, resource-policy or evidence mutation. A future healthy-job trace should show long owner waits and no repeated short outer yields solely for liveness, while resource sampling and event interruption continue.
