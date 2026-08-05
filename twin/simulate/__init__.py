"""Stage 2 — Simulate.

A scenario declares a fault. Simulation propagates it across the estate graph and returns
an event-ordered timeline: what breaks, in what order, and why.

Only the slice Stage 4 needs to grade itself is built here — one fault kind, propagated at
column grain. The full propagation model, the remaining scenarios and the paging lists are
Stage 2 proper, and this package grows into them rather than being replaced.
"""

from __future__ import annotations

from twin.simulate.propagate import Event, Timeline, predict
from twin.simulate.scenario import Fault, Scenario, ScenarioError, load_scenario

__all__ = [
    "Event",
    "Fault",
    "Scenario",
    "ScenarioError",
    "Timeline",
    "load_scenario",
    "predict",
]
