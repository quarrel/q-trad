# `capture-v1` OCI qualification

**Status:** IN PROGRESS  
**Candidate window:** 2026-07-14T03:05:33Z to no earlier than 2026-07-17T03:05:33Z  
**Universe:** `capture-v1`, seven IG demo instruments  
**Application image:** `sha256:3ca07eaee8cf1500546c1779bb0732d9260b085e8a179e3514a507da4ee77d80`  
**Deployment descriptor:** `89c7553160705ca0fd859fbb0477163efc0e279d`
**Deployment descriptor SHA-256:** `a95e53c3f7bec61ebc11126484ad61ad71828f542727c72a3d9654d88541c57d`
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
- The first scheduled backup after hour 24 ran from `2026-07-15T03:30:07Z` to
  `2026-07-15T03:30:40Z` and completed successfully. Independent Object Storage listing confirmed
  the daily dump (`195589108` bytes), checksum (`102` bytes) and manifest (`574` bytes) at their
  expected object names. During the run, readiness remained seven of seven with matching global
  and checkpoint positions at `1123450`.
- The host's apparent 35.7 GB transmit total after reboot was not a backup-volume anomaly. The
  PostgreSQL container reported 34.6 GB of block writes, and `/dev/sdb` is attached over iSCSI at
  `169.254.2.2:3260` through the same primary VNIC. A read-only 15-second sample observed `4789149`
  VNIC transmit bytes alongside `4669440` block-volume write bytes. Treat this as storage I/O and
  PostgreSQL write-amplification evidence, not as public-internet egress; the actual daily backup
  upload was approximately 196 MB.
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
- The post-window reconciliation plan
  `06a78e2f4bf407ea9e6be6de5d67a169aca1e2c5209fb39da7b072656a3882e3`
  atomically marked the five known pre-candidate non-terminal rows `FAILED` at
  `2026-07-17T04:36:27Z`. Read-back found exactly one current ingestion run and confirmed that raw
  capture and canonical events were outside the mutation.
- The first immutable automatic snapshot was written at `2026-07-17T04:37:33Z` with evidence SHA-256
  `47641c55cc84e9373b4d66aa9572e86379d4a35cbbfbd1ffff9d5f34efbae20d`.
  It correctly failed `adapter_ok` for 22,029 dropped records. It also exposed two closure-tool
  bookkeeping defects: the documented descriptor hash referred to the later source checkout rather
  than descriptor commit `89c7553`, and the mount check did not admit XFS beneath systemd automount.
  The active descriptor independently hashes to `a95e53c3f7bec61ebc11126484ad61ad71828f542727c72a3d9654d88541c57d`,
  exactly matching that frozen commit; `findmnt` records the read/write `/dev/sdb` XFS mount beneath
  the same-target `autofs` entry. The original snapshot remains immutable.
- Before any restart or deployment, the snapshot's bounded log bundle was written and independently
  verified with manifest SHA-256
  `093f5267d9ae8a6acd838180d84a60fcb0c2a995d3f8436fa30c991bd3047b43`.
- A numbered retry using the corrected descriptor and mount assertions was written at
  `2026-07-17T04:43:57Z`. Its evidence SHA-256 is
  `62a9227792a6e2c396f394a4c6346da0dde42af3aeef9312443bb7f0b060f0dc`; `adapter_ok` is its only
  failed automatic check, and its 70-gap log bundle independently verifies at manifest SHA-256
  `0b1e8eb5593903bf6d02974334b71942bd525f9c91bf35ed664bfc641c7e8e2a`.
- A post-retry collector backup completed at `2026-07-17T04:46:06Z`. Its archive set was downloaded
  without transferring OCI credentials, checksum-verified and imported into the isolated local
  `qtrad_research_capture_20260717` database. Import evidence
  `0e72c538e1d04726a8ba3dd8d8a0a7579680a67b324a028ff6c2eb40724da5dd` binds the
  `2026-07-17T04:45:55Z` snapshot, migration `0003`, 3,047,086 raw messages and 3,478,536 canonical
  events. The first import attempt exposed that psql does not expand variables in `--command`; the
  database-existence guard now supplies its parameterised SQL on stdin and the real import passes.
- Formal finalisation exposed one further closure defect: the finaliser rejected any automatic-gate
  failure before it could write a `FAIL` decision. It now requires the recorded aggregate to equal the
  individual checks, carries the actual aggregate into final evidence and permits `PASS` only when
  both automatic checks and operator reviews pass. A failed automatic result is therefore preserved
  as a hash-bound `FAIL`, never accepted or discarded.
- The original rectangular historical plan was rejected before provider I/O: its 20,741 requested
  minute-points exceed IG's documented 10,000-point weekly allowance. The minute-aligned union of the
  same evidence is 56 instrument/range spans and only 267 points. Historical corroboration therefore
  requires a sparse hash-bound plan set; inflating quota evidence or querying the wasteful rectangle
  is prohibited.
- The candidate is formally closed as `FAIL`. Final evidence SHA-256
  `d7bcd88e3179aca9eda89673f14383d6525bcd92e602462c6c56815892fb5c3f` binds automatic evidence
  `62a9227792a6e2c396f394a4c6346da0dde42af3aeef9312443bb7f0b060f0dc`, operator review
  `35007f18acab7d8a5d9ddbb73b660cbd713354fb23d7a04bb384d73b061964af`, the 22,029 dropped records
  and all 70 `UNEXPLAINED` gaps. The result does not admit `capture-v2`.
- After formal closure and the post-evidence backup, the corrected seven-instrument application was
  deployed at `2026-07-17T05:02Z`. Release descriptor `807a96734e4aa1181acc24501f8031e0455b3bf3`
  pins image digest `cb9d8efa9951daea91269e596c798c85fa262ab7100d93050025461eecb363ee`;
  the collector database advanced expand-only from `0003` to `0009`. New run
  `15ea66f4-a04a-4c99-b897-877bcb021877` reached seven fresh subscriptions, exact projection
  catch-up, zero reconnects/drops/provider operations and queue high-water 7/10,000. The failed run
  stopped cleanly and remains immutable.

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
