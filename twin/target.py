"""Runtime configuration for one independently scoped data estate.

The Twin engine operates on an :class:`~twin.read.model.EstateGraph`; paths, schemas and
catalog namespaces belong to the estate that produced that graph.  Keeping that distinction
explicit is what lets the same scorer and verifier be evaluated against a second domain
without copying the first demo and quietly tuning it again.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any
import os
import subprocess
import sys

import yaml


@dataclass(frozen=True)
class CatalogScope:
    """How one target is separated from other estates in the same DataHub instance."""

    dataset_platform_instance: str | None
    dataset_path_prefix: str
    non_dataset_urn_prefixes: tuple[str, ...] = ()

    def accepts(self, entity: dict[str, Any]) -> bool:
        """Return whether a catalog entity belongs to this target."""
        urn = str(entity.get("urn", ""))
        if ":li:dataset:(" in urn:
            body = urn.split(":li:dataset:(", 1)[1].rstrip(")")
            # The platform-instance form nests a comma inside the first URN component,
            # so split from the right where dataset name and environment are stable.
            parts = body.rsplit(",", 2)
            if len(parts) != 3 or not parts[1].startswith(self.dataset_path_prefix):
                return False
            if parts[0].startswith("urn:li:dataPlatformInstance:("):
                embedded = parts[0].rsplit(",", 1)[-1].rstrip(")")
                if embedded != self.dataset_platform_instance:
                    return False
            # DataHub's platform-instance helper encodes the instance in the dataset
            # name (for example operations.warehouse.ops_marts.model), while the first
            # URN component remains urn:li:dataPlatform:postgres/dbt. The explicit path
            # prefix is therefore the stable discriminator; the instance field documents
            # why that prefix exists and prevents a target config from omitting it.
            if self.dataset_platform_instance is not None and not parts[1].startswith(
                f"{self.dataset_platform_instance}."
            ):
                return False
            return True
        # An empty prefix list is the explicit legacy scope: all non-dataset entities belong
        # to the commerce target. Other targets must name their own entity namespace.
        return not self.non_dataset_urn_prefixes or any(
            urn.startswith(prefix) for prefix in self.non_dataset_urn_prefixes
        )


@dataclass(frozen=True)
class TwinTarget:
    """All estate-specific inputs needed by the generic Twin commands."""

    name: str
    catalog: CatalogScope
    dbt_project: Path
    workload: Path
    scenario_dir: Path
    cache_dir: Path
    seed_module: str
    metadata_module: str
    workload_module: str
    verify_module: str
    postgres_recipe: Path
    dbt_recipe: Path
    source_layers: frozenset[str]
    model_schemas: frozenset[str]
    source_env_vars: tuple[str, ...] = ()
    dbt_target: str = "shadow"
    shadow_prefix: str = "twin_shadow_"
    # The scenario the nightly runs for this estate, and so also the name of the standing
    # capture that stands in as verification evidence when no dated nightly capture exists.
    # Estate-specific, so it belongs in the estate's config rather than in the nightly script
    # or the renderer.
    nightly_scenario: Path | None = None

    @property
    def verification_example(self) -> Path | None:
        if self.nightly_scenario is None:
            return None
        return Path("examples/verification") / f"{self.nightly_scenario.stem}.txt"


def _path(value: str, root: Path) -> Path:
    path = Path(value)
    return path if path.is_absolute() else root / path


def load_target(name: str | None = None, config_dir: Path = Path("targets")) -> TwinTarget:
    """Load a target from ``targets/<name>.yml``.

    The environment override is intentionally small: it makes cron and Docker entry points
    deterministic while keeping every target's paths and catalog scope reviewable in git.
    """
    selected = name or os.environ.get("TWIN_TARGET", "commerce")
    path = config_dir / f"{selected}.yml"
    payload = yaml.safe_load(path.read_text()) or {}
    catalog = payload.get("catalog") or {}
    runtime = payload.get("runtime") or {}
    return TwinTarget(
        name=str(payload.get("name", selected)),
        catalog=CatalogScope(
            dataset_platform_instance=(
                str(catalog["dataset_platform_instance"])
                if catalog.get("dataset_platform_instance") is not None else None
            ),
            dataset_path_prefix=str(catalog["dataset_path_prefix"]),
            non_dataset_urn_prefixes=tuple(catalog.get("non_dataset_urn_prefixes") or ()),
        ),
        dbt_project=_path(str(runtime["dbt_project"]), path.parent.parent),
        workload=_path(str(runtime["workload"]), path.parent.parent),
        scenario_dir=_path(str(runtime.get("scenario_dir", "scenarios")), path.parent.parent),
        cache_dir=_path(str(runtime.get("cache_dir", f".twin/{selected}")), path.parent.parent),
        nightly_scenario=(
            _path(str(runtime["nightly_scenario"]), path.parent.parent)
            if runtime.get("nightly_scenario")
            else None
        ),
        seed_module=str(runtime["seed_module"]),
        metadata_module=str(runtime["metadata_module"]),
        workload_module=str(runtime["workload_module"]),
        verify_module=str(runtime["verify_module"]),
        postgres_recipe=_path(str(runtime["postgres_recipe"]), path.parent.parent),
        dbt_recipe=_path(str(runtime["dbt_recipe"]), path.parent.parent),
        source_layers=frozenset(runtime.get("source_layers") or ()),
        model_schemas=frozenset(runtime.get("model_schemas") or ()),
        source_env_vars=tuple(runtime.get("source_env_vars") or ()),
        dbt_target=str(runtime.get("dbt_target", "shadow")),
        shadow_prefix=str(runtime.get("shadow_prefix", "twin_shadow_")),
    )


def _run(command: list[str], target: TwinTarget, cwd: Path | None = None) -> None:
    """Run one target adapter command with the target's dbt project as its profile root."""
    environment = os.environ.copy()
    environment["DBT_PROFILES_DIR"] = str(target.dbt_project.resolve())
    subprocess.run(command, cwd=(cwd or Path.cwd()).resolve(), env=environment, check=True)


def run_target_command(command: str, target: TwinTarget) -> int:
    """Build one target through its declared adapter, without shell interpolation."""
    if command in {"seed", "estate"}:
        _run([sys.executable, "-m", target.seed_module], target)
    if command == "seed":
        return 0

    if command in {"build", "estate"}:
        _run(["dbt", "build", "--target", "dev"], target, target.dbt_project)
        _run(["dbt", "docs", "generate", "--target", "dev", "--no-compile"], target, target.dbt_project)
    if command == "build":
        return 0

    if command in {"ingest", "estate"}:
        _run(["datahub", "ingest", "-c", str(target.postgres_recipe)], target)
        _run(["datahub", "ingest", "-c", str(target.dbt_recipe)], target)
        _run([sys.executable, "-m", target.metadata_module], target)
    if command == "ingest":
        return 0

    if command in {"workload", "estate"}:
        _run([sys.executable, "-m", target.workload_module], target)
    if command == "workload":
        return 0

    if command == "estate":
        return 0
    if command == "verify":
        _run([sys.executable, "-m", target.verify_module], target)
        return 0
    if command == "scenarios":
        scenarios = sorted(target.scenario_dir.glob("*.yml"))
        if not scenarios:
            raise ValueError(f"target {target.name!r} has no scenarios in {target.scenario_dir}")
        for scenario in scenarios:
            _run([sys.executable, "-m", "twin.run", "--target", target.name, str(scenario)], target)
        return 0
    raise ValueError(f"unknown target command: {command}")


def main(argv: list[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(description="Run a declared Twin estate adapter.")
    parser.add_argument("command", choices=("seed", "build", "ingest", "workload", "estate", "verify", "scenarios"))
    parser.add_argument("--target", default=None)
    args = parser.parse_args(argv)
    return run_target_command(args.command, load_target(args.target))


if __name__ == "__main__":
    raise SystemExit(main())
