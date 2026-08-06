# q-trad-2 IBKR host controls

These files are deployment templates for the separate paper, read-only IBKR runtime. Do not copy
licensed Gateway/API archives, IBC configuration, passwords, 2FA material or rendered environment
files into Git.

The continuous IBKR adapter and its operator API are not implemented. The direct official TWS
historical adapter, bounded request-profile canary, and one-shot historical executor now exist. Host
service deployment remains deliberately gated: the executor must be launched explicitly, uses a
PostgreSQL-held single-execution lock, and never starts orders, continuous ingest, or a health service.
The bounded capability probe, canary/profile verifiers, and independently verified execution/result
artifacts are the required evidence boundaries.

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
6. Authenticate Docker to OCIR before running `deploy.sh`:
   `docker login syd.ocir.io`; the registry token must remain outside Git and shell history. The
   publishing principal needs repository-scoped `manage repos` permission for the IBKR repository;
   `read repos` or `use repos` alone is insufficient for Buildx manifest, SBOM and provenance publication.
   The policy shape is:
   ~~~text
   Allow group id <group-ocid> to read repos in compartment id <compartment-ocid> where target.repo.name = 'qtrad/qtrad-ibkr'
   Allow group id <group-ocid> to manage repos in compartment id <compartment-ocid> where all {target.repo.name = 'qtrad/qtrad-ibkr', request.permission = 'REPOSITORY_UPDATE'}
   ~~~
7. Run `deploy.sh` only as the invariant check; it pulls the immutable image digest before local
   inspection. It does not enable services while continuous ingest is absent. Run the explicit bounded
   command:
   `qtrad instruments review --provider ibkr --environment paper --execute-account-probe`.

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

Retain the current image digest, runtime lock, evidence and approved rollback material. Review
after `docker system df`, exited probe containers and historical qtrad-app/qtrad-ibkr-probe image tags
before any cleanup; remove only targeted, unreferenced feasibility artefacts after their evidence is
preserved. Never use a broad `docker system prune` on this host.

PostgreSQL dumps and checksums belong under `/srv/qtrad/postgres/backups`; the backup script rejects
other locations. Rollout rollback archives must have an explicit retention/location decision rather than
accumulating under the 25G root filesystem. The disk timer checks both `/` and `/srv/qtrad/postgres`;
timer failures are visible in journald/Beszel and must be investigated rather than retried in a crash loop.
