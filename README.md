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
service, installs the Codex, Python and Ruff extensions, and configures the Tilth,
Context7 and repository-scoped GitHub MCP servers through the Codex CLI. Codex is resolved
from npm's `latest` release when the container
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

The private canonical remote is `origin` at `https://github.com/quarrel/q-trad.git`.
Review commits and the worktree before pushing, then synchronise completed work regularly
rather than accumulating a large unpublished local history. GitHub MCP credentials are
scoped to this repository; they do not belong in tracked files or command output.

The Dev Container joins the operator's Tailscale network and may administer the collector
over Tailscale SSH using `ssh opc@q-trad-capture`. Tailscale policy permits this container
to reach only the collector's SSH service; it does not provide general access to the
collector or the operator's other network services.

Run project commands directly in the container:

```bash
uv run python -m qtrad db upgrade
uv run pytest
uv run ruff check src tests
uv run pyright
uv run ty check
```

After a feed-capable collector release is separately approved and deployed, a locally established
SSH or Tailscale tunnel can be probed without persisting a consumer cursor:

```bash
uv run qtrad feed probe \
  --endpoint http://127.0.0.1:18080 \
  --source-id SOURCE \
  --universe-name UNIVERSE \
  --configuration-hash SHA256
```

The endpoint must be a literal loopback address with an explicit port. The probe fetches one
bounded page, performs the strict feed and identity checks, and reports
`"cursor_persisted": false`. It does not establish the tunnel or acknowledge any events.

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

Historical backfill is deliberately split from research export and from live ingestion. First
create a non-overwriting plan for explicit instruments and an exact half-open UTC range:

```bash
docker compose run --rm app python -m qtrad backfill plan \
  --universe /app/config/capture-v1.toml \
  --start 2026-07-01T00:00:00Z \
  --end 2026-07-01T06:00:00Z \
  --remaining-allowance 10000 \
  --output /app/data/backfill-plan.json \
  fx:aud-usd fx:eur-usd
```

Planning reads already validated listings from PostgreSQL, requires them to match the selected
universe, reserves 20% of the operator-reported allowance, and makes no IG request. Inspect the
JSON and its printed hash. Registration requires that exact hash and still makes no IG request:

```bash
docker compose run --rm app python -m qtrad backfill register \
  --plan /app/data/backfill-plan.json \
  --confirm-plan-hash <reviewed-sha256>
```

Only then execute the persisted plan by hash. This is the credential-gated provider operation:

```bash
docker compose run --rm app python -m qtrad backfill execute \
  --plan-hash <reviewed-sha256>

docker compose run --rm app python -m qtrad research export \
  --universe /app/config/capture-v1.toml \
  --start 2026-07-01T00:00:00Z \
  --end 2026-07-02T00:00:00Z
docker compose run --rm app python -m qtrad replay --manifest /app/data/research/manifests/MANIFEST.json
```

Execution cannot rediscover or substitute listings, widen the range or alter live-gap evidence.
Identical bars are idempotent; changed historical values append correction revisions. It records
IG's provider-reported remaining allowance separately when available. Verify the current IG
allowance before planning and again before execution.

Research export records a run and manifest, so run it against an isolated restored database or
another explicitly approved writable research copy—not the live collector or its read-only SSH
tunnel role. Schema-version-2 manifests bind the selected universe/configuration, application
identity, coverage, gaps, provenance and per-file hashes. Replay verifies that complete identity
and the decoded semantic bars; legacy schema-version-1 manifests remain readable.

The supported collector-backup path is documented in the
[research snapshot runbook](docs/RESEARCH_SNAPSHOT_RUNBOOK.md). It verifies a complete downloaded
backup set, creates a new `qtrad_research_*` database without overwriting, emits hash-verified import
evidence and allows `research export --snapshot-import-evidence PATH` to bind the source snapshot
into the resulting Parquet manifest.

To measure physical capture growth, take two non-overwriting storage snapshots against the same
database and capture source, then compare them offline:

```bash
uv run qtrad storage snapshot \
  --universe config/capture-v1.toml \
  --output tmp/storage-before.json

# Wait for a representative capture interval, then use a new output path.
uv run qtrad storage snapshot \
  --universe config/capture-v1.toml \
  --output tmp/storage-after.json

uv run qtrad storage compare \
  --output tmp/storage-comparison.json \
  tmp/storage-before.json \
  tmp/storage-after.json
```

The snapshot transaction is read-only. The comparison writes a non-overwriting, self-hashed artifact
reporting whole-database, raw relation and canonical relation bytes per new raw message; the relation
figures are more useful than serialized JSON length alone. Do not run the changed-field candidate on
the collector during its frozen qualification window.

After separate merged-state and changed-field comparisons pass their automated gates, create their
release-bound contrast, record one bounded operator active-market review per comparison, and qualify
the exact set offline:

```bash
uv run qtrad storage contrast --output tmp/storage-contrast.json \
  tmp/merged-comparison.json tmp/changed-comparison.json
uv run qtrad storage review --output tmp/merged-review.json \
  tmp/merged-comparison.json tmp/merged-review-input.json
uv run qtrad storage review --output tmp/changed-review.json \
  tmp/changed-comparison.json tmp/changed-review-input.json
uv run qtrad storage qualify --output tmp/storage-qualification.json \
  tmp/storage-contrast.json tmp/merged-review.json tmp/changed-review.json
```

The input schema and operator procedure are in the
[capture operations runbook](docs/CAPTURE_OPERATIONS_RUNBOOK.md). Qualification preserves an honest
negative review as `FAIL`; even `PASS` is evidence only and cannot approve a storage-schema,
retention or archive decision.

Snapshot version 3 remains compatible with versions 1 and 2. It compares JSONB with PostgreSQL's JSON
text rendering, reports per-index growth/scan deltas and binds raw representation counts. The
evidence thresholds and schema candidates are recorded in the
[capture storage audit](docs/CAPTURE_STORAGE_AUDIT.md); no index or retention constraint is removed
automatically.

## Documentation

- [Agent instructions](AGENTS.md)
- [Implementation plan](PLAN.md)
- [Current status](docs/STATUS.md)
- [Seven-instrument soak runbook](docs/SOAK_RUNBOOK.md)
- [Research snapshot runbook](docs/RESEARCH_SNAPSHOT_RUNBOOK.md)
- [Capture storage audit](docs/CAPTURE_STORAGE_AUDIT.md)
- [Soak evidence record](docs/SOAK_EVIDENCE.md)
- [Implemented architecture](docs/ARCHITECTURE.md)
- [Engineering rules](docs/ENGINEERING.md)
- [Long-term preplan](PREPLAN.md)
- [Architecture decisions](docs/adr/)
