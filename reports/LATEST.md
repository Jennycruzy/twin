# Twin — latest verified run

This page is generated from captured outputs and append-only history; it is not a second computation of the score.

Run artifacts: [`reports/nightly/2026-08-07/`](nightly/2026-08-07/)

**1 later nightly run(s) did not complete.** The numbers below are therefore older than the most recent attempt; see [runs that did not complete](#runs-that-did-not-complete).

| Evidence | Generated artifact |
|---|---|
| Scorecard | [scorecard](nightly/2026-08-07/scorecard.md) |
| Verification result | [verification](nightly/2026-08-07/verification.md) |
| MCP readback | [MCP readback](nightly/2026-08-07/mcp-readback.md) |
| Estate fingerprint | [fingerprint](nightly/2026-08-07/estate-fingerprint.md) |

History source: `examples/history/nightly.jsonl` and `examples/history/fragility.jsonl`.

## Runs that did not complete

These runs were attempted and failed. They appended no estate read and no score, so nothing above reflects them — they are listed because a success-only history cannot distinguish a failed night from a night nobody ran.

Source artifact: `examples/history/attempts.jsonl`.

| attempted (UTC) | stage reached | commit | detail |
|---|---|---|---|
| 2026-08-08T03:17:01+00:00 | test suite | `5ef4c81` | 2 failed: TestFxRates rates_are_positive and rates_move_as_a_walk; gen_fx_rates yielded str rates under commit b80ad24, reverted in c540f8d — reconstructed from `/var/log/twin-nightly.log` |
