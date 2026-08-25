# R2 IBKR historical exploratory laboratory

This package preserves six completed model-development workstreams over the already-consumed
historical IBKR research input. Every result is `EXPLORATORY_POST_HOC_ONLY`, retains source class
`IBKR_HISTORICAL_RESEARCH`, and is neither a second holdout nor confirmation, promotion, or
decision-grade evidence.

All workstreams were authorised from base
`f31cf4731fc233726f45f67f54064c40965d01d7`. LAB-0 produced the shared authenticated dataset at
`/workspace/tmp/qtrad-r2-lab/LAB-0`; its manifest SHA-256 is
`462e40fa84038156b16c68bde4b68d574ab7862c680657ebd1a0035b39bf0072`.

## Workstreams

| Job | Package | Preserved branch head | Outcome | Raw output |
|---|---|---|---|---|
| LAB-0 | [`lab_0/`](lab_0/) | `73b35e2be152f9bacb8882dadb09a01e28ad4d37` | Canonical downstream dataset and baseline reconstruction | `/workspace/tmp/qtrad-r2-lab/LAB-0` |
| LAB-H | [`lab_h/`](lab_h/) | `b581b699d30d115eca690436b27d9f5dbd6c27c2` | No tested horizon or cadence beat zero | `/workspace/tmp/qtrad-r2-lab/LAB-H` |
| LAB-L | [`lab_l/`](lab_l/) | `d224d49bc94d4f53261d036e4395a41cf7b5b004` | No tested sequence or nonlinear model beat zero | `/workspace/tmp/qtrad-r2-lab/LAB-L-attempt-2`, `LAB-L-ALL20` |
| LAB-S | [`lab_s/`](lab_s/) | `9573e8451319c6cca0de7bc47cc4c4d9a556c8e7` | Tiny pooled development edge failed on the post-hoc terminal block | `/workspace/tmp/qtrad-r2-lab/LAB-S` |
| LAB-T | [`lab_t/`](lab_t/) | `be13527eaa4b57a8d8736b01c74e48e1de90dd22` | No nonlinear tabular candidate qualified | `/workspace/tmp/qtrad-r2-lab/LAB-T-rerun-1` |
| LAB-U | [`lab_u/`](lab_u/) | `a66b3af2230639684d932ad2b54cd06ae435fb83` | Wider pooling gains were concentrated and failed terminal evaluation | `/workspace/tmp/qtrad-r2-lab/LAB-U-attempt2` |

LAB-H, LAB-L, LAB-S, and LAB-U import LAB-0's preprocessing and/or authenticated harness explicitly
from `lab_0`. LAB-T remains deliberately self-contained because integration preserves the executed
experiment rather than behaviourally refactoring it.

The package-root `__init__.py` contains only programme-wide source/evidence constants. Each
workstream directory owns its executable module, compact configuration, run notes, and result
summary. Tests mirror this structure under `tests/experiments/r2_historical_lab/`.

Raw and large outputs remain outside Git under `/workspace/tmp/qtrad-r2-lab/`. The branch heads
above and the execution heads recorded in each result are scientific provenance; later namespace or
archive commits do not become the code identity that produced those results. Do not overwrite
historical outputs. Any reproduction must use a new create-only output path and must not make
provider calls or reinterpret the consumed terminal block.
