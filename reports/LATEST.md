# Twin — latest verified run

This page is generated from captured outputs and append-only history; it is not a second computation of the score.

Each estate is reported separately. Fragility scores are positions within an estate,
so two estates' numbers are not comparable and are never combined here.

## commerce

Run artifacts: [`reports/nightly/2026-08-07/commerce/`](nightly/2026-08-07/commerce/)

**1 later commerce run(s) did not complete.** The numbers below are therefore older than the most recent attempt; see [runs that did not complete](#runs-that-did-not-complete).

| Evidence | Generated artifact |
|---|---|
| Scorecard | [scorecard](nightly/2026-08-07/commerce/scorecard.md) |
| Verification result | [verification](nightly/2026-08-07/commerce/verification.md) |
| MCP readback | [MCP readback](nightly/2026-08-07/commerce/mcp-readback.md) |
| Estate fingerprint | [fingerprint](nightly/2026-08-07/commerce/estate-fingerprint.md) |

History source: `examples/history/commerce/nightly.jsonl` and `examples/history/commerce/fragility.jsonl`.

## operations

Run artifacts: [`reports/nightly/2026-08-08/operations/`](nightly/2026-08-08/operations/)

Pipeline status: `succeeded`
Tests passed: `177`
Verification precision: `1.0`
Verification recall: `1.0`

| Evidence | Generated artifact |
|---|---|
| Scorecard | [scorecard](nightly/2026-08-08/operations/scorecard.md) |
| Verification result | [verification](nightly/2026-08-08/operations/verification.md) |
| MCP readback | [MCP readback](nightly/2026-08-08/operations/mcp-readback.md) |
| Estate fingerprint | [fingerprint](nightly/2026-08-08/operations/estate-fingerprint.md) |

History source: `examples/history/operations/nightly.jsonl` and `examples/history/operations/fragility.jsonl`.

## Runs that did not complete

These runs were attempted and failed. They appended no estate read and no score, so nothing above reflects them — they are listed because a success-only history cannot distinguish a failed night from a night nobody ran.

Source artifact: `examples/history/attempts.jsonl`.

| attempted (UTC) | estate | stage reached | commit | detail |
|---|---|---|---|---|
| 2026-08-08T03:17:01+00:00 | commerce | test suite | `5ef4c81` | 2 failed: TestFxRates rates_are_positive and rates_move_as_a_walk; gen_fx_rates yielded str rates under commit b80ad24, reverted in c540f8d — reconstructed from `/var/log/twin-nightly.log` |
