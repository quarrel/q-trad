# Seven-instrument soak evidence

**Verdict:** NOT RUN  
**Operator:**  
**Candidate commit:** `3c4eb12de10da19b7ecab528df14f944c4f1632a`  
**Runtime diff SHA-256:** `e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855`  
**`uv.lock` SHA-256:** `c1a89d38451a90d1ee86d31dab8d9ea18cf1b1fe6b989d89344ea437f17c5369`  
**Configuration hash:** `541647d96d0b1a3ac9d42d4572e3e247df147508eaf31f2fc018bd7d976d6515`  
**Start UTC:**  
**End UTC:**  
**Elapsed:**  

This is the evidence record for the WP7 soak defined in `docs/SOAK_RUNBOOK.md`. Do not
include credentials, session tokens, account identifiers, raw broker payloads or
unbounded logs.

## Preflight

- [x] Static checks and PostgreSQL-backed suite passed (86 tests; 2026-07-04).
- [x] Migration head applied (2026-07-04).
- [x] Candidate identity recorded and runtime frozen.
- [ ] IG environment confirmed as demo.
- [ ] Exactly seven canonical instruments and validated listings confirmed.
- [ ] Standard-contract mappings, currencies and product types confirmed.
- [ ] One API key has no other active ingestion connection.
- [ ] UTC clock and available storage checked.
- [x] Operator console health endpoints returned successfully (2026-07-04 rehearsal).

Notes:

The runtime diff hash is the SHA-256 of the binary Git diff restricted to `src`,
`migrations`, `pyproject.toml` and `uv.lock`. Its empty-diff value confirms that the
runtime candidate exactly matches the recorded commit. Test and documentation
improvements remain uncommitted and do not alter the frozen runtime.

## Session coverage

| Instrument | First update UTC | Last update UTC | Active session observed | Latest quality | Notes |
|---|---|---|---|---|---|
| `fx:aud-usd` |  |  |  |  |  |
| `fx:eur-usd` |  |  |  |  |  |
| `fx:usd-jpy` |  |  |  |  |  |
| `fx:gbp-usd` |  |  |  |  |  |
| `index:australia-200` |  |  |  |  |  |
| `index:us-500` |  |  |  |  |  |
| `index:ftse-100` |  |  |  |  |  |

## Observations

| UTC | Elapsed | Raw total/delta | Canonical total/delta | Projection lag | Gaps | Dropped | Health | Evidence reference |
|---|---|---|---|---|---|---|---|---|
|  |  |  |  |  |  |  |  |  |

## Forced reconnect

**Requested UTC:**  
**Completed UTC:**  
**Duration:**  
**Subscriptions before/after:**  
**Reconnect count before/after:**  
**Dropped records before/after:**  
**Health after reconnect:**  
**Evidence reference:**  

- [ ] REST session refreshed.
- [ ] Only one Lightstreamer connection existed.
- [ ] All seven subscriptions resumed.
- [ ] No unexplained data gap or loss was observed.

## Fresh-process restart

**Stop requested UTC:**  
**Previous run finalised UTC/status:**  
**New process started UTC:**  
**First all-seven healthy UTC:**  
**Interruption duration:**  
**Evidence reference:**  

- [ ] Previous run finalised cleanly.
- [ ] Previous connection closed before restart.
- [ ] All seven subscriptions resumed.
- [ ] No duplicate canonical facts or unexplained gap was observed.

## Final quality review

**Raw records:**  
**Canonical quote events:**  
**Bars by basis/provenance:**  
**Open gaps:**  
**Maximum projection lag:**  
**Dropped records:**  
**Failed/degraded intervals and explanations:**  
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

**Verdict (`PASS`, `FAIL` or `INCONCLUSIVE`):**  
**Rationale:**  
**Follow-up issues:**  
**PLAN/STATUS updated by:**  
