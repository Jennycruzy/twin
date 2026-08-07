# Nightly history

Rendered from `nightly.jsonl` and `fragility.jsonl` in this directory by
`ops/render-history.py`. Those files are the record; this is a view of them.
Regenerate with `make examples`.

Each line was written by a run that actually happened — the nightly appends only
after a read succeeds, so a failed night leaves no row rather than an asserted one.
A changed fingerprint means the estate's structure changed; an unchanged one across
nights is the evidence that scoring is deterministic.

## Estate reads

| read at (UTC) | fingerprint | assets | datasets | edges | column edges | unowned | commit |
|---|---|---|---|---|---|---|---|
| 2026-08-05T09:30:04+00:00 | 1ab1aaacad9403ce | 91 | 66 | 125 | 251 | 9 | — |
| 2026-08-05T10:20:09+00:00 | 1ab1aaacad9403ce | 91 | 66 | 125 | 251 | 9 | — |
| 2026-08-06T03:27:37+00:00 | 2b0ff33cd937f51f | 91 | 66 | 125 | 322 | 9 | 421ce50 |
| 2026-08-07T03:28:17+00:00 | 2b0ff33cd937f51f | 91 | 66 | 125 | 322 | 9 | 92a1cb5 |

## Fragility scoring

| scored at (UTC) | fingerprint | assets scored | mean score | most fragile |
|---|---|---|---|---|
| 2026-08-05T12:41:55+00:00 | 2b0ff33cd937f51f | 66 | 22.693 | raw_pg.fx_rates (77.942) |
| 2026-08-06T03:27:37+00:00 | 2b0ff33cd937f51f | 66 | 17.997 | raw_pg.fx_rates (61.517) |
| 2026-08-07T03:28:17+00:00 | 2b0ff33cd937f51f | 66 | 17.997 | raw_pg.fx_rates (61.517) |
