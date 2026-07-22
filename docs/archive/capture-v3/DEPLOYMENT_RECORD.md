# capture-v3 deployment record

The 2026-07-22 release advanced the OCI IG demo collector from the 19-market `capture-v2` universe
to the 20-market `capture-v3` universe. Hang Seng was added as
`IX.D.HANGSENG.IFM.IP`; Bitcoin and VIX remained quarantined rather than accepting an ambiguous or
unavailable listing.

## Why the two pre-sync starts failed

The new 19-market configuration had been activated before its provider listings were synchronised
into PostgreSQL. Ingestion deliberately failed closed when it found only the previous seven
approved listings. The systemd unit used `Restart=on-failure` with a 60-second delay, so it retried
while the separate manual synchronisation was still incomplete and failed for the same reason.

The fix removes that ordering dependency: ingestion now discovers and validates only approved
configuration entries through its existing IG REST session, persists missing or changed listings,
and only then subscribes. Reload uses the same validation and synchronisation path before replacing
the subscription set.

## Saturation incident and correction

Before replacement, the running collector remained connected on all 19 Lightstreamer channels but
its application queue reached 9,999/10,000 and its process reported 451,041 dropped callbacks. It
was using about 95% CPU and 1.95 GiB memory. The one-minute bar builder was sorting all historical
minute buckets for every quote and retaining closed buckets indefinitely, so work and memory grew
with collector age.

The deployed correction scans only open intervals, retains closed correction state for one hour,
records an explicit gap for later corrections, and caches confirmed stream versions after a
successful append. The affected pre-deployment interval is incomplete evidence; retained raw and
canonical history was not rewritten or deleted.

## Release and live evidence

- Merged commit: `f317c1ac0b47586782ac9047bab3bb18f31287ff`
- OCI index digest: `sha256:9d46c139fc58b580bff8ffeb53b6ed00e4ee3dd8a91c8d40694d96390ce1edba`
- Universe configuration hash:
  `50202ef7218f1d9816ebc88673259ecb5470f9360abe6b40f1f730c06d712836`
- Pre-deployment backup and schema gate: successful
- Readiness after cutover: HTTP 200; 20/20 subscribed, updated and recent; projection caught up
- Transport/application loss after cutover: zero reconnects, drops and Lightstreamer lost updates
- Queue after cutover: 0/10,000, high-water 20
- Representative post-start resource sample: about 11% ingest CPU and 113 MiB memory
- Hang Seng quote: healthy bid/ask received for `index:hong-kong-hs50`
- No-op dynamic reload: `SIGHUP` logged `capture_universe_reload_unchanged`, retained generation 1,
  and readiness remained HTTP 200

The release preserves the previous `ab86b9de` application and `sha256:5849acc0...` image as the
immediate rollback point.

## Follow-up query repair

Live verification exposed an existing PostgreSQL ambiguity in the optional instrument filter used
by the bars query. It affected the read-only single-instrument endpoint, not capture. The parameter
is now explicitly typed as `TEXT`, with the endpoint covered by the real-PostgreSQL integration
gate. PR 16 passed that gate and was merged as
`33827d5957216865bbdf399f54e72fbfdebc198e`, published as immutable OCI index digest
`sha256:c4959063abb6f9cad618437f66953daf3b615e3830ed342937d47820cbd8cbc9`, and deployed. Final
verification returned HTTP 200 for the Hang Seng instrument with its current quote, quote-derived
bars and approved listing, while collector readiness remained 20/20 with queue depth and loss at
zero.
