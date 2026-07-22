# q-trad

q-trad is a single-user experimental framework for testing whether short-horizon multi-asset
forecasts can justify useful paper portfolio positions after realistic costs and joint risk. It has
no external order capability.

## Current and intended paths

Implemented data foundation:

```text
IG demo / fixtures → raw and canonical PostgreSQL events → one-minute bars
→ immutable Parquet research datasets → deterministic replay → read-only health views
```

Retained framework proof:

```text
simple single-horizon forecasts → causal outcomes and executable-side shadow fills
→ independently checked P&L → reproducible ranking report
```

Active programme:

```text
aligned multi-asset data → chronological multi-horizon forecasts
→ explicit costs and portfolio risk → horizon-aware constrained paper targets
→ causal outcomes and component-aware experiment reports
```

Continuous live shadow paper follows the reproducible offline MVP. The old ranking report remains
framework evidence, not the intended portfolio architecture or a strategy-effectiveness claim.

The live `capture-v4` collector contains 23 reviewed FX, equity-index, volatility and commodity
markets. Its 22 non-VIX markets are potentially tradable subject to experiment role and fail-closed
paper eligibility; the AUD-denominated VIX is context-only. Korea 200 and Bitcoin remain
quarantined after fail-closed demo review. Future publication and activation require separate
authority.

## Development

Use the VS Code Dev Container or Docker Compose. Python dependencies and commands use `uv`.

Inside the Dev Container:

```bash
uv sync
ops/dev/verify.sh
uv run qtrad --help
```

`ops/dev/verify.sh` uses a disposable PostgreSQL 18 test database and runs the complete milestone
gate. Focused tests and static checks are appropriate while iterating. See `docs/DEVELOPMENT.md` for
the database boundary.

Copy `.env.example` to the ignored `.env` only for credential-gated IG demo work. Never commit or
print credentials, session tokens or account identifiers.

The local operator console is available at `http://localhost:8080` after starting the API role. It is
a diagnostic surface, not the primary research product.

## Collector boundary

The OCI collector is capture-only and routine development does not require access to it. Read-only
observation, provider review, publication, activation and cloud changes are distinct operation
classes. Follow `docs/CAPTURE_OPERATIONS_RUNBOOK.md` and obtain explicit authority for every write or
lifecycle operation.

Never improvise a collector restart, migration, database write or universe activation. Keep the
active `capture-v4` collector running until an immutable reviewed replacement is separately
authorised. Research and paper processes use verified snapshots or exports and never write to the
collector database.

## Documentation

Minimum reading path:

1. [Agent instructions](AGENTS.md)
2. Relevant active section of [the milestone plan](PLAN.md)
3. [Current status](docs/STATUS.md) when live operational or research state matters

Task-routed references:

- [Trading-research programme](docs/TRADING_RESEARCH.md) for targets, forecasts, evaluation, risk,
  portfolio or paper work
- Relevant section of [architecture](docs/ARCHITECTURE.md) for system-boundary changes
- [Engineering guidelines](docs/ENGINEERING.md) for implementation conventions
- [Capture operations](docs/CAPTURE_OPERATIONS_RUNBOOK.md) for collector operations
- [Research snapshot import](docs/RESEARCH_SNAPSHOT_RUNBOOK.md) for isolated research datasets
- [Architecture decisions](docs/adr/) when a task touches an accepted decision
- [Archived records](docs/archive/) only for historical reconstruction or retained evidence

The canonical private Git remote is `origin` at `https://github.com/quarrel/q-trad.git`. Review the
worktree and outgoing commits before pushing; never include credentials, captured market data or
unfinished operational evidence.
