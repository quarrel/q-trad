# `capture-v1` OCI qualification

**Status:** IN PROGRESS  
**Candidate window:** 2026-07-14T03:05:33Z to no earlier than 2026-07-17T03:05:33Z  
**Universe:** `capture-v1`, seven IG demo instruments  
**Application image:** `sha256:3ca07eaee8cf1500546c1779bb0732d9260b085e8a179e3514a507da4ee77d80`  
**Deployment descriptor:** `89c7553160705ca0fd859fbb0477163efc0e279d`
**Deployment descriptor SHA-256:** `c686332b24eff24e57a3c7128279777e2c45882e18b9e15d3149797272d40d84`
**Configuration SHA-256:** `227ff98752a8f54b5813f0aecaa307bd777cb5a388b0ce15ecd3e5cf5f24873b`

This record is the evidence ledger for the first persistent cloud collector. It does not
admit `capture-v2`, paper execution, strategies or any production broker capability.

## Entry evidence

- The ARM64 image, migration, backup upload, isolated restore, custom metrics, digest
  rollback and restricted direct/Tailscale SSH routes passed before the candidate window.
- The backup verifier restored migration `0003` and read 1,642 canonical events.
- Capture, one-minute healthwatch, daily backup and weekly restore-verification timers are
  enabled. The API and database remain unexposed.
- Initial readiness required seven fresh instruments and exact equality between the global
  event position and projection checkpoint.

## Lifecycle evidence

The first restart attempt at 2026-07-14T03:02Z was not candidate evidence. It revealed that
Compose's default `SIGTERM` terminated Python before ingestion could persist its terminal run
state. The deployment descriptor was corrected to send `SIGINT` and wait up to 90 seconds;
the 72-hour candidate clock therefore starts with the corrected descriptor.

Earlier deployment smokes also left non-terminal operational run rows under the old stop
contract. They pre-date the corrected candidate and do not imply concurrent containers, but
must be explicitly reconciled as interrupted/failed operational records before qualification
sign-off; they must not be silently deleted or presented as successful runs.

- Corrected descriptor activation finalised run
  `20b672d6-b0e9-4463-87e6-3b90cc5d3abc` as `STOPPED` and restored caught-up readiness.
- A host reboot finalised run `18674337-ee5b-4ad9-9b76-cba8f1e2d2ee` as `STOPPED`.
  Tailscale, Docker, the XFS database mount, the collector and all operations timers returned;
  replacement run `f51663b0-4f50-429c-ba56-8bca4e4a6488` reached seven fresh instruments
  with global and checkpoint positions both 4,409.
- A subsequent deliberate `docker restart` finalised that replacement run as `STOPPED` and
  started run `9ef5ca09-4025-429e-a7c0-383a8a2644fd`. It reached seven fresh instruments
  with global and checkpoint positions both 5,102.
- The first scheduled post-reboot healthwatch invocation succeeded, as did a direct
  healthwatch publication check.
- The read-only checkpoint at `2026-07-14T10:48:34Z` found the capture service plus
  healthwatch, backup and restore-verification timers active. API and PostgreSQL containers were
  healthy, ingestion had remained up for eight hours, all seven instruments were fresh, readiness
  had no reasons, and global/checkpoint positions were both `352275`. The most recent healthwatch,
  backup and restore-verification service results were successful.

## Exit checks

The reviewed `ops/capture/qualification-evidence.sh` command is prepared locally but is not part of
the frozen collector release. At or after the not-before time it will create one self-hashed,
root-only, non-overwriting snapshot binding the candidate window, release/configuration identity,
readiness, adapter and run evidence, systemd/Compose state, backup/restore ages, migration and disk
capacity. It cannot make the final decision: gap classification, full-window log and monitoring
review, and active-market representativeness remain explicit operator judgements. The latter asks
whether the qualification included meaningful open-market conditions; it is not ADR 0018's separate
physical-storage comparison.

The locally prepared `ops/capture/qualification-finalise.sh` consumes that immutable snapshot plus a
bounded operator-review JSON. It verifies the snapshot's self-hash, exact evidence binding,
full-window log/monitoring periods and one classification for every candidate gap. It produces a
second self-hashed, non-overwriting `PASS` or `FAIL` record. Invalid or tampered input produces no
decision record; a valid failed review is preserved and exits non-zero. This tool is also undeployed
and performs no collector, database, OCI or provider I/O.

At or after the candidate-window end, record and require:

- one current ingestion run, seven configured/fresh subscriptions and caught-up projection;
- explicit reconciliation of every pre-candidate non-terminal run record;
- no unexplained coverage gaps, dropped records or terminal adapter failure;
- successful scheduled backup evidence and no stale backup;
- successful restore evidence within its required age;
- acceptable database-volume free space and measured raw/canonical growth;
- no failed collector, timer or host units; and
- review of run lifecycle, container logs and monitoring history across the full window.

Any failure resets the candidate window after remediation. Expansion to `capture-v2` remains
separately gated by fail-closed IG demo listing validation and its own 72-hour qualification.
