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
- The read-only checkpoint at `2026-07-14T15:26:37Z` again found caught-up all-seven readiness:
  raw messages were `709986`, canonical events were `725683`, global/checkpoint positions were both
  `725684`, adapter state was `READY`, and reconnects, dropped records, provider operations and gaps
  were all zero. The API and database containers were healthy; capture, backup, healthwatch and
  restore-verification timers were active, and the most recent backup and healthwatch results were
  successful. Five older pre-candidate rows remain `RUNNING` alongside candidate run
  `9ef5ca09-4025-429e-a7c0-383a8a2644fd`; they are records left by the superseded stop contract, not
  concurrent containers.
- The read-only checkpoint at `2026-07-15T02:03:21Z` found all-seven readiness with global and
  checkpoint positions both `1078020`, raw count `1049423` and canonical count `1078167`. The
  current ingestion run and containers had remained up for about 23 hours; adapter generation was
  still one with seven subscriptions, zero reconnects, zero dropped records and zero provider
  operations. The latest 100,000 raw callbacks spanned 2.650 hours, the prior hour contained 41,276
  callbacks, the PostgreSQL volume had approximately 103 GB free, and the external host interface
  reported no RX/TX errors or drops. Backup and healthwatch results remained successful and the next
  scheduled backup was pending normally.
- The hour-24 read-only checkpoint at `2026-07-15T03:09:43Z` again found all-seven readiness,
  generation one, seven subscriptions and zero reconnects, drops or provider operations. The
  atomic readiness response had matching global/checkpoint positions of `1113552`; a subsequent
  system read observed the live projection at `1113555`. Raw messages were `1083421`, canonical
  events `1113551` and one-minute bars `30102`. The three containers had remained up for 24 hours,
  all capture/backup/healthwatch/restore timers were active with successful latest results, and the
  PostgreSQL volume had `103434051584` bytes free. The candidate gap count remained 21 with no gap
  detected after `2026-07-14T21:59:33Z`.
- That checkpoint also proved that the frozen digest's readiness response predates the later
  configuration-hash field and that the host's Compose version emits newline-delimited JSON for
  `ps --format json`. ADR 0023 fixes the undeployed closure helpers rather than the collector:
  Compose output is normalised, and after the reviewed five-row reconciliation the exact frozen
  digest may bind readiness only when exactly one ingestion row remains running in total and it has
  configuration hash `227ff98752a8f54b5813f0aecaa307bd777cb5a388b0ce15ecd3e5cf5f24873b`.
  The exact application and PostgreSQL images, descriptor, source, adapter and all-seven readiness
  gates remain mandatory; later images must expose endpoint configuration identity.
- The detailed gap review found 21 unrepaired candidate records between `2026-07-14T20:38:23Z`
  and `21:59:33Z`: eight FX and thirteen index gaps. Full raw callbacks resumed on the same
  generation without reconnect; the longest underlying callback pause was about 14 minutes, while
  the three index subscriptions shared a roughly 6.4-minute pause. AUD/USD and USD/JPY emitted
  provider `DLG_FLAG=CALL` around their longest pauses. Bounded ingest logs recorded stale-channel
  warnings but no disconnect, reconnect, unsubscription, drop or terminal failure. These are
  preliminary continuity observations, not a completed classification. ADR 0021 and the v2 review
  contract require per-gap retained evidence and full-window log/monitoring review before an
  operator may use `EXPECTED_MARKET_INACTIVITY`; ambiguity remains `UNEXPLAINED` and cannot pass.
- After the frozen window ends, query the IG demo historical API for every candidate gap's exact
  instrument and UTC interval through the reviewed planned-history path, using an isolated writable
  database rather than the collector. Retain the requested range, listing version, returned
  one-minute bars and quota evidence, then compare them with the raw/canonical live record. Historical
  bars during a live silence are evidence that IG later published prices for the interval and warrant
  deeper stream-path investigation; no returned bars support, but do not prove, upstream inactivity.
  The historical service is a distinct provider path, so either result is corroborating evidence
  only: it neither repairs a live gap nor replaces the continuity, log and monitoring review required
  by ADR 0021.
- A read-only retention audit at `2026-07-15T02:44:04Z` found Docker's `local` logging driver with
  effective `max-file=5` and `max-size=10m` for all three collector containers. Each still had one
  retained log file: ingestion was 19,337 bytes, API 325,519 bytes and PostgreSQL 126,679 bytes.
  Their retained histories began at candidate startup; the ingestion log's last entry was earlier
  because that process emits bounded lifecycle/warning events rather than a heartbeat. The system
  journal occupied 8 MiB and retained the candidate boot. These observed volumes were far below
  rotation capacity, so no logging change was made during the frozen window.

The queued comparison is implemented locally as `qtrad qualification gap-history` under ADR 0022.
After the automatic snapshot, it requires a verified post-evidence collector snapshot imported into
an isolated `qtrad_research_*` database, one reviewed exact-range IG demo plan covering every gap,
completed BID/ASK/MID coverage and a hash-verified version-two research export. Its self-hashed output
records per-gap returned historical points and completeness only; it cannot classify or repair a gap.
The companion `qualification gap-plan` command removes manual range/instrument transcription while
retaining operator allowance entry, plan review and explicit hash confirmation. It fails unless the
configured database is the verified post-evidence `qtrad_research_*` import at the current migration
head.

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

The locally prepared `ops/capture/qualification-log-evidence.sh` verifies and binds the exact
automatic snapshot, derives its candidate-to-snapshot interval, and writes one root-only,
non-overwriting bundle of filtered container identities, bounded timestamped container logs and the
three relevant systemd journals. Its self-hashed manifest records every retained file hash and the
effective image, restart and log-rotation identity. It must run immediately after the automatic
snapshot and before later lifecycle work. The bundle supports, but cannot perform, the explicit
full-window operator review and must never be committed to Git.
The companion `ops/capture/qualification-log-verify.sh` independently rejects a mismatched automatic
snapshot, changed manifest or file, unsafe mode/owner, symlink, unexpected file, identity mismatch or
out-of-window source bound. A successful integrity check still says nothing about semantic log
coverage or gap cause; those remain operator judgements under ADR 0021.

The locally prepared `runs reconcile-plan`/`runs reconcile` path closes the known pre-candidate run
record issue without deleting evidence or guessing a clean stop. Its first step is read-only and
writes a self-hashed plan for the complete `RUNNING` ingestion set strictly before
`2026-07-14T03:05:33.653928Z`. The operator must confirm that exact hash and five-run set. Execution
also requires the same immutable one-shot image, then locks and rechecks the complete set in one
transaction, marks only those rows `FAILED`, and
records the cutoff as an asserted interruption upper bound. It remains undeployed and must not run
until the candidate window has ended; the current candidate run, raw data and canonical events are
outside its mutation boundary.

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
