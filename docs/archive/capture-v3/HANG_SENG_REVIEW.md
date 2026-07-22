# Hang Seng IG demo review

The bounded IG demo review observed `2026-07-22T00:43:33.486221Z` was run against
`config/capture-v3-hang-seng-candidate.toml`.

- Catalogue SHA-256: `6bfcf421e650551bddfc3c39326933e7a1f6bc3c58c72b638409aa1d74f09613`
- Review SHA-256: `f7aa58a401e3ac2bf8b5beb74ff00d2f0122871e0790894d2207c252eb04284e`
- Explicit-selection SHA-256: `9e226eb83314fb3a0fe06504ef8b30c84dda12c3628f859679085b3c0d22bc87`
- Selected listing: `ig:demo:IX.D.HANGSENG.IFM.IP`
- Selected market: `Hong Kong HS50 Cash (HK10)`; rolling, tradeable, HKD; minimum quantity
  `0.5`; contract and lot size `10`; one index point is one pip worth HKD 10.

The review also found the rolling HKD 50 contract `IX.D.HANGSENG.IFD.IP` eligible. The HKD 10
contract was selected explicitly because it carries the same underlying cash-index feed with the
smaller contract economics. Dated futures and AUD/USD-denominated variants were not selected.

The same-day bounded searches for `VIX` and `Volatility Index` returned no eligible IG demo API
candidate. IG's public product schedule still lists an undated VIX CFD, but the `trading-ig`
maintainers record that IG does not make VIX data available through the web API. VIX therefore
remains outside capture rather than being assigned a guessed epic.
