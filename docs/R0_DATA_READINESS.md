# R0 data-readiness evidence

**Measured:** 2026-07-22 15:35 UTC  
**Source:** live OCI `capture-v4`, configuration hash `eca6649c...606078`  
**Method:** read-only PostgreSQL queries against the collector projection

## Native coverage findings

- The store contained 9,563,038 `MarketQuoteObserved` events from 2026-07-13 03:28 UTC to
  2026-07-22 15:33 UTC, 645,402 quote-derived one-minute bar rows, 152 recorded
  `NO_HEALTHY_QUOTE_DURING_EXPECTED_STREAM` gaps, and 23 current quotes.
- All 23 current quotes were `HEALTHY` and had positive bid and ask sizes. In a bounded sample
  from 15:00 UTC onward, every observed quote for every instrument had both sizes present and
  positive. This establishes field availability, not trade-volume semantics.
- Every stored bar was `HEALTHY`, `QUOTE_DERIVED` and had BID, ASK and MID bases. The common
  observed MID interval for all 23 instruments was 2026-07-22 09:24–15:35 UTC. Of 371 possible
  one-minute slots, 20 instruments had 353 bars, China A50 had 331, Taiwan had 338 and VIX had
  337. The common missing interval overlaps the deployment/restart incident and is excluded from
  aligned research evidence until independently qualified.
- Native history is not yet balanced: the original seven instruments have bars from 13 July, the
  earlier capture set has coverage from 20 July, and China A50, Taiwan and VIX begin on 22 July.
  This is sufficient to define the R1 contracts but not to select a model or support an
  effectiveness claim.
- Revisions are material on seven earlier instruments (including AUD/USD, USD/JPY, EUR/USD,
  GBP/USD, US 500, FTSE 100 and Australia 200); the new APAC/VIX rows have no revisions observed
  yet. Research exports must retain revision identity and select the latest valid revision only
  through an explicit, tested rule.
- The current listing records contain product type, currency, minimum quantity, contract/lot size
  and value-per-point economics for all 23 instruments. `price_increment` is null for all of them,
  so paper eligibility must remain fail-closed until tick precision and the remaining
  spread/slippage, financing, commission and conversion assumptions are versioned.
- An authoritative session calendar is not persisted in the current listing metadata. Observed
  closures, provider silence and deployment gaps therefore remain explicit data conditions rather
  than inferred sessions.

## Data-role and historical-source decision

| Data | Permitted role | Boundary |
|---|---|---|
| Native IG streaming bid/ask quotes | Decision-grade quote and executable-side paper evidence | Preserve raw/canonical provenance; never forward-fill executable prices |
| Native quote-derived bars | R1 targets/features after gap, session and revision rules | No claim beyond observed native capture interval |
| IG historical candles | Bounded prototype or corroboration of a named interval | Separate provenance; cannot reconstruct quote paths or prove IG fills |
| External historical samples | Optional prototype/hypothesis rejection only | No source, licence or adapter is approved by R0; cannot support IG paper conclusions |
| Top-of-book sizes | Candidate quote-imbalance feature after semantic/coverage checks | Not trade volume and never labelled CVD |
| AUD VIX | Context-only feature candidate | Not paper-tradable |

R0 does not authorise a historical-data purchase, new external adapter or bulk historical request.
The next research work should use the native stream and, where specifically useful, bounded IG
candles with provenance-distinct manifests. An external source can be reconsidered only with an
identified venue/product mapping, timestamp and correction semantics, retention/licensing terms,
and a decision that its incremental value justifies the cost.

## R0 consequence

The native audit and bounded historical-source decision are complete. R1 may define the aligned
panel, target, gap, revision and fold contracts, while model selection remains blocked on a longer
and better-qualified native history and the missing economics fields.

## Restore verification

The repaired independent restore check passed on 2026-07-22 at 16:57:59 UTC. It restored
`daily/qtrad-capture-20260722T161655Z.dump`, verified its checksum and `qtrad-capture-backup-v2`
manifest, confirmed migration `0010`, counted 10,319,635 canonical events, and removed the
disposable PostgreSQL target. The target was backed by `/srv/qtrad/postgres/restore-verification`,
not the small Docker root filesystem. The live collector database was not modified.

The restore was materially I/O-intensive on the shared host. The preceding ingestion run recorded
12,738 callback drops from 16:38:29 to 16:50:18 UTC; that run remains visible as incomplete
evidence. Ingest was restarted at 17:02:04 UTC and the new generation was healthy at 23/23 with
zero drops. Future restore checks require an explicitly accepted low-load window.

R0 is complete. R1 may now define the aligned panel, target, gap, revision and fold contracts;
model selection remains blocked on longer and better-qualified native history and the missing
economics fields.
