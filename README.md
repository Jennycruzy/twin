# Twin

Twin is a resilience engine for data platforms. It reads the catalog an agent would use,
models the consequences of losing an asset, executes selected failures against a disposable
warehouse copy, compares the result with what actually happened, and publishes the evidence
back into the catalog.

The important property is not the score. It is the closed loop:

```text
DataHub context
      │
      ▼
immutable estate graph ──▶ deterministic propagation ──▶ fragility ranking
      │                                      │                    │
      │                                      ▼                    ▼
      └──────────────────────────── real shadow execution ──▶ catalog properties
                                                               │
                                                               ▼
                                                    repair proposals and next experiment
```

Twin does not claim that a catalog is correct because it contains a claim. It tests whether
the claim survives contact with a warehouse, names its misses, and makes uncertainty visible.

Using DataHub this hard surfaced five interface gaps, all filed upstream with reproduction
steps: two new issues ([mcp-server-datahub#197](https://github.com/acryldata/mcp-server-datahub/issues/197),
[datahub#18974](https://github.com/datahub-project/datahub/issues/18974)) and evidence on three
existing ones. They are written up in [docs/UPSTREAM.md](docs/UPSTREAM.md), and every one of
them came out of a run that failed rather than a reading of the documentation.

## The result

Twin has been run against two independently designed estates using the same graph model,
propagation engine, scorer, verifier, and write-back code.

| Estate | Shape | Live result |
|---|---|---|
| Commerce | 66 datasets, 125 table edges, 322 column edges | `raw_pg.fx_rates` ranks first at 61.517 |
| Operations | 25 datasets, 52 table edges, 82 column edges | `ops_erp.shipments` ranks first at 43.789 |

The operations estate is logistics rather than a renamed commerce graph: ERP and telemetry
sources, facility and carrier models, an independent workload, different ownership, and a
separate DataHub platform-instance namespace.

The verifier has run raw-source, column, type, staleness, and relation-loss scenarios for real.
The source-column adversarial case is intentionally imperfect: it measured 0.69 precision and
1.00 recall, with five named false alarms. Thin catalog context creates over-prediction; Twin
reports that instead of tuning it away.

The current repository also contains:

- 185 passing tests;
- deterministic experiment selection that lowers verification novelty after a real run;
- context confidence written as three auditable DataHub properties, including which assets
  have been broken for real and which are still ranked on inference alone;
- final MCP read-back of Twin properties on 66 commerce and 25 operations assets;
- a reviewable repair proposal for a missing source-column contract;
- a repository gate used by local pushes and GitHub Actions.

## Run it

[Latest verified run → reports/LATEST.md](reports/LATEST.md)

Requirements: Docker with Compose v2 and roughly 6 GB of free memory. No cloud account or
paid service is required.

```bash
make up
make estate
make verify-estate
make read
make score
make run SCENARIO=scenarios/merchant_id_nulled_at_source.yml
make writeback
make prove-writeback
```

The second estate uses the same commands through a declared target:

```bash
make estate TARGET=operations
make verify-estate TARGET=operations
make read TARGET=operations
make score TARGET=operations
make scenarios TARGET=operations
```

The graph is cached under `.twin/` by content fingerprint. A repeated read of an unchanged
estate produces the same graph and the same scores. The cache is an optimization, not a source
of truth: `make read` refreshes it from DataHub over MCP.

## What the engine measures

The graph folds physical and dbt siblings into logical assets, preserves table and column
lineage, and carries ownership, service metadata, usage, replication, fallbacks, and consumer
types.

For each dataset, the scorer performs a deterministic knockout simulation and reports five
separate components:

| Component | Question |
|---|---|
| Blast | What datasets and consumers disappear? |
| Exposure | How much measured query traffic depends on them? |
| Recovery | Is there a replica or declared fallback? |
| Concentration | How many people can repair the affected radius? |
| Blindness | How long can the damage remain unseen? |

Scores are shares within one estate. They are not presented as universal risk probabilities
and should not be compared numerically across unrelated platforms.

The context-confidence model is separate from fragility. It measures whether an automated
action has enough supporting context: lineage, schema, operational metadata, ownership, usage,
and real verification evidence. Missing information lowers confidence; it never becomes a
silent positive.

## Real verification

Scenario files declare a fault and nothing about the expected result. Twin then:

1. predicts the affected assets from the graph;
2. creates a uniquely named disposable shadow schema;
3. applies the fault only inside that schema;
4. rebuilds downstream dbt models and re-runs real consumer queries;
5. compares row counts and order-independent content checksums with production;
6. reports hits, false alarms, misses, and ungraded consumers separately.

Degraded output matters as much as failed builds. A model with the right row count and wrong
values is still broken, so verification compares content rather than trusting exit codes or
row counts alone.

Run the deterministic campaign with:

```bash
make campaign TARGET=operations
make campaign TARGET=operations CAMPAIGN_EXECUTE=1
```

It ranks candidate experiments by measured impact, context gap, and verification novelty. A
real run appends evidence to the target cache; the next plan discounts the experiment just
executed and selects the next useful check.

That evidence is also what the published `verification` component reports, so an asset Twin
has actually broken is distinguishable in the catalog from one it has only reasoned about.
Executing `fx_rate_column_drop` on commerce demoted it from first to third and promoted
`owner_departure` to first; `staging.stg_fx_rates` now reads `verification=1.00` while
`raw_pg.fx_rates`, ranked first for fragility, still reads `0.00` and is the next selection.

## Catalog write-back

Twin writes seventeen structured properties through the DataHub SDK and reads them back over MCP:
fragility, resilience, rank, blast radius, illustrative blast-radius cost, bus factor,
single-point-of-failure status, timestamp, provenance, five score components, and context
confidence/state/evidence. The cost estimate is always labelled with the assumptions in
`config/cost_model.yaml`.

```bash
make writeback TARGET=operations
make prove-writeback TARGET=operations
make unwrite TARGET=operations
```

The `twin_` namespace makes cleanup exact. `unwrite` clears values and resolves Twin incidents;
it deliberately leaves inert property definitions because the DataHub index retains deleted
structured-property mappings and rejects a later definition with the same name.

## Repair proposals

When the graph shows table lineage but lacks field lineage for a high-value source column, Twin
can produce a PR-ready proposal without mutating the warehouse or opening a remote pull request:

```bash
make repair TARGET=commerce
```

The command writes a Markdown review and a standard `.patch` under `examples/repair-prs/`. The
proposal contains the measured gap, affected consumers, graph fingerprint, context confidence,
an exact source-contract diff, and acceptance checks. A maintainer applies it, re-ingests the
metadata, reruns the scenario, and keeps it only if the new evidence explains the change.

This boundary is intentional. A generated patch is reviewable; an agent claiming to have
repaired a production catalog without a reviewable diff would not be.

## Quality gate

```bash
make gate
```

The gate checks both target configurations, every scenario, configured modules and source
redirects, generated-artifact hygiene, graph serialization, scoring determinism, and the full
test suite. It runs on every push and pull request through `.github/workflows/tests.yml`, and
the local pre-push hook invokes the same command inside the tools image.

The nightly job that actually runs on the VPS is [`ops/nightly-read.sh`](ops/nightly-read.sh),
installed in host cron because the stack is already up on that box. It verifies the live estate,
reads it over MCP, scores it, writes current properties, runs a real verification, records the
test and precision/recall results, and appends measured history. GitHub Actions is disabled on
this account; `.github/workflows/twin-nightly.yml` remains the reproducible CI-shaped variant
that builds the stack from nothing, but is not the source of the host's nightly evidence.

A failed run produces no history line and no score — a run that did not finish has nothing to
contribute. It does append to `examples/history/attempts.jsonl`, and commits and pushes that
record before exiting. This is the difference between a trail that is silent about its gaps
and one that names them: a missing date alone cannot distinguish a night nobody ran from a
night that ran and failed, and `reports/LATEST.md` states any failure newer than the run it is
showing. The nightly of 2026-08-08 failed at the test suite and is recorded there.

The same proof can run without GitHub Actions or GitHub Actions billing:

```bash
make nightly
git add reports/ examples/history/ examples/verification/
git commit -m "nightly: verified Twin run $(date -u +%Y-%m-%d)"
git push origin main
```

`make nightly` leaves the generated evidence uncommitted by default so a Mac launchd job or
cron entry can review it before publishing. Direct `ops/nightly-read.sh` invocation keeps the
VPS mode, which commits and pushes automatically after a successful run.

## Safety boundary

Verification is destructive by design, but its authority is narrow by construction:

- PostgreSQL production objects belong to `twin`; the shadow connection uses `twin_shadow`,
  which owns none of them.
- Every disposable schema starts with `twin_shadow_` and is checked at the SQL boundary.
- Read-routed statements execute inside `BEGIN READ ONLY`, including nested writes and
  `EXPLAIN ANALYZE`.
- A teardown runs in a `finally` block, including failed builds and interrupted observations.
- `make dry-run` prints statements without executing them.

The full account is in [docs/SAFETY.md](docs/SAFETY.md).

## Repository map

```text
targets/              estate-specific runtime contracts
twin/read/            DataHub MCP client, graph materialization, cache
twin/simulate/        deterministic fault propagation
twin/score/           knockout sweep and fragility model
twin/verify/          shadow warehouse execution and observation
twin/context.py       context confidence and experiment selection
twin/repair/          evidence-backed catalog repair proposals
twin/gate/            repository quality gate
twin/write/           DataHub properties, incidents, and cleanup
estate/               commerce warehouse, dbt project, and workload
operations_estate/    independent logistics warehouse and workload
scenarios/            commerce fault declarations
operations_scenarios/ logistics fault declarations
examples/             outputs captured from real runs
reports/              generated latest-run evidence for readers who will not run Docker
docs/                 safety, scoring, and upstream findings
```

## Known limits

- The estates are deterministic and synthetic. Their data is not a company's production data.
- Refresh cadence, SLA, ownership, replication, and fallback metadata are declared in the
  estate projects rather than measured from an orchestrator.
- PostgreSQL is the only warehouse execution backend implemented.
- DataHub's MCP server does not currently expose usage statistics, source-column landing fields,
  or a write tool. Twin documents each SDK exception and keeps the MCP read-back proof.
- When column lineage is absent, propagation conservatively falls back to table grain. This
  catches the observed failure at the cost of false alarms, which the scorecard names.
- Verification grades which assets broke, not the passage of simulated wall-clock time.

## Upstream findings

The five interface findings and their reproduction details are recorded in
[docs/UPSTREAM.md](docs/UPSTREAM.md). They were discovered while using DataHub for real work,
not inferred from documentation.

## License

Apache 2.0. See [LICENSE](LICENSE).
