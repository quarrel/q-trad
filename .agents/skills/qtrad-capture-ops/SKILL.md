---
name: qtrad-capture-ops
description: Safely inspect and operate q-trad's demo-only OCI capture collector. Use for collector health checks, qualification evidence, backups or restores, storage measurements, run reconciliation, image publication, deployment, rollback, or OCI and Tailscale access. Classify read-only observation separately from evidence writes, collector mutations, releases, and cloud control-plane changes.
---

# q-trad Capture Operations

Operate the collector through the repository's accepted evidence and release contracts. Do not
duplicate the runbook or invent an easier operational path.

## Establish authority and scope

1. Work from the repository root and read `AGENTS.md`.
2. Read the active universe/collector milestone in `PLAN.md` and the current risks/actions in
   `docs/STATUS.md`.
3. Read only the relevant headings in `docs/CAPTURE_OPERATIONS_RUNBOOK.md`.
4. Read ADR 0026 for the current proportionality boundary plus the accepted ADR governing the
   operation. Usually select among:
   - `docs/adr/0012-capture-operations-release.md` for isolation, release and recovery;
   - `docs/adr/0018-lightstreamer-delta-capture-and-storage-evidence.md` and
     `docs/adr/0020-raw-payload-representation-and-legacy-epochs.md` for storage;
   - `docs/adr/0019-verified-snapshot-to-research-import.md` for backups and research import.
5. Confirm the requested action remains demo-only and inside the active research-framework phase.
   Stop if it could reach an order API, a production IG endpoint or a paper/research writer on the
   collector.

## Classify the operation

Choose exactly one class before issuing a remote or external command:

- **Observation:** bounded health, readiness, logs, counts, identities, filesystem capacity or
  cloud-resource reads. Keep it read-only.
- **Evidence operation:** a reviewed helper may write a new non-overwriting artifact or perform a
  specifically approved reconciliation. Require its documented gate, immutable tool image and
  plan/hash confirmation.
- **Publication:** GitHub Actions writes a new immutable image from an exact green `main` commit.
  Publication does not authorise deployment.
- **Deployment or rollback:** changes the running collector image, configuration, schema or
  lifecycle. Require explicit operator authorisation and follow the release runbook literally.
- **Control-plane change:** changes OCI, GitHub, Tailscale or another external service. Require
  explicit operator authorisation, use the least-privileged identity and read the resource back.

If a request combines classes, perform the lower-impact observation first and report the later
gate before crossing it.

## Protect qualification and measurement windows

1. Determine from `docs/STATUS.md` and current evidence whether a qualification or storage
   measurement interval is active.
2. During an active interval, do not deploy, restart, migrate, reconcile, reprocess or perform
   operator-initiated database maintenance unless the reviewed protocol explicitly requires it.
3. Treat an unplanned mutation as invalidating the affected interval. Stop and report its exact
   UTC time and observed impact; never conceal it by restarting the interval silently.
4. Never rewrite or selectively delete raw capture or canonical events. Apply changed-field
   representation only to a later versioned writer. A legacy epoch can leave the operational
   database only through a separately accepted, hash-bound archive and restore/replay decision.

## Use access and credentials safely

- Use `ssh opc@q-trad-capture` through Tailscale MagicDNS for normal administration. Direct
  restricted IPv6 and OCI Bastion are recovery routes, not automation dependencies.
- Keep APIs and PostgreSQL loopback-bound. Do not expose a temporary public port.
- Never print `/etc/qtrad/*.env`, a rendered Compose model, container environment values, tokens,
  database URLs or provider credentials. Inspect names or execute inside the intended process
  without rendering values when necessary.
- Use the operator workstation's OCI profile for control-plane administration and the host's OCI
  instance principal for workload access. Do not copy OCI API keys onto the collector.

## Execute through reviewed boundaries

For observation, prefer existing bounded APIs, checked-in commands and read-only SQL. Record the
UTC observation time plus source, universe, configuration and image identity needed to interpret
the result. Do not infer readiness from a running container or aggregate message activity.

For evidence operations, use the checked-in guarded helper and an already-local immutable
`repository@sha256:...` image. Preserve its `--no-deps --pull never`, non-overwrite, exact-target
and root-owned evidence protections. Review a generated plan before repeating its exact hash.

For publication, verify the reviewed branch state, merge to `main`, wait for green `main` CI and
dispatch the main-only workflow from that exact commit. Retain the returned multi-platform OCI
index digest. Never publish `latest`, reuse a tag or build on the collector host.

For deployment or rollback, follow the runbook's pre-deploy backup, expand-only migration,
readiness and rollback checks. Roll back application/configuration only; never roll back schema,
raw records or canonical events.

For a control-plane write, treat CLI completion as provisional. Read the resource back through an
independent get/list operation and compare its effective policy, lifecycle state and scope with the
requested result.

## Report evidence

Return a concise record containing:

1. the classified operation and authorisation used;
2. repository commit and immutable image identity when relevant;
3. commands or checked-in helpers used without secret-bearing arguments;
4. observed or changed state with UTC timestamps;
5. verification performed after a write;
6. the next unsatisfied gate, or an explicit statement that no mutation occurred.

Let unexpected failures propagate. Never turn a partial command, missing identity, ambiguous
mapping or unavailable evidence into a plausible success report.
