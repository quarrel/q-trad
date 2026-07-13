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
   dynamic group only Object Storage write, custom-metric publish and Notifications use
   permissions in this compartment. Create OCI Monitoring email alarms for collector
   readiness, stale backup/restore and low disk.
6. Install Docker Engine, Docker Compose plugin and OCI CLI. Use the pinned PostgreSQL
   container's client tools so backup validation cannot drift from the server major version.
   Place the reviewed release checkout at `/opt/qtrad-capture`; create root-owned
   `/etc/qtrad/capture.env`, `/etc/qtrad/capture-backup.env` and
   `/etc/qtrad/capture-monitor.env` with mode `0600`.
7. Configure `docker-credential-ocir` for `syd.ocir.io` using the collector's instance
   principal. Grant that instance only repository read and image create/update permissions
   for `qtrad/qtrad-app`; do not store an operator auth token on the host. The helper is
   built from reviewed commit `e2411c3c86c633537a8f10113c96c99c2fc71e5e`.

The PostgreSQL container bind-mounts `/srv/qtrad/postgres/data`, which must reside on the
dedicated iSCSI block volume mounted at `/srv/qtrad/postgres`. The capture systemd unit
requires that mount and must fail rather than write database data to the boot volume.

`capture.env` contains the IG demo credentials, database URLs for `qtrad_capture`, the
approved `QTRAD_CAPTURE_UNIVERSE_PATH`, and immutable image references. It is never copied
to a workstation, log, image or repository.

Every Compose invocation must pass `--env-file /etc/qtrad/capture.env`. Compose's service
`env_file` supplies the container environment but does not supply image references while
interpolating the Compose model. Do not print the rendered model because it contains
resolved secrets.

## Release and rollback

1. From development, run all static gates and tests, build the `linux/amd64` and
   `linux/arm64` images, test the ARM image, and publish an immutable registry digest.
2. Commit a reviewed deployment descriptor that pins only that digest and the approved
   universe/configuration hash. On the host, use `git pull --ff-only`; never build or use
   a mutable image tag there.
3. Run `qtrad db upgrade`, take a successful backup, start `qtrad-capture.service`, then
   require `GET /health/ready` to return HTTP 200 with all seven expected instruments.
4. Roll back only by restoring the previous digest/configuration and restarting Compose.
   Migrations are expand-only; canonical events and PostgreSQL volumes are never rolled
   back by deployment.

OCI Container Registry in Sydney currently rejects repository-level immutability. Publish
each release once under a unique `git-<commit>` tag, never publish `latest` or reuse a tag,
and deploy only the returned OCI index digest.

## Operations

- Install and enable the capture, backup and healthwatch systemd units/timers from
  `ops/systemd/`. The collector must continue after SSH or Bastion disconnects.
- Use direct IPv6 SSH for normal access. Use Bastion only for recovery or temporary local
  port forwarding to the loopback-only console.
- Run daily custom-format PostgreSQL backups, validate each archive with `pg_restore
  --list`, upload it with its checksum, retain 14 daily and 8 weekly copies, and perform a
  weekly restore into a disposable PostgreSQL container before recording success.
- Do not run development migrations, projection rebuilds, tests or paper/research writers
  against the capture database. Research consumes immutable Parquet manifests or a
  read-only snapshot over an SSH tunnel.

## Qualification

Before unattended collection, prove ARM startup, migration, direct IPv6 SSH, Bastion
recovery, backup/restore, image-digest rollback and host reboot recovery. Then collect
`capture-v1` for 72 hours, including a deliberate container restart and host reboot.

Only after that evidence passes may the candidate 20-instrument universe receive reviewed
IG epics and become a new capture configuration. It must pass its own 72-hour
qualification. A failed or ambiguous mapping leaves the collector on `capture-v1`.
