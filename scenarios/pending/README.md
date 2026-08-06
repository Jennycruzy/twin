# Scenarios that cannot run yet

Not a staging area for half-written ideas. A file lands here when it describes a fault Twin
should be able to execute and currently cannot, so that the gap is visible in the tree rather
than remembered.

`scenarios/*.yml` does not match this directory, so `make scenarios` and
`ops/capture-examples.sh` skip it. Everything in `scenarios/` proper loads and runs; that is
an invariant worth keeping, and it is why these are not simply left there commented out.

## merchant_id_nulled_at_source.yml

The same fault as `merchant_id_nulled`, one level upstream at `raw_pg.orders`, where the
catalog has table lineage and no column lineage at all.

Blocked on the shadow source override. dbt resolves `source()` through `sources.yml`, whose
schema is fixed, so a fault on a raw source is built into the shadow schema and then read by
nothing: the models build from production and Stage 4 grades a fault that never landed. The
scenario loader refuses it by name for exactly that reason — see `twin/faults.py`
(`SOURCE_LAYERS`) and the *Limitations* entry in the top-level README.

Item 1 in `HANDOFF.md` is the scope for unblocking it. When it runs, it is expected to
over-predict: it predicted 37 assets against 16 that a dbt build can grade. That is the point
of it. Whatever precision it produces is the number that ships.
