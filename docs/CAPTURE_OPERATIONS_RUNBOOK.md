# Capture operations runbook

This runbook deploys a demo-only market-data collector. It does not authorise any order,
paper-execution or production-provider operation.

## OCI operator steps

1. Create a dedicated capture compartment in Sydney and require MFA for the operator.
2. Create an Ampere A1 instance with 2 OCPUs and 12 GB RAM on Oracle Linux. Attach a
   separate PostgreSQL block volume; begin with 100 GB and review measured growth after
   the first cloud week.
3. Configure private IPv4 with NAT egress. Enable public IPv6 ingress only for TCP/22
   from the operator's delegated stable IPv6 prefix; do not assign a public IPv4 address.
   Attach an instance NSG with no other ingress. Keep the host's standard SSH firewalld
   service enabled rather than duplicating the changeable source prefix there: the OCI
   Console remains the recovery path if the operator's ISP reassigns that prefix.
4. Disable password and root SSH. Install the operator's Ed25519 public key for a
   dedicated non-root administrator. Create an OCI Bastion in the same VCN as a
   break-glass route to the instance private address.
5. Create a private Object Storage bucket with versioning for backups. Grant the instance
   dynamic group bucket-scoped object read/write/delete access and custom-metric publication
   in this compartment. The instance does not need Notifications permission. Create OCI
   Monitoring email alarms for collector readiness, stale backup/restore and low disk.
6. Install Docker Engine, Docker Compose plugin and OCI CLI. Use the pinned PostgreSQL
   container's client tools so backup validation cannot drift from the server major version.
   Place the reviewed release checkout at `/opt/qtrad-capture`; create root-owned
   `/etc/qtrad/capture.env`, `/etc/qtrad/capture-backup.env` and
   `/etc/qtrad/capture-monitor.env` with mode `0600`.
7. Configure `docker-credential-ocir` for `syd.ocir.io` using the collector's instance
   principal. Grant that instance repository read only for `qtrad/qtrad-app`; GitHub Actions
   owns image publication. Do not store an operator or CI auth token on the host. The helper
   is built from reviewed commit `e2411c3c86c633537a8f10113c96c99c2fc71e5e`.

### Backup bucket and instance access

Run these from the operator workstation after setting the existing collector variables:

```bash
export BACKUP_BUCKET_NAME=qtrad-capture-backups

oci os bucket create \
  --compartment-id "$COLLECTOR_COMPARTMENT_OCID" \
  --name "$BACKUP_BUCKET_NAME" \
  --public-access-type NoPublicAccess \
  --storage-tier Standard \
  --versioning Enabled \
  --region "$OCI_REGION"
```

Create `capture-backup-lifecycle.json` locally with the following content. OCI replaces the
complete bucket policy when this command runs, so review the current policy before repeating
it later.

```json
[
  {
    "action": "DELETE",
    "isEnabled": true,
    "name": "delete-daily-after-14-days",
    "objectNameFilter": {"inclusionPrefixes": ["daily/"]},
    "target": "objects",
    "timeAmount": 14,
    "timeUnit": "DAYS"
  },
  {
    "action": "DELETE",
    "isEnabled": true,
    "name": "delete-weekly-after-56-days",
    "objectNameFilter": {"inclusionPrefixes": ["weekly/"]},
    "target": "objects",
    "timeAmount": 56,
    "timeUnit": "DAYS"
  },
  {
    "action": "DELETE",
    "isEnabled": true,
    "name": "delete-previous-versions-after-7-days",
    "objectNameFilter": {},
    "target": "previous-object-versions",
    "timeAmount": 7,
    "timeUnit": "DAYS"
  }
]
```

```bash
oci os object-lifecycle-policy put \
  --bucket-name "$BACKUP_BUCKET_NAME" \
  --items file://capture-backup-lifecycle.json \
  --force \
  --region "$OCI_REGION"
```

Add these statements to the existing collector dynamic-group policy. The first statement
permits upload, weekly restore verification and lifecycle deletion only in this bucket; the
second permits reading its bucket metadata.

```text
Allow dynamic-group qtrad-capture-instances to manage objects in compartment id <collector-compartment-ocid> where target.bucket.name = 'qtrad-capture-backups'
Allow dynamic-group qtrad-capture-instances to read buckets in compartment id <collector-compartment-ocid> where target.bucket.name = 'qtrad-capture-backups'
Allow dynamic-group qtrad-capture-instances to use metrics in compartment id <collector-compartment-ocid> where target.metrics.namespace = 'qtrad_capture'
```

Once GitHub publication succeeds, remove the collector instance's repository create/update
statement and retain only its repository-read statement.

Create the local directories and install the checked-in environment template on the host:

```bash
sudo install -d -o root -g root -m 0700 \
  /srv/qtrad/postgres/backups /var/lib/qtrad-capture
sudo install -o root -g root -m 0600 \
  /opt/qtrad-capture/ops/capture/capture-backup.env.example \
  /etc/qtrad/capture-backup.env
sudoedit /etc/qtrad/capture-backup.env
```

Only `QTRAD_BACKUP_BUCKET` normally needs changing. The file deliberately contains no OCI,
IG or PostgreSQL credential: backup and restore use the host's instance principal and the
running database container. Before enabling either timer, prove the policy with one manual
backup and one manual restore verification.

Qualification-era images write `qtrad-capture-backup-v1`. A later reviewed release containing
ADR 0019 writes self-hashed v2 manifests that additionally bind capture source, universe name and
migration version; do not change the running qualification image merely to obtain v2. The restore
verifier accepts both contracts and uses `QTRAD_EXPECTED_V1_MIGRATION_VERSION` only for legacy v1.
Operator download and isolated research import are documented separately in
`docs/RESEARCH_SNAPSHOT_RUNBOOK.md`.

Install `capture-monitor.env.example` in the same way and replace its compartment OCID. Keep
the explicit regional `telemetry-ingestion` endpoint: OCI custom-metric publication does not
use the normal Monitoring query endpoint.

The PostgreSQL container bind-mounts `/srv/qtrad/postgres/data`, which must reside on the
dedicated iSCSI block volume mounted at `/srv/qtrad/postgres`. The capture systemd unit
requires that mount and must fail rather than write database data to the boot volume.

`capture.env` contains the IG demo credentials, database URLs for `qtrad_capture`, the
approved `QTRAD_CAPTURE_UNIVERSE_PATH`, immutable image references and a stable
`QTRAD_CAPTURE_SOURCE_ID`. `QTRAD_DB_PORT` may override the loopback-only PostgreSQL host port;
leave its default of `15432` unless that port conflicts. The first collector release omitted the
explicit variable and therefore established the validated default `local-development` as this
canonical store's effective source identity. Preserve that value across image and universe releases
and restores of the same history despite its generic name; changing it now would falsely create a
new source boundary. Never reuse it for an independent event store. The file is never copied to a
workstation, log, image or repository.

Every Compose invocation must pass `--env-file /etc/qtrad/capture.env`. Compose's service
`env_file` supplies the container environment but does not supply image references while
interpolating the Compose model. Do not print the rendered model because it contains
resolved secrets.

## Release and rollback

1. GitHub Actions runs formatting, lint, type, shell and isolated PostgreSQL gates on every
     main-branch push and pull request. The manually dispatched `Publish application image`
      workflow has an explicit `refs/heads/main` job gate: a branch dispatch must not publish.
      After operator review, take the PR out of draft, merge it, require the resulting main-branch
      CI run to pass, and dispatch publication from that exact `main` commit. The workflow builds
      `linux/amd64` and `linux/arm64`, publishes the commit-SHA tag and records its immutable OCI
      index digest. Configure the protected `capture-release` environment
    with only `OCI_REGISTRY_USERNAME` and `OCI_REGISTRY_TOKEN`; never add IG or database
    credentials to GitHub. The dedicated publisher requires `manage repos` in the capture
    compartment: `use repos` authenticates successfully but OCIR denies Buildx's
    multi-platform manifest, SBOM and provenance push.
2. Commit a reviewed deployment descriptor that pins only that digest and the approved
   universe/configuration hash. The host has a repository-scoped, read-only GitHub deploy
   key and checkout at `/home/opc/q-trad-source`. Run `git -C ~/q-trad-source pull
   --ff-only`, archive that exact commit under `/opt/qtrad-releases/<full-commit>`, then
   atomically repoint `/opt/qtrad-capture`. Never build or use a mutable image tag there.
3. Run `qtrad db upgrade`, take a successful backup, start `qtrad-capture.service`, then
   require `GET /health/ready` to return HTTP 200 with all seven expected instruments.
4. Roll back only by restoring the previous digest/configuration and restarting Compose.
   Migrations are expand-only; canonical events and PostgreSQL volumes are never rolled
   back by deployment.

OCI Container Registry in Sydney currently rejects repository-level immutability. Publish
each release once under its unique full commit-SHA tag, never publish `latest` or reuse a tag,
and deploy only the returned OCI index digest.

Publishing does not authorise deployment. A newly published image may be used by the guarded
`--no-deps --pull never` reconciliation or storage-inspector helpers after their documented gates;
it must not replace the frozen ingestion/API roles before qualification evidence permits that
release transition.

Before deploying migration `0008`, run this bounded preflight against the target database. It must
return no rows; do not let a migration or operator guess which epic is authoritative:

```sql
SELECT provider, environment, instrument_id, count(*) AS effective_count
FROM reference.provider_listings
WHERE valid_to IS NULL
GROUP BY provider, environment, instrument_id
HAVING count(*) > 1;
```

Migration `0008` adds the matching partial unique index. New application code commits each
`ProviderListingValidated` event and the instrument-level supersession projection atomically, and
projection rebuild recreates event-backed listing rows from canonical history. If preflight ever
finds ambiguity, stop the release and reconcile it through a reviewed universe validation with the
new image against schema `0007` before retrying the migration; never delete an arbitrary row.

## Operations

- Install the capture, backup, weekly restore-verification and healthwatch systemd units
  and timers from `ops/systemd/`. Enable them only after their manual gates pass. The
  collector must continue after SSH or Bastion disconnects.
- From the authorised Dev Container, use Tailscale MagicDNS and
  `ssh opc@q-trad-capture` for normal administration. The tailnet policy permits that peer
  to reach only TCP/22 on the collector. The Dev Container deliberately has no direct IPv6
  route because Docker/WSL IPv6 forwarding proved unreliable; do not treat that as an access
  failure while Tailscale is healthy. Retain restricted direct IPv6 SSH from the operator's
  WSL host as an independently routed fallback, and OCI Bastion for break-glass recovery or
  temporary port forwarding to the loopback-only console.
- Run the Beszel agent as a separate container with tailnet policy allowing only its
  outbound report to the operator's Beszel hub port. Beszel alerts supplement but do not
  replace the collector readiness watcher, OCI alarms, backup-age checks or restore
  evidence.
- Retain the Oracle Linux Chrony client enabled against OCI's link-local NTP service at
  `169.254.169.254`; containers inherit the host kernel clock and must not run independent NTP
  clients. Future healthwatch hardening should publish whether Chrony has an online source, whether
  the system reports synchronised time, leap status and absolute clock offset. Alarm on a missing
  source, unsynchronised or abnormal-leap state, and—initially—an absolute offset above 100 ms when
  sustained; qualify the final offset threshold against observed normal jitter before making it a
  release gate.
- Run daily custom-format PostgreSQL backups, validate each archive with `pg_restore
  --list`, and upload it with its checksum and a manifest binding the universe and image
  digests. Bucket lifecycle rules retain 14 daily and 8 weekly copies. A weekly job verifies
  the latest daily archive in an isolated, networkless, temporary PostgreSQL container
  using the manifest-pinned database image before recording success.
- On this host, the dedicated PostgreSQL volume is an iSCSI attachment whose
  `169.254.2.0/24` storage traffic uses the primary VNIC. Host, Beszel and OCI VNIC transmit
  counters therefore include database block writes and must not be labelled public-internet
  egress. Correlate `VnicToNetworkBytes` or `NetworksBytesOut` with the block volume's
  `VolumeWriteThroughput`, and measure Object Storage uploads from the actual backup objects.
  OCI documents both [VNIC byte semantics](https://docs.oracle.com/en-us/iaas/Content/Network/Reference/vnicmetrics.htm)
  and the [iSCSI block-volume route](https://docs.oracle.com/en-us/iaas/Content/Network/Tasks/managingVNICs.htm).
  Do not change the attachment or its route during a qualification or storage-measurement
  interval merely to make a network graph easier to interpret.
- Do not run development migrations, projection rebuilds, tests or paper/research writers
  against the capture database. Research consumes immutable Parquet manifests or a
  read-only snapshot over an SSH tunnel.

### Immutable research export

`research export` is not a read-only collector operation: it records an export run and manifest in
the database. Restore a capture backup into an isolated local PostgreSQL instance with a writable
application role, set a separate writable `QTRAD_RESEARCH_ROOT`, and select the exact universe
explicitly:

```bash
uv run qtrad research export \
  --universe config/capture-v1.toml \
  --start 2026-07-01T00:00:00Z \
  --end 2026-07-02T00:00:00Z
uv run qtrad replay --manifest data/research/manifests/<manifest-id>.json
```

Do not point this command at the collector or the exceptional read-only tunnel. The current
schema-version-2 manifest binds the requested UTC interval, universe/configuration, application
image/version, coverage, gaps,
provenance, file hashes and semantic bar content. A release-quality export should set
`QTRAD_IMAGE` to the immutable source image digest rather than a mutable tag. Replay validates all
of that identity before accepting the bars. New partitions live under `bars-v2/`; rolling the
application back to the legacy exporter can write only `bars/` and cannot overwrite them. Migration
`0006` leaves its new columns nullable so the previous image can run after the forward migration;
do not downgrade the database once a version-two manifest has been recorded.

### Exceptional read-only database access

Prefer immutable Parquet manifests or a database snapshot. When direct inspection is necessary,
create a dedicated login after migration `0004` has installed the non-login privilege role. From
an interactive `psql` session as the collector database administrator, run:

```sql
CREATE ROLE qtrad_research LOGIN;
GRANT qtrad_capture_reader TO qtrad_research WITH INHERIT TRUE, SET TRUE;
ALTER ROLE qtrad_research SET default_transaction_read_only = on;
\password qtrad_research
```

The `\password` prompt prevents the secret entering shell history or process arguments. Store it
outside `capture.env`; rotate or drop this login independently. Never grant it the collector
application role and never grant `raw` access.

Open the tunnel from the operator workstation without placing the password in the command:

```bash
ssh -N -L 15432:127.0.0.1:15432 opc@q-trad-capture
psql --host 127.0.0.1 --port 15432 --username qtrad_research --dbname qtrad_capture
```

PostgreSQL remains bound to host loopback, so OCI and Tailscale expose no database listener. Direct
queries must remain bounded and read-only; do not point development tools, migrations, projection
rebuilds or paper writers at this tunnel. Drop the login when it is no longer needed; migration
ownership of `qtrad_capture_reader` remains unchanged.

## Reviewed historical coverage

Historical backfill is an operator-controlled data operation, not an ingestion recovery action.
Never run it merely because `/api/v1/gaps` reports a live-stream interruption, and do not run it
on the frozen collector during a qualification window. Prefer an isolated local database or an
explicitly approved maintenance window after the candidate release containing migration `0005`
has passed CI and deployment review.

Verify the current IG demo historical allowance, choose explicit instruments, and create an exact
half-open UTC range plan. The selected universe may be an approved non-streaming candidate when
exploring a new instrument, but its listings must already have passed the normal validation path.

For a promoted, epic-pinned candidate, validate it explicitly against the isolated writable
database that will receive the historical bars:

```bash
uv run qtrad instruments sync --universe tmp/capture-v2.toml
```

This command uses the supplied universe only for listing discovery/validation. It neither changes
`QTRAD_CAPTURE_UNIVERSE_PATH` nor starts ingestion, so it does not admit the instrument to live
capture. Do not run candidate-universe validation against the persistent collector: an effective
listing change there could interfere with a later `capture-v1` restart. Keep the candidate universe,
listing-validation events and historical plan in the same isolated database.

```bash
uv run qtrad backfill plan \
  --universe config/capture-v1.toml \
  --start 2026-07-01T00:00:00Z \
  --end 2026-07-01T06:00:00Z \
  --remaining-allowance 10000 \
  --output tmp/backfill-plan.json \
  fx:aud-usd fx:eur-usd
```

This stage reads PostgreSQL but does not contact IG. Inspect the complete JSON: universe and
configuration hash, exact listing IDs and effective versions, range, resolution, chunks and quota
observation. Retain it as review evidence. Repeat the printed hash to register it:

```bash
uv run qtrad backfill register \
  --plan tmp/backfill-plan.json \
  --confirm-plan-hash <reviewed-sha256>
```

Registration is idempotent for identical content and creates open BID/ASK/MID historical coverage
attempts without provider access. Execution is the only credential-gated stage:

```bash
uv run qtrad backfill execute --plan-hash <reviewed-sha256>
```

Execution atomically claims a registered `PLANNED` or explicitly retried `FAILED` plan before
constructing the IG adapter. It uses only the persisted listing versions and exact range. Review
the terminal plan/run status, provider-reported allowance and
`GET /api/v1/historical-coverage?only_open=true` afterward. A `COMPLETED` plan requires returned
data for all three bases. It does not imply that every market minute traded and never repairs or
closes an observed live-stream gap. Repeating a reviewed range creates separate plan evidence;
unchanged bars append nothing, while changed values append canonical correction revisions.

## Qualification

Before unattended collection, prove ARM startup, migration, restricted direct IPv6 SSH,
a second operator access route, backup/restore, image-digest rollback and host reboot
recovery. The second route may initially be policy-constrained Tailscale SSH; retain OCI
Bastion as a recommended break-glass improvement rather than blocking collection when the
other two routes are working. Then collect `capture-v1` for 72 hours, including a deliberate
container restart and host reboot. The ingestion container must receive `SIGINT` with its
configured 90-second grace period so the interrupted run is terminal before its replacement
starts.

For this first candidate only, five pre-candidate ingestion rows were abandoned by the superseded
`SIGTERM` stop contract. At or after the qualification not-before time, reconcile them before taking
the automatic evidence snapshot. Publish and pull a reviewed immutable image containing the
reconciliation command, but do not deploy it or restart any collector role. The guarded helper runs
only a one-off `--no-deps --pull never` command against the existing database:

The image is eligible only when its main-branch CI has first migrated a fresh PostgreSQL 18 database
to the collector's exact migration `0003`, passed the stale-run reconciliation integration test there,
then upgraded the same database to the current head and passed the complete suite. This explicitly
proves the newer one-shot command against the frozen schema; it does not authorise migrating the
collector. Publication still requires the reviewed pull request to merge, green main-branch CI and
operator approval of the protected `capture-release` environment. Those steps may occur after the
72-hour boundary while collection continues, but complete them promptly enough to preserve the
full-window logs required below.

```bash
TOOL_ROOT=/home/opc/q-trad-source
RECONCILIATION_IMAGE='<reviewed repository@sha256:digest>'
sudo docker pull "$RECONCILIATION_IMAGE"

sudo env \
  QTRAD_CAPTURE_ROOT=/opt/qtrad-capture \
  QTRAD_CAPTURE_ENV=/etc/qtrad/capture.env \
  QTRAD_RUN_RECONCILIATION_EVIDENCE_DIR=/var/lib/qtrad-capture/qualification \
  QTRAD_RUN_RECONCILIATION_IMAGE="$RECONCILIATION_IMAGE" \
  "$TOOL_ROOT/ops/capture/reconcile-runs.sh" \
  plan pre-candidate 2026-07-14T03:05:33.653928Z
```

Review the root-only plan. Require the documented capture source, `qtrad_capture`, `capture-v1`,
the exact configuration hash, the reviewed application version and `RECONCILIATION_IMAGE`, terminal
status `FAILED`, reason
`PRE_CANDIDATE_PROCESS_INTERRUPTED`, time basis
`OPERATOR_ASSERTED_CUTOFF_UPPER_BOUND`, and exactly the five known stale run IDs. The current
candidate starts after the strict cutoff and must not appear. Then repeat the printed hash before
execution:

```bash
PLAN=/var/lib/qtrad-capture/qualification/run-reconciliation-pre-candidate.json
sudo jq '{plan_hash,cutoff,capture_source_id,database_name,universe_name,
  configuration_hash,application_version,application_image,terminal_status,
  reason_code,finished_at_basis,targets}' "$PLAN"
PLAN_HASH="$(sudo jq -er .plan_hash "$PLAN")"

sudo env \
  QTRAD_CAPTURE_ROOT=/opt/qtrad-capture \
  QTRAD_CAPTURE_ENV=/etc/qtrad/capture.env \
  QTRAD_RUN_RECONCILIATION_EVIDENCE_DIR=/var/lib/qtrad-capture/qualification \
  QTRAD_RUN_RECONCILIATION_IMAGE="$RECONCILIATION_IMAGE" \
  "$TOOL_ROOT/ops/capture/reconcile-runs.sh" \
  execute pre-candidate "$PLAN_HASH"
```

Execution re-verifies the plan hash, capture/universe/database and immutable tool-image identity,
plus the complete eligible row set under lock. Any omitted, added, changed or already-terminal
target aborts the transaction.
On success, only those rows become `FAILED`; `finished_at` is explicitly an asserted upper bound,
not an invented exact stop time. Raw messages, canonical events and the candidate run are untouched.
Retain the plan with qualification evidence and confirm `/api/v1/runs` now contains exactly one
`RUNNING` row, the current candidate. Do not reuse this exceptional procedure for a healthy
terminal run.

At or after the recorded not-before time, create one non-overwriting automatic evidence snapshot
from a reviewed detached checkout. This does not deploy that checkout: `QTRAD_CAPTURE_ROOT` remains
the active pinned release, and the tool makes only loopback GET requests, Compose `ps`, one
`SELECT` of the migration version, systemd reads and filesystem-capacity reads. First confirm the
tool checkout is clean and at the reviewed PR commit. The evidence records the tool's own SHA-256.

```bash
TOOL_ROOT=/home/opc/q-trad-source
git -C "$TOOL_ROOT" status --short
git -C "$TOOL_ROOT" rev-parse HEAD

CURRENT_IMAGE="$(sudo sed -n 's/^QTRAD_IMAGE=//p' /etc/qtrad/capture.env)"
case "$CURRENT_IMAGE" in
  *@sha256:3ca07eaee8cf1500546c1779bb0732d9260b085e8a179e3514a507da4ee77d80) ;;
  *) printf 'unexpected qualification image: %s\n' "$CURRENT_IMAGE" >&2; exit 1 ;;
esac

sudo install -d -o root -g root -m 0700 /var/lib/qtrad-capture/qualification
sudo env \
  QTRAD_CAPTURE_ROOT=/opt/qtrad-capture \
  QTRAD_CAPTURE_ENV=/etc/qtrad/capture.env \
  QTRAD_STATUS_DIR=/var/lib/qtrad-capture \
  QTRAD_DATA_MOUNT=/srv/qtrad/postgres \
  QTRAD_QUALIFICATION_START=2026-07-14T03:05:33Z \
  QTRAD_QUALIFICATION_NOT_BEFORE_END=2026-07-17T03:05:33Z \
  QTRAD_QUALIFICATION_IMAGE="$CURRENT_IMAGE" \
  QTRAD_QUALIFICATION_DESCRIPTOR_COMMIT=89c7553160705ca0fd859fbb0477163efc0e279d \
  QTRAD_QUALIFICATION_DESCRIPTOR_SHA256=a95e53c3f7bec61ebc11126484ad61ad71828f542727c72a3d9654d88541c57d \
    QTRAD_QUALIFICATION_SOURCE_ID=local-development \
  QTRAD_QUALIFICATION_CONFIGURATION_HASH=227ff98752a8f54b5813f0aecaa307bd777cb5a388b0ce15ecd3e5cf5f24873b \
  QTRAD_QUALIFICATION_MIGRATION=0003 \
  "$TOOL_ROOT/ops/capture/qualification-evidence.sh" \
  /var/lib/qtrad-capture/qualification/capture-v1-final.json
```

The command exits non-zero but still writes reviewable evidence when an expected automatic gate
fails, including an HTTP 503 readiness response or unreconciled pre-candidate run. A successful
automatic result still reports `qualification_decision=PENDING_OPERATOR_REVIEW`: inspect and record
candidate-gap classification, bounded container-log history, OCI/Beszel monitoring history and the
active-market representativeness of the candidate window in `docs/CAPTURE_V1_QUALIFICATION.md`.
This review is distinct from ADR 0018's later physical-storage comparison. Never edit the generated
JSON; copy it with its `evidence_sha256` intact. Preserve failed evidence and use a new numbered
output name for a later retry; the helper will not overwrite the first attempt.

Inspect `readiness_configuration_basis` in the automatic record. Per ADR 0023, the frozen digest may
report `LEGACY_SINGLE_MATCHING_RUN_SHARED_RELEASE` only because its readiness response predates the
configuration-hash field. This basis passes only when reconciliation has left exactly one running
ingestion row in total, that row carries the expected hash, the application containers are the exact
frozen digest, PostgreSQL is the expected digest and every descriptor/source/readiness/adapter gate
also passes. Any later image must
report `ENDPOINT_CONFIGURATION_HASH`; a missing endpoint identity fails. The helper accepts both the
array and newline-delimited forms of Compose JSON but records one normalised array.

Immediately after the automatic snapshot, and before any restart, deployment or other lifecycle
operation, preserve the exact log window in a separate root-only bundle:

```bash
AUTOMATIC=/var/lib/qtrad-capture/qualification/capture-v1-final.json
LOG_BUNDLE=/var/lib/qtrad-capture/qualification/capture-v1-log-bundle

sudo env \
  QTRAD_CAPTURE_ROOT=/opt/qtrad-capture \
  QTRAD_CAPTURE_ENV=/etc/qtrad/capture.env \
  "$TOOL_ROOT/ops/capture/qualification-log-evidence.sh" \
  "$AUTOMATIC" "$LOG_BUNDLE"

VERIFIED_MANIFEST_SHA="$(
  sudo "$TOOL_ROOT/ops/capture/qualification-log-verify.sh" \
    "$AUTOMATIC" "$LOG_BUNDLE"
)"
printf 'verified log manifest: %s\n' "$VERIFIED_MANIFEST_SHA"
```

The helper verifies the automatic snapshot's self-hash and derives the candidate start and snapshot
time from it; do not transcribe a second window. It requires the three current Compose services to be
running, proves that the application containers use the snapshot's immutable image, and captures
filtered container identity, timestamped Docker logs and the `docker`, `qtrad-capture` and `tailscaled`
systemd journals. Each source is capped at 32 MiB by default while it is read. The manifest binds the
automatic evidence hash, helper hash, container image/restart/logging identities and every retained
file hash. The directory is non-overwriting mode `0700`; its files are mode `0600`.

The independent verifier is read-only. It requires the exact root ownership and modes, rejects
symlinks and extra or missing files, rechecks both self-hashes and the automatic-evidence binding,
and verifies inspection identity plus every retained byte, line count, first/last timestamp and
source-window bound. Its only output is the verified `manifest_sha256`; use that value in the
evidence reference. A verification failure leaves the bundle untouched and cannot be waived by
editing or rehashing it; preserve the failed bundle and recapture to a new directory only when the
reviewed protocol permits.

This bundle is retained operator evidence, not a third qualification decision and not an automated
claim that the logs are complete or healthy. Review the first/last timestamps, log-rotation metadata,
reboots and all relevant messages against the full candidate window. Reference the manifest path and
`manifest_sha256` from bounded gap/log reviews. Never commit the bundle or raw logs to Git, and never
edit a retained file; a failed capture gets a new output directory.

Create a separate operator review file. It must bind the automatic evidence hash, use canonical UTC
timestamps, cover the full candidate-to-snapshot window for logs and monitoring, and classify every
reported gap exactly once. `UNEXPLAINED` cannot pass. Evidence references are stable, non-secret
labels or paths to retained screenshots/reports; do not paste unbounded logs into this JSON.

```json
{
    "schema": "qtrad-capture-qualification-review-v2",
  "qualification_evidence_sha256": "<automatic evidence_sha256>",
  "reviewed_at": "2026-07-17T05:00:00Z",
  "reviewer": "operator",
  "reviews": {
    "candidate_gap_classification": {
      "decision": "NOT_REQUIRED",
      "gaps": [],
      "notes": "The automatic evidence contains no candidate-window gaps."
    },
      "container_log_history": {
        "decision": "PASS",
        "window_start": "2026-07-14T03:05:33Z",
        "window_end": "<automatic generated_at>",
        "evidence_refs": ["capture-v1-log-bundle/manifest.json#sha256:<manifest_sha256>"],
        "notes": "No terminal failure, traceback or unexplained restart was present."
      },
    "monitoring_history": {
      "decision": "PASS",
      "window_start": "2026-07-14T03:05:33Z",
      "window_end": "<automatic generated_at>",
      "evidence_refs": ["oci-beszel-review-20260717.txt"],
      "notes": "Readiness, backup, restore and disk history were reviewed."
    },
    "active_market_representativeness": {
      "decision": "PASS",
      "evidence_refs": ["market-hours-review-20260717.txt"],
      "notes": "The window included representative active Asia, Europe and US sessions."
    }
  }
}
```

When gaps exist, set the gap decision to `PASS` or `FAIL` and provide one matching `gap_id`, a
non-empty rationale, a non-empty bounded `evidence_refs` array and one of
`EXPECTED_MARKET_CLOSURE`, `EXPECTED_MARKET_INACTIVITY`, `EXPLAINED_PROVIDER_MAINTENANCE`,
`EXPLAINED_LIFECYCLE_EVENT` or `UNEXPLAINED` for each automatic-evidence gap.

`EXPECTED_MARKET_INACTIVITY` is not a synonym for “no ticks arrived”. Per ADR 0021, retain evidence
that the same connection generation and subscription set bracketed the interval, no disconnect,
reconnect, unsubscription, dropped record or terminal failure occurred, quotes resumed spontaneously
before the configured stale-reconnect threshold, and dealing-state, cross-channel or market-session
context supports inactivity rather than capture-path failure. If any part is missing or ambiguous,
use `UNEXPLAINED`; it cannot pass. Classification does not set `repaired_at` or alter raw/canonical
history. A gap entry therefore has this shape:

```json
{
  "gap_id": "<automatic-evidence gap UUID>",
  "classification": "EXPECTED_MARKET_INACTIVITY",
  "evidence_refs": ["gap-review-<gap UUID>.json"],
  "rationale": "Same-generation continuity and spontaneous recovery are demonstrated by the retained bounded review."
}
```

After the candidate window, investigate each gap through the reviewed demo historical-coverage
workflow in an isolated writable database. Build an explicit plan for the exact instrument, effective
listing version and UTC interval (expanded only as required to align one-minute bars), retain quota
and returned-bar evidence, and compare it with the immutable live raw/canonical record. Historical
bars present during the silence justify further stream-path investigation; no bars are evidence
consistent with upstream inactivity. Neither outcome proves what the streaming endpoint emitted,
changes the gap, or substitutes for the ADR 0021 continuity and full-window reviews.

Make that comparison reproducible as follows:

1. Preserve the automatic qualification evidence, then create and import a verified collector
   snapshot whose `source_created_at` is at or after the evidence `generated_at`. Use the isolated
   `qtrad_research_*` database and import evidence from `docs/RESEARCH_SNAPSHOT_RUNBOOK.md`. Never
   migrate the collector as part of this investigation.
2. Apply the reviewed migrations to the isolated database, set its normal q-trad database and capture
   source configuration, then derive the plan without manually transcribing gap times or instruments:

   ```bash
   uv run qtrad qualification gap-plan \
     --evidence capture-v1-final.json \
     --snapshot-import-evidence capture-v1-snapshot-import.json \
     --universe config/capture-v1.toml \
     --remaining-allowance '<current IG demo allowance>' \
     --output gap-history-backfill-plan.json
   ```

   The command requires the configured target to be the exact verified `qtrad_research_*` import,
   checks that the snapshot postdates the automatic evidence, proves source/universe identity and
   requires the database to be at the repository's single current Alembic head. It rounds the earliest
   gap down and latest gap up to UTC minute boundaries and sorts the distinct gap instruments. Review
   the resulting listing versions, range, quota and plan hash. Then register and execute only that
   confirmed hash through the normal backfill commands. Never point this operation at the collector
   database.
3. Export the exact plan interval from the isolated database with `--snapshot-import-evidence`.
   Retain the version-two manifest ID printed by the command.
4. Produce the offline comparison from the same configured research root:

   ```bash
   uv run qtrad qualification gap-history \
     --evidence capture-v1-final.json \
     --plan gap-history-backfill-plan.json \
     --manifest "${QTRAD_RESEARCH_ROOT}/manifests/<manifest-id>.json" \
     --output capture-v1-gap-history.json
   ```

The command makes no IG or database request. It re-verifies the automatic evidence, plan, verified
snapshot import, exact copied live gaps, completed plan coverage, manifest and Parquet hashes before
writing a non-overwriting `qtrad-qualification-gap-history-v1` artifact. Retain that artifact as an
ADR 0021 evidence reference. `HISTORICAL_DATA_PRESENT` prompts deeper streaming-path investigation;
`NO_HISTORICAL_DATA_RETURNED` supports but does not prove upstream inactivity. Neither is itself a
pass-eligible gap classification.

Then create the final hash-bound record offline from the reviewed checkout:

```bash
"$TOOL_ROOT/ops/capture/qualification-finalise.sh" \
  capture-v1-final.json \
  capture-v1-operator-review.json \
  capture-v1-qualification-decision.json
```

The finaliser verifies the automatic self-hash, automatic PASS, exact review binding, review windows,
gap set and bounded v2 review schema, including evidence references for every gap. It refuses
symlinks and overwrite. A valid failed operator review still writes a self-hashed v2 `FAIL` record
and exits non-zero; malformed, incomplete, mismatched or tampered input writes nothing. Only a
self-hashed final v2 `PASS` closes `capture-v1` qualification.

Only after that evidence passes may the candidate 20-instrument universe receive reviewed
IG epics and become a new capture configuration. It must pass its own 72-hour
qualification. A failed or ambiguous mapping leaves the collector on `capture-v1`.

### Physical storage growth

Do not deploy or restart services merely to measure the currently qualified representation. After
the inspector candidate has passed CI and been published by immutable digest, invoke that image as
a one-shot read-only process while the pinned collector continues unchanged. Pull the reviewed
digest once, then use the guarded helper. It rejects mutable images and unsafe labels, refuses to
overwrite evidence, uses `compose run --rm --no-deps --pull never`, and restores the evidence
directory plus completed file to root-only ownership after the non-root application writes it:

```bash
STORAGE_INSPECTOR_IMAGE='<reviewed repository@sha256:digest>'
sudo docker pull "$STORAGE_INSPECTOR_IMAGE"
sudo env \
  QTRAD_CAPTURE_ROOT=/opt/qtrad-capture \
  QTRAD_STORAGE_EVIDENCE_DIR=/var/lib/qtrad-capture/storage-evidence \
  QTRAD_STORAGE_INSPECTOR_IMAGE="$STORAGE_INSPECTOR_IMAGE" \
  /opt/qtrad-capture/ops/capture/storage-snapshot.sh pinned-before
```

Repeat the helper with `pinned-after` after a representative interval. Copy both evidence files to
an operator workstation and run
`uv run qtrad storage compare --output PINNED_COMPARISON BEFORE AFTER`; comparison is offline,
non-overwriting and requires no collector credentials. It writes a self-hashed comparison artifact
which retains the exact snapshot hashes and application image/version. The helper verifies that the
reviewed image is already local
and shell interpolation overrides the deployment environment's `QTRAD_IMAGE` only for this command.
`run --no-deps` does not recreate `db`, `ingest` or `api`; the candidate command opens one bounded
read-only transaction and exits. The snapshot records exact raw/canonical counts from one
repeatable-read transaction plus observed physical sizes. Database-wide growth is contextual; raw
and canonical relation deltas are the primary retention inputs.

Current snapshot schema version 3 remains able to load version-one and version-two evidence. It
reports JSON text-rendering sample size, individual index byte/scan deltas, and exact raw
representation-code counts when migration `0007` is present. Offline comparison attributes combined
retained growth to heap, indexes and auxiliary PostgreSQL storage, reports the
canonical-event/raw-message ratio and derives the representations added during the interval. Follow
`docs/CAPTURE_STORAGE_AUDIT.md`: use at least six active-market hours or 100,000 new raw messages,
whichever is longer, and reject a restart/statistics-reset interval for index-usage conclusions. Do
not remove an index or change payload representation from one small sample.

The comparison command requires identical capture source, database, universe, configuration,
application version and immutable image identity. Its `measurement_gate` requires both six elapsed
hours and 100,000 new raw messages and separately reports whether index-scan evidence is usable.
`operator_active_market_review_required` remains true because elapsed time and row count cannot
establish representative market activity by themselves.
For the later changed-field candidate, also require `raw_representation_evidence.status` to be
`CODED`, `all_new_rows_changed_fields` to be true and `legacy_unclassified_rows_delta` to be zero.
Any other result means the measured interval does not prove an uninterrupted changed-field writer.

After both release-bound comparison artifacts pass their automated thresholds, run:

```bash
uv run qtrad storage contrast \
  --output merged-vs-changed.json \
  pinned-comparison.json \
  changed-field-comparison.json
```

Contrast requires the same capture source, database, universe and configuration; distinct
digest-pinned images; a merged/pre-marker baseline; and a coded all-`CHANGED_FIELDS` candidate with
no new `LEGACY_UNCLASSIFIED` rows. It reports mechanical changes in database, raw, canonical and
combined bytes per raw message. The artifact deliberately records
`storage_decision_accepted=false` and keeps both active-market reviews required; the command cannot
turn automated thresholds into an operator decision.

For each comparison, create a bounded operator input after inspecting whether its exact measured
interval represented ordinary active-market conditions. Replace the zero hash below with the
`artifact_sha256` printed by `storage compare`, and use a review time after the interval end:

```json
{
  "schema_version": 1,
  "comparison_sha256": "0000000000000000000000000000000000000000000000000000000000000000",
  "reviewed_at": "2026-07-20T08:00:00Z",
  "reviewer_id": "operator@example.com",
  "active_market_representative": true,
  "reason": "The interval covered normal open-market activity without exceptional provider recovery."
}
```

Record both assertions and then qualify their exact contrast:

```bash
uv run qtrad storage review \
  --output pinned-active-market-review.json \
  pinned-comparison.json \
  pinned-active-market-review-input.json

uv run qtrad storage review \
  --output changed-active-market-review.json \
  changed-field-comparison.json \
  changed-active-market-review-input.json

uv run qtrad storage qualify \
  --output merged-vs-changed-qualification.json \
  merged-vs-changed.json \
  pinned-active-market-review.json \
  changed-active-market-review.json
```

The review command rejects a mismatched comparison hash, a pre-interval review time, surrounding
reason whitespace or oversized/extra input. Qualification rejects a review belonging to another
comparison or release. If either honest review is negative, it writes a hash-verified `FAIL` with a
stable reason instead of losing that evidence. A `PASS` closes only this comparison's review gate:
all review and qualification artifacts retain `storage_decision_accepted=false`. Their hashes bind
content but do not authenticate the named reviewer, so retain them in the reviewed evidence set.

`observed_rate_extrapolation` reports the interval's raw/canonical rates and the combined capture
relation bytes implied over one, 30 and 365 days if that exact rate continued. Treat it as a storage
sizing scenario only after the evidence gate and active-market review pass; it deliberately excludes
database-wide catalogue, backup and unrelated-relation growth.

ADR 0018's changed-field raw representation is a separate candidate release. Measure and complete
the active qualification first; do not mix representations within its 72-hour evidence window.

### `capture-v2` review and explicit promotion

Do not run this provider-backed review until the `capture-v1` qualification gate closes. Run it
from an isolated operator environment with IG demo credentials; it does not need PostgreSQL and
must never run on the collector merely to save a workstation REST call.

```bash
uv run qtrad instruments review \
  --catalogue config/capture-v2-candidates.toml \
  --output tmp/capture-v2-review.json
```

Inspect every instrument and candidate. The manifest may retain multiple eligible listings and
never recommends one. It is bounded, hash-addressed and declares `selection_authority=false`.
Create `tmp/capture-v2-selections.toml` manually with the manifest's exact hashes and exactly one
full IG demo listing ID for every catalogue instrument:

```toml
schema_version = 1
catalogue_hash = "<64-character catalogue hash>"
review_hash = "<64-character review hash>"

[[selection]]
instrument_id = "fx:aud-usd"
listing_id = "ig:demo:<explicitly reviewed epic>"

# Repeat [[selection]] for all 20 instruments. No omission or duplicate is allowed.
```

Then produce an undeployed candidate release:

```bash
uv run qtrad instruments promote \
  --catalogue config/capture-v2-candidates.toml \
  --review tmp/capture-v2-review.json \
  --selections tmp/capture-v2-selections.toml \
  --release-name capture-v2 \
  --output tmp/capture-v2.toml
```

Promotion rechecks the review hash, catalogue identity, exact instrument set, candidate
eligibility and one-to-one explicit selections. It refuses existing output files and performs no
IG call, database write, sync, deployment or stream start. Review the emitted TOML and its source
review/selection hashes before deliberately adding an approved release under `config/`. Merely
generating `tmp/capture-v2.toml` does not authorise a collector change.
