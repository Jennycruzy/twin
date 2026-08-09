"""The commerce verifier must count its own estate, not the whole deployment.

One DataHub instance holds every estate at once. The commerce verifier was written when
commerce was the only estate, so its entity searches were unscoped. Once the operations
estate was ingested alongside it, an intact 66-dataset commerce estate reported 91 — the
sum of both — and the nightly failed with nothing actually wrong.
"""

from __future__ import annotations

from estate.verify_estate import EstateInspector
from twin.target import load_target


class FakeGraph:
    """Returns every entity in the deployment, the way an unscoped search does."""

    def __init__(self, urns: list[str]) -> None:
        self.urns = urns

    def get_urns_by_filter(self, entity_types=None, platform=None, **_):
        wanted = (entity_types or [None])[0]
        return [
            urn for urn in self.urns
            if (wanted is None or f":li:{wanted}:(" in urn)
            and (platform is None or f"dataPlatform:{platform}" in urn)
        ]


COMMERCE_DATASET = "urn:li:dataset:(urn:li:dataPlatform:postgres,warehouse.marts.orders,PROD)"
OPERATIONS_DATASET = (
    "urn:li:dataset:(urn:li:dataPlatform:postgres,operations.warehouse.ops_marts.legs,PROD)"
)
COMMERCE_DASHBOARD = "urn:li:dashboard:(looker,revenue)"
OPERATIONS_DASHBOARD = "urn:li:dashboard:(ops-looker,control-tower)"


def test_inspector_excludes_another_estate_sharing_the_instance():
    graph = FakeGraph([COMMERCE_DATASET, OPERATIONS_DATASET, COMMERCE_DASHBOARD, OPERATIONS_DASHBOARD])

    inspector = EstateInspector(graph, load_target("commerce").catalog)

    assert inspector.postgres == [COMMERCE_DATASET]
    assert inspector.dashboards == [COMMERCE_DASHBOARD]


def test_operations_scope_excludes_commerce_from_the_same_search():
    graph = FakeGraph([COMMERCE_DATASET, OPERATIONS_DATASET, COMMERCE_DASHBOARD, OPERATIONS_DASHBOARD])

    inspector = EstateInspector(graph, load_target("operations").catalog)

    assert inspector.postgres == [OPERATIONS_DATASET]
    assert inspector.dashboards == [OPERATIONS_DASHBOARD]
