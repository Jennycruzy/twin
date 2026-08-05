# Twin

**Twin is chaos engineering for data platforms.** It reads DataHub's context graph,
simulates failures across it, executes those failures for real against a live warehouse to
verify its own predictions, and writes fragility scores back into DataHub so every other
agent inherits a dimension the catalog didn't have before.

---

## Build status

Twin is being built in milestones, and this README describes only what is actually in the
repository. Nothing below is aspirational.

| Milestone | Scope | State |
|---|---|---|
| **M1** | The demo estate — warehouse, dbt project, DataHub ingestion, verification gate | **Done** |
| **M2** | Stage 1 — read the estate through the DataHub MCP server | **Done** |
| M3 | Stage 4 — shadow execution and self-grading | Not started |
| M4 | Stage 2 — propagation engine and failure timelines | Not started |
| M5 | Stage 3 — fragility scoring and the knockout sweep | Not started |
| M6 | Stage 5 — write-back, incidents, repair PRs | Not started |

The pipeline is deliberately not being built in pipeline order. Stage 4 — executing a real
fault and grading the prediction against what actually broke — is the component the whole
project stands on, so it is built third rather than last. A simulator that proves itself
against a real dbt run is a different class of thing from one that does not, and the way
that goes wrong is discovering on the final night that shadow execution was never feasible.

## What exists today: the estate

Twin needs something real to reason about, so the first milestone builds a synthetic but
plausible e-commerce/fintech data platform — not a handful of toy tables, because a shallow
graph would make any engine sitting on top of it look trivial.

```
docker compose up -d      # or: make up
make estate               # seed, build, ingest        (~3 minutes)
make verify-estate        # prove it is real
```

`make verify-estate` queries DataHub over the wire and prints this. Every number is read at
the moment the check runs; nothing is cached and nothing is hardcoded.

```
  ESTATE VERIFICATION
  --------------------------------------------------------------------------
  CHECK                             OBSERVED                       EXPECTED
  --------------------------------------------------------------------------
  Datasets (postgres)               66                             = 66         ok
  Datasets (dbt siblings)           66                             = 66         ok
  Dashboards                        4                              = 4          ok
  Charts                            10                             = 10         ok
  ML features                       9                              = 9          ok
  Table lineage edges               70                             >= 65        ok
  Datasets with column lineage      66                             >= 35        ok
  Datasets with usage stats         13                             >= 10        ok
  Recorded query executions         3,458                          > 0          ok
  Distinct owners                   5                              >= 5         ok
  Top owner concentration           priya.raghavan@example.com 30% 25%-40%      ok
  Unowned datasets                  9                              >= 5         ok
  ML path featuretable->deployment  resolved                       resolved     ok
  --------------------------------------------------------------------------

  Ownership distribution
    priya.raghavan@example.com          20 ( 30%)  ############
    marcus.webb@example.com             12 ( 18%)  #######
    dana.oyelaran@example.com           10 ( 15%)  ######
    tomas.lindqvist@example.com          8 ( 12%)  #####
    amara.chen@example.com               7 ( 11%)  ####
    (no owner)                           9 ( 14%)  #####

  all 13 checks passed
```

### What that estate contains

- **66 datasets** across four layers: 27 raw landing tables, 19 staging models,
  9 intermediate models, 8 marts and 3 ML feature tables.
- **Two source systems with different freshness characteristics.** A transactional
  Postgres replica that lands one batch a night, and an event stream that lands
  continuously, delivers out of order, and leaves ~4% of sessions unterminated. Twin's
  propagation model is driven by refresh cadence, so this difference is load-bearing.
- **A real dbt project.** Not YAML describing models — 39 models that compile and run
  against Postgres. `dbt build` runs 114 nodes including 75 tests.
- **Column-level lineage**, emitted by dbt ingestion, on all 66 datasets. A dropped column
  should break only the assets that reference it; without column grain Twin would have to
  treat every schema change as a whole-table failure.
- **4 BI dashboards and 10 charts** registered as DataHub entities, downstream of the marts.
- **A real ML branch**: feature tables → `MLFeatureTable` → `MLModel` → `MLModelDeployment`,
  resolvable end to end. A failure that reaches this branch is a wrong decision on a live
  payment, not a stale report.
- **Deliberately uneven ownership.** One engineer owns 30% of the estate; nine assets have
  no owner at all.
- **Real usage statistics** — see below, because this is the part most easily faked.

### The usage numbers are real, in a specific and limited sense

Twin weights fragility by how heavily an asset is actually used. The easy way to produce
that data is to write plausible query counts into DataHub, where they would be
indistinguishable from real ones and every score derived from them would rest on a number
nobody measured.

Instead, `estate/ingest/queries/workload.yml` declares a consumer workload, and
`make estate` **actually executes it** — 3,458 real queries against the warehouse, as the
read-only `twin_reader` role. The usage statistics published to DataHub are counts of
executions that really happened. Failed queries are not counted.

To be precise about what is and is not being claimed: the workload is synthetic. It stands
in for BI tools and analysts this demo estate does not have. What it buys is a realistic
*skew* — the finance dashboard is refreshed constantly, a support query runs twice a week —
which is what makes usage-weighted scoring produce a meaningful ranking instead of a flat
one. Those same queries are reused by Stage 4, which has to run the real dashboard-backing
queries against the shadow environment and see which genuinely break.

### The weaknesses are planted, but not labelled

The estate contains several genuine structural weaknesses. They are not annotated anywhere,
and no file in this repository names them as the answer. Twin has to discover them, and if
its top finding is not one of them, the scoring model is wrong and the scoring model gets
fixed — never the estate.

One example of why that matters. `stg_fx_rates` has 14 downstream assets, but it ranks
*fourth* in the estate by raw fan-out, behind `orders`. What makes it fragile is not its
size — it is that it is unreplicated while `orders` has a standby, and that its sole owner
also owns the entire ML feature branch. A scorer that only counts downstream nodes will
confidently return the wrong answer here. That is the point.

## Stage 1: reading the estate over MCP

Twin reads DataHub through its **official MCP server**, not through the Python SDK and not
through GraphQL. That is a constraint rather than a convenience. The premise of the project
is that an agent should be able to inherit fragility as a dimension of the catalog, and an
agent reaches DataHub over MCP — so Twin reads the way its consumer reads, which keeps it
honest about what that interface actually exposes. Where the interface is thin, this README
says so instead of quietly reaching around it.

```
make read     # read the estate over MCP and cache the graph  (~2 minutes)
make graph    # print the cached graph without touching DataHub
```

```
  ESTATE GRAPH
  --------------------------------------------------------------------
  Read from      http://datahub-gms:8080 over MCP in 125.4s
  Fingerprint    1ab1aaacad9403ce  (first read)
  Cached at      .twin/graph-1ab1aaacad9403ce.json

  ASSETS BY LAYER              ASSETS BY KIND            DEPENDENCIES
    raw_pg             18        dataset            66     table edges       125
    raw_events          9        chart              10     column edges      251
    staging            19        mlFeature           9     columns read      686
    intermediate        9        dashboard           4
    marts               8        mlFeatureTable      1    OPERATIONAL METADATA
    ml                 14        mlModel             1      with an SLA        66
    bi                 14                                   unreplicated        3
                                                            unowned             9

  WIDEST DIRECT FAN-OUT
    intermediate.int_orders_enriched              7 direct, 33 total
    intermediate.int_payment_attempts             4 direct, 17 total
    ml.feature_customer_risk                      4 direct,  6 total

  estate 1ab1aaacad9403ce: 91 assets (66 datasets, 25 consumers), 125 edges, 251 column edges, 9 unowned
```

Three things about that output are worth explaining, because they are decisions rather than
formatting.

**Assets are logical, not physical.** DataHub holds a Postgres entity and a dbt entity for
every model, which is correct for a catalog and wrong for a failure model — dropping a
column breaks one asset, not two. Twin folds the pair into a single asset that remembers
both URNs, taking column types from the physical entity and ownership, tags and operational
metadata from the dbt entity. Without the fold the estate would appear to have 132 datasets
and every table would list its own sibling as a dependency.

**251 column edges, not 125 table edges.** Column-grain lineage is the difference between a
blast radius and a guess. `stg_fx_rates.rate` has three direct consumers; `rate_date` has
none. A table-grain model scores both columns as the same whole-table failure, which
overstates the damage in exactly the direction that makes a demo look impressive.

**The fingerprint is content-addressed.** It hashes the assets, edges, column edges and
operational metadata, and deliberately excludes when and where the read happened. The same
platform read twice produces the same fingerprint — verified across runs and across
concurrency settings — which is what makes the cache safe and what lets a nightly run state
whether the platform actually changed since yesterday rather than merely that it was read
again.

### What the MCP server does not expose

Recorded here rather than worked around, because the gaps shape what later stages can claim.

- **No usage statistics.** DataHub holds the estate's real query counts, and no MCP tool
  returns them. `get_dataset_queries` reads Query entities, which is a different thing.
  Usage-weighted scoring in Stage 3 therefore needs a decision that is not yet made, and
  the options — emit Query entities during ingestion, or read usage through the SDK and
  label it as a documented exception — will be recorded here once one is taken.
- **No column-to-column lineage.** Column lineage answers with the downstream *datasets*
  that consume a column, not the downstream fields. Resolving field to field means calling
  `get_lineage_paths_between` for candidate column pairs, which is quadratic in columns per
  edge. Twin records what the interface returns rather than inferring the missing half.
- **No `mlModelDeployment`.** It is not searchable through the filter syntax and its
  properties are not returned by `get_entities`, so Twin's ML branch is modelled as far as
  the model itself. The deployment is the one estate entity the pipeline cannot see, and
  `make verify-estate` still checks it because that script deliberately uses a different
  access path.

### The nightly read

`.github/workflows/twin-nightly.yml` runs on a cron from the day Stage 1 started producing
output. Each night it stands the stack up from nothing, builds the estate, reads it back
over MCP, and appends one measured line to `examples/history/nightly.jsonl`. The record
widens as stages land — fragility scores join it when Stage 3 exists — but it stays
append-only and a line is written only when a run genuinely succeeded. A trend cannot be
backfilled, so the accumulation starts now rather than the week of a deadline.

## Architecture

Five stages, two feedback loops, three entry points.

```
                    ┌──────────────────────────────────────────────────┐
                    │                                                  │
   DataHub ──▶ 1 Read ──▶ 2 Simulate ──▶ 3 Score ──▶ 4 Verify ──▶ 5 Write back
   (via MCP)              (scenario       (knockout   (shadow      (properties,
                           YAML)           sweep)      warehouse)   incidents, PRs)
                                              ▲            │             │
                                              └────────────┘             │
                                          inner loop: calibration        │
                                          (observed accuracy re-weights  │
                                           propagation rules)            │
                    │                                                    │
                    └────────────────────────────────────────────────────┘
                       outer loop: nightly snapshots become the trend

   Three entry points, one engine:
     make nightly            scheduled — full pipeline, writes back, opens incidents
     make run SCENARIO=...   one scenario — stages 1-4, report to stdout
     make gate               CI — stages 1-3 on changed assets, non-zero if it worsens
```

Twin has no chat interface and will not be getting one. It is invoked by a scheduler, a
scenario file, or a CI trigger.

## Safety

Twin executes destructive operations — that is the point of Stage 4 — so the guardrails are
structural rather than conventional. The warehouse role model is already in place and is
described in full in [docs/SAFETY.md](docs/SAFETY.md). The short version:

Estate objects are owned by the `twin` role. Stage 4 will execute as `twin_shadow`, which
owns nothing in the estate. In PostgreSQL, `DROP TABLE` and `ALTER TABLE` require object
ownership and ownership cannot be assumed at will, so `twin_shadow` is structurally
incapable of dropping or altering a real estate table whatever statement it is handed. It
can only destroy what it created, inside schemas it created.

The execution-boundary guard that refuses any destructive statement naming an object
outside a `twin_shadow_` prefix belongs to Stage 4 and is not built yet.

## Running it

Prerequisites: Docker with Compose v2, and about 6GB of free memory. No cloud account, no
credentials, no paid tier.

| Command | What it does |
|---|---|
| `make up` | Start DataHub and the warehouse |
| `make estate` | Seed 1.9M rows, build 39 dbt models, ingest everything, run the workload |
| `make verify-estate` | Prove the estate is real; exits non-zero if not |
| `make read` | Read the estate over MCP and cache the graph |
| `make graph` | Print the cached graph without touching DataHub |
| `make test` | Run the test suite |
| `make down` | Stop everything and remove the volumes |
| `make help` | List every target that exists |

Timings on a 4-core VPS: `make up` takes ~3 minutes on first run (image pull), `make estate`
takes ~3 minutes, `make verify-estate` takes ~20 seconds, `make read` takes ~2 minutes. The
read is dominated by round trips to GMS rather than by Twin, which is why the graph is
cached and why raising client concurrency barely moves it.

The stack binds host ports outside DataHub's defaults — 19002 for the UI, 18080 for GMS,
15432 for the warehouse — so it will not collide with anything already running. Every
container has an explicit memory limit for the same reason.

### If something goes wrong

- **`make up` hangs or containers restart.** The stack needs ~6GB. Check `docker stats`;
  OpenSearch and GMS are the memory-hungry ones and both have explicit caps in
  `docker-compose.yml`.
- **`make verify-estate` reports zero datasets.** DataHub indexes asynchronously through
  OpenSearch. Wait ten seconds and run it again.
- **`make estate` fails at the ingest step.** Confirm GMS is healthy: `make ps`.

## Limitations

Honest and specific, and this list will grow rather than shrink as stages land.

- **The estate is synthetic.** Volumes, distributions and failure modes are modelled on
  real platforms, but no real company's data is in here. The distributions that matter —
  Zipf product popularity, weekly seasonality, per-processor decline rates, unterminated
  sessions — are documented in `estate/seed/generate.py`.
- **Usage statistics count a synthetic workload.** Real executions, real counts, synthetic
  consumers. See above.
- **The SLA and refresh-cadence metadata is declared, not measured.** In a real deployment
  these would come from orchestrator run history. Here they are `meta` blocks in the dbt
  project, which is also how a great many real teams actually record them.
- **Only Postgres is supported as a warehouse.** Nothing in the design is Postgres-specific
  except the Stage 4 execution layer, but nothing else has been built or tested.
- **Stage 1 sees only what MCP exposes.** Usage statistics, column-to-column lineage and
  the ML deployment are not reachable through the interface Twin reads from — see *What the
  MCP server does not expose*. Everything Twin scores is built from what is listed there.
- **Stages 2 through 5 do not exist yet.** See the build status table.

## License

Apache 2.0. See [LICENSE](LICENSE).
