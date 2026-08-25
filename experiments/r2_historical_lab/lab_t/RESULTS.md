# LAB-T nonlinear tabular result

Status: COMPLETE — `EXPLORATORY_POST_HOC_ONLY`  
Source class: `IBKR_HISTORICAL_RESEARCH`  
Authorised base: `f31cf4731fc233726f45f67f54064c40965d01d7`  
Corrected retained execution head: `a47e65f75ce4d142c20f3b7dd36b1f649418663b`  
Preserved branch head: `be13527eaa4b57a8d8736b01c74e48e1de90dd22`

Seven fitted configurations plus `ZERO_RETURN` were evaluated over CORE_6 and ALL_20. Pooled Ridge,
fixed/conservative histogram boosting, and small MLP variants all had negative aggregate development
skill. The MLP settings were numerically ineffective. P1 features improved histogram boosting
relative to P0, but the result remained negative and concentrated.

No nonlinear configuration passed advancement, the finalist freeze was empty, and the former
consumed holdout was never accessed.

The first retained attempt was rejected before selection because timestamp-derived membership
produced CORE_6 support 239,655 instead of LAB-0's authenticated 239,535. The corrected mechanism
authenticates retained target-ID membership and fails before registration on a support or zero-MSE
mismatch.

Nominate neither histogram boosting nor the tested MLP. Availability, range, time-of-day, and group
feature rankings remain low-priority hypotheses only.

Corrected output and detailed report: `/workspace/tmp/qtrad-r2-lab/LAB-T-rerun-1/`.
