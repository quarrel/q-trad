# Development database and verification

Local development and integration verification use separate PostgreSQL 18 services.

- `db` / `qtrad_dev` is persistent, disposable interactive development state.
- `test-db` creates a fresh guarded `qtrad_test_*` database for the complete verification gate.
- Integration tests require `QTRAD_TEST_DATABASE_URL` and cannot fall back to the development
  database, a research snapshot or the OCI collector.

Run the complete milestone gate inside the Dev Container:

```bash
ops/dev/verify.sh
```

It applies the required migration sequence, then runs formatting, linting, strict typing, shell
validation and the PostgreSQL-backed test suite before removing its temporary database. Use focused
checks while iterating; use the complete gate for milestones and release candidates.

The Dev Container does not mount the host Docker socket and cannot administer the collector. The
older local `qtrad` database is unselected legacy development state; do not automatically migrate or
delete it.

Provider streaming and load experiments are specialised operational work. Their retained historical
design is under `docs/archive/capture-v1/`, and current execution must follow the checked-in guarded
scripts plus `docs/CAPTURE_OPERATIONS_RUNBOOK.md`. Do not load or run them during ordinary strategy
development.
