# capture-v4 deployment record

The 2026-07-22 release advanced the OCI IG demo collector from the 20-market `capture-v3` universe
to the 23-market `capture-v4` universe. It added China A50 (`IX.D.XINHUA.IFM.IP`), Taiwan
(`IX.D.TAIWAN.IFM.IP`) and an AUD-denominated context-only VIX (`CC.D.VIX.UMA.IP`). Korea 200 and
Bitcoin remained quarantined.

## Release identity

- Lifecycle-safe application commit: `1696c30afd6744e90d065c53517209ba24d3ba89`
- Deployment-descriptor commit: `1cbfda41d9b57f76c1d33e919fbdc22f949ea688`
- Exact descriptor CI run: GitHub Actions `29908647344`, successful
- OCI index digest:
  `sha256:0ebdf75d5abb484c14b0dac7375ceba8533e275ac8072653abb8354746b7a3bb`
- Universe configuration hash:
  `eca6649cfd2477204d9a6d5970596657ad0d94b0a25916f8b26b9c5f0c606078`
- Schema head: `0010`
- Pre-deployment backup: successful at `2026-07-22T09:20:35Z`, with the exact `capture-v3`
  configuration identity

## Failed first activation and rollback

The first activation used application digest `sha256:a61b2c33...ed0740f`. All 23 channels became
healthy, but subscription replacement overlapped a stale stream-status reconnect. The candidate run
later failed and emitted `capture_universe_reload_rejected`. The universe was rolled back to
`capture-v3`, ingestion was restarted, and exact 20/20 readiness and clean run state were restored.
No captured history was rewritten or deleted.

The same deployment preparation exposed a backup validation defect: the validation container did
not mount the active universe and inherited an unsuitable path. Commits `a2169ed`, `4fe101e` and
`66ab8d5` corrected and formatted that boundary before the successful backup above.

## Lifecycle correction

Commit `1696c30` serialised public subscription replacement with reconnect handling and added a
regression test for stale callbacks during replacement. Exact CI run `29908281930` and replacement
image publication run `29908389790` succeeded. Before a second universe activation, the replacement
image was deployed against unchanged `capture-v3` and proved exact 20/20 readiness.

## Successful activation evidence

One `SIGHUP` at `2026-07-22T09:41:40Z` produced `capture_universe_reloaded` at
`2026-07-22T09:41:43Z`. Verification showed:

- readiness HTTP 200 with the exact `capture-v4` hash and 23/23 subscribed, updated and recent;
- healthy bid and ask quotes for China A50, Taiwan and VIX;
- zero reconnects, dropped records, Lightstreamer lost updates, subscription errors and server
  errors;
- a caught-up core projection and empty queue, with high-water 24/10,000;
- one `capture-v4` ingestion run `RUNNING` and the immediately preceding `capture-v3` run `STOPPED`;
  and
- ingest and API containers on the exact replacement digest and release symlink on the exact
  descriptor commit.

The failed first candidate run remains truthful retained incident evidence. The immediate rollback
remains the immutable `capture-v3` universe and application/image identity recorded in
`config/capture-v4-deployment.toml`.
