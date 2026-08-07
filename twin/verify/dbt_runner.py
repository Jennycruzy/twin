"""Rebuild the downstream estate against the faulted shadow copy, for real.

This is the part that makes verification evidence rather than argument. dbt runs the actual
project — the same models, the same tests, the same SQL that built the estate — against a
warehouse where one column genuinely no longer exists. What fails, fails because PostgreSQL
refused it, and the error text Twin reports is the error PostgreSQL returned.

Three details matter.

**dbt connects as ``twin_shadow``.** The ``shadow`` target in ``profiles.yml`` uses the role
that owns nothing in the estate, so even a mistake in model selection cannot write outside
the shadow schema — the database refuses it.

**Every model lands in one schema.** ``generate_schema_name`` collapses the layer schemas
onto the shadow schema for this target, so a rebuilt model and the passthrough view it reads
are found the same way, and teardown is a single ``DROP SCHEMA``.

**Artifacts are written somewhere else.** dbt's default target directory holds the estate's
production manifest and catalog, which the estate ingestion reads. A shadow build writes to
its own directory so a verification run cannot corrupt the metadata the catalog was built
from.
"""

from __future__ import annotations

import json
import os
import subprocess
from dataclasses import dataclass
from pathlib import Path

from twin.faults import is_source_layer
from twin.read.model import KIND_DATASET, EstateGraph
from twin.verify.shadow import ShadowEstate, model_name

# dbt statuses. Models that error failed on their own; models that are skipped were never
# attempted because something they read failed. Both mean the asset is not there after the
# refresh, which is what "broken" means to a consumer.
_ERROR = "error"
_SKIPPED = "skipped"
_FAIL = "fail"

_MODEL_PREFIX = "model."
_TEST_PREFIX = "test."


@dataclass(frozen=True)
class NodeResult:
    """What happened to one dbt node."""

    node: str
    key: str | None
    status: str
    message: str

    @property
    def is_model(self) -> bool:
        return self.node.startswith(_MODEL_PREFIX)

    @property
    def is_test(self) -> bool:
        return self.node.startswith(_TEST_PREFIX)

    @property
    def broke(self) -> bool:
        return self.status in (_ERROR, _SKIPPED)


@dataclass(frozen=True)
class BuildOutcome:
    """The result of rebuilding the downstream estate against the fault."""

    command: tuple[str, ...]
    returncode: int
    results: tuple[NodeResult, ...]
    stderr: str

    @property
    def models(self) -> tuple[NodeResult, ...]:
        return tuple(r for r in self.results if r.is_model)

    @property
    def broken_models(self) -> tuple[str, ...]:
        return tuple(sorted({r.key for r in self.models if r.broke and r.key}))

    @property
    def failed_tests(self) -> tuple[NodeResult, ...]:
        return tuple(r for r in self.results if r.is_test and r.status in (_FAIL, _ERROR))

    def failure_for(self, key: str) -> NodeResult | None:
        return next((r for r in self.models if r.key == key and r.broke), None)


def _name_index(graph: EstateGraph) -> dict[str, str]:
    """dbt model name -> asset key."""
    return {model_name(a.key): a.key for a in graph.of_kind(KIND_DATASET)}


def _selector(key: str, source_layers: tuple[str, ...] | None = None) -> str:
    """The dbt selector that names an asset and everything downstream of it."""
    is_source = is_source_layer(key) if source_layers is None else key.split(".")[0] in source_layers
    if is_source:
        # dbt does not match a raw source by its warehouse key alone. The source: selector is
        # what expands from a source node into the models that read it.
        return f"source:{key}"
    return model_name(key)


def _parse_results(path: Path, index: dict[str, str]) -> tuple[NodeResult, ...]:
    if not path.exists():
        return ()
    payload = json.loads(path.read_text())
    results = []
    for result in payload.get("results", []):
        node = result.get("unique_id", "")
        name = node.split(".")[-1]
        results.append(
            NodeResult(
                node=node,
                key=index.get(name),
                status=str(result.get("status", "")),
                message=str(result.get("message") or "").strip(),
            )
        )
    return tuple(sorted(results, key=lambda r: r.node))


def _invoke(
    command: tuple[str, ...],
    graph: EstateGraph,
    layout: ShadowEstate,
    project_dir: Path,
    artifacts_dir: Path,
    dbt_target: str = "shadow",
    source_env_vars: tuple[str, ...] = (
        "TWIN_SHADOW_RAW_PG_SCHEMA",
        "TWIN_SHADOW_RAW_EVENTS_SCHEMA",
    ),
) -> BuildOutcome:
    environment = dict(os.environ)
    # The tools image has a legacy commerce default for DBT_PROFILES_DIR. A target's
    # project and profile are one contract; letting the container default leak here makes
    # dbt fail before it runs and can turn passthrough views into false "identical" probes.
    environment["DBT_PROFILES_DIR"] = str(project_dir.resolve())
    environment["TWIN_SHADOW_SCHEMA"] = layout.schema
    # dbt's source() calls must resolve into the same disposable namespace as rebuilt models.
    # The source declarations default back to raw_pg/raw_events for normal dev builds.
    for variable in source_env_vars:
        environment[variable] = layout.schema
    completed = subprocess.run(
        command,
        cwd=project_dir,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )
    return BuildOutcome(
        command=command,
        returncode=completed.returncode,
        results=_parse_results(artifacts_dir / "run_results.json", _name_index(graph)),
        stderr=completed.stderr.strip(),
    )


def probe_model(
    graph: EstateGraph,
    layout: ShadowEstate,
    project_dir: Path,
    artifacts_dir: Path,
    key: str,
    dry_run: bool = False,
    dbt_target: str = "shadow",
    source_env_vars: tuple[str, ...] = (
        "TWIN_SHADOW_RAW_PG_SCHEMA",
        "TWIN_SHADOW_RAW_EVENTS_SCHEMA",
    ),
) -> BuildOutcome:
    """Build one model on its own, with every other model healthy.

    This is the experiment that can falsify the prediction. Everything this model reads is a
    passthrough onto production except the faulted asset, so if the build fails it failed
    because of the fault itself rather than because something upstream was missing. A model
    that Twin said would break and that builds cleanly here is a false alarm with nowhere to
    hide, and one that breaks without being predicted is a miss.

    Tests are excluded deliberately. A failing test means the data is wrong, which is a
    different and softer finding than a model that cannot be built at all; mixing the two
    would let a loosened test threshold flatter the score.
    """
    command = (
        "dbt",
        "run",
        "--target",
        dbt_target,
        "--select",
        model_name(key),
        "--target-path",
        str(artifacts_dir),
        "--no-use-colors",
    )
    if dry_run:
        return BuildOutcome(command=command, returncode=0, results=(), stderr="")
    return _invoke(
        command,
        graph,
        layout,
        project_dir,
        artifacts_dir,
        dbt_target,
        source_env_vars,
    )


def rebuild_downstream(
    graph: EstateGraph,
    layout: ShadowEstate,
    project_dir: Path,
    artifacts_dir: Path,
    dry_run: bool = False,
    source_layers: tuple[str, ...] = (),
    dbt_target: str = "shadow",
    source_env_vars: tuple[str, ...] = (
        "TWIN_SHADOW_RAW_PG_SCHEMA",
        "TWIN_SHADOW_RAW_EVENTS_SCHEMA",
    ),
) -> BuildOutcome:
    """Run the real dbt project against the shadow estate.

    The selection is ``<faulted model>+`` minus the faulted model itself: everything
    downstream is rebuilt, and the faulted copy is left exactly as the fault made it. Were it
    included, dbt would rebuild it from the real source and quietly undo the fault.
    """
    selector = _selector(layout.faulted, source_layers or None)
    command = (
        "dbt",
        "build",
        "--target",
        dbt_target,
        "--select",
        f"{selector}+",
        "--exclude",
        selector,
        "--target-path",
        str(artifacts_dir),
        "--no-use-colors",
    )
    if dry_run:
        return BuildOutcome(command=command, returncode=0, results=(), stderr="")
    return _invoke(
        command,
        graph,
        layout,
        project_dir,
        artifacts_dir,
        dbt_target,
        source_env_vars,
    )
