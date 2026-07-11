# q-trad

q-trad is a broker-neutral intraday research and trading framework. The current phase builds the market-data foundation against IG demo without any order-submission capability.

## Current data path

```text
IG demo / fixtures
  → raw PostgreSQL audit record
  → canonical quote events
  → one-minute bid/ask/midpoint bars
  → operational projections
  → Parquet research datasets
  → deterministic replay
  → read-only operator console
```

## Development

Docker and Docker Compose are the supported local runtime.

```bash
docker compose build app
docker compose up -d db
docker compose run --rm app python -m qtrad db upgrade
docker compose run --rm app coverage run -m pytest
docker compose run --rm app coverage report
docker compose run --rm app ruff format --check src tests
docker compose run --rm app ruff check src tests
docker compose run --rm app pyright
docker compose run --rm app ty check
docker compose up api
```

The operator console is then available at `http://localhost:8080`. Set
`QTRAD_API_PORT` if that host port is occupied.

Copy `.env.example` to `.env` only when running credential-gated IG demo integration. `.env` is ignored by Git. Never commit credentials.

### VS Code Dev Container

The repository includes a Dev Container for running the Codex extension and development
tools inside Docker rather than directly in WSL.

1. Install the VS Code Dev Containers extension.
2. Run **Dev Containers: Reopen in Container**.
3. Sign in to Codex when prompted inside the container.

The Debian Trixie container uses Python 3.13 and `uv`, starts the PostgreSQL 18 `db`
service, installs the Codex, Python and Ruff extensions, and configures the Tilth and
Context7 MCP servers. Codex is resolved from npm's `latest` release when the container
image is rebuilt without cache; Tilth is installed from a pinned npm package and release
binary. To update Codex, run **Dev Containers: Rebuild Container Without Cache** rather
than a cached rebuild. The container
keeps its virtual environment, dependency cache and Codex state in named volumes. It
copies the host's global `~/.codex/AGENTS.md` through a gitignored staging file, without
copying other Codex state. It does not mount the host Docker socket or any directory
outside this repository.

Docker is the outer isolation boundary, so the container-local Codex configuration uses
full access without approval prompts. Codex can modify anything in the repository,
including `.git` and `.env`, but cannot use Docker to reach other WSL containers or
filesystems. The Dev Container has unrestricted outbound internet access.

Run project commands directly in the container:

```bash
uv run python -m qtrad db upgrade
uv run pytest
uv run ruff check src tests
uv run pyright
uv run ty check
```

Closing VS Code does not stop the container or database, which allows a `tmux`-hosted
soak to continue. Stop them explicitly from WSL when required:

```bash
docker compose -f compose.yaml -f .devcontainer/compose.devcontainer.yaml down
```

## First data run

```bash
docker compose run --rm app python -m qtrad db upgrade
docker compose run --rm app python -m qtrad instruments sync
docker compose run --rm app python -m qtrad ingest --environment ig-demo
```

For a bounded smoke run that closes the broker session and finalises run tracking:

```bash
docker compose run --rm app python -m qtrad ingest --environment ig-demo --max-seconds 60
```

Ingestion carries all seven `PRICE` subscriptions on one Lightstreamer connection.
Do not run concurrent ingestion processes for the same IG API key.

To exercise a bounded token refresh and stream rebuild:

```bash
docker compose run --rm app python -m qtrad ingest --environment ig-demo \
  --max-seconds 90 --force-reconnect-after-seconds 20
```

Instrument synchronisation validates the configured standard-contract preference,
canonical quote currency and rolling/cash metadata for every instrument. It fails
closed if a preferred listing is missing or invalid.

Bounded backfill and research export are separate commands:

```bash
docker compose run --rm app python -m qtrad backfill --max-points 1000
docker compose run --rm app python -m qtrad research export
docker compose run --rm app python -m qtrad replay --manifest /app/data/research/manifests/MANIFEST.json
```

The backfill command treats the supplied allowance as operator-reported and reserves
at least 20%. It records IG's provider-reported remaining allowance separately when
the response supplies it. Verify the current IG allowance before invoking it.

## Documentation

- [Agent instructions](AGENTS.md)
- [Implementation plan](PLAN.md)
- [Current status](docs/STATUS.md)
- [Seven-instrument soak runbook](docs/SOAK_RUNBOOK.md)
- [Soak evidence record](docs/SOAK_EVIDENCE.md)
- [Implemented architecture](docs/ARCHITECTURE.md)
- [Engineering rules](docs/ENGINEERING.md)
- [Long-term preplan](PREPLAN.md)
- [Architecture decisions](docs/adr/)
