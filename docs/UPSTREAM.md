# Upstream findings

Five things Twin found by building on DataHub's agent-facing interfaces, written as issue
drafts ready to file. All five were discovered by using the interface for real work rather
than by reading its documentation, and each carries a reproduction rather than an assertion.

Filing status: **not yet filed.** These are drafts.

---

## 1. MCP server exposes no write tool

**Repo:** `datahub-project/mcp-server-datahub`
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

## 3. Incidents written through the SDK cannot be hydrated by GraphQL search

**Repo:** `datahub-project/datahub`
**Type:** bug
**Version:** v1.7.0 (GMS, OpenSearch backend)

An incident created by emitting an `incidentInfo` aspect is stored correctly — `exists()`
returns true and `get_aspect` returns the full aspect with its type, status, entities, title
and description. But it cannot be listed. `get_urns_by_filter(entity_types=["incident"])`
finds the documents in the index and then fails to resolve them:

```
The field at path '/scrollAcrossEntities/searchResults[0]/entity' was declared as
a non null type, but the code involved in retrieving data has wrongly returned a
null value. ... NullValueInNonNullableField
```

Every result fails this way, so the whole query errors rather than degrading — one
unhydratable incident makes the incident list unusable.

The obvious guess is a missing `status` aspect, since that is what marks an entity live for
search elsewhere. That is not available here: emitting `status` for an incident is rejected
with `Unknown aspect status for entity incident` (422).

**Reproduction:**

```python
graph.emit(MetadataChangeProposalWrapper(entityUrn=INCIDENT_URN, aspect=IncidentInfoClass(...)))
assert graph.exists(INCIDENT_URN)                       # True
graph.get_aspect(INCIDENT_URN, IncidentInfoClass)       # returns the aspect
graph.get_urns_by_filter(entity_types=["incident"])     # GraphError
```

**Why it matters:** a tool that raises incidents cannot then find its own incidents to resolve
them. Twin works around it by making incident URNs deterministic — derived from the scenario
name and the asset key — so the set can be reconstructed without asking the catalog. That
works, but it means any tool raising incidents programmatically has to invent the same
workaround, and a tool that raised them with random ids could not clean up after itself at
all.

**Suggested fix:** whatever aspect GraphQL requires to hydrate an incident should either be
written automatically on creation, or be writable through the SDK, or the search should skip
unhydratable results rather than failing the query.

---

## 4. MCP exposes no usage statistics

**Repo:** `datahub-project/mcp-server-datahub`
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

**Repo:** `datahub-project/mcp-server-datahub`
**Type:** feature gap / performance

Asking which assets consume a column answers with the downstream *datasets*, not the
downstream *fields*. The landing column is obtainable only by asking
`get_lineage_paths_between` whether one specific pair is connected — one call per candidate
pair, thousands per estate read.

For a 66-dataset estate this is roughly four thousand round trips and turns a read into
twelve minutes, almost all of it waiting on GMS. Twin pays that cost because damage cannot be
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
