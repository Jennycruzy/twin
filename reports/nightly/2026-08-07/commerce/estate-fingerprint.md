# Estate fingerprint

The records below are copied from the append-only history artifacts.

## Latest estate read

Source artifact: `examples/history/commerce/nightly.jsonl`.

```json
{
  "assets": 91,
  "column_edges": 322,
  "commit": "92a1cb5",
  "datasets": 66,
  "dirty": false,
  "edges": 125,
  "fingerprint": "2b0ff33cd937f51f",
  "read_at": "2026-08-07T03:28:17+00:00",
  "source": "http://datahub-gms:8080",
  "unowned_datasets": 9
}
```

## Latest fragility score

Source artifact: `examples/history/commerce/fragility.jsonl`.

```json
{
  "assets_scored": 66,
  "commit": "92a1cb5",
  "dirty": true,
  "fingerprint": "2b0ff33cd937f51f",
  "mean_score": 17.997,
  "scored_at": "2026-08-07T03:28:17+00:00",
  "scoring_config": "a65901c08535194f",
  "top": [
    {
      "key": "raw_pg.fx_rates",
      "score": 61.517
    },
    {
      "key": "staging.stg_fx_rates",
      "score": 61.314
    },
    {
      "key": "intermediate.int_orders_enriched",
      "score": 43.325
    },
    {
      "key": "staging.stg_orders",
      "score": 37.939
    },
    {
      "key": "raw_pg.orders",
      "score": 37.775
    }
  ]
}
```

