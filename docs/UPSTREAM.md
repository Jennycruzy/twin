# Upstream findings

Five things Twin found by building on DataHub's agent-facing interfaces. All five were
discovered by using the interface for real work rather than by reading its documentation, and
each carries a reproduction rather than an assertion.

Filing status: **filed/attached 2026-08-07.** Three findings were attached to existing
upstream issues to avoid duplicates; two were filed as new issues. The record is below.

## Filing record

1. MCP write-tool gap — evidence comment on [acryldata/mcp-server-datahub#143](https://github.com/acryldata/mcp-server-datahub/issues/143#issuecomment-5217656747).
2. Structured-property deletion collision — new [datahub-project/datahub#18974](https://github.com/datahub-project/datahub/issues/18974).
3. Incident readback shape — evidence comment on [acryldata/mcp-server-datahub#172](https://github.com/acryldata/mcp-server-datahub/issues/172#issuecomment-5217657059).
4. Usage statistics unavailable over MCP — evidence comment on [acryldata/mcp-server-datahub#171](https://github.com/acryldata/mcp-server-datahub/issues/171#issuecomment-5217656908).
5. Column lineage landing field — new [acryldata/mcp-server-datahub#197](https://github.com/acryldata/mcp-server-datahub/issues/197).

---

## 1. MCP server exposes no write tool

**Repo:** `acryldata/mcp-server-datahub`
**Type:** feature gap / design question

The server exposes six tools, and every one of them reads:

```
search   get_lineage   get_dataset_queries   get_entities
list_schema_fields     get_lineage_paths_between
```

An agent can therefore consume the context graph over MCP but cannot contribute to it. That
is a gap in the premise rather than in coverage: the case for an agent-facing catalog
interface is that agents participate in the graph — they enrich it, annotate it, and write
back what they learned. Today an agent that computes something worth knowing has to reach
around MCP and use the Python SDK to record it, which means the agent needs a second set of
credentials and a second client, and the MCP session it was given is not sufficient for the
job it was given.

Concretely, Twin computes a fragility score per dataset and writes it as a structured
property. The read half is MCP. The write half cannot be, so it is the SDK, and the project
documents that split as an exception it did not want to make.

**Reproduction:** `list_tools()` against `mcp-server-datahub` at v1.7.0.

**Suggested shape:** a tool that upserts an aspect for an entity — even restricted to
structured properties, tags, glossary terms and editable descriptions — would close this. The
authorization story is the interesting part and is presumably why it does not exist yet;
worth stating that explicitly in the README if the answer is "deliberately read-only for
now", because that is a reasonable position and currently a reader has to infer it.

---

## 2. Hard-deleting a structured property burns its qualifiedName

**Repo:** `datahub-project/datahub`
**Type:** bug
**Version:** v1.7.0 (GMS, OpenSearch backend)

Deleting a structured property removes the entity but leaves its Elasticsearch field mapping
in place. Defining a property with the same `qualifiedName` afterwards is rejected:

```
Structured property Elasticsearch field 'twin_fragility_score' collides with
existing property mapping. Qualified names that differ only by '.' vs '_'
normalize to the same field name (proposed qualifiedName='twin_fragility_score').
```

`graph.exists(urn)` returns `False` for the deleted property, so from the API's point of view
the name is free while the index still holds it. The name is unusable for the life of the
index.

**Reproduction:**

```python
graph.emit(MetadataChangeProposalWrapper(entityUrn=PROP_URN, aspect=definition))
graph.hard_delete_entity(PROP_URN)
assert graph.exists(PROP_URN) is False
graph.emit(MetadataChangeProposalWrapper(entityUrn=PROP_URN, aspect=definition))  # 422
```

**Why it matters:** any tool that defines its own properties and offers to clean up after
itself hits this on its second run. The natural implementation of "remove everything I wrote"
makes the tool unable to run again. Twin now clears property *values* and leaves definitions
in place for exactly this reason, which is the right behaviour but was arrived at by
tripping over the wrong one.

**Recovery, for anyone who hits it:** drop `datasetindex_v2`, recreate it through the
`SystemUpdate` job in `datahub-upgrade` — plain `RestoreIndices` is not enough, it repopulates
documents into an auto-mapped index where `urn` comes back as `text` instead of `keyword` and
search then fails — and repopulate with `RestoreIndices`. Metadata is preserved throughout,
since MySQL is the source of truth.

**Suggested fix:** either clean the field mapping on hard delete, or reject the delete with a
message saying the name will be unusable, or permit redefinition when the mapping's type
matches. Silently succeeding and then refusing the recreate is the worst of the three.

---

## 3. Incidents are not reachable as top-level entities, and the failure is loud

**Repo:** `datahub-project/datahub`
**Type:** rough edge / API shape
**Version:** v1.7.0 (GMS, OpenSearch backend)

Incidents cannot be resolved through the generic entity paths. `entity(urn:)` returns null
for an incident URN, and `get_urns_by_filter(entity_types=["incident"])` finds documents in
the search index and then fails the entire query:

```
The field at path '/scrollAcrossEntities/searchResults[0]/entity' was declared as
a non null type, but the code involved in retrieving data has wrongly returned a
null value. ... NullValueInNonNullableField
```

They *are* reachable through the asset, and this works correctly:

```graphql
query($urn: String!) {
  dataset(urn: $urn) { incidents(start: 0, count: 100) { incidents { urn status { state } } } }
}
```

**This is not an SDK problem, which is worth stating because it looks like one.** An incident
created by DataHub's own `raiseIncident` GraphQL mutation behaves identically: it reads back
correctly through the asset, and `entity(urn:)` returns null for it too. Incidents written by
emitting an `incidentInfo` aspect are indistinguishable from ones the UI creates.

**What is worth fixing** is the failure mode rather than the design. If incidents are
deliberately not top-level searchable entities — a reasonable choice, since an incident
without its asset is close to meaningless — then `scrollAcrossEntities` should not accept
`incident` as an entity type and then fail on hydration. One unhydratable result nulls a
non-nullable field and takes the whole query with it, so the caller gets a schema violation
instead of an empty list or an error naming the cause. Rejecting the filter up front, or
skipping results that cannot be hydrated, would both be clearer.

**Reproduction:**

```python
graph.execute_graphql(RAISE_INCIDENT_MUTATION, ...)   # DataHub's own creation path
graph.execute_graphql('query($u:String!){ entity(urn:$u){ urn } }', {'u': urn})  # -> None
graph.get_urns_by_filter(entity_types=["incident"])                              # -> GraphError
graph.execute_graphql(INCIDENTS_ON_DATASET, {'urn': dataset_urn})                # -> works
```

---

## 4. MCP exposes no usage statistics

**Repo:** `acryldata/mcp-server-datahub`
**Type:** feature gap

DataHub holds per-dataset usage statistics — real counts of query executions — and no MCP
tool returns them. `get_dataset_queries` returns Query entities: their text, their subjects,
who last ran them. It carries no execution counts.

Any agent reasoning about *importance* needs this. "How many things read this asset" is the
difference between a mart three dashboards refresh hourly and an unread intermediate model,
and over MCP the two are indistinguishable. Twin scores fragility partly by measured usage
exposure and reads those counts through the SDK as a documented exception, because the
alternatives were dropping the component or inventing numbers.

**Reproduction:** publish `datasetUsageStatistics`, then attempt to read it back through any
of the six tools.

---

## 5. Column lineage does not return the landing column

**Repo:** `acryldata/mcp-server-datahub`
**Type:** feature gap / performance

Asking which assets consume a column answers with the downstream *datasets*, not the
downstream *fields*. The landing column is obtainable only by asking
`get_lineage_paths_between` whether one specific pair is connected — one call per candidate
pair, thousands per estate read.

For a 66-dataset estate this is roughly four thousand round trips and turns a read into about
ten minutes, almost all of it waiting on GMS — 10m07s and 10m43s on the nightlies of
2026-08-06 and 2026-08-07. Twin pays that cost because damage cannot be
followed at column grain past the first hop without it, and column grain is the difference
between predicting fifteen damaged assets and the ten that actually break.

Two further wrinkles worth documenting whichever way this goes:

- absence of a path is reported by *raising*, in two different wordings, rather than by
  returning an empty result
- a pair that cannot be resolved is indistinguishable from a pair that is genuinely
  unconnected, so a caller has to decide whether to assume connected (over-predict) or
  dropped (under-predict)

**Suggested shape:** have the column-lineage response carry the downstream field, as the
GraphQL API already can.
