"""Deterministic fault propagation.

A scenario declares a fault. Simulation propagates it across the estate graph and returns
an event-ordered timeline: what breaks, in what order, and why.

The same propagation model drives both scenario prediction and the fragility sweep, so a
score can be checked against the verifier that executes it.
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
