# q-trad-2 IBKR host controls

These files are deployment templates for the separate paper, read-only IBKR runtime. Do not copy
licensed Gateway/API archives, IBC configuration, passwords, 2FA material or rendered environment
files into Git.

The continuous IBKR adapter and its operator API are not implemented in this milestone. The host
entry point is therefore verification-only: it checks the invariants and never starts a Gateway,
ingest or health service. The bounded capability probe remains the only executable IBKR operation.

Before running the bounded probe:

1. Attach and mount the OCI block device at `/srv/qtrad/postgres`; `verify-host.sh` fails closed if
   the mount is absent.
2. Install the official matched 10.49 Gateway/API pair (retain 10.45 archives for rollback) and IBC
   3.24.1 outside the repository. Record the Gateway archive SHA-256 in the private host deployment
   manifest ("ibkr-gateway.identity.example.json" documents the non-secret shape); the manifest must be
   installed privately at `/etc/qtrad/ibkr-gateway-manifest.json`.
3. Build the application from the exact API ZIP; build-image.sh verifies its SHA-256, computes the
   runtime source-manifest fingerprint, extracts IBJts/source/pythonclient, and installs that official
   subtree. Set OCI labels for API/Gateway versions and archive identities, source digest, application
   commit and build time; use only an immutable image digest; build-image.sh requires
   QTRAD_IBKR_PUSH=1, tags the matched build, and prints both the source fingerprint and pushed digest.
4. Set QTRAD_IBKR_CHECKPOINT_ROOT to a writable absolute path on the PostgreSQL volume,
   QTRAD_IBKR_API_PACKAGE_FINGERPRINT to the source fingerprint printed by the build, and
   QTRAD_IBKR_GATEWAY_MANIFEST/QTRAD_IBKR_GATEWAY_ARCHIVE_SHA256 to the private installation
   identity. Host verification requires these values to match the image labels. The wrapper mounts the
   checkpoint directory and runs the image as UID 10001, so a container restart preserves evidence.
5. Run deploy.sh only as the invariant check; it pulls the immutable image digest before local
   inspection. It does not enable services while continuous ingest is absent. Run the explicit bounded
   command:
   `qtrad instruments review --provider ibkr --environment paper --execute-account-probe`.
6. Use the example `qtrad-ibkr-postgres.service` as the required readiness dependency when the
   future continuous adapter is introduced. It must provide the host's PostgreSQL start, readiness,
   and stop wrappers.

The API port remains inaccessible from outside the host. Tailscale/approved IPv6 access reaches the
operator API only after a future API service is installed; the Gateway socket remains localhost-only.
Weekly authentication/2FA expiry remains an operator action.

## Host maintenance

The health script is retained as a future control-plane hook. Its restart history must live under
`QTRAD_IBKR_RESTART_HISTORY_PATH` on persistent host storage, not `/run`, so a service restart cannot
reset the three-per-hour Gateway budget. It must only be enabled alongside a real continuous adapter
and API service. `OPERATOR` is logged once per cooldown and is not crash-looped.

Install `journald.conf.example` as a drop-in and run `systemctl restart systemd-journald`. The disk
timer checks both `/` and `/srv/qtrad/postgres`; backup files and checksums remain on the 100G
volume. The weekly restore-verification timer validates the newest dump through the PostgreSQL
container without modifying the live database. Timer failures are visible in journald/Beszel and
must be investigated rather than retried in a crash loop.
