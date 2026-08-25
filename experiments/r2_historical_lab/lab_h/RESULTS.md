# LAB-H horizon workstream result

Status: COMPLETE — `EXPLORATORY_POST_HOC_ONLY`  
Source class: `IBKR_HISTORICAL_RESEARCH`  
Authorised base: `f31cf4731fc233726f45f67f54064c40965d01d7`  
Execution and preserved branch head: `b581b699d30d115eca690436b27d9f5dbd6c27c2`

LAB-H evaluated local and pooled Ridge over 5, 15, 30, and 60-minute targets, plus
non-overlapping cadence offsets. Twelve configurations and 156 cadence-screen rows completed.

No aggregate development configuration beat `ZERO_RETURN`. Pooling reduced the damage relative to
local fitting but remained negative, and non-overlapping offsets did not rescue the result. The
frozen 5-minute and 30-minute finalists were evaluated once on the former consumed holdout as an
explicitly post-hoc development block; all terminal comparisons were also negative.

Do not nominate a horizon or cadence change for active-programme integration. Retain pooled
shrinkage only as relative context, not positive predictive evidence.

Raw output and detailed report: `/workspace/tmp/qtrad-r2-lab/LAB-H/`.
