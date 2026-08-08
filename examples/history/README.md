# Nightly history

Rendered from each estate's `nightly.jsonl` and `fragility.jsonl`, plus the shared
`attempts.jsonl`, by `ops/render-history.py`. Those files are the record; this is a
view of them. Regenerate with `make examples`.

Every read and score below was written by a run that actually happened — the nightly
appends only after a read succeeds, so a failed night contributes no numbers rather
than asserted ones. It does contribute a row to the attempts table: a success-only
history cannot tell a failed night from a night nobody ran, and the difference
matters, because a failure means the newest verified numbers are older than the
newest attempt. A changed fingerprint means the estate's structure changed; an
unchanged one across nights is the evidence that scoring is deterministic.

## commerce

### Estate reads

| read at (UTC) | fingerprint | assets | datasets | edges | column edges | unowned | commit |
|---|---|---|---|---|---|---|---|
| 2026-08-05T09:30:04+00:00 | 1ab1aaacad9403ce | 91 | 66 | 125 | 251 | 9 | — |
| 2026-08-05T10:20:09+00:00 | 1ab1aaacad9403ce | 91 | 66 | 125 | 251 | 9 | — |
| 2026-08-06T03:27:37+00:00 | 2b0ff33cd937f51f | 91 | 66 | 125 | 322 | 9 | 421ce50 |
| 2026-08-07T03:28:17+00:00 | 2b0ff33cd937f51f | 91 | 66 | 125 | 322 | 9 | 92a1cb5 |

### Fragility scoring

| scored at (UTC) | fingerprint | assets scored | mean score | most fragile |
|---|---|---|---|---|
| 2026-08-05T12:41:55+00:00 | 2b0ff33cd937f51f | 66 | 22.693 | raw_pg.fx_rates (77.942) |
| 2026-08-06T03:27:37+00:00 | 2b0ff33cd937f51f | 66 | 17.997 | raw_pg.fx_rates (61.517) |
| 2026-08-07T03:28:17+00:00 | 2b0ff33cd937f51f | 66 | 17.997 | raw_pg.fx_rates (61.517) |

## operations

### Estate reads

| read at (UTC) | fingerprint | assets | datasets | edges | column edges | unowned | commit |
|---|---|---|---|---|---|---|---|
| 2026-08-08T14:34:52+00:00 | 464884b669b60aef | 37 | 25 | 52 | 82 | 0 | b257006 |

### Fragility scoring

| scored at (UTC) | fingerprint | assets scored | mean score | most fragile |
|---|---|---|---|---|
| 2026-08-08T14:34:52+00:00 | 464884b669b60aef | 25 | 24.536 | ops_erp.shipments (43.789) |

## Every attempt, including failures

| attempted (UTC) | status | stage reached | commit | detail |
|---|---|---|---|---|
| 2026-08-06T03:17:01+00:00 | succeeded | complete | 421ce50 | read, scored, wrote back, and appended history (reconstructed from `/var/log/twin-nightly.log`) |
| 2026-08-07T03:17:01+00:00 | succeeded | complete | 92a1cb5 | read, scored, wrote back, and appended history (reconstructed from `/var/log/twin-nightly.log`) |
| 2026-08-08T03:17:01+00:00 | failed | test suite | 5ef4c81 | 2 failed: TestFxRates rates_are_positive and rates_move_as_a_walk; gen_fx_rates yielded str rates under commit b80ad24, reverted in c540f8d (reconstructed from `/var/log/twin-nightly.log`) |
| 2026-08-08T14:31:52+00:00 | succeeded | complete | b257006 | read, scored, wrote back, and appended history |
