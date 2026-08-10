# q-trad-2 IBKR host controls

These files are deployment templates for the separate paper, read-only IBKR runtime. Do not copy
licensed Gateway/API archives, IBC configuration, passwords, 2FA material or rendered environment
files into Git.

B3 now supplies the software-only exact-two native-capture release boundary: promotion reuses reviewed B2 evidence for AUD/USD (conId 14433401) and Australia 200 (conId 111987484), while the offline preflight binds the immutable image, configuration hash, matched API/Gateway/IBC identities, private endpoints, dedicated database, current migration head and service templates. The wrappers use the existing `qtrad ingest --provider ibkr` and read-only API commands with persistent checkpoints and bounded health recovery.

B3 does not run a host, Gateway, database, deployment or qualification operation. `qtrad deployment ibkr-verify` and `qtrad deployment ibkr-preflight` are offline checks; `deploy.sh --check` is non-mutating, while `deploy.sh --apply` is an explicitly invoked host mutation reserved for separately authorized operation. No B3 result is an overall IBKR capture or Stage-6 qualification claim.

The dedicated native PostgreSQL service has its own earlier mutation boundary.
Create `/etc/qtrad/ibkr-postgres.env` from the reviewed example, then run
`postgres-provision.sh --apply`. A subsequent `--check` authenticates the
installed lifecycle scripts and unit against the reviewed checkout, the
immutable PostgreSQL image, the dedicated `qtrad_ibkr` database identity, the
loopback-only host port, and `/srv/qtrad/postgres/ibkr-native-data`. Run
`qtrad db migrate` against that empty database; never use `qtrad db upgrade`,
which also invokes the generic capture-universe seeder. `deploy.sh --check`
requires this database service and the expected migration head before any B3
runtime mutation.

The host does not install the q-trad Python application. By default,
`deploy.sh` runs offline release preflight through `qtrad-container-cli.sh` in
the exact immutable IBKR image, with networking disabled and only `/etc/qtrad`
and `/srv/qtrad/ibkr` mounted read-only. This keeps the preflight executable
identity aligned with the image that later runs the collector and API. The
wrapper invokes the installed virtual-environment Python directly so the
read-only preflight does not require a writable package-manager cache.

The official Gateway may expose its API listener as a wildcard socket. That
shape is accepted only because `verify-host.sh` independently requires the
reviewed `TrustedIPs` configuration and a firewalld policy that prevents remote
access to port 4002. The ingest wrapper requires the authenticated listener to
exist; it does not contradict that host-level authority by requiring a literal
loopback socket.

## Before running the bounded probe

1. Attach and mount the OCI block device at `/srv/qtrad/postgres`; `verify-host.sh` fails closed if
   the mount is absent.
2. Install the official matched 10.49 Gateway/API pair (retain 10.45 archives for rollback) and IBC
   3.24.1 outside the repository. Record the Gateway archive SHA-256 in the private host deployment
   manifest (`ibkr-gateway.identity.example.json` documents the non-secret shape); the manifest must be
   installed privately at `/etc/qtrad/ibkr-gateway-manifest.json`.
3. Install `ops/ibkr/gateway-config-check.sh` at the private path named by
   `QTRAD_IBKR_GATEWAY_CONFIG_CHECK`. Set `QTRAD_IBKR_GATEWAY_SETTINGS` to the active Gateway settings
   directory. The checker reads its `config.ini` for `TradingMode=paper`, `ReadOnlyApi=yes/true` and an
   explicit `AutoRestartTime` matching `QTRAD_IBKR_AUTO_RESTART_TIME`; it also requires
   `[IBGateway]/ApiOnly=true/yes` and `[IBGateway]/TrustedIPs=127.0.0.1` in `jts.ini`.
4. Build the application from the exact API ZIP; build-image.sh verifies its SHA-256, computes the
   runtime source-manifest fingerprint, extracts IBJts/source/pythonclient, and installs that official
   subtree. Set OCI labels for API/Gateway versions and archive identities, source digest, application
   commit and build time; use only an immutable image digest; build-image.sh requires
   `QTRAD_IBKR_PUSH=1`, tags the matched build, and prints both the source fingerprint and pushed digest.
   The IBKR image also embeds the commit in `/app/.qtrad-commit`, so runtime closure verification does
   not depend on Git being installed in the published image.
5. Set QTRAD_IBKR_CHECKPOINT_ROOT to a writable absolute path on the PostgreSQL volume,
   QTRAD_IBKR_API_PACKAGE_FINGERPRINT to the source fingerprint printed by the build, and
   QTRAD_IBKR_GATEWAY_MANIFEST/QTRAD_IBKR_GATEWAY_ARCHIVE_SHA256 to the private installation
   identity. Host verification requires these values to match the image labels. The wrapper mounts the
   checkpoint directory and runs the image as UID 10001, so a container restart preserves evidence.
   The reference environment uses
   `/srv/qtrad/postgres/qtrad-ibkr-checkpoints`; do not put acquisition checkpoints on the 25G root
   filesystem. When explicit historical CLI commands verify the private archive inputs, keep those files
   root-owned and grant UID 10001 only traverse/read ACLs on the named directory/files; never make the
   licensed archives world-readable. For example:
   `sudo setfacl -m u:10001:x /srv/qtrad/ibkr/artifacts`
   and `sudo setfacl -m u:10001:r-- /srv/qtrad/ibkr/artifacts/{ibgateway-10.49.1d-standalone-linux-arm.sh,twsapi_macunix.1049.02.zip,IBCLinux-3.24.1.zip}`.
6. Authenticate Docker to OCIR before running `deploy.sh`:
   `docker login syd.ocir.io`; the registry token must remain outside Git and shell history. The
   publishing principal needs repository-scoped `manage repos` permission for the IBKR repository;
   `read repos` or `use repos` alone is insufficient for Buildx manifest, SBOM and provenance publication.
   The policy shape is:
   ~~~text
   Allow group id <group-ocid> to read repos in compartment id <compartment-ocid> where target.repo.name = 'qtrad/qtrad-ibkr'
   Allow group id <group-ocid> to manage repos in compartment id <compartment-ocid> where all {target.repo.name = 'qtrad/qtrad-ibkr', request.permission = 'REPOSITORY_UPDATE'}
   ~~~
## Offline B3 release checks

Use `qtrad deployment ibkr-promote` only with an already reviewed B2 configuration and the immutable capability-review, operator-selection, contract-selection, catalogue and probe files; it replays that closure and writes their exact hashes into a new exact-two release create-only. `qtrad deployment ibkr-verify` requires the same authority files and replays them against the final persisted evidence. `qtrad deployment ibkr-preflight` reads their absolute paths from the immutable descriptor and performs the same replay. The B3 implementation and verification boundary stops there; live Gateway, database, deployment, restart/reconnect, backup/restore and qualification evidence require separate authorization.

## Offline B4 software boundary

B4 remains offline software until a separately authorized, real B3 qualification has
been frozen. The exact-six policy is AUD/USD, EUR/USD, Australia 200, US 500, Gold,
and US Crude. The four new conIds and all contract fields must come from a fresh,
replayed capability-review/selection closure; the implementation does not guess them.

B4 promotion, verification, and preflight remain fail-closed unless the qualification
artifact is independently replayed against the exact live capture database and a fresh,
hash-checked disposable restore. A sealed qualification summary, restore database name,
API response, fake store or copied database is not provenance and cannot mint the
runtime-only capability.

Run qualification commands, exact B4 promotion, and exact B4 preflight only as the
child command of `qtrad-ibkr-postgres-restore-verify`, through the installed
`qtrad-ibkr-qualification` wrapper. Set the selected archive with
`QTRAD_IBKR_RESTORE_ARCHIVE` and the new provenance path with
`QTRAD_IBKR_RESTORE_EVIDENCE_PATH`; the archive is never a positional argument.
The restore wrapper checks the selected backup's recorded SHA-256 before
`pg_restore --exit-on-error`, marks the disposable `qtrad_ibkr_restore_verify_*`
database with that archive identity, writes create-only restore evidence binding
source database, restored database, schema, archive SHA and completion, exports
the ephemeral restore URL/evidence path, executes the bounded qualification
command while the database exists, and then drops it. The qualification wrapper
is the single runtime composition: it preserves UID/GID 10001, read-only/cap-drop
hardening, exact live/restore database identities, and the narrowly writable
qualification-evidence directory. The application re-hashes the archive,
authenticates the database marker and exact evidence before any snapshot or
verifier can proceed.

The qualifying collector run must be cleanly stopped before backup so its final metrics
and last healthy, post-reconnect snapshot are immutable in the run record and replay
exactly after restore. Before that deliberate stop, stop `qtrad-ibkr-health.timer` and
any active `qtrad-ibkr-health.service`, then stop `qtrad-ibkr-ingest.service`. The health
unit no longer has a `Wants=` or `Requires=` activation edge to ingest, so a timer firing
cannot pull the collector up merely through dependency resolution. It can still execute
an explicit recovery action; quiescing the timer remains part of the qualification
boundary.

Run the bounded collector through the installed hardened ingest wrapper, not a rebuilt
Docker command. It accepts either no arguments for the continuous systemd service or
exactly the bounded qualification pair:

```bash
set -a
. /etc/qtrad/ibkr-ingest.env
set +a
/usr/local/sbin/qtrad-ibkr-ingest \
  --max-seconds 180 \
  --force-reconnect-after-seconds 60
```

Keep continuous ingest stopped until the qualifying backup has completed. On every
success or failure path, restore `qtrad-ibkr-ingest.service` and
`qtrad-ibkr-health.timer`; verify readiness before leaving the boundary.

Fresh backup archives, sidecars and restore evidence are root-owned mode `0640` with
runtime group `10001`; their containing directories are mode `0750` with that group.
This grants the hardened verifier read-only access without running its container as root
or applying operator ACLs. `QTRAD_IBKR_RUNTIME_GID=10001` is a required authenticated
backup-environment identity, not a default. Archives created before this contract require
a fresh backup rather than manual permission repair. The operator API exposes the same
bounded evidence query at `/api/v1/capture/qualification-evidence`; that read-only
endpoint never grants qualification authority.

After genuine B3 evidence is available, `qtrad deployment ibkr-promote
--policy b4-exact-six` requires the authenticated B3 release, its five authority files,
the B3 deployment descriptor, and fresh exact-six authority. The B4 release records
parent release and qualification identities and remains create-only under
`qtrad-ibkr-native-release-v2`. Run promotion through the same bounded restore
composition shown below, using `qtrad-ibkr-qualification deployment ibkr-promote
--policy b4-exact-six ...`; the wrapper rejects other promotion policies.

Before the first B4 deployment installs that wrapper revision, invoke the same executable
wrapper directly from the immutable, clean repository for the exact application commit that
will be recorded in the B4 release and descriptor. Keep it as the restore verifier's child;
do not copy, edit, or hand-compose the container command on the host.

B4 `deploy.sh --check` and `--apply` must themselves run as the restore verifier's child,
with `QTRAD_B3_PREFLIGHT_BIN` set to that qualification wrapper. The existing deployer then
routes its exact `deployment ibkr-preflight --policy b4-exact-six` invocation through the
live/restore composition while the disposable database exists; the wrapper rejects B3 or
other preflight policies.

The B4 exact-six authority must preserve the B3-qualified AUD/USD and Australia 200
listing identities, conIds, and immutable listing semantics. The other four contracts
must come from the fresh replayed authority closure. B4 continues to reuse the existing
service, database, port, client-ID, and checkpoint topology.

This software phase did not create a qualification artifact or real B4 release and did
not query either database. Closed-market connectivity is not qualification evidence.
Real qualification still requires LIVE bid and ask evidence during authenticated
ACTIVE periods, zero-loss persistence, controlled reconnect with fresh post-reconnect
data, and verified backup/restore.

The bounded single-restore interface remains the B3 snapshot/verification path:

```bash
QTRAD_IBKR_RESTORE_ARCHIVE=/srv/qtrad/postgres/backups/qtrad-ibkr-YYYYMMDDTHHMMSSZ.dump \
QTRAD_IBKR_RESTORE_EVIDENCE_PATH=/var/lib/qtrad/ibkr/restore-evidence/<new-name>.json \
  /usr/local/sbin/qtrad-ibkr-postgres-restore-verify \
  /usr/local/sbin/qtrad-ibkr-qualification \
  deployment ibkr-qualification-snapshot <snapshot arguments>
```

B4 snapshot and verification require two simultaneous, independently owned restores: the
qualification-bound parent B3 archive and the new B4 archive. Use the nested hardened wrapper:

```bash
QTRAD_IBKR_PARENT_RESTORE_ARCHIVE=<parent-B3-archive> \
QTRAD_IBKR_PARENT_RESTORE_EVIDENCE_PATH=<new-parent-evidence-path> \
QTRAD_IBKR_RESTORE_ARCHIVE=<current-B4-archive> \
QTRAD_IBKR_RESTORE_EVIDENCE_PATH=<new-B4-evidence-path> \
  /usr/local/sbin/qtrad-ibkr-dual-restore-qualification \
  deployment ibkr-qualification-snapshot --policy b4-exact-six <snapshot arguments>
```

Use the same composition with `ibkr-qualification-verify` and two fresh evidence paths
for independent replay.

B5 promotion and deployment preflight use that dual-restore wrapper with B3 as the parent
and the qualified B4 archive as the current restore. B5 snapshot and verification require
three simultaneous, independently owned restores:

```bash
QTRAD_IBKR_GRANDPARENT_RESTORE_ARCHIVE=<qualified-B3-archive> \
QTRAD_IBKR_GRANDPARENT_RESTORE_EVIDENCE_PATH=<new-B3-evidence-path> \
QTRAD_IBKR_PARENT_RESTORE_ARCHIVE=<qualified-B4-archive> \
QTRAD_IBKR_PARENT_RESTORE_EVIDENCE_PATH=<new-B4-evidence-path> \
QTRAD_IBKR_RESTORE_ARCHIVE=<current-B5-archive> \
QTRAD_IBKR_RESTORE_EVIDENCE_PATH=<new-B5-evidence-path> \
  /usr/local/sbin/qtrad-ibkr-triple-restore-qualification \
  deployment ibkr-qualification-snapshot --policy b5-full-universe <snapshot arguments>
```

Use the same composition with `ibkr-qualification-verify` and three fresh evidence paths
for independent B5 replay. Never decompose the wrappers' database lifecycles, Docker mounts,
user identity, or archive selection into an ad-hoc operator command.

## Running explicit historical CLI commands

The published IBKR image has an `uv` entrypoint. To invoke a q-trad subcommand, override that
entrypoint and retain the frozen dependency flags:

```bash
docker run --rm --network host --user 10001:10001 --entrypoint uv \
  --env-file /etc/qtrad/ibkr.env \
  --volume /srv/qtrad/ibkr:/data \
  --volume /srv/qtrad/postgres/qtrad-ibkr-checkpoints:/srv/qtrad/postgres/qtrad-ibkr-checkpoints \
  "$QTRAD_IBKR_IMAGE" run --frozen --no-dev --no-sync \
  python -m qtrad historical ibkr <command> ...
```

The B3 deployment, ingest, and API wrappers invoke the installed virtual-environment Python
directly. This preserves the frozen image dependency set without requiring a writable package-manager
cache inside their unprivileged, read-only containers. The ingest wrapper binds the reviewed
configuration and persistent checkpoint paths and is installed only by an explicitly authorized
`deploy.sh --apply`.

## OCIR repository setup

OCIR Sydney currently rejects `--is-immutable true`/repository-level immutability. Create the private
repository without that flag, publish each release once under its full commit-SHA tag, and deploy only
the returned `@sha256:` digest. Never publish `latest` or reuse a tag.

When filtering repository listings, JMESPath keys containing hyphens must be quoted. This is a safe
shell form:

~~~bash
oci artifacts container repository list \
  --compartment-id "$COMPARTMENT_OCID" \
  --all \
  --query "data[?\"display-name\"=='qtrad/qtrad-ibkr']" \
  --region "$OCI_REGION"
~~~

An empty result is the expected answer before the repository is created; it is not a query failure.
Keep the repository private and verify the resulting name/namespace before the first login.

## Host maintenance

The first Gateway start may briefly scan large JARs and show high CPU from `unzip` helper processes.
Do not launch multiple Gateway instances or repeatedly restart it while it is completing startup. Confirm
the effective paper/read-only settings and inspect `systemctl show qtrad-ibgateway-10.49.service -p NRestarts`
before diagnosing a restart loop.

Run at most one historical executor at a time. The executor takes a PostgreSQL advisory lock and fails
closed when another run is active; preserve the failed container and its logs for diagnosis rather than
starting a second run. `QTRAD_IMAGE_DIGEST` may be a full immutable `qtrad-ibkr@sha256:` reference for
canary binding; execution closure verification normalizes it to the locked bare `sha256:` digest.

Image retention is part of the normal deployment lifecycle. `deploy.sh --check` reports the
repository-scoped keep/remove plan without mutation. After a successful `--apply` restart,
the deployer recalculates that plan, preserves the exact deployed image and every image referenced
by any container, retains the most-recent additional unreferenced qtrad-ibkr image for rollback,
and removes only older unreferenced immutable digests from that repository. Images lacking an
immutable repository digest are retained for explicit review. It never invokes broad image or
system pruning and does not touch unrelated repositories, containers, build cache, volumes,
databases, backups, checkpoints or evidence.

PostgreSQL dumps and checksums belong under `/srv/qtrad/postgres/backups`; the backup script rejects
other locations. Rollout rollback archives must have an explicit retention/location decision rather than
accumulating under the 25G root filesystem. The disk timer checks both `/` and `/srv/qtrad/postgres`;
timer failures are visible in journald/Beszel and must be investigated rather than retried in a crash loop.
