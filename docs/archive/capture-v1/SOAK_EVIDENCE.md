# Seven-instrument soak evidence

**Verdict:** FAIL\
**Operator:** Codex, under operator direction\
**Candidate commit:** `09d46ddad5320c39327e1078072d1d712f1772e9`\
**Runtime diff SHA-256:** `e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855`  
**`uv.lock` SHA-256:** `c1a89d38451a90d1ee86d31dab8d9ea18cf1b1fe6b989d89344ea437f17c5369`  
**Configuration hash:** `541647d96d0b1a3ac9d42d4572e3e247df147508eaf31f2fc018bd7d976d6515`  
**Start UTC:** 2026-07-06T06:17:00.060837+00:00\
**End UTC:** 2026-07-06T07:34:20.659797+00:00\
**Elapsed:** 01:17:20.598960

This is the evidence record for the WP7 soak defined in `docs/SOAK_RUNBOOK.md`. Do not
include credentials, session tokens, account identifiers, raw broker payloads or
unbounded logs.

## Preflight

- [x] Static checks and PostgreSQL-backed suite passed (86 tests; 2026-07-06).
- [x] Migration head applied (2026-07-06).
- [x] Candidate identity recorded and runtime frozen.
- [x] IG environment confirmed as demo.
- [x] Exactly seven canonical instruments and validated listings confirmed.
- [x] Standard-contract mappings, currencies and product types confirmed.
- [x] One API key has no other active local ingestion connection.
- [x] UTC clock and available storage checked.
- [x] Operator console health endpoints returned successfully (2026-07-06 rehearsal).

Notes:

The runtime diff hash is the SHA-256 of the binary Git diff restricted to `src`,
`migrations`, `pyproject.toml` and `uv.lock`. Its empty-diff value confirms that the
runtime candidate exactly matches the recorded commit. The host and PostgreSQL UTC clocks
were within one millisecond, 792 GB of workspace storage was available, and no local
ingestion process or tmux session was active before launch.

The start-window data-quality review found IG `TIMESTAMP` values consistently about
1.5 seconds later than q-trad receive time while host and PostgreSQL clocks remained
aligned. Treat this as provider clock skew, not negative network latency. It must be
quantified over the complete run and carried into paper-fill latency assumptions.

## Session coverage

| Instrument | First update UTC | Last update UTC | Active session observed | Latest quality | Notes |
|---|---|---|---|---|---|
| `fx:aud-usd` | 2026-07-06T06:17:13.232Z | 2026-07-06T07:17:04.953Z | No | STALE | Stream stopped |
| `fx:eur-usd` | 2026-07-06T06:17:11.738Z | 2026-07-06T07:17:04.995Z | No | STALE | Stream stopped |
| `fx:usd-jpy` | 2026-07-06T06:17:11.342Z | 2026-07-06T07:17:05.157Z | No | STALE | Stream stopped |
| `fx:gbp-usd` | 2026-07-06T06:17:13.255Z | 2026-07-06T07:17:04.970Z | No | STALE | Stream stopped |
| `index:australia-200` | 2026-07-06T06:17:12.224Z | 2026-07-06T07:17:01.241Z | No | STALE | Cash session not covered |
| `index:us-500` | 2026-07-06T06:17:09.973Z | 2026-07-06T07:17:04.652Z | No | STALE | Cash session not covered |
| `index:ftse-100` | 2026-07-06T06:17:10.846Z | 2026-07-06T07:17:04.574Z | Yes | STALE | Stream stopped during cash session |

## Observations

| UTC | Elapsed | Raw total/delta | Canonical total/delta | Projection lag | Gaps | Dropped | Health | Evidence reference |
|---|---|---|---|---|---|---|---|---|
| 2026-07-06T06:17:11.785Z | 00:00:12 | 1,418/+76 | 1,740/+76 | 0 events | 0 | 0 | HEALTHY | Start API snapshot; run `80e13c32-5ebf-4a87-830a-1f2217339ba1` |
| 2026-07-06T11:27:50Z | 05:10:50 | 37,763/+36,421 | 39,324/+37,660 | Stopped at 39,324 | 0 | 0 | STOPPED/STALE | Failure investigation; last receive 07:17:03Z |

## Forced reconnect

**Requested UTC:** 2026-07-06T07:17:00.060837+00:00 (scheduled at process start)\
**Completed UTC:** Not completed successfully\
**Duration:** Reconnect exhaustion recorded at 2026-07-06T07:34:20Z\
**Subscriptions before/after:** 7 / 0 healthy\
**Reconnect count before/after:** 0 / 3\
**Dropped records before/after:** 0 / 0\
**Health after reconnect:** STOPPED; all latest quotes STALE\
**Evidence reference:** run `80e13c32-5ebf-4a87-830a-1f2217339ba1`; bounded tmux log review

- [ ] REST session refreshed.
- [ ] Only one Lightstreamer connection existed.
- [ ] All seven subscriptions resumed.
- [ ] No unexplained data gap or loss was observed.

## Fresh-process restart

**Stop requested UTC:**  
**Previous run finalised UTC/status:** 2026-07-06T07:34:20.659797Z / COMPLETED (incorrect operational status)\
**New process started UTC:**  
**First all-seven healthy UTC:**  
**Interruption duration:**  
**Evidence reference:** Restart supervisor refused to start a second connection because all-seven health failed.

- [ ] Previous run finalised cleanly.
- [ ] Previous connection closed before restart.
- [ ] All seven subscriptions resumed.
- [ ] No duplicate canonical facts or unexplained gap was observed.

## Final quality review

**Raw records:** 36,421 during the attempt\
**Canonical quote events:** 36,421 during the attempt\
**Bars by basis/provenance:** 413 each BID/ASK/MID, all `QUOTE_DERIVED`\
**Open gaps:** 0 (the process stopped before a gap was persisted)\
**Maximum projection lag:**  
**Dropped records:** 0\
**Failed/degraded intervals and explanations:** No updates after 07:17:03Z; stale-stream reconnect retries exhausted at 07:34:20Z.
**Redaction review result:**  

## Export and replay

**Manifest ID/path:**  
**Row count:**  
**Coverage:**  
**First replay SHA-256:**  
**Second replay SHA-256:**  

- [ ] Both replay hashes are identical.
- [ ] Manifest coverage includes all seven instruments.
- [ ] Provenance and gaps are represented.

## Pass-condition review

- [ ] At least 24 elapsed hours were observed.
- [ ] Australia 200, FTSE 100 and US 500 active sessions were included.
- [ ] One connection carried all seven subscriptions.
- [ ] Every instrument received and projected data.
- [ ] Forced reconnect passed.
- [ ] Fresh-process restart passed.
- [ ] Queue drops, gaps, staleness and projection lag were reviewed.
- [ ] No secret or account identifier exposure was found.
- [ ] Export replay was deterministic.

## Verdict and follow-up

**Verdict (`PASS`, `FAIL` or `INCONCLUSIVE`):** FAIL\
**Rationale:** The forced reconnect did not restore all seven subscriptions, ingestion ended after about 77 minutes, and the required restart and 24-hour/session coverage were not completed.\
**Follow-up issues:** Investigate the failed REST/session refresh, repeated stale reconnects, un-awaited Lightstreamer disposal callback, incorrect `COMPLETED` run status after reconnect exhaustion, and resident ingestion processes after finalisation.\
**PLAN/STATUS updated by:** Codex

Enduring lessons and pre-soak lifecycle qualification are recorded in ADR 0010,
`AGENTS.md`, `docs/ENGINEERING.md` and `docs/SOAK_RUNBOOK.md`.

## Remediation candidate qualification

This section is follow-up evidence, not a revision of the failed soak verdict above.

- 2026-07-06 deterministic gate: formatting, Ruff, Pyright and `ty` passed; 94 tests
  passed.
- Implemented generation-scoped readiness requiring transport, all expected subscription
  acknowledgements and a healthy update from every instrument.
- Implemented superseded-callback rejection, a `WILL-RETRY` watchdog, shared capped
  full-jitter retry cycles, fatal provider-code classification, terminal iterator failure
  propagation and confirmed stream disconnect.
- Applied a narrow compatibility repair to the pinned Lightstreamer 1.0.3 disposal
  callback; no unverified client/server upgrade was made.
- Credential-gated smoke run `2dfc0e77-4494-4a65-825b-da8cc65ed555` ran from
  2026-07-06T12:01:31Z to 12:03:37Z. The first forced session rebuild returned a bounded
  retryable `IGException`; the next attempt restored the stream.
- The recovered generation delivered healthy quotes for all seven instruments, recorded
  one reconnect and zero dropped records, finalised as `STOPPED`, confirmed transport
  shutdown and left no ingestion process resident.

The repeated reconnect sequence, two-hour endurance/restart gate and fresh uninterrupted
24-hour soak remain outstanding. The failed soak remains `FAIL` until replaced by a new
complete evidence record.
