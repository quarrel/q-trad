# q-trad

q-trad is a single-user experimental framework for testing short-horizon factor-style strategies
against historical and live IG demo market data. The current objective is a trustworthy loop from
market data to continuous shadow paper outcomes and reproducible strategy rankings. It has no
external order capability.

## Current and intended paths

Implemented data foundation:

```text
IG demo / fixtures → raw and canonical PostgreSQL events → one-minute bars
→ Parquet research datasets → deterministic replay → read-only health views
```

Current extension:

```text
canonical data → comparable strategy forecasts → shadow paper fills/P&L
→ causal outcomes → effectiveness scores → market-state-aware ranking
```

The seven `capture-v1` markets proved ingestion. The next candidate research universe is the 20 FX,
index, commodity and crypto markets in `config/capture-v2-candidates.toml`; it cannot authorise
provider mappings or deployment.

## Development

Use the VS Code Dev Container or Docker Compose. Python dependencies and commands use `uv`.

Inside the Dev Container:

```bash
uv sync
ops/dev/verify.sh
uv run qtrad --help
```

`ops/dev/verify.sh` uses a disposable PostgreSQL 18 test database and runs the complete milestone
gate. Focused tests and static checks are appropriate while iterating. See
`docs/DEVELOPMENT.md` for the database boundary.

Copy `.env.example` to the ignored `.env` only for credential-gated IG demo work. Never commit or
print credentials, session tokens or account identifiers.

The local operator console is available at `http://localhost:8080` after starting the API role. It is
a diagnostic surface, not the primary strategy-research product.

## Collector boundary

An OCI collector currently runs the seven-market `capture-v1` configuration. Routine development
does not require access to it. Read-only observation, evidence collection, publication, deployment
and cloud changes are different operation classes; follow `docs/CAPTURE_OPERATIONS_RUNBOOK.md` and
the q-trad capture-operations skill when one is explicitly required.

Never improvise a collector restart, migration, database write or universe deployment. The candidate
20-market catalogue must pass bounded IG demo review and explicit operator selection before a release
is proposed.

## Documentation

Normal agent reading path:

1. [Agent instructions](AGENTS.md)
2. [Active plan](PLAN.md)
3. [Current status](docs/STATUS.md)
4. Relevant section of [architecture](docs/ARCHITECTURE.md)
5. Task-specific ADR or runbook only when needed

[Product direction](PREPLAN.md) explains the longer-term strategy population, evaluation, regime
and selection loop. [Archived records](docs/archive/) preserve superseded chronology without placing
it on every task's context path.

Other focused references:

- [Engineering guidelines](docs/ENGINEERING.md)
- [Research snapshot runbook](docs/RESEARCH_SNAPSHOT_RUNBOOK.md)
- [Paper framework acceptance](docs/PAPER_SLICE_ACCEPTANCE.md)
- [Architecture decisions](docs/adr/)

The canonical private Git remote is `origin` at `https://github.com/quarrel/q-trad.git`. Review the
worktree and outgoing commits before pushing; never include credentials, captured market data or
unfinished operational evidence.
