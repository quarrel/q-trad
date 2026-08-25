# LAB-L temporal representation result

Status: COMPLETE — `EXPLORATORY_POST_HOC_ONLY`  
Source class: `IBKR_HISTORICAL_RESEARCH`  
Authorised base: `f31cf4731fc233726f45f67f54064c40965d01d7`  
Retained execution head: `21aae53d6ee1f0483f2e769a7dc360e5e9ffb74b`  
Preserved branch head after documentation cleanup: `d224d49bc94d4f53261d036e4395a41cf7b5b004`

CORE_6 and ALL_20 each evaluated engineered pooled Ridge, an engineered-feature MLP, and four
one-layer LSTMs from 15/60-minute lookbacks and hidden sizes 8/16. Every model lost directly to
`ZERO_RETURN` in aggregate, every chronological development block, and every instrument. Ridge was
best but negative; the 60-minute/16-hidden LSTM was the closest sequence design but remained worse
than Ridge and zero. The MLP was worst.

No candidate passed screening, no finalist freeze was created, and the former consumed holdout was
never loaded. The result supports neither generic nonlinearity nor temporal memory.

Do not nominate the tested LSTM or MLP design. Any future untouched experiment should test a
materially different hypothesis rather than tune these architectures post hoc.

Outputs: `/workspace/tmp/qtrad-r2-lab/LAB-L-attempt-2/` and
`/workspace/tmp/qtrad-r2-lab/LAB-L-ALL20/`. Execution record:
`/workspace/tmp/qtrad-r2-lab/LAB-L-EXECUTION.md`.
