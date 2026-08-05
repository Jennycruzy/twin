# Safety: what Twin executes, and where

Twin's central claim is that it does not ask you to trust a simulation — it runs the
failure for real and grades its own prediction. That makes it a tool which deliberately
executes destructive statements against a database, and it means the guardrails are the
most important component in the system, not a footnote to it.

This document states plainly what Twin runs, where it runs, and what stops it running
anywhere else. It describes the state of the repository as built. Where a guard belongs to
a stage that does not exist yet, it says so rather than describing an intention as though
it were a fact.

## What Twin executes

Inside a schema named `twin_shadow_<scenario>`, and nowhere else:

- `CREATE SCHEMA twin_shadow_<scenario>`, and a view onto the real relation for every estate
  model, so the shadow estate stands up in seconds without copying any data
- the fault the scenario declares, expressed as the shadow copy of the affected asset: a
  relation genuinely missing a column, genuinely holding the wrong type, genuinely filled
  with nulls, genuinely short of recent rows, or genuinely absent
- real dbt builds of the downstream models against that shadow schema, as `twin_shadow`
- the real dashboard-backing queries from `estate/ingest/queries/`, re-pointed at the
  shadow schema
- `DROP SCHEMA twin_shadow_<scenario> CASCADE` in a `finally`, so two consecutive runs
  leave zero residue

Every one of those statements passes the execution boundary described below before it is
sent. `make dry-run` prints the complete list for a scenario and executes none of it.

Fault kinds the execution layer cannot run — revoking access is the notable one, because
Stage 4 owns everything it creates and cannot convincingly revoke its own privileges — are
rejected by the scenario loader rather than silently accepted. A fault Twin cannot execute
would produce a prediction nothing could grade.

## The three roles

The warehouse is created with three roles that have deliberately different power. They are
defined in `estate/warehouse/init/01-roles.sql`, which runs automatically on first start.

| Role | Used by | Privileges |
|---|---|---|
| `twin` | The estate build (seed + dbt) | Owns every estate object |
| `twin_reader` | Stage 1 read, Stage 3 scoring, the consumer workload | `SELECT` on the estate. No write privilege anywhere in the database |
| `twin_shadow` | Stage 4 execution | `SELECT` on the estate, and `CREATE` on the database so it can make its own shadow schemas. Owns nothing in the estate |

`twin_reader` is not a convention that Twin follows voluntarily. The consumer workload in
`estate/ingest/workload.py` connects as `twin_reader` today, which means the role's read
path is exercised on every `make estate` and a regression in its grants would surface
immediately rather than the first time someone audited it.

## The guarantee that matters

In PostgreSQL, `DROP TABLE` and `ALTER TABLE` require ownership of the object, and a role
cannot assume ownership at will.

Every estate object is owned by `twin`. `twin_shadow` owns nothing in the estate.
Therefore `twin_shadow` is structurally incapable of dropping or altering a real estate
table, **whatever statement it is handed** — including one produced by a bug in Twin, a
malformed scenario file, or a mistaken hand-edit. It can only destroy what it created
itself, which is only ever inside a schema it created.

This is a property of the database's permission model, not of Twin's code being correct.
That distinction is the whole point: a guard that depends on the calling code being
bug-free is not a guard.

`twin_shadow` does hold `CREATE` on the database, which is the widest privilege it has. It
needs this to create shadow schemas. Creating a schema confers no power over schemas it did
not create, so the blast radius of that privilege is bounded by what the role can already
do: make new empty namespaces.

## The second layer, and why there are two

The execution boundary lives in `twin/verify/guard.py` and every statement Twin sends passes
through it, because the check is inside `execute` rather than at the call sites — there is no
unguarded path to the database in the codebase.

Its rule is narrow and mechanical. A statement is either read-only, in which case it may read
anything the role can see including the real estate, or it is destructive, in which case the
object it acts on must be inside *this run's* shadow schema. A shadow prefix alone is not
enough: naming another run's shadow schema is refused too, so two concurrent scenarios cannot
corrupt each other's evidence. Anything the guard cannot confidently classify is refused,
which means teaching it a new fault kind is a deliberate act rather than something that
happens by accident.

The statements it is asked to refuse are in `tests/test_execution_guard.py`, including
`DROP TABLE marts.mart_revenue_daily` — the statement this layer exists to stop — as well as
destructive verbs hidden behind comments, multi-statement strings, and unqualified names that
would resolve through a `search_path` Twin does not control.

Two layers, because they fail differently. The database guard cannot be bypassed by a bug in
Twin, but it also cannot stop Twin doing something destructive *inside* a shadow schema that
the operator did not intend — it has no idea what Twin meant to do. The code guard
understands intent but is only as correct as the code. Neither alone is sufficient; together
they cover each other's failure mode.

`make dry-run` prints every statement a scenario would execute and executes none of them, so
the statements can be read before they are trusted.

## What is deliberately not secured

This stack is a local, disposable demo, and pretending otherwise would be its own kind of
dishonesty:

- **DataHub metadata service authentication is disabled**, matching the upstream quickstart.
  Anything that can reach GMS can read and write metadata. The port is bound on localhost.
- **Warehouse passwords are fixed and committed** (`twin`, `twin_reader`, `twin_shadow`).
  There is no real data in the warehouse — it is generated by `estate/seed/generate.py` from
  a seeded RNG, and every customer in it is named `Customer <n>`.
- **The warehouse port is published to the host** (15432 by default) so that a reader can
  inspect the estate with `psql` and check these claims rather than believe them.

None of these are appropriate for an instance holding real data. If you point Twin at one,
enable DataHub auth, supply `DATAHUB_GMS_TOKEN`, and provision the three roles with real
credentials. The role *separation* is what must be preserved; the passwords are incidental.
