# Paper vertical-slice research record

**Status:** In progress while the data-foundation soak runs
**Purpose:** record bounded questions, evidence and adopt/reject outcomes for the next
phase. This is not strategy-performance evidence.

## S1 — live quote and timestamp evidence

**Question:** Does the IG demo stream provide sufficient causal evidence for a
conservative top-of-book paper model?

**Initial evidence:** the start-window capture contains bid and ask values for all seven
instruments with no incomplete or crossed observations in the inspected sample. Raw
updates expose `BIDPRICE1`, `ASKPRICE1`, bid/ask sizes, dealing flag and provider
`TIMESTAMP`. IG event timestamps were consistently about 1.5 seconds later than q-trad
receive time even though host and PostgreSQL clocks agreed within one millisecond.

**Outcome:** ADOPT top-of-book bid/ask as executable evidence. Use q-trad receive time and
canonical global position for causal ordering. REJECT interpreting event/receive
difference as network latency and REJECT queue-position or partial-fill inference.

**Remaining evidence:** repeat the coverage, spread, incomplete-side, crossed-market,
source-clock-offset and update-interval analysis over the complete soak and by instrument
and session.

## S2 — bar source and decision cadence

**Question:** Should the contract-proof strategies consume IG historical candles or
q-trad-derived bars?

**Evidence:** quote-derived bars retain bid, ask, midpoint, sample count, revision and
source provenance. IG historical candles cannot reconstruct the ordered quote path and
have distinct `IG_HISTORICAL` provenance.

**Outcome:** ADOPT completed one-minute q-trad-derived midpoint bars for signal state and
retain bid/ask quotes for execution. REJECT provider candles as interchangeable signal or
fill evidence. Historical bars may warm indicators only when their provenance and
continuity are explicit; warm-up cannot emit an execution instruction.

**Initial live evidence:** every inspected post-start minute produced healthy bid, ask and
midpoint bars for all seven instruments. Median samples per minute varied materially by
instrument, from roughly 20 for FTSE 100 to 187 for USD/JPY, reinforcing that bar presence
does not imply homogeneous quote activity. The existing historical and quote-derived
datasets had no matching instrument/basis/minute tuples, so direct OHLC comparison is
currently INCONCLUSIVE rather than presumed equivalent. Run a deliberately overlapping,
quota-bounded backfill only after the frozen soak ends.

## S3 — session definitions

**Question:** Which sessions should govern new paper exposure?

**Evidence:** the [ASX cash-market timetable](https://www.asx.com.au/markets/market-resources/trading-hours-calendar/cash-market-trading-hours)
defines normal trading in Sydney time. The
[London Stock Exchange business-day calendar](https://www.londonstockexchange.com/equities-trading/business-days)
publishes full and shortened trading days, and the
[NYSE hours/calendar](https://www.nyse.com/markets/hours-calendars) defines its core
session and holiday exceptions in New York time. IG index CFDs also quote outside those
underlying sessions, so feed activity alone does not identify the economic session.

**Outcome:** ADOPT versioned venue-time reference-session profiles with explicit holiday
exceptions and `UNDERLYING_CASH`, `EXTENDED`, `CLOSED` and `UNKNOWN` classifications.
Only `UNDERLYING_CASH` permits new index exposure in the first slice. REJECT fixed UTC
windows and REJECT treating every fresh IG quote as an underlying cash-session quote.

**Remaining evidence:** pin authoritative holiday/session inputs for ASX, London, New York
and the FX rollover profile before runtime implementation.

## S4 — product economics and paper eligibility

**Question:** Does the current provider-listing model contain enough information to size
and value paper positions?

**Evidence:** it preserves currency, minimum deal size and price increment but not
contract size, lot size, dealing unit or pip value. IG's official
[`/markets/{epic}` reference](https://labs.ig.com/reference/markets-epic.html) exposes
`contractSize`, `lotSize`, `unit`, `valueOfOnePip`, currencies, opening hours and dealing
rules.

Although listing discovery receives the complete market-detail response and hashes it,
the current sync path persists only the reduced canonical `ProviderListing` as metadata.
The missing economics therefore cannot be recovered from the database after the REST
session closes. Repeated syncs also retain open-ended historical rows for the same
provider/external ID; application reads deliberately select the latest distinct row, but
the next migration must make effective-version closure explicit.

**Outcome:** REJECT paper eligibility based on the current listing fields alone. ADOPT
effective, versioned product-economics metadata containing quantity unit/step,
value-per-price-unit, quote currency and source metadata version. Missing or ambiguous
economics fail closed. Persist a bounded allow-list of market-detail fields rather than
the full provider response, and close the superseded effective version atomically during
sync. No order endpoint is required to collect these market details.

## S5 — historical depth and licensing

**Question:** What data can support later strategy inference without confusing another
venue or product with IG CFDs?

**Initial evidence:** IG backfill is quota-constrained and candle-only. Commercial vendors
such as [Tick Data](https://www.tickdata.com/product/historical-forex-data/) advertise
multi-year bid/ask FX history, while exchange/futures archives can provide centralised
index proxies. Neither reproduces the exact IG CFD quote stream.

**Outcome:** ADOPT continued IG quote capture as the product-specific forward dataset.
Treat licensed FX/futures data as separately identified research inputs and require an ADR
before adding a vendor adapter or dependency. REJECT buying or integrating a source
before sample files, licence terms, timestamp semantics, corrections and exact instrument
coverage pass review.

**Remaining evidence:** obtain price/licence/sample information for credible FX and
Australia/UK/US index-futures candidates, then present a purchase decision. This does not
block the contract-proof paper slice and cannot support a profitability claim by itself.

### Initial vendor shortlist

| Candidate | Relevant evidence | Initial disposition |
|---|---|---|
| [Tick Data](https://www.tickdata.com/product/historical-futures-data/) | Advertises millisecond Level-I bid/ask and trade history for more than 280 global futures, plus separate historical spot-FX quotes. Exact SPI/FTSE/S&P contracts and licence terms require a quote. | SHORTLIST for samples and coverage confirmation. |
| [Databento](https://databento.com/futures) | Self-service direct-feed futures data with trades, BBO/depth and usage-based pricing for CME and ICE among other venues; ASX coverage is not stated. | SHORTLIST for US/UK proxy experiments only; not a seven-instrument answer. |
| [LSEG Tick History](https://www.lseg.com/en/data-analytics/market-data/data-feeds/tick-history) | Global Level-I/II history, venue/reference data and custom extracts extending to the 1990s; pricing requires sales contact. | SHORTLIST as the broad-coverage benchmark, subject to cost and redistribution terms. |
| [Kibot](https://www.kibot.com/) | Lower-cost downloadable FX bid/ask and futures history with published update pricing, but exact source lineage and required index contracts need sample validation. | SAMPLE-ONLY challenger; do not adopt from marketing claims. |

No candidate is adopted. The purchase gate requires, for all requested instruments:
sample files spanning DST/session changes and volatile opens; bid/ask and timestamp
definitions; correction policy; contract/roll metadata; permitted local storage and
derived-result use; non-display/redistribution fees; and a reproducible total-cost quote.
