# Seven-instrument soak runbook

This runbook governs the pending WP7 operational soak. It does not authorise order
placement and it does not change the data-only phase boundary.

## Pass conditions

The soak passes only when all of the following are evidenced:

- one ingestion process runs continuously for at least 24 elapsed hours;
- one Lightstreamer connection carries all seven canonical subscriptions;
- the run overlaps active Australia 200, FTSE 100 and US 500 sessions;
- every instrument receives raw updates and has a current canonical latest quote;
- one forced Lightstreamer reconnect completes and resumes all seven subscriptions;
- one application stop and fresh-process restart completes without a concurrent
  ingestion connection;
- no credentials, session tokens or account identifier appear in persisted subscription
  labels, captured evidence or logs;
- queue drops, gaps, staleness, projection lag and failed runs are reviewed and explained;
- the final research export replays twice to the same semantic SHA-256.

Uptime alone is not a pass. Any missing instrument, concurrent connection, unexplained
gap, secret exposure, non-deterministic replay or inability to establish price/timestamp
basis fails the run and must be investigated before another attempt.

## Candidate freeze

Before the soak:

1. Run every command in the rehearsal section.
2. Commit or otherwise preserve the exact tracked candidate; do not alter runtime source,
   migrations, dependencies or configuration during the soak.
3. Copy the candidate identifiers from `docs/SOAK_EVIDENCE.md`.
4. Confirm `.env` is ignored and contains IG demo credentials only.
5. Confirm no other ingestion process is using the same API key.

Documentation-only evidence updates are permitted during the soak. A runtime change
creates a new candidate and requires a fresh uninterrupted 24-hour run.

## Rehearsal

From the Dev Container:

```bash
uv sync --frozen
uv run ruff format --check src tests
uv run ruff check src tests
uv run pyright
uv run ty check
uv run coverage erase
uv run coverage run --branch -m pytest
uv run coverage report
uv run python -m qtrad db upgrade
```

Then exercise the credential-free API:

```bash
uv run python -m qtrad api --host 127.0.0.1 --port 8000
curl --fail --silent http://127.0.0.1:8000/health
curl --fail --silent http://127.0.0.1:8000/api/v1/system
curl --fail --silent http://127.0.0.1:8000/api/v1/instruments
```

Stop the API after the three requests succeed. A short IG demo smoke may be used to
rehearse credentials and collection, but it is not soak evidence:

```bash
uv run python -m qtrad instruments sync
uv run python -m qtrad ingest --environment ig-demo \
  --max-seconds 90 --force-reconnect-after-seconds 20
```

## Preflight

Record UTC time, candidate identifiers and operator in `docs/SOAK_EVIDENCE.md`, then:

```bash
date --utc --iso-8601=seconds
git rev-parse HEAD
sha256sum uv.lock
uv run python -m qtrad db upgrade
uv run python -m qtrad instruments sync
```

Using the operator console, verify:

- exactly seven active canonical instruments and seven validated IG demo listings;
- the expected standard-contract mappings and quote currencies;
- database and projection health;
- UTC host clock health and sufficient database/research storage;
- no active ingestion run or other process using the API key.

Do not proceed if an instrument mapping is ambiguous or if any endpoint could resolve to
IG production.

## Start and observe

Run ingestion in a persistent `tmux` session. The first process performs the required
forced reconnect:

```bash
tmux new-session -s qtrad-soak
uv run python -m qtrad ingest --environment ig-demo \
  --force-reconnect-after-seconds 3600
```

Start the read-only console separately:

```bash
uv run python -m qtrad api --host 127.0.0.1 --port 8000
```

At start, after reconnect, after restart, during each index session and at shutdown,
capture these read-only endpoints into the evidence record:

```bash
curl --fail --silent http://127.0.0.1:8000/api/v1/system
curl --fail --silent http://127.0.0.1:8000/api/v1/instruments
curl --fail --silent http://127.0.0.1:8000/api/v1/gaps
curl --fail --silent http://127.0.0.1:8000/api/v1/runs
```

Record, without pasting raw credentials or tokens:

- UTC observation time and elapsed duration;
- all seven latest-quote timestamps and quality states;
- adapter health, reconnect count and dropped-record count;
- projection checkpoint/lag and open gaps;
- raw and canonical growth since the previous observation;
- relevant bounded log event names and error types.

## Required fresh-process restart

After the forced reconnect has completed:

1. Send `Ctrl-C` to the ingestion process and wait for its run to finalise as `STOPPED`.
2. Confirm the broker session has closed and no ingestion process remains.
3. Record the stop and restart times and the finalised run through `/api/v1/runs`.
4. Start the same frozen candidate again without the forced reconnect option:

```bash
uv run python -m qtrad ingest --environment ig-demo
```

The combined observation window must still contain at least 24 elapsed hours. The restart
must be brief, deliberate and recorded; it does not permit overlapping connections.

## Shutdown and deterministic replay

After at least 24 elapsed hours and all three index sessions have been observed:

1. Send `Ctrl-C` and verify the ingestion run finalises as `STOPPED`.
2. Capture final system, instrument, gap and run snapshots.
3. Export and replay:

```bash
uv run python -m qtrad research export
uv run python -m qtrad replay --manifest data/research/manifests/MANIFEST.json
uv run python -m qtrad replay --manifest data/research/manifests/MANIFEST.json
```

Replace `MANIFEST.json` with the emitted manifest. Record the manifest ID, row count and
both hashes. Review logs and persisted labels for redaction without copying sensitive
values into evidence.

## Verdict

Complete every field in `docs/SOAK_EVIDENCE.md`. If any pass condition lacks evidence,
record `FAIL` or `INCONCLUSIVE`; never infer `PASS`. Only a passing record permits WP7,
`PLAN.md` and `docs/STATUS.md` to move to `DONE`.
