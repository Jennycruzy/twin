# Twin

**Twin is chaos engineering for data platforms.** It reads DataHub's context graph,
simulates failures across it, executes those failures for real against a live warehouse to
verify its own predictions, and writes fragility scores back into DataHub as structured
properties, so every other agent inherits a dimension the catalog didn't have before.

Those scores are read back out *over MCP* — the same interface another agent would use to
find them — by `make prove-writeback`. Assets that Stage 4 *proved* broken are raised as
DataHub incidents carrying the warehouse's own error text, and `make unwrite` removes every
value and resolves every incident.

---

## Build status

Twin is being built in milestones, and this README describes only what is actually in the
repository. Where something is planned rather than built it is labelled as planned, in the
same sentence, and never in the present tense.

| Milestone | Scope | State |
|---|---|---|
| **M1** | The demo estate — warehouse, dbt project, DataHub ingestion, verification gate | **Done** |
| **M2** | Stage 1 — read the estate through the DataHub MCP server | **Done** |
| **M3** | Stage 4 — shadow execution and self-grading | **Done** |
| **M4** | Stage 2 — propagation engine and failure timelines | **Done** |
| **M5** | Stage 3 — fragility scoring and the knockout sweep | **Done** |
| **M6** | Stage 5 — write-back of fragility as structured properties | **Done** |
| **M6b** | Stage 5 — incidents on assets that failed verification | **Done** |
| M7 | Repair PRs, CI gate | Not started |

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
make read     # read the estate over MCP and cache the graph  (~12 minutes, see below)
make graph    # print the cached graph without touching DataHub
```

```
  ESTATE GRAPH
  --------------------------------------------------------------------
  Read from      http://datahub-gms:8080 over MCP in 716.2s
  Fingerprint    2b0ff33cd937f51f  (first read)
  Cached at      .twin/graph-2b0ff33cd937f51f.json

  ASSETS BY LAYER              ASSETS BY KIND            DEPENDENCIES
    raw_pg             18        dataset            66     table edges       125
    raw_events          9        chart              10     column edges      322
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

  estate 2b0ff33cd937f51f: 91 assets (66 datasets, 25 consumers), 125 edges, 322 column edges, 9 unowned
```

Three things about that output are worth explaining, because they are decisions rather than
formatting.

**Assets are logical, not physical.** DataHub holds a Postgres entity and a dbt entity for
every model, which is correct for a catalog and wrong for a failure model — dropping a
column breaks one asset, not two. Twin folds the pair into a single asset that remembers
both URNs, taking column types from the physical entity and ownership, tags and operational
metadata from the dbt entity. Without the fold the estate would appear to have 132 datasets
and every table would list its own sibling as a dependency.

**322 column edges, not 125 table edges.** Column-grain lineage is the difference between a
blast radius and a guess. `stg_fx_rates.rate` has three direct consumers; `rate_date` has
none. A table-grain model scores both columns as the same whole-table failure, which
overstates the damage in exactly the direction that makes a demo look impressive.

**The fingerprint is content-addressed.** It hashes the assets, edges, column edges and
operational metadata, and deliberately excludes when and where the read happened. The same
platform read twice produces the same fingerprint, which is what makes the cache safe and
what lets a nightly run state whether the platform actually changed since yesterday rather
than merely that it was read again.

That has been checked the hard way. The stack was torn down with its volumes deleted, and
the whole estate rebuilt from nothing — 1.9M rows regenerated, 39 models rebuilt, everything
re-ingested and re-read. The fingerprint came back `2b0ff33cd937f51f`, identical to the read
before the teardown. Determinism here is a measured property rather than an intention.

### What the MCP server does not expose

Recorded here rather than worked around, because the gaps shape what later stages can claim.

- **No usage statistics.** DataHub holds the estate's real query counts, and no MCP tool
  returns them. `get_dataset_queries` reads Query entities, which is a different thing.
  Usage-weighted scoring in Stage 3 needed a decision about this, and the decision taken was
  to read usage through the SDK and label it as a documented exception rather than drop the
  component or invent counts. `twin/score/usage.py` is the only module in the pipeline that
  does not read over MCP, it says so in its first paragraph, and it is the only thing that
  changes if DataHub later exposes usage over MCP. Everything else — structure, lineage,
  ownership, operational metadata — comes over MCP.
- **Column-to-column lineage is not returned; it has to be interrogated.** Asking which
  assets consume a column answers with the downstream *datasets*, not the downstream fields.
  The landing column is obtainable, but only by asking `get_lineage_paths_between` whether a
  specific pair is connected — one call per candidate pair, thousands per read. Twin pays
  that cost because damage cannot be followed at column grain past the first hop without it,
  and it is the difference between predicting fifteen damaged assets and the ten that are.
  Two further wrinkles worth knowing if you build on this interface: absence of a path is
  reported by raising rather than by returning an empty result, in two different wordings,
  and a pair that cannot be resolved is counted and assumed connected rather than dropped.
- **No write tool at all.** The server exposes six tools — `search`, `get_lineage`,
  `get_dataset_queries`, `get_entities`, `list_schema_fields`, `get_lineage_paths_between` —
  and every one of them reads. An agent can consume the context graph over MCP but cannot
  contribute to it, which is a gap in the premise rather than in coverage: the case for an
  agent-facing catalog interface is that agents participate in the graph. Stage 5 therefore
  writes through the SDK and reads back over MCP, and says so wherever it claims a round
  trip. This is the second documented exception, after usage statistics.
- **No `mlModelDeployment`.** It is not searchable through the filter syntax and its
  properties are not returned by `get_entities`, so Twin's ML branch is modelled as far as
  the model itself. The deployment is the one estate entity the pipeline cannot see, and
  `make verify-estate` still checks it because that script deliberately uses a different
  access path.

### The nightly read

A fragility trend is a claim about change over time, and it is only worth anything if the
runs actually happened. History cannot be backfilled, so it started accumulating the day
Stage 1 produced output rather than the week of a deadline.

Two paths write to the same append-only file, `examples/history/nightly.jsonl`:

- **`ops/nightly-read.sh`**, run from a host cron at 03:17. Suits a machine where the stack
  is already up — it verifies the estate, reads it over MCP, and commits one line.
- **`.github/workflows/twin-nightly.yml`**, the same job on a runner that builds the estate
  from nothing first. Present but currently disabled, along with the test workflow, because
  Actions are unavailable on this account — every scheduled run failed before starting and
  left a misleading red mark on a commit that was fine. The host cron is doing the work in
  the meantime, and the commit trail it produces is the record. Both workflows re-enable with
  `gh workflow enable` and nothing else.

Either way a line is written only when a read genuinely succeeded, and the estate is
verified before the read, so a broken estate produces no history rather than a line
asserting something nobody checked. The record widens as stages land — Stage 3 now appends
to `examples/history/fragility.jsonl` alongside it — but it stays append-only.

Which is why the two lines in `nightly.jsonl` today report 251 column edges and fingerprint
`1ab1aaacad9403ce`, against the 322 and `2b0ff33cd937f51f` shown above. They were read at
09:30 and 10:20 on 2026-08-05, before `03d001f` followed damage past the first hop and
`6ca2842` reverted a column-pair narrowing that was losing edges. The history is append-only,
so it records what was true when it ran rather than being rewritten to agree with the
current README.

Those two lines carry no commit SHA, which is why the paragraph above is necessary — a reader
had to be told the reason rather than being able to check it. Every record written from
`306dc8b` onward carries the commit that produced it and whether the tree was dirty at the
time, and fragility records also carry a digest of the weights file, so a ranking that moves
can be attributed to the estate changing or to the model changing without anyone having to
explain it in a README. The two lines above stay as they are: rewriting history to make it
tidier is the one thing an append-only record cannot do.

## Stage 2: five failure modes, not five faults

The five scenarios in `scenarios/` are chosen so that each one fails *differently*. Five
variations on "delete something" would grade well and prove very little.

| Scenario | Fault | Twin must predict | How reality answers |
|---|---|---|---|
| `payments_table_dropped` | asset deleted | unavailable | relation missing |
| `fx_rate_column_drop` | column stops arriving | unavailable | build error |
| `fx_rate_type_regression` | column arrives as text | unavailable | cast error |
| `merchant_id_nulled` | join key becomes null | **degraded** | builds, contents differ |
| `orders_feed_stalls` | rows stop arriving | **degraded** | builds, three days short |

The bottom two are the point of M4. **Unavailable** means the asset is not there — loud,
alarming, comparatively easy. **Degraded** means it built successfully and is wrong: nothing
alarms, the dashboard renders, and the number on it is untrue. A model that reports every
fault as an outage is right about the cheap failures and wrong about the expensive ones, so
the two are predicted separately and graded separately.

That distinction forces a second observation channel. A dbt exit code cannot see staleness,
so every rebuilt model is compared against production by row count *and* an order-independent
checksum over every row. This is not a detail — a revenue mart missing three days of orders
has exactly the same number of rows as production and understates three days of revenue.
Row counts alone called that healthy and scored a correct prediction as a false alarm.

```
make scenarios                    # run all five
make run SCENARIO=scenarios/orders_feed_stalls.yml
```

### Results across the five, executed

| Scenario | Isolated probe | Full refresh | Identical to production |
|---|---|---|---|
| `payments_table_dropped` | 1.00 / 1.00 | 1.00 / 1.00 (6 unavailable) | 5 |
| `fx_rate_column_drop` | 1.00 / 1.00 | 1.00 / 1.00 (14 unavailable) | 11 |
| `fx_rate_type_regression` | 1.00 / 1.00 | 1.00 / 1.00 (14 unavailable) | 11 |
| `orders_feed_stalls` | 1.00 / 1.00 | 1.00 / 1.00 (15 degraded) | 13 |
| `merchant_id_nulled` | 1.00 / 1.00 | 1.00 / 1.00 (10 degraded) | 5 |

Precision / recall. **The last column is the one to read.** It counts models rebuilt in the
same run that came out byte-for-byte identical to production — 45 across the five scenarios,
every one an opportunity to raise a false alarm that was not taken. A comparison that
reported "different" for everything would manufacture a table of 1.00s, and that column is
what distinguishes this from one.

### How the last row got there, because it did not start there

`merchant_id_nulled` first scored **0.67**, with five assets predicted degraded that came out
identical to production. The cause was real: the first wave of a column fault was found at
column grain, but degradation *after* that first hop propagated at table grain, so everything
downstream of a degraded asset inherited it whether or not it read the affected values.

The fix follows column lineage the whole way down for degradation, while leaving
unavailability at table grain — a missing relation cannot be read whichever column you
wanted from it, so table grain is correct there and column grain is correct for damage. That
required Stage 1 to resolve which column each dependency *lands on*, which DataHub does not
return and has to be interrogated pair by pair. It is why a read takes twelve minutes rather
than two, and why the graph gained 71 column edges.

Two things are worth stating about that, because a table of five perfect scores invites
exactly this suspicion:

**The change was made generally and then re-run against all five**, not tuned until one
number moved. It is pinned by unit tests including the fallback case, and none of the other
four scenarios changed.

**There is now no scenario in this repository where Twin is wrong**, and that is a weaker
position to be in, not a stronger one. The honest reading of the table above is "the model
survives the five faults it has been tested against", not "the model is correct". The report
prints that warning on every run that scores perfectly.

### Who gets paged

A timeline says what fails. A paging list says what that costs at 05:30.

```
  WHO GETS PAGED
  --------------------------------------------------------------------------
    4 owner(s) paged, heaviest dana.oyelaran@example.com with 15 asset(s), 12 asset(s) page nobody

       +00:00  dana.oyelaran@example.com          15 asset(s), first intermediate.int_orders_enriched
       +00:00  amara.chen@example.com              6 asset(s), first ml.feature_customer_risk
       ...
    pages nobody — 12 asset(s) with no owner
```

Ordered by when the phone rings rather than by severity, because the owner of the first
asset to fail is paged first whether or not theirs is the important one. The unowned assets
are listed rather than dropped: an asset that pages nobody is more dangerous than one that
does, and a response plan that silently omits them describes an incident nobody attends.

## Stage 3: scoring fragility

```
make score
```

The estate contains structural weaknesses that are **not annotated anywhere in this
repository**. The scoring model is correct to the extent that it finds them without being
told, and the case that decides it is where size and danger disagree.

`raw_pg.orders` has the widest reach of anything here — 16 datasets and 22 consumers.
`raw_pg.fx_rates` reaches less: 15 and 21. A scorer that ranks by fan-out returns orders.

```
   #  ASSET                             SCORE   blast  expos  recov  conce  blind   BLAST
   1  raw_pg.fx_rates                    61.5    0.40   0.84   1.00   0.29   0.12   15+21
   2  staging.stg_fx_rates               61.3    0.38   0.84   1.00   0.30   0.12   14+21
   3  intermediate.int_orders_enriched   43.3    0.36   0.83   0.33   0.35   0.00   12+21
   4  staging.stg_orders                 37.9    0.41   0.89   0.00   0.29   0.12   15+22
   5  raw_pg.orders                      37.8    0.42   0.89   0.00   0.26   0.12   16+22
```

Orders wins on blast **and** on exposure — the two components most scorers would use — and
loses on recovery, because it has a standby and the FX feed does not. That single difference
is the whole ranking, and it is the one that matters operationally: losing orders is
survivable because the data can be served from elsewhere; losing fx_rates is not.

**The model follows the metadata rather than remembering the answer.** Flip the two feeds —
give orders no standby and fx_rates one, changing nothing else — and the ranking flips with
it: `raw_pg.orders` becomes the top finding. That is the cheapest available evidence that the
scorer is not fitted to this estate, and it is pinned by a test.

**The sweep does not count graph edges.** It runs the propagation model once per asset with a
`drop_asset` fault — the same model Stage 4 executes against a real warehouse — and reads the
blast radius off the resulting timeline. That means a fragility score is a claim shadow
execution can be pointed at and made to prove or disprove. A number derived from adjacency
could not be checked against anything.

Every component is printed beside the total, because a fragility number nobody can take apart
is a number nobody should act on. The weights live in `config/scoring.yml` so that
disagreement is a config change rather than an argument with a black box, and each component
is explained in [docs/SCORING.md](docs/SCORING.md).

Three limits, stated where they can be seen rather than discovered:

- **Scores are shares of this estate**, not absolute values. Blast is a fraction of the
  assets present and exposure a fraction of the queries recorded, so two estates' numbers are
  not comparable. They are comparable *across nights on the same estate*, which is what the
  fragility trend needs — an earlier version normalised against the highest-scoring asset of
  the night, which made every score move whenever anything else did.
- **Recovery depends on metadata existing.** Every asset here can be traced to a source that
  declares replication, so `make score` reports 100% coverage for it. On an estate where
  nobody records it, the component goes flat and the ranking collapses back toward fan-out —
  which is to say back toward the wrong answer. The report prints coverage for exactly this
  reason, and warns when the component it is leaning on has nothing underneath it.
- **The model finds the weaknesses planted in this estate**, and follows the metadata when
  they move. That is evidence it is not arbitrary or fitted. It is still not evidence that it
  generalises to a platform built by someone else, which would need a second estate.

## Stage 4: executing the failure and grading the prediction

This is the part the project stands on. Twin does not ask you to believe a simulation — it
removes the column for real, rebuilds the real downstream models against the result, re-runs
the real dashboard queries, and then scores its own prediction against what actually broke.

```
make run                                        # the default scenario
make run SCENARIO=scenarios/<name>.yml          # any scenario
make dry-run                                    # print every statement, execute none
```

A run performs **two experiments**, because they answer different questions and only one of
them is hard.

**Does it read the dropped column?** Each downstream model is built on its own while every
other model is a healthy view onto production. A model that fails here fails because of the
fault itself, not because something upstream is missing. This is the falsifiable test: a
model Twin predicted would break and that builds cleanly is a false alarm with nowhere to
hide.

```
  DOES IT READ THE DROPPED COLUMN?
  --------------------------------------------------------------------------
    each model built alone, everything else healthy — the falsifiable test
    scope: 14 models

    hit          intermediate.int_orders_enriched      Database Error in model int_orders_...
    hit          intermediate.int_payment_attempts     Database Error in model int_payment_...
    hit          marts.mart_subscription_health        Database Error in model mart_subscri...

    predicted 3   observed 3   precision 1.00   recall 1.00
    11 model(s) in scope did not break — each one a chance to raise a false alarm
    note: a perfect score on one scenario is weak evidence, not strong
```

The eleven models that did not break are the point. They sit downstream of `stg_fx_rates`
and a table-grain blast radius would have condemned every one of them. Column-grain lineage
said they were fine, and executing the fault agreed.

**What does a full refresh look like?** The second experiment drops those views and rebuilds
the whole downstream estate at once, the way a nightly refresh would. Two models fail on
their own and twelve are never produced, which is what a consumer actually experiences.

```
  CONSUMER QUERIES
  --------------------------------------------------------------------------
    15 real queries re-run against the shadow estate, 11 failed
    failed       revenue_trend.sql            Finance — Revenue Review   96/day
                 relation "twin_shadow_fx_rate_column_drop.mart_revenue_daily" does not exist
```

Those are the same queries the consumer workload executes on every `make estate` — the ones
DataHub's usage statistics were counted from. They were not written for this demonstration.

### What is and is not being claimed

- **The failures are real.** Every error printed is the error PostgreSQL returned, from a
  real dbt build of the real project against a warehouse where the column genuinely does not
  exist.
- **The scores are scoped before they are calculated.** Only models the experiment could
  observe are graded. Twin also predicts that dashboards, charts and the ML branch break;
  a dbt build cannot observe those, so they are listed as ungraded and counted nowhere.
- **The ordering of the timeline is not verified.** Twin predicts that a `daily_0700` table
  breaks at 07:00 and a `daily_0800` mart an hour later. Verification grades *which* assets
  broke, not *when* — checking the clock would mean holding a warehouse for a simulated day.
- **One scenario is weak evidence.** Precision and recall of 1.00 on a single fault says the
  column lineage was right about fourteen models. It does not say the model generalises, and
  the report says so on every run rather than only when it is convenient.

### Safety

Twin executes destructive statements, so the guardrails are structural. Full detail is in
[docs/SAFETY.md](docs/SAFETY.md); the short version is two layers that fail differently.

The database layer: every estate object is owned by `twin`, and Stage 4 connects as
`twin_shadow`, which owns nothing. In PostgreSQL `DROP TABLE` and `ALTER TABLE` require
ownership and ownership cannot be assumed at will, so `twin_shadow` is structurally incapable
of altering a real estate table whatever statement it is handed.

The code layer: an execution boundary that every statement passes through, which refuses any
destructive statement naming anything outside *this run's* `twin_shadow_` schema — including
another run's shadow schema — and runs everything it routes read-only inside a `BEGIN READ
ONLY` transaction, so a write nested inside a `WITH` query or an `EXPLAIN ANALYZE` is refused
by PostgreSQL rather than by a pattern match. `tests/test_execution_guard.py` shows exactly
what it refuses, starting with `DROP TABLE marts.mart_revenue_daily`. See *Safety* below for
what that layer got wrong first.

## Stage 5: writing the fragility dimension back

Everything up to here produces a ranking that lives in this repository, and a ranking in a
repository is a report. Reports are read by the person who ran them. A structured property on
the asset is read by whoever opens the asset, and by any agent that asks the catalog what it
knows — which is the claim the project is actually making.

```
make writeback         # define the properties and write every score   (~40s)
make prove-writeback   # read them back out over MCP                   (~30s)
make unwrite           # remove every value Twin wrote                 (~30s)
```

```
   RANK  ASSET                                      SCORE  BLAST  BUS  SPOF
  --------------------------------------------------------------------------
      1  raw_pg.fx_rates                           61.517     36    4    no
      2  staging.stg_fx_rates                      61.314     35    4    no
      3  intermediate.int_orders_enriched          43.325     33    3    no
      4  staging.stg_orders                        37.939     37    4    no
      5  raw_pg.orders                             37.775     38    5    no
  --------------------------------------------------------------------------
  66 of 66 assets carry Twin's properties, read over MCP
  provenance in the catalog: graph 2b0ff33cd937f51f; commit 47adecc; weights a65901c08535194f
```

Thirteen properties per dataset: the fragility score, its complement as a resilience score,
rank, blast radius, bus factor, a single-point-of-failure flag, when it was scored, the
provenance line above, and all five score components. The components are published because a
score nobody can take apart is a score nobody should act on, and that principle should
survive into the catalog rather than stopping at this repository's stdout. Each property
carries the rule it was computed by in its own description, so a ranking can be disputed on
its parts inside DataHub.

**`make prove-writeback` is the target that matters.** Writing through the SDK and reading
back through the SDK would prove only that the SDK is self-consistent. Reading back over MCP
proves the score is visible through the interface another agent would use to find it. The
table is ordered by the rank DataHub returned rather than by one recomputed locally, so a
disagreement between the catalog and the scorer appears here instead of being hidden by the
sort.

### Incidents: what Twin proved, not what it predicted

A fragility score is a prediction. An incident is a statement that something *did* happen,
and Stage 4 puts Twin in a position to make that statement honestly — it executed the fault,
rebuilt the models with dbt, and holds PostgreSQL's own error for every asset that broke.

```
make incidents SCENARIO=scenarios/fx_rate_column_drop.yml
```

```
  INCIDENTS RAISED IN DATAHUB
  --------------------------------------------------------------------------
    intermediate.int_orders_enriched             unavailable
    marts.mart_revenue_daily                     unavailable
    ml.feature_txn_velocity                      unavailable
    ...
  --------------------------------------------------------------------------
    14 raised against observed failures; resolve with: make unwrite
```

Only observed failures qualify. An asset Twin *predicted* would break gets no incident, so
the incident list is always a subset of what the scorecard graded — a catalog full of alerts
for things that did not happen would undo the only property that makes Twin's output worth
reading. Each incident carries the warehouse's error text verbatim, the scenario that caused
it, and the run's provenance, because an incident with no stated cause is an alert nobody can
act on.

Incident URNs are deterministic, so re-running a scenario updates its incidents instead of
duplicating them, and `make unwrite` marks them **resolved rather than deleting them**. The
condition was real when it was recorded, and a catalog that forgets its incidents cannot be
used to argue about how often anything breaks.

Raising them is behind a flag rather than on by default: a verification run should not change
the estate's metadata because somebody wanted to see a scorecard.

### Removing it, and a DataHub constraint worth knowing

`make unwrite` clears every value Twin wrote and deliberately leaves the thirteen definitions
in place. That looks like the less tidy choice and is the correct one.

Hard-deleting a structured property removes the entity but leaves its Elasticsearch field
mapping behind. Defining the same `qualifiedName` afterwards is rejected:

```
Structured property Elasticsearch field 'twin_fragility_score' collides with
existing property mapping.
```

The name is burnt for the life of the index. So an `unwrite` that deleted definitions would
make the *second* `make writeback` fail — which is exactly the sequence a judge runs. This
was found by doing it: the first implementation deleted them, and recovering the names on the
development stack required dropping `datasetindex_v2`, recreating it through DataHub's own
`SystemUpdate` job and repopulating it with `RestoreIndices`.

A definition holding no values appears on no asset and in no search result, so it is not the
residue that matters. For anyone who wants the catalog truly empty and accepts that the names cannot be reused
without rebuilding the index:

```
docker compose run --rm twin python -m twin.write --unwrite --purge
```

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

   Entry points that exist today:
     make run SCENARIO=...   one scenario — stages 1-4, report to stdout
     make scenarios          all five, each graded
     make score              stage 3 — rank the estate by fragility
     make writeback          stage 5 — write fragility into DataHub
     make prove-writeback    read it back over MCP

   Planned, and deliberately not in the Makefile until they work:
     make nightly            scheduled — full pipeline, writes back, opens incidents
     make gate               CI — stages 1-3 on changed assets, non-zero if it worsens
```

The nightly job that runs today is `ops/nightly-read.sh` and
`.github/workflows/twin-nightly.yml`, described under *The nightly read*. They verify, read
and score. Wiring `make writeback` into them is a one-line change and is not made yet, so the
scores in the catalog are from the last manual run rather than from last night.

Twin has no chat interface and will not be getting one. It is invoked by a scheduler, a
scenario file, or a CI trigger.

## Safety

Twin executes destructive operations — that is the point of Stage 4 — so the guardrails are
structural rather than conventional. The warehouse role model is already in place and is
described in full in [docs/SAFETY.md](docs/SAFETY.md). The short version:

Estate objects are owned by the `twin` role. Stage 4 executes as `twin_shadow`, which owns
nothing in the estate. In PostgreSQL, `DROP TABLE` and `ALTER TABLE` require object
ownership and ownership cannot be assumed at will, so `twin_shadow` is structurally
incapable of dropping or altering a real estate table whatever statement it is handed. It
can only destroy what it created, inside schemas it created.

The execution boundary in `twin/verify/guard.py` is the second layer. Every statement Twin
sends passes through it, and it refuses any destructive statement naming an object outside
*this run's* `twin_shadow_` schema — including another run's shadow schema, which the role
model does not cover because `twin_shadow` owns every schema it creates.

That second layer was wrong until `f9e27f0`, and the correction is worth stating rather than
quietly shipping. It classified statements by their leading keyword, so `WITH` and `EXPLAIN`
were treated as read-only and returned unchecked — but PostgreSQL allows `DELETE` inside a
`WITH` query and `EXPLAIN ANALYZE` executes what it explains, and both forms modified data
after passing the guard. Statements routed read-only now execute inside a `BEGIN READ ONLY`
transaction, so the guarantee is enforced by the server at any nesting depth instead of by a
regex. `tests/test_execution_guard.py` shows what the boundary refuses, starting with
`DROP TABLE marts.mart_revenue_daily`, and what the server now contains.

Note that dbt's connection does not pass through the guard at all — it opens its own from
`estate/dbt/profiles.yml`, so most of the SQL a scenario causes to run is constrained by the
role model alone. [docs/SAFETY.md](docs/SAFETY.md) has the full account.

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
| `make run` | Run a scenario through stages 1-4 and grade the prediction |
| `make scenarios` | Run all five scenarios in turn |
| `make score` | Rank every asset by fragility |
| `make dry-run` | Print every statement a scenario would execute, execute none |
| `make writeback` | Write fragility into DataHub as structured properties |
| `make prove-writeback` | Read Twin's scores back out over MCP |
| `make incidents` | Run a scenario and raise incidents for what actually broke |
| `make unwrite` | Remove every value Twin wrote, and resolve its incidents |
| `make test` | Run the test suite |
| `make down` | Stop everything and remove the volumes |
| `make help` | List every target that exists |

Timings on a 4-core VPS, measured from a fresh clone with the volumes wiped rather than on a
warm machine: `make up` 2m13s, `make estate` 2m08s, `make verify-estate` 36s, `make read`
10-12 minutes, `make run` about a minute per scenario, `make writeback` 40s,
`make prove-writeback` 30s.

The read is the outlier and the reason the graph is cached. It is dominated by round trips
to GMS rather than by Twin — resolving which column each dependency lands on is roughly
four thousand individual questions, each a graph traversal on DataHub's side. Raising client
concurrency barely moves it. Narrowing the questions does move it, and is wrong: see the
note in `twin/read/materialize.py` about the edge that disappears.

`make verify-estate` waits for DataHub to finish indexing before judging, because ingestion
returns before the entities it wrote are searchable. It waits for the numbers to stop
changing rather than for them to become correct, so a genuinely incomplete estate still
fails, and fails immediately.

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
- **Five fault kinds are executable**, and only those five. A fault the execution layer
  cannot run is refused by the scenario loader rather than silently accepted, because it
  would produce a prediction nothing can grade. Revoking access is the notable absence: Stage
  4 executes as the owner of everything it creates, so it cannot revoke its own privileges
  convincingly, and a simulated permission error would be exactly the kind of pretend
  evidence this project exists to avoid.
- **Where column lineage is absent, damage falls back to table grain** and over-predicts.
  That is deliberate — under-predicting a quiet failure is the more expensive error — but it
  means the precision of a degrading fault depends on how completely the catalog describes
  the columns involved.
- **Every scenario currently scores 1.00.** Read that as "survives the five faults it has
  been tested against", not as "correct". The control column above is the reason to believe
  the scores at all, and a sixth fault chosen adversarially would be worth more than a sixth
  variation on the five.
- **Verification grades what broke, not when.** The predicted timeline's ordering is not
  checked by shadow execution. See *What is and is not being claimed*.
- **Incidents cannot be listed through DataHub's own search.** An incident written by
  emitting `incidentInfo` through the SDK is stored correctly and reads back by URN, but
  GraphQL search refuses to hydrate it and fails the whole query. Twin therefore derives its
  incident URNs deterministically rather than discovering them. See `docs/UPSTREAM.md`.
- **Write-back goes through the SDK**, because the MCP server exposes no write tool. Twin
  reads over MCP and writes beside it, which is stated wherever a round trip is claimed —
  see *What the MCP server does not expose*.
- **The public evidence trail is thin.** Both GitHub Actions workflows are disabled because
  Actions are unavailable on this account, so the record is a host cron and the commits it
  produces. `examples/` holds the history files and nothing else yet: no committed
  verification reports, no incidents, no repair PRs.

## License

Apache 2.0. See [LICENSE](LICENSE).
