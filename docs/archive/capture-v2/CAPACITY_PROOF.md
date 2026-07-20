# Capture-v2 local capacity proof

This evidence supports the callback-to-PostgreSQL part of the M1 expansion decision. It does not
replace the post-deployment requirement for one real Lightstreamer connection and per-channel live
delivery evidence.

On 2026-07-20 the current application ran the checked-in disposable-database experiment with 40
synthetic instruments—twice the target catalogue size—at 200 callbacks per second for 60 seconds.
It exercised the production IG callback normalisation, bounded queue, asynchronous consumer, raw and
canonical persistence, bar advancement and a complete-state subscription renewal at 30 seconds.

```bash
bash ops/dev/stream-load-experiment.sh tmp/capture-v2-load-proof-current.json \
  --duration-seconds 60 \
  --callbacks-per-second 200 \
  --instruments 40 \
  --queue-capacity 10000 \
  --persistence-delay-ms 1 \
  --renewal-at-seconds 30 \
  --maximum-persistence-lag-seconds 30 \
  --drain-timeout-seconds 180
```

Result: `PASS`; evidence SHA-256
`c089a79523fb665122e2c925a68c1eb3023706a4cc5a9bd6ca64db66103b16b1`.

- 12,000 of 12,000 callbacks were normalised and persisted.
- Internal drops, Lightstreamer-reported loss, quarantines and normalisation errors were zero.
- Queue high-water was 51 of 10,000 and final depth was zero.
- Persistence lag was 3.9 ms median, 5.3 ms p95, 117.8 ms p99 and 257.6 ms maximum.
- Effective persistence throughput was 196.7 callbacks per second.
- All 40 complete-state renewals were observed.
- The consumer exited cleanly and the guarded disposable database was dropped.

The running collector observation separately showed one connected stream generation, seven of seven
subscriptions and recent quote channels, queue high-water 15, caught-up projection and zero q-trad or
SDK loss. Together these results give reasonable local headroom for the accepted subset up to 20;
they do not authorise deployment or prove that every candidate subscription is provider-eligible.
