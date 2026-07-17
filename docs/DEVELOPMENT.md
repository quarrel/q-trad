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
