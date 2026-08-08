"""Translate a measured blast radius into an explicit, configurable cost estimate."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Mapping

import yaml

from twin.read.model import KIND_DASHBOARD, EstateGraph

CONFIG = Path("config/cost_model.yaml")


@dataclass(frozen=True)
class CostModel:
    """Illustrative assumptions, kept separate from measured fragility."""

    path: Path
    engineer_hours_per_broken_model: float
    consumer_hours_per_affected_dashboard: float
    engineer_hourly_rate_usd: float
    consumer_hourly_rate_usd: float

    @classmethod
    def load(cls, path: Path = CONFIG) -> "CostModel":
        payload = yaml.safe_load(path.read_text()) or {}
        try:
            values = {
                name: float(payload[name])
                for name in (
                    "engineer_hours_per_broken_model",
                    "consumer_hours_per_affected_dashboard",
                    "engineer_hourly_rate_usd",
                    "consumer_hourly_rate_usd",
                )
            }
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError(f"{path}: cost assumptions must be numeric and complete") from exc
        if any(value < 0 for value in values.values()):
            raise ValueError(f"{path}: cost assumptions cannot be negative")
        return cls(path=path, **values)

    def assumptions(self) -> Mapping[str, float]:
        return {
            "engineer_hours_per_broken_model": self.engineer_hours_per_broken_model,
            "consumer_hours_per_affected_dashboard": self.consumer_hours_per_affected_dashboard,
            "engineer_hourly_rate_usd": self.engineer_hourly_rate_usd,
            "consumer_hourly_rate_usd": self.consumer_hourly_rate_usd,
        }

    def assumptions_line(self) -> str:
        return (
            f"under the assumptions in {self.path}: "
            f"{self.engineer_hours_per_broken_model:g} engineer-hours per broken model at "
            f"${self.engineer_hourly_rate_usd:,.2f}/hour and "
            f"{self.consumer_hours_per_affected_dashboard:g} consumer-hours per affected "
            f"dashboard at ${self.consumer_hourly_rate_usd:,.2f}/hour"
        )

    def estimate(
        self,
        graph: EstateGraph,
        datasets_lost: Iterable[str],
        consumers_lost: Iterable[str],
    ) -> float:
        """Estimate cost from the measured lost datasets and affected dashboards."""
        broken_models = len(tuple(datasets_lost))
        dashboards = sum(
            1
            for key in consumers_lost
            if graph.has(key) and graph.asset(key).kind == KIND_DASHBOARD
        )
        return round(
            broken_models * self.engineer_hours_per_broken_model * self.engineer_hourly_rate_usd
            + dashboards
            * self.consumer_hours_per_affected_dashboard
            * self.consumer_hourly_rate_usd,
            2,
        )
