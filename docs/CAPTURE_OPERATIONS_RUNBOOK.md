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

Install `capture-monitor.env.example` in the same way and replace its compartment OCID. Keep
the explicit regional `telemetry-ingestion` endpoint: OCI custom-metric publication does not
use the normal Monitoring query endpoint.

The PostgreSQL container bind-mounts `/srv/qtrad/postgres/data`, which must reside on the
dedicated iSCSI block volume mounted at `/srv/qtrad/postgres`. The capture systemd unit
requires that mount and must fail rather than write database data to the boot volume.

`capture.env` contains the IG demo credentials, database URLs for `qtrad_capture`, the
approved `QTRAD_CAPTURE_UNIVERSE_PATH`, immutable image references and a stable
`QTRAD_CAPTURE_SOURCE_ID`. `QTRAD_DB_PORT` may override the loopback-only PostgreSQL host port;
leave its default of `15432` unless that port conflicts. Use a non-secret machine identifier such as
`oci-sydney-capture-1`; retain it across image and universe releases and restores of the same
canonical history, but never reuse it for an independent event store. The file is never copied
to a workstation, log, image or repository.

Every Compose invocation must pass `--env-file /etc/qtrad/capture.env`. Compose's service
`env_file` supplies the container environment but does not supply image references while
interpolating the Compose model. Do not print the rendered model because it contains
resolved secrets.

## Release and rollback

1. GitHub Actions runs formatting, lint, type, shell and isolated PostgreSQL gates on every
   main-branch push and pull request. The manually dispatched `Publish application image`
    workflow builds `linux/amd64` and `linux/arm64`, publishes the commit-SHA tag and records
    its immutable OCI index digest. Configure the protected `capture-release` environment
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

## Operations

- Install the capture, backup, weekly restore-verification and healthwatch systemd units
  and timers from `ops/systemd/`. Enable them only after their manual gates pass. The
  collector must continue after SSH or Bastion disconnects.
- From the authorised Dev Container, use Tailscale MagicDNS and
  `ssh opc@q-trad-capture` for normal administration. The tailnet policy permits that peer
  to reach only TCP/22 on the collector. Retain restricted direct IPv6 SSH as an
  operator-controlled fallback and OCI Bastion for break-glass recovery or temporary
  port forwarding to the loopback-only console.
- Run the Beszel agent as a separate container with tailnet policy allowing only its
  outbound report to the operator's Beszel hub port. Beszel alerts supplement but do not
  replace the collector readiness watcher, OCI alarms, backup-age checks or restore
  evidence.
- Run daily custom-format PostgreSQL backups, validate each archive with `pg_restore
  --list`, and upload it with its checksum and a manifest binding the universe and image
  digests. Bucket lifecycle rules retain 14 daily and 8 weekly copies. A weekly job verifies
  the latest daily archive in an isolated, networkless, temporary PostgreSQL container
  using the manifest-pinned database image before recording success.
- Do not run development migrations, projection rebuilds, tests or paper/research writers
  against the capture database. Research consumes immutable Parquet manifests or a
  read-only snapshot over an SSH tunnel.

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

Only after that evidence passes may the candidate 20-instrument universe receive reviewed
IG epics and become a new capture configuration. It must pass its own 72-hour
qualification. A failed or ambiguous mapping leaves the collector on `capture-v1`.

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
