# Estate fingerprint

The records below are copied from the append-only history artifacts.

## Latest estate read

Source artifact: `examples/history/commerce/nightly.jsonl`.

```json
{
  "assets": 91,
  "column_edges": 322,
  "commit": "4812475",
  "datasets": 66,
  "dirty": true,
  "edges": 125,
  "fingerprint": "2b0ff33cd937f51f",
  "pipeline_status": "succeeded",
  "read_at": "2026-08-12T03:30:37+00:00",
  "source": "http://datahub-gms:8080",
  "target": "commerce",
  "tests_passed": 187,
  "unowned_datasets": 9,
  "verification_artifact": "examples/verification/nightly-commerce-2026-08-12T03-17-01.txt",
  "verification_precision": 0.69,
  "verification_recall": 1.0
}
```

## Latest fragility score

Source artifact: `examples/history/commerce/fragility.jsonl`.

```json
{
  "assets_scored": 66,
  "commit": "4812475",
  "cost_model_config": "7c81f8489e55b5f7",
  "dirty": true,
  "fingerprint": "2b0ff33cd937f51f",
  "mean_score": 17.997,
  "scored_at": "2026-08-12T03:30:37+00:00",
  "scoring_config": "90bdafd3f34d1cc9",
  "target": "commerce",
  "top": [
    {
      "blast_radius_cost": 9450.0,
      "key": "raw_pg.fx_rates",
      "score": 61.517
    },
    {
      "blast_radius_cost": 8850.0,
      "key": "staging.stg_fx_rates",
      "score": 61.314
    },
    {
      "blast_radius_cost": 7650.0,
      "key": "intermediate.int_orders_enriched",
      "score": 43.325
    },
    {
      "blast_radius_cost": 9450.0,
      "key": "staging.stg_orders",
      "score": 37.939
    },
    {
      "blast_radius_cost": 10050.0,
      "key": "raw_pg.orders",
      "score": 37.775
    }
  ]
}
```

