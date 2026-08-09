# Estate fingerprint

The records below are copied from the append-only history artifacts.

## Latest estate read

Source artifact: `examples/history/operations/nightly.jsonl`.

```json
{
  "assets": 37,
  "column_edges": 82,
  "commit": "30cdcb6",
  "datasets": 25,
  "dirty": true,
  "edges": 52,
  "fingerprint": "464884b669b60aef",
  "pipeline_status": "succeeded",
  "read_at": "2026-08-09T17:03:25+00:00",
  "source": "http://datahub-gms:8080",
  "target": "operations",
  "tests_passed": 181,
  "unowned_datasets": 0,
  "verification_artifact": "examples/verification/nightly-operations-2026-08-09T16-59-41.txt",
  "verification_precision": 1.0,
  "verification_recall": 1.0
}
```

## Latest fragility score

Source artifact: `examples/history/operations/fragility.jsonl`.

```json
{
  "assets_scored": 25,
  "commit": "30cdcb6",
  "cost_model_config": "7c81f8489e55b5f7",
  "dirty": true,
  "fingerprint": "464884b669b60aef",
  "mean_score": 24.536,
  "scored_at": "2026-08-09T17:03:25+00:00",
  "scoring_config": "90bdafd3f34d1cc9",
  "target": "operations",
  "top": [
    {
      "blast_radius_cost": 6900.0,
      "key": "ops_erp.shipments",
      "score": 43.789
    },
    {
      "blast_radius_cost": 6300.0,
      "key": "ops_staging.stg_shipments",
      "score": 43.173
    },
    {
      "blast_radius_cost": 1950.0,
      "key": "ops_erp.inventory_snapshots",
      "score": 42.819
    },
    {
      "blast_radius_cost": 1350.0,
      "key": "ops_core.int_facility_conditions",
      "score": 42.352
    },
    {
      "blast_radius_cost": 1350.0,
      "key": "ops_staging.stg_inventory_snapshots",
      "score": 42.352
    }
  ]
}
```

