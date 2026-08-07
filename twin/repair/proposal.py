"""Turn a measured catalog gap into a reviewable source-contract patch.

Twin can prove that a source column is used without being represented by column lineage.
The safe response is a proposal a data-platform owner can review and apply, followed by a
fresh ingestion and verification run. This module never writes to DataHub and never edits the
estate in place; its output is deliberately ordinary Markdown and a standard unified diff.
"""

from __future__ import annotations

import difflib
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from twin.context import ContextConfidence, confidence
from twin.provenance import commit, is_dirty
from twin.read.model import KIND_DATASET, EstateGraph
from twin.simulate.scenario import Scenario
from twin.target import TwinTarget


class RepairError(ValueError):
    """The requested gap cannot be turned into a safe, specific proposal."""


@dataclass(frozen=True)
class RepairProposal:
    """A source-contract change with the evidence and checks needed for review."""

    target: str
    source_key: str
    column: str
    source_file: Path
    consumers: tuple[str, ...]
    graph_fingerprint: str
    confidence: ContextConfidence
    patch: str
    markdown: str

    @property
    def stem(self) -> str:
        return f"{self.target}-{self.source_key.replace('.', '-')}-{self.column}"


def build_proposal(
    graph: EstateGraph,
    target: TwinTarget,
    scenario: Scenario,
) -> RepairProposal:
    """Build a proposal for a source column with table lineage but no field lineage."""
    source_key = scenario.fault.asset
    column = scenario.fault.column
    if not column:
        raise RepairError("a catalog repair needs a column-level scenario")
    if not graph.has(source_key):
        raise RepairError(f"{source_key} is not present in graph {graph.fingerprint}")

    asset = graph.asset(source_key)
    if asset.kind != KIND_DATASET or source_key.split(".", 1)[0] not in target.source_layers:
        raise RepairError(
            f"{source_key} is not a landed source for target {target.name}; "
            "repair proposals are restricted to source contracts"
        )
    if not any(item.name == column for item in asset.columns):
        raise RepairError(f"{source_key}.{column} is not present in the catalog schema")
    if graph.columns_consuming(source_key, column):
        raise RepairError(
            f"{source_key}.{column} already has column lineage; no catalog gap was found"
        )

    source_file = _source_file(target).resolve()
    source_name, table_name = source_key.split(".", 1)
    source_document = _load_yaml(source_file)
    table = _source_table(source_document, source_name, table_name)
    declared = {
        str(item.get("name"))
        for item in table.get("columns", [])
        if isinstance(item, dict) and item.get("name")
    }
    if column in declared:
        raise RepairError(
            f"{source_file}: {source_name}.{table_name}.{column} is already declared; "
            "the missing evidence is downstream of the dbt source contract"
        )

    consumers = tuple(sorted(graph.downstream(source_key)))
    if not consumers:
        raise RepairError(f"{source_key}.{column} has neither column lineage nor consumers")

    patched = _add_source_column(source_file.read_text(), table_name, column)
    relative_file = _display_path(source_file)
    patch = "".join(
        difflib.unified_diff(
            source_file.read_text().splitlines(keepends=True),
            patched.splitlines(keepends=True),
            fromfile=f"a/{relative_file}",
            tofile=f"b/{relative_file}",
        )
    )
    context = confidence(graph, source_key)
    return RepairProposal(
        target=target.name,
        source_key=source_key,
        column=column,
        source_file=source_file,
        consumers=consumers,
        graph_fingerprint=graph.fingerprint,
        confidence=context,
        patch=patch,
        markdown=_markdown(
            target,
            source_key,
            column,
            source_file,
            consumers,
            graph,
            context,
            scenario,
            patch,
        ),
    )


def write_proposal(proposal: RepairProposal, output_dir: Path) -> tuple[Path, Path]:
    """Write Markdown and unified-diff artifacts and return their paths."""
    output_dir.mkdir(parents=True, exist_ok=True)
    markdown_path = output_dir / f"{proposal.stem}.md"
    patch_path = output_dir / f"{proposal.stem}.patch"
    markdown_path.write_text(proposal.markdown)
    patch_path.write_text(proposal.patch)
    return markdown_path, patch_path


def _source_file(target: TwinTarget) -> Path:
    candidates = sorted(target.dbt_project.rglob("sources.yml"))
    if len(candidates) != 1:
        raise RepairError(
            f"expected exactly one sources.yml below {target.dbt_project}, found {len(candidates)}"
        )
    return candidates[0]


def _load_yaml(path: Path) -> dict[str, Any]:
    try:
        value = yaml.safe_load(path.read_text()) or {}
    except yaml.YAMLError as exc:
        raise RepairError(f"cannot parse {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise RepairError(f"{path} must contain a mapping")
    return value


def _source_table(document: dict[str, Any], source_name: str, table_name: str) -> dict[str, Any]:
    for source in document.get("sources", []):
        if not isinstance(source, dict) or source.get("name") != source_name:
            continue
        for table in source.get("tables", []):
            if isinstance(table, dict) and table.get("name") == table_name:
                return table
    raise RepairError(f"source declaration {source_name}.{table_name} was not found")


_ITEM = re.compile(r"^(?P<indent>\s*)-\s+name:\s*(?P<name>[^#\s]+)")


def _add_source_column(text: str, table_name: str, column: str) -> str:
    """Insert one column while preserving the hand-authored YAML layout."""
    lines = text.splitlines(keepends=True)
    table_index = next(
        (
            index
            for index, line in enumerate(lines)
            if (match := _ITEM.match(line))
            and len(match.group("indent")) == 6
            and match.group("name") == table_name
        ),
        None,
    )
    if table_index is None:
        raise RepairError(f"could not locate the {table_name!r} table in sources.yml")

    table_end = len(lines)
    for index in range(table_index + 1, len(lines)):
        match = _ITEM.match(lines[index])
        if match and len(match.group("indent")) == 6:
            table_end = index
            break
        if match and len(match.group("indent")) == 2:
            table_end = index
            break

    columns_index = next(
        (
            index
            for index in range(table_index + 1, table_end)
            if lines[index].startswith("        columns:")
        ),
        None,
    )
    if columns_index is None:
        insertion = [
            "        columns:\n",
            f"          - name: {column}\n",
        ]
        lines[table_end:table_end] = insertion
        return "".join(lines)

    column_end = table_end
    for index in range(columns_index + 1, table_end):
        stripped = lines[index].strip()
        if stripped and len(lines[index]) - len(lines[index].lstrip()) <= 8:
            column_end = index
            break
    lines[column_end:column_end] = [f"          - name: {column}\n"]
    return "".join(lines)


def _markdown(
    target: TwinTarget,
    source_key: str,
    column: str,
    source_file: Path,
    consumers: tuple[str, ...],
    graph: EstateGraph,
    context: ContextConfidence,
    scenario: Scenario,
    patch: str,
) -> str:
    provenance = commit() or "unknown"
    dirty = is_dirty()
    dirty_label = "dirty working tree" if dirty else "clean working tree"
    consumer_lines = "\n".join(f"- `{consumer}`" for consumer in consumers)
    relative_file = _display_path(source_file)
    return f"""# Catalog repair proposal: `{source_key}.{column}`

This proposal is generated from a measured catalog gap. It is intentionally a normal patch
for review; Twin does not apply it to the warehouse or open a remote pull request.

## Finding

The `{target.name}` graph `{graph.fingerprint}` contains table lineage from `{source_key}`
but no column lineage for `{source_key}.{column}`. The column exists in the observed warehouse
schema and the fault scenario `{scenario.name}` exercises it. The missing field-level context
forces Twin to use a conservative table-grain prediction.

Current context confidence for `{source_key}` is **{context.score:.2f} ({context.state})**.
The proposal changes `{relative_file}` by declaring the source column explicitly, giving dbt
and its metadata ingestion a stable source contract from which column lineage can be emitted.

Direct table consumers identified in the graph:

{consumer_lines}

## Proposed change

```diff
{patch.rstrip()}
```

## Validation

1. Apply the patch and run `make estate-build TARGET={target.name}`.
2. Re-ingest the dbt metadata with `make estate-ingest TARGET={target.name}`, then rebuild the
   target graph with `make read TARGET={target.name}`.
3. Confirm `{source_key}.{column}` has a column edge to the expected landing fields.
4. Re-run `make run TARGET={target.name} SCENARIO={scenario.path}` and compare the scorecard with the
   recorded `{scenario.name}` result. A better precision score is evidence; an unchanged score
   is also a valid result if the catalog ingestion does not derive field lineage from source
   declarations.
5. Run `make gate` and keep this proposal only if the graph fingerprint and scenario evidence
   explain the resulting change.

## Review notes

- The change is metadata-only: it does not alter source data or model SQL.
- The column name is copied from the warehouse schema; no semantic value is invented here.
- The proposal was generated by commit `{provenance}` ({dirty_label}) from graph
  `{graph.fingerprint}`. Regenerate it after applying the change rather than editing the
  artifact by hand.
"""


def _display_path(path: Path) -> Path:
    root = Path.cwd().resolve()
    try:
        return path.relative_to(root)
    except ValueError:
        return path
