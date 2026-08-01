# q-trad-2 IBKR host controls

These files are deployment templates for the separate paper, read-only IBKR runtime. Do not copy
licensed Gateway/API archives, IBC configuration, passwords, 2FA material or rendered environment
files into Git.

Before enabling either service:

1. Attach and mount the OCI block device at `/srv/qtrad/postgres`; `verify-host.sh` fails closed if
   the mount is absent.
2. Install the official matched 10.49 Gateway/API pair (retain 10.45 archives for rollback) and IBC
   3.24.1 outside the repository. Record SHA-256 values in the private host deployment configuration.
3. Build the application from the exact API ZIP; `build-image.sh` verifies its SHA-256, extracts
   `IBJts/source/pythonclient`, and installs that official subtree. Set OCI image labels for API
   version, Gateway version, source digest, application commit and build time; use only an immutable
   image digest.
4. Install the example units after providing `/usr/local/sbin/qtrad-ibgateway` and
   `/usr/local/sbin/qtrad-ibkr-ingest` wrappers. The ingest wrapper must assert host networking,
   `/srv/qtrad/postgres`, localhost-only Gateway binding and the firewalld denial before starting.
5. Install `healthcheck.sh` as `/usr/local/sbin/qtrad-ibkr-healthcheck`, then enable the health timer.
   Only explicit persisted `RESTART_ADAPTER` or `RESTART_GATEWAY` actions cause restarts. Missing
   weekend ticks and missing entitlements do not. `OPERATOR` is logged once per cooldown and is not
   crash-looped.
6. Run `verify-host.sh` after every image or Gateway change. It must pass before a connection-only
   acceptance test.

The API port remains inaccessible from outside the host. Tailscale/approved IPv6 access reaches the
operator API, not the Gateway socket. Weekly authentication/2FA expiry remains an operator action.
`/health/ready` is reserved for capture validity; the health timer uses `/api/v1/system` recovery
actions so closed markets cannot restart infrastructure.

## Host maintenance

Install the example units and scripts with the matching names under `/usr/local/sbin` and
`/etc/systemd/system`. `deploy.sh` is the single idempotent host entry point: it checks the
immutable image, mounted and writable evidence/PostgreSQL paths, localhost-only Gateway API,
firewall denial, matched image labels, then starts Gateway before ingest and enables the health,
disk, backup and restore-verification timers.

Install `journald.conf.example` as a drop-in and run `systemctl restart systemd-journald`. The disk
timer checks both `/` and `/srv/qtrad/postgres`; backup files and checksums remain on the 100G
volume. The weekly restore-verification timer validates the newest dump through the PostgreSQL
container without modifying the live database. Timer failures are visible in journald/Beszel and
must be investigated rather than retried in a crash loop.
