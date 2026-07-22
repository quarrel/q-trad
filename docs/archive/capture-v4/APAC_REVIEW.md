# China A50, Korea 200, Taiwan, VIX and Bitcoin IG demo review

The bounded IG demo review observed `2026-07-22T08:34:21.032434Z` was run against
`config/capture-v4-apac-candidates.toml`. IG's `VIX` search returned the VIX row after the adapter's
provider-type filter was corrected to recognise IG's `COMMODITIES` classification for this canonical
index. An exact review hint was used only for the user-supplied Bitcoin epic, which ordinary search
does not return. Hints add evidence candidates but carry no selection authority.

- Catalogue SHA-256: `b9a193d40c8f0ca832dae12caeaec762cc9552a3486d5126f6d72fe3ac5567b8`
- Review SHA-256: `ed17cf86d566f742c0be3e0f003fa24ffc0bbb7a31642c71a386ec3e3782cf13`
- Explicit-selection SHA-256: `424aa02defcfe44f6bd5cd4847f9be6c44693ec846259b59e17c595ec43c4a3c`
- Selected China listing: `ig:demo:IX.D.XINHUA.IFM.IP`
- Selected Taiwan listing: `ig:demo:IX.D.TAIWAN.IFM.IP`
- Selected context-only VIX listing: `ig:demo:CC.D.VIX.UMA.IP`

The selected China A50 market is rolling and tradeable in USD, with minimum quantity `1.0`,
contract/lot size `0.2`, and one index point worth USD 0.20. The review also found the rolling USD 1
contract eligible. The USD 0.20 contract was selected for its smaller reviewed economics.

The selected Taiwan Index market is rolling and tradeable in USD, with minimum quantity `2.0`,
contract/lot size `10`, and one index point worth USD 10. The review also found the USD 40 contract
eligible. The USD 10 contract was selected for its smaller reviewed economics. Broad search results
also included unrelated Emerging Markets Index contracts, which operator selection rejected.

The VIX search resolved to `Volatility Index (A$1)`, rolling and tradeable in AUD, with minimum
quantity, contract size and lot size `1`, and one index point worth AUD 1. IG classifies this listing
as `COMMODITIES`; the adapter records that provider-specific classification without changing the
canonical index identity. VIX is selected for context capture only and is paper-ineligible. A later
research experiment may use it as a feature, but capture status does not grant a trading role.

The Korea searches found no eligible Korea 200 demo API listing. Broader aliases returned only
unrelated Emerging Markets Index listings. Historical or public-product availability cannot
override absent current demo evidence, so Korea 200 remains quarantined.

The supplied Bitcoin epic `CS.D.BITCOIN.CMA.IP` resolved directly to rolling Bitcoin USD economics:
minimum quantity `0.01`, contract/lot size `1`, and a USD 1 value per price unit. Normal search also
returned USD 1 and USD 0.10 Bitcoin contracts. All were unavailable when reviewed, so none passed
the fail-closed market-state gate. Bitcoin remains quarantined and may be reviewed again when the
exact listing is available.

The resulting undeployed `capture-v4` configuration has SHA-256
`eca6649cfd2477204d9a6d5970596657ad0d94b0a25916f8b26b9c5f0c606078` and contains the 20
`capture-v3` instruments plus selected China A50, Taiwan and context-only VIX listings. Publication
and activation have not been authorised; `capture-v3` remains the live collector.
