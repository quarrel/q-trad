# Current capture operations runbook

This runbook governs the running OCI IG demo market-data collector. It does not authorise an IG
order, production endpoint, research write to the collector database or paper-execution operation.

Historical provisioning, `capture-v1` qualification, gap-investigation and storage-comparison
procedures are preserved in
`docs/archive/capture-operations/CAPTURE_OPERATIONS_RUNBOOK-2026-07-22.md`. They are not routine
release gates under ADR 0026/0027.

## Authority classes

- **Read-only observation:** inspect the existing health/API/monitoring state without changing the
  collector, database, configuration, evidence or cloud resources.
- **Provider review:** make the bounded IG demo REST requests defined by the reviewed candidate
  workflow. This does not select a listing or change capture.
- **Release preparation/publication:** change reviewed repository artefacts or publish an immutable
  image. Publication does not authorise deployment.
- **Activation/lifecycle:** change the mounted universe, signal/restart a service, deploy an image,
  run a migration, restore data or mutate cloud state. Each requires explicit task authority.

When authority is absent, remain read-only and report the exact proposed operation.

## Current operating identity

- Active universe: `config/capture-v4.toml`, 23 instruments, including reviewed China A50 and
  Taiwan plus context-only AUD VIX. Korea 200 and Bitcoin remain quarantined after fail-closed demo
  review.
- The active universe is mounted at `/etc/qtrad/universe/active.toml` and synchronised inside the
  existing ingest process before subscriptions are replaced.
- The collector's canonical source identity remains the established configured value across
  releases and restores of the same history. Never reuse it for an independent event store.
- The host runs immutable digest-pinned application and PostgreSQL images. Do not build on the host,
  deploy a mutable tag or print the rendered Compose model.
- `/etc/qtrad/capture.env`, backup/monitor environments, database data and retained evidence are
  private host state and must never enter logs, commands shown in reports or version control.

The previous accepted image/configuration is the immediate rollback point. `docs/STATUS.md` records
current observed state; the checked-in deployment descriptor and host environment carry the exact
runtime identity.

## Read-only observation

Use bounded observation to establish current truth. Do not protect an arbitrary observation window
from unrelated authorised work unless a reviewed experiment explicitly requires it.

For the active configuration confirm:

- readiness HTTP status and exact configuration identity;
- all expected subscriptions acknowledged, updated and recent;
- heartbeat current for the active connection generation;
- projection caught up with known lag;
- queue depth/high-water and application callback drops;
- Lightstreamer lost updates, reconnects, subscription errors and server errors;
- process/container restart state, CPU, memory and disk; and
- backup and restore-verification age/status.

A connected socket, aggregate stream activity or quiet price alone is not channel readiness.
Observed market silence remains visible and is not automatically classified as transport loss.

Do not expose response bodies or rendered environment/configuration values. Record bounded counts,
times, reason codes, immutable identities and non-secret evidence references.

## Candidate review and release preparation

The provider-review workflow for a future universe is:

1. Add provider-neutral candidate concepts without deployment authority. Use provider epics only as
   explicit bounded review hints when ordinary search omits a user-observed listing.
2. Run the existing bounded non-authoritative IG demo review. It may report candidates and stable
   rejection reasons but cannot choose a listing. When normal search omits a known listing, an
   exact-review epic hint may fetch bounded detail; the hint is hash-bound evidence input, not
   selection authority.
3. Review product type, rolling/expiry semantics, market state, duplicate/equivalent exposure,
   session, currency, quantity rules and value-per-price-unit.
4. Author an explicit mapping bound to the exact catalogue and provider-review hashes. Missing,
   inconsistent or ambiguous values fail closed.
5. Render an undeployed, versioned configuration containing only accepted mappings. Rejected
   candidates remain quarantined without inventing epics for them.
6. Run normal code/configuration validation and prepare an immutable release descriptor.

Neither a public IG market page nor a candidate/review file authorises capture. Do not select the
smallest contract, a similarly named listing or a dated future by inference.

The existing 40-instrument load proof supplies capacity evidence when only the reviewed universe
changes. Repeat broad load testing only if callback fields, frequency, queue/database design or
measured rate changes materially. An activated universe still requires exact per-channel readiness,
visible loss/lag and a proportionate active-session observation.

## Application release and rollback

Release preparation and deployment remain separate operations:

1. GitHub Actions must pass for the exact main-branch commit. Workflow-run evidence is authoritative
   because the GitHub token cannot read check runs.
2. Manually publish the application image only from that reviewed main commit. Use its immutable
   multi-platform OCI index digest and unique commit tag; never publish or deploy `latest`.
3. Commit a deployment descriptor binding the application digest, configuration hash and required
   schema state. Publishing this descriptor still does not authorise deployment.
4. Run only reviewed expand-compatible migrations before deployment. The automated path currently
   requires the database to be at the descriptor's exact schema head; it never infers or applies a
   migration.
5. With explicit deployment authority and a clean main-branch checkout, calculate and review the
   descriptor file's SHA-256, then run the single operator command:

   ```bash
   ops/capture/deploy.sh config/<release>-deployment.toml operator@capture-host <descriptor-sha256>
   ```

6. The orchestrator requires successful CI for the exact release commit, validates the descriptor
   and universe, installs the immutable release, pulls the pinned image, proves the declared
   rollback identity and takes a fresh backup before changing runtime state.
7. It deploys the candidate image against the unchanged universe first, requires old-universe
   readiness, performs exactly one dynamic activation, observes the candidate for a bounded period
   and verifies loss counters, run transition, reload event, image and release identity.
8. A failed post-mutation gate automatically restores the prior universe, environment and release,
   restarts through the normal lifecycle and verifies old-universe readiness. Both success and
   failure produce a sanitised `qtrad-capture-deployment-v1` evidence file under the private
   deployment evidence directory.

The orchestrator deliberately does not publish an image, apply a migration, repair a rejected
provider mapping or infer deployment authority. Those remain explicit preceding decisions. The
lower-level activation sequence below documents the mechanism and is a diagnostic fallback, not
the normal release interface.

Rollback restores the previous immutable image/configuration and restarts only through an explicitly
authorised lifecycle operation. Never roll back canonical events, database volumes or retained
history. If a new universe cannot establish readiness, the ingest replacement path must restore the
old stream; verify that it actually did so rather than assuming rollback.

## Dynamic universe activation

Do not run a separate `instruments sync` process on the collector. A second IG REST session can
invalidate the active session, and changing subscriptions before listing synchronisation creates an
invalid intermediate state.

With explicit activation authority:

1. Put the reviewed release TOML in `/etc/qtrad/universe` under a temporary name on the same
   filesystem.
2. Validate it with the pinned candidate application image and require its calculated hash to equal
   the reviewed release/descriptor hash.
3. Atomically rename it to `/etc/qtrad/universe/active.toml`.
4. Send `SIGHUP` to the running ingest container:

   ```bash
   sudo docker kill --signal HUP qtrad-capture-ingest-1
   ```

5. The existing process must validate missing/changed listings through its authenticated session,
   commit canonical listing evidence, open a replacement subscription set, prove readiness and only
   then close the superseded stream/start the new evidence run.
6. Expect temporary HTTP 503 while replacement is unresolved. Require HTTP 200 with the exact new
   configuration hash and every expected channel subscribed, updated and recent.
7. Inspect the old/new ingestion run records and `capture_universe_reloaded` event. Record any
   failure, rollback, callback loss, subscription error or lag honestly.

An unchanged-file signal may prove the mechanism but is not evidence that a new universe works.
Never repeatedly signal a failed candidate without diagnosing the stable failure.

## Backup and restore verification

The collector uses private versioned Object Storage plus root-owned local staging. Backup and weekly
restore verification are independent of universe activation and must not be waived because capture
is otherwise ready.

- Backup and restore helpers run from the checked-in release and pinned PostgreSQL client image.
- The instance principal supplies OCI access; do not put operator API keys in the repository,
  container environment or `capture.env`.
- Backups include the database dump, checksum and self-describing manifest. Never rename or separate
  members of one evidence set.
- Restore verification uses an isolated disposable target, verifies the manifest/checksum and
  required schema/row evidence, and does not alter the collector database.
- A successful backup does not override a failed or stale restore verification. Keep the failed
  result visible until an independently authorised repair produces a new result.
- Never delete retained source bundles while a derived research manifest still depends on them.

Host systemd units/timers and environment paths are operational state. Inspect their current
installation before changing them; use the archived procedure only when reconstructing why a legacy
host was provisioned that way.

## Immutable research export

Routine research must not query or migrate the collector database directly.

1. Select one complete backup object set and import it into a new isolated `qtrad_research_*`
   database using `docs/RESEARCH_SNAPSHOT_RUNBOOK.md`.
2. Verify source, universe, schema and import evidence before applying research-side expand-only
   migrations to the copy.
3. Export the required interval to a new private research root. Retain the immutable manifest and
   all referenced file hashes/provenance.
4. Model, replay and paper experiments consume the export or isolated copy. They do not receive
   collector credentials or write access.

Exceptional direct read-only database access requires a bounded reviewed purpose that cannot be
satisfied by health/API or snapshot/export evidence. Use one repeatable-read transaction, bounded
queries and no secret-bearing output; never make direct access the normal research interface.

## Failure and stop rules

Stop rather than guess when:

- provider identity, product economics, session, currency or effective listing is ambiguous;
- expected configuration/readiness identity cannot be proven;
- a command would create a second provider session or write to the collector database unexpectedly;
- backup/restore targets or retained evidence paths are unclear;
- an operation could expose credentials/account identifiers; or
- authority covers review/publication but not the requested activation, restart, migration or cloud
  change.

Preserve material failed evidence. A collector that exhausts bounded recovery terminates as truthful
`FAILED`; it does not fabricate readiness or silently continue with an incomplete universe.
