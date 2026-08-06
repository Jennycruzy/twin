"""Scenario files: the declared fault Twin simulates and then executes for real.

A scenario is deliberately small and declarative. It names one fault against one asset, and
nothing about what is expected to happen — the prediction is Twin's job, and a scenario file
that carried an expected blast radius would let the answer be written next to the question.

Fault kinds are validated against the ones Twin can actually execute in a shadow warehouse.
Declaring a fault that only the simulator understands would produce a prediction nothing
could grade, which is the failure mode this whole project exists to avoid.
"""

from __future__ import annotations

import datetime as dt
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from twin.faults import KINDS, SOURCE_LAYERS, FaultKind, is_source_layer, kind as fault_kind

# A scenario's name becomes part of the shadow schema it executes in, so it is restricted to
# characters that are an identifier in PostgreSQL without quoting or escaping. Validating it
# here means the execution layer never has to reason about a hostile name.
_NAME = re.compile(r"^[a-z][a-z0-9_]{2,48}$")

# How much recent data a staleness fault withholds, when the scenario does not say.
_DEFAULT_WITHHOLD_DAYS = 3

DROP_COLUMN = "drop_column"

_DEFAULT_FAULT_TIME = "04:12"


class ScenarioError(ValueError):
    """A scenario file is malformed, or declares something Twin cannot execute."""


@dataclass(frozen=True)
class Fault:
    """What goes wrong, to what, and when."""

    kind: str
    asset: str
    column: str | None
    at: dt.time
    withhold_days: int = _DEFAULT_WITHHOLD_DAYS

    @property
    def definition(self) -> FaultKind:
        return fault_kind(self.kind)

    def describe(self) -> str:
        target = f"{self.asset}.{self.column}" if self.column else self.asset
        if self.kind == "stop_new_rows":
            return f"{self.asset} stops receiving rows for {self.withhold_days} days"
        return f"{target}: {self.definition.summary}"


@dataclass(frozen=True)
class Scenario:
    name: str
    title: str
    description: str
    fault: Fault
    path: Path


def _require(payload: dict[str, Any], key: str, where: str) -> Any:
    if key not in payload or payload[key] in (None, ""):
        raise ScenarioError(f"{where} is missing required field {key!r}")
    return payload[key]


def _parse_time(value: Any) -> dt.time:
    try:
        hours, minutes = str(value).split(":")
        return dt.time(int(hours), int(minutes))
    except (ValueError, TypeError) as exc:
        raise ScenarioError(f"fault.at must look like HH:MM, got {value!r}") from exc


def load_scenario(path: Path) -> Scenario:
    """Read and validate a scenario file."""
    try:
        payload = yaml.safe_load(path.read_text()) or {}
    except yaml.YAMLError as exc:
        raise ScenarioError(f"{path} is not valid YAML: {exc}") from exc
    if not isinstance(payload, dict):
        raise ScenarioError(f"{path} must contain a mapping")

    fault_payload = _require(payload, "fault", str(path))
    if not isinstance(fault_payload, dict):
        raise ScenarioError(f"{path}: fault must be a mapping")

    kind = _require(fault_payload, "kind", f"{path}: fault")
    if kind not in KINDS:
        raise ScenarioError(
            f"{path}: fault kind {kind!r} cannot be executed. "
            f"Stage 4 knows how to run: {', '.join(sorted(KINDS))}"
        )

    asset = str(_require(fault_payload, "asset", f"{path}: fault"))
    if is_source_layer(asset):
        raise ScenarioError(
            f"{path}: {asset} is in a raw source layer ({', '.join(SOURCE_LAYERS)}), and "
            "Stage 4 cannot execute a fault there. dbt resolves source() through sources.yml, "
            "whose schema is fixed, so the shadow copy would be built and never read: the "
            "models would build from production, nothing would break for the reason claimed, "
            "and the scorecard would grade a fault that never landed. Name a dbt model instead."
        )

    definition = fault_kind(str(kind))
    column = fault_payload.get("column")
    if definition.needs_column and not column:
        raise ScenarioError(
            f"{path}: a {kind} fault must name {definition.column_role}"
        )

    name = str(_require(payload, "name", str(path)))
    if not _NAME.match(name):
        raise ScenarioError(
            f"{path}: scenario name {name!r} must be lowercase letters, digits and "
            "underscores — it becomes part of the shadow schema this fault executes in"
        )

    return Scenario(
        name=name,
        title=str(payload.get("title") or payload.get("name")),
        description=str(payload.get("description") or "").strip(),
        fault=Fault(
            kind=str(kind),
            asset=asset,
            column=str(column) if column else None,
            at=_parse_time(fault_payload.get("at", _DEFAULT_FAULT_TIME)),
            withhold_days=int(fault_payload.get("withhold_days", _DEFAULT_WITHHOLD_DAYS)),
        ),
        path=path,
    )
