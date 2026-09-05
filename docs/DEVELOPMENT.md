# Development database, verification and GitHub semantics

Local development and integration verification use separate PostgreSQL 18 services.

- `db` / `qtrad_dev` is persistent, disposable interactive development state.
- `test-db` creates a fresh guarded `qtrad_test_*` database for the complete verification gate.
- Integration tests require `QTRAD_TEST_DATABASE_URL` and cannot fall back to the development
  database, a research snapshot or the OCI collector.

Apply `docs/ENGINEERING.md#testing-proportionality` when changing tests or selecting validation.

Use focused tests and static checks while iterating. At a milestone, schema, evidence-boundary or
release candidate, run the complete clean gate inside the Dev Container:

```bash
ops/dev/verify.sh
```

It applies migrations, formatting, Ruff, strict typing, shell validation and tests. It runs
non-PostgreSQL tests concurrently with xdist and PostgreSQL tests serially. Tests using
`QTRAD_TEST_DATABASE_URL` or the shared test database must carry `pytest.mark.postgres`; do not
parallelise that lane without per-worker database isolation. Raw `uv run pytest` is not a substitute
for the complete gate unless the guarded database has already been provisioned exactly as required.

Tests must be process-isolated and order-independent outside the PostgreSQL lane. Do not suppress
linting or typing failures broadly. For elapsed shell timing use Bash's `time` keyword or epoch
arithmetic; the slim runtime image does not install `/usr/bin/time`.

The Dev Container does not mount the host Docker socket and cannot administer the collector. The
older local `qtrad` database is unselected legacy development state; do not automatically migrate or
delete it. Provider streaming/load experiments require their checked-in guarded scripts and current
runbook; do not run them during ordinary development.

## GitHub

The authenticated `gh` CLI is available. Candidate-specific validation, review, PR state and merge
authority bind to one exact commit SHA; head movement invalidates affected conclusions.

The fine-grained PAT cannot access the Checks API. When needed verify CI through GitHub Actions workflow runs for
the exact commit.

GitHub Actions testing is temporarily paused. Its `verify` job covers formatting, linting, typing and
shell checks only. A green workflow is static evidence, not a test pass. Local
`ops/dev/verify.sh` remains the complete test authority.
