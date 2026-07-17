# Development database and verification

The local Compose environment deliberately separates interactive state from test state.

## Persistent development database

`db` is the application-facing PostgreSQL 18 service and `qtrad_dev` is its development database.
Its named volume survives Dev Container
rebuilds so manual API, migration and research-export work can retain disposable development data.
The Dev Container sets `QTRAD_DATABASE_URL` and `QTRAD_MIGRATION_DATABASE_URL` only for this
database and runs `alembic upgrade head` at container start. It is not a test fixture, capture
archive or source of operational evidence.

The earlier local `qtrad` database predates this separation. It is not selected by any current
development or test URL and must not be automatically migrated or deleted; retire it only after an
explicit review of its disposable legacy contents.

## Disposable integration database

`test-db` is a separate PostgreSQL 18 service using the same pinned image digest as CI. Its data
directory is tmpfs-backed, it exposes no host port and its administrator has no route or credential
for `db` or the OCI collector.

Run the complete local gate from the Dev Container:

```bash
ops/dev/verify.sh
```

The helper fails closed unless its host is `test-db` or literal loopback. It creates a database
whose name begins `qtrad_test_`, points all application, test and migration URLs at that database,
runs formatting, linting, both type checkers and ShellCheck, and then mirrors CI:

1. migrate the clean database to frozen collector schema `0003`;
2. run the exact stale-run reconciliation compatibility test;
3. migrate the same isolated database to repository head;
4. run the complete pytest suite;
5. forcibly drop the disposable database during teardown.

`tests/test_postgres_integration.py` reads only `QTRAD_TEST_DATABASE_URL`. An ordinary
`uv run pytest` therefore cannot silently use the persistent development database. CI supplies an
explicit test URL. Do not point the test variable at `db`, a restored research database or any
remote host.

The host Docker socket remains outside the Dev Container. It is effectively a root-capability
boundary and is unnecessary for local PostgreSQL integration now that `test-db` is provisioned by
the outer Compose project.

## Streaming load experiment

Run a bounded callback-to-PostgreSQL experiment against another uniquely named tmpfs database:

```bash
ops/dev/stream-load-experiment.sh tmp/stream-load.json \
  --duration-seconds 300 \
  --callbacks-per-second 200 \
  --instruments 40 \
  --persistence-delay-ms 1
```

The helper applies migrations, drives a worker-thread callback stream through the real IG adapter
handoff, ingestion service, bar/gap logic and PostgreSQL store, writes one mode-0600 self-hashed JSON
result without overwrite, and forcibly removes its database. It fails closed on a non-local host,
unsafe database name, queue or SDK loss, incomplete persistence, excessive lag or an unclean
consumer exit. Generated evidence belongs under ignored `tmp/`, not in Git.

## Provider streaming contrast

Do not run the provider contrast while the OCI collector or any other Lightstreamer client uses the
same IG API key. It is intentionally guarded by an exact acknowledgement and is eligible only after
the current collector measurement has ended and an operator-approved stop has been verified.

With IG demo credentials available through the normal `QTRAD_IG_*` settings, run one bounded
three-hour, single-connection contrast spanning the target market window:

```bash
export QTRAD_PROVIDER_CONTRAST_SINGLE_CONNECTION_ACK=COLLECTOR_STOPPED_AND_NO_OTHER_STREAM
ops/dev/provider-stream-contrast.sh \
  tmp/provider-contrast.json \
  tmp/provider-contrast-events.jsonl.gz \
  --duration-seconds 10800 \
  --silence-seconds 180
```

The probe subscribes to the seven reviewed PRICE items, the matching seven CHART:TICK items and the
IG heartbeat on exactly one connection. It writes a mode-0600, non-overwriting, self-hashed JSON
manifest plus a gzip JSON-lines event stream containing receive time, provider timestamp,
changed-field identity and bounded lifecycle codes. It records no account identifier, token,
provider message or price value. Partial readiness, queue overflow, Lightstreamer loss, subscription
or server errors, feed discrepancies and unverified unsubscribe/disconnect all fail the run. A
failed login still produces an empty hash-bound event stream and failure manifest.
