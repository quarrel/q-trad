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
docker compose run --rm app pytest
docker compose run --rm app ruff check .
docker compose run --rm app pyright
docker compose up api
```

The operator console is then available at `http://localhost:8080`. Set
`QTRAD_API_PORT` if that host port is occupied.

Copy `.env.example` to `.env` only when running credential-gated IG demo integration. `.env` is ignored by Git. Never commit credentials.

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
- [Implemented architecture](docs/ARCHITECTURE.md)
- [Engineering rules](docs/ENGINEERING.md)
- [Long-term preplan](PREPLAN.md)
- [Architecture decisions](docs/adr/)
