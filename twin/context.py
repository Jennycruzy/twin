"""Evidence-based context confidence and deterministic experiment selection.

Fragility asks "what would hurt?" Context confidence asks "how much of the catalog can an
agent safely rely on before acting?" It is deliberately not an LLM judgement: every point
comes from graph evidence that a reviewer can inspect, and the campaign chooses experiments
with a stable formula and a stable tie-break.
"""

from __future__ import annotations

import datetime as dt
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Mapping

from twin.read.model import KIND_DATASET, EstateGraph
from twin.score.usage import Usage
from twin.simulate.scenario import Scenario


@dataclass(frozen=True)
class ContextConfidence:
    key: str
    score: float
    state: str
    lineage: float
    schema: float
    operational: float
    ownership: float
    usage: float
    verification: float = 0.0


def _state(score: float) -> str:
    if score >= 0.8:
        return "high"
    if score >= 0.5:
        return "partial"
    return "low"


def confidence(
    graph: EstateGraph,
    key: str,
    usage: Mapping[str, Usage] | None = None,
    verified: Iterable[str] = (),
) -> ContextConfidence:
    """Measure whether one asset has enough context for an automated action.

    The components are intentionally observable: lineage edges, catalog columns, five
    operational fields, owners, measured usage and a recorded real shadow experiment. A
    missing value lowers confidence; it is never silently treated as a positive claim.
    """
    asset = graph.asset(key)
    upstream = graph.upstream(key)
    downstream = graph.downstream(key)
    lineage = 1.0 if upstream or downstream or asset.kind != KIND_DATASET else 0.0
    schema = min(len(asset.columns) / 8.0, 1.0) if asset.kind == KIND_DATASET else 1.0
    fields = (asset.team, asset.refresh_cadence, asset.sla_hours, asset.criticality_tier, asset.replicated)
    operational = sum(value is not None for value in fields) / len(fields)
    ownership = 1.0 if asset.owners else 0.0
    usage_score = 1.0 if usage and key in usage and usage[key].queries > 0 else 0.0
    verification_score = 1.0 if key in set(verified) else 0.0
    score = round(
        0.25 * lineage + 0.20 * schema + 0.25 * operational +
        0.15 * ownership + 0.10 * usage_score + 0.05 * verification_score,
        4,
    )
    return ContextConfidence(
        key=key, score=score, state=_state(score), lineage=round(lineage, 4),
        schema=round(schema, 4), operational=round(operational, 4),
        ownership=round(ownership, 4), usage=round(usage_score, 4),
        verification=round(verification_score, 4),
    )


def all_confidence(
    graph: EstateGraph, usage: Mapping[str, Usage] | None = None, verified: Iterable[str] = ()
) -> tuple[ContextConfidence, ...]:
    return tuple(confidence(graph, asset.key, usage, verified) for asset in graph.assets)


def evidence_path(cache_dir: Path) -> Path:
    return cache_dir / "campaign-evidence.jsonl"


def verified_assets(path: Path, fingerprint: str) -> set[str]:
    if not path.exists():
        return set()
    result: set[str] = set()
    for line in path.read_text().splitlines():
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if row.get("fingerprint") == fingerprint:
            result.add(str(row.get("fault_asset", "")))
    return result


def record_evidence(
    path: Path,
    target: str,
    graph: EstateGraph,
    scenario: Scenario,
    predicted: Iterable[str],
    observed: Mapping[str, object],
    consumer_failures: int,
) -> None:
    """Append the result of a real shadow run for the deterministic campaign to consume."""
    row = {
        "recorded_at": dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat(),
        "target": target,
        "fingerprint": graph.fingerprint,
        "scenario": scenario.name,
        "fault_asset": scenario.fault.asset,
        "predicted": sorted(predicted),
        "observed": {
            key: (
                {"impact": getattr(value, "impact", None), "detail": getattr(value, "detail", "")}
                if not isinstance(value, dict) else value
            )
            for key, value in observed.items()
        },
        "consumer_failures": consumer_failures,
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a") as handle:
        handle.write(json.dumps(row, sort_keys=True) + "\n")


@dataclass(frozen=True)
class Candidate:
    scenario: Scenario
    priority: float
    impact: float
    context_gap: float
    novelty: float
    confidence: ContextConfidence


def rank_candidates(
    graph: EstateGraph,
    scenarios: Iterable[Scenario],
    fragility: Mapping[str, float],
    usage: Mapping[str, Usage],
    evidence: Path,
) -> tuple[Candidate, ...]:
    """Rank real experiments by impact, context gap, and stale verification evidence."""
    seen: set[object] = set()
    if evidence.exists():
        for line in evidence.read_text().splitlines():
            row = _json(line)
            if row and row.get("fingerprint") == graph.fingerprint:
                seen.add(row.get("scenario"))
    verified = verified_assets(evidence, graph.fingerprint)
    result = []
    estate_size = max(len(graph.assets), 1)
    for scenario in scenarios:
        if not graph.has(scenario.fault.asset):
            continue
        context = confidence(graph, scenario.fault.asset, usage, verified)
        impact = min(fragility.get(scenario.fault.asset, 0.0) / 100.0, 1.0)
        if not impact:
            impact = min(len(graph.reachable_downstream(scenario.fault.asset)) / estate_size, 1.0)
        gap = 1.0 - context.score
        novelty = 0.2 if scenario.name in seen else 1.0
        priority = round(0.50 * impact + 0.35 * gap + 0.15 * novelty, 6)
        result.append(Candidate(scenario, priority, round(impact, 4), round(gap, 4), novelty, context))
    return tuple(sorted(result, key=lambda c: (-c.priority, c.scenario.name, str(c.scenario.path))))


def _json(line: str) -> dict | None:
    try:
        value = json.loads(line)
    except json.JSONDecodeError:
        return None
    return value if isinstance(value, dict) else None
