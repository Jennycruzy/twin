## Twin PR risk gate

**FAIL** — changed assets were re-scored against cached graph `2b0ff33cd937f51f`.

- Manifest: `examples/pr-manifests/risky-fx-rates.yml`
- Target: `commerce`
- Threshold: `50` (high fragility is score ≥ threshold)
- Provenance: commit `b80ad24`
- Run provenance: generated locally by `make pr-gate`

| Changed asset | Rank | Fragility | Blast radius | Cost | Gate |
|---|---:|---:|---:|---:|---|
| `raw_pg.fx_rates` | 1 | 61.517 | 36 | $9,450.00 | FAIL |

Cost is illustrative, under the assumptions in config/cost_model.yaml: 4 engineer-hours per broken model at $150.00/hour and 2 consumer-hours per affected dashboard at $75.00/hour.

This gate reads the manifest and the repository's cached graph; it does not claim a CI run or a live catalog read.
