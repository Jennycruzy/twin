"""Stage 4 — Verify.

Twin executes the fault it simulated against a real warehouse, rebuilds the real downstream
models against it, re-runs the real consumer queries, and grades its own prediction against
what actually broke.

This is the stage the project stands on. A simulator that proves itself against a real dbt
run is a different class of thing from one that does not, and everything else here is
cuttable before it.

Safety is structural rather than conventional: execution happens as a role that owns nothing
in the estate, inside a schema carrying a non-configurable prefix, behind a guard that
refuses any destructive statement naming anything else. See docs/SAFETY.md.
"""

from __future__ import annotations

from twin.verify.consumers import ConsumerCheck, run_consumer_queries
from twin.verify.dbt_runner import BuildOutcome, NodeResult, rebuild_downstream
from twin.verify.grade import Scorecard, grade
from twin.verify.guard import SHADOW_PREFIX, UnsafeStatement, assert_safe
from twin.verify.shadow import ShadowEstate, plan, shadow_estate
from twin.verify.warehouse import Credentials, ShadowConnection

__all__ = [
    "BuildOutcome",
    "ConsumerCheck",
    "Credentials",
    "NodeResult",
    "SHADOW_PREFIX",
    "Scorecard",
    "ShadowConnection",
    "ShadowEstate",
    "UnsafeStatement",
    "assert_safe",
    "grade",
    "plan",
    "rebuild_downstream",
    "run_consumer_queries",
    "shadow_estate",
]
