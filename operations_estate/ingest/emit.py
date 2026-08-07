"""Emit the operations estate's people, dashboards and deployed risk features."""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from typing import Iterable

from datahub.emitter.mce_builder import make_data_platform_urn
from datahub.emitter.mcp import MetadataChangeProposalWrapper
from datahub.emitter.rest_emitter import DatahubRestEmitter
from datahub.metadata.schema_classes import (
    AuditStampClass, ChangeAuditStampsClass, ChartInfoClass, ChartTypeClass,
    CorpUserInfoClass, DashboardInfoClass, EdgeClass, GlobalTagsClass,
    MLFeaturePropertiesClass, MLFeatureTablePropertiesClass,
    MLModelDeploymentPropertiesClass, MLModelGroupPropertiesClass, MLModelPropertiesClass,
    OwnerClass, OwnershipClass, OwnershipTypeClass, TagAssociationClass, VersionTagClass,
)

WAREHOUSE_DB = os.environ.get("WAREHOUSE_DB", "warehouse")
ENV = "PROD"
INSTANCE = "operations"
BI_PLATFORM = "ops-looker"
FEATURE_PLATFORM = "ops-fs"
ML_PLATFORM = "ops-mlflow"
EMIT_TIME_MS = 1_754_006_400_000
AUDIT = AuditStampClass(time=EMIT_TIME_MS, actor="urn:li:corpuser:datahub")


def dataset_urn(schema: str, table: str, platform: str = "dbt") -> str:
    return f"urn:li:dataset:(urn:li:dataPlatform:{platform},{INSTANCE}.{WAREHOUSE_DB}.{schema}.{table},{ENV})"


def user_urn(email: str) -> str:
    return f"urn:li:corpuser:{email}"


def owner_aspect(email: str | None) -> OwnershipClass:
    return OwnershipClass(
        owners=[] if not email else [OwnerClass(owner=user_urn(email), type=OwnershipTypeClass.TECHNICAL_OWNER)],
        lastModified=AUDIT,
    )


def tags_aspect(tags: list[str]) -> GlobalTagsClass:
    return GlobalTagsClass(tags=[TagAssociationClass(tag=f"urn:li:tag:{tag}") for tag in tags])


@dataclass(frozen=True)
class Person:
    email: str
    name: str
    title: str
    team: str


PEOPLE = (
    Person("lin.nguyen@example.com", "Lin Nguyen", "Network Operations Lead", "network-operations"),
    Person("sofia.kim@example.com", "Sofia Kim", "Transport Analytics Lead", "transport-analytics"),
    Person("diego.mora@example.com", "Diego Mora", "Facilities Reliability Engineer", "facilities"),
    Person("mira.patel@example.com", "Mira Patel", "Risk Scientist", "risk-science"),
    Person("dispatch@example.com", "Dispatch Desk", "Operations Consumer", "dispatch"),
)


def people_mcps() -> Iterable[MetadataChangeProposalWrapper]:
    for person in PEOPLE:
        yield MetadataChangeProposalWrapper(
            entityUrn=user_urn(person.email),
            aspect=CorpUserInfoClass(active=True, displayName=person.name, email=person.email,
                                     title=person.title, fullName=person.name, departmentName=person.team),
        )


def chart_urn(key: str) -> str:
    return f"urn:li:chart:({BI_PLATFORM},{key})"


def dashboard_urn(key: str) -> str:
    return f"urn:li:dashboard:({BI_PLATFORM},{key})"


CHARTS = (
    ("network-sla", "Network delivery SLA", "On-time and open shipments by facility.", "ops_marts", "mart_delivery_sla", ChartTypeClass.LINE),
    ("capacity-watch", "Facility capacity watch", "Load ratio and temperature alerts.", "ops_marts", "mart_capacity_risk", ChartTypeClass.BAR),
    ("exception-queue", "Exception queue", "Scanner exceptions joined to work orders.", "ops_marts", "mart_exception_queue", ChartTypeClass.BAR),
    ("carrier-score", "Carrier network score", "Reliability by carrier and service tier.", "ops_marts", "mart_carrier_network", ChartTypeClass.BAR),
)


def dashboard_mcps() -> Iterable[MetadataChangeProposalWrapper]:
    stamps = ChangeAuditStampsClass(created=AUDIT, lastModified=AUDIT)
    dashboards = (
        ("control-tower", "Operations Control Tower", "Morning view of service risk across the physical network.", "lin.nguyen@example.com", "tier:tier1", CHARTS[:3]),
        ("carrier-review", "Carrier Performance Review", "Weekly carrier review with reliability and exception context.", "sofia.kim@example.com", "tier:tier2", CHARTS[2:]),
    )
    for key, title, description, owner, tier, charts in dashboards:
        for chart_key, chart_title, chart_description, schema, table, chart_type in charts:
            yield MetadataChangeProposalWrapper(
                entityUrn=chart_urn(chart_key),
                aspect=ChartInfoClass(
                    title=chart_title, description=chart_description, lastModified=stamps,
                    type=chart_type, inputEdges=[EdgeClass(destinationUrn=dataset_urn(schema, table))],
                ),
            )
            yield MetadataChangeProposalWrapper(entityUrn=chart_urn(chart_key), aspect=owner_aspect(owner))
        yield MetadataChangeProposalWrapper(
            entityUrn=dashboard_urn(key),
            aspect=DashboardInfoClass(
                title=title, description=description, lastModified=stamps,
                chartEdges=[EdgeClass(destinationUrn=chart_urn(c[0])) for c in charts],
                datasetEdges=[EdgeClass(destinationUrn=dataset_urn(c[3], c[4])) for c in charts],
            ),
        )
        yield MetadataChangeProposalWrapper(entityUrn=dashboard_urn(key), aspect=owner_aspect(owner))
        yield MetadataChangeProposalWrapper(entityUrn=dashboard_urn(key), aspect=tags_aspect([tier]))


FEATURES = (
    ("late_probability", "feature_delivery_risk", "observed_late", "Probability proxy from delivery SLA."),
    ("exception_scan_ratio", "feature_delivery_risk", "exception_scan_ratio", "Share of scans carrying a route exception."),
    ("facility_load_ratio", "feature_facility_congestion", "load_ratio", "Committed network load divided by facility capacity."),
    ("temperature_alerts", "feature_facility_congestion", "temperature_alerts", "Cold-chain alert count at the facility."),
)


def feature_urn(name: str) -> str:
    return f"urn:li:mlFeature:({FEATURE_PLATFORM},{name})"


def feature_table_urn() -> str:
    return f"urn:li:mlFeatureTable:({make_data_platform_urn(FEATURE_PLATFORM)},operations-risk-features)"


def model_group_urn() -> str:
    return f"urn:li:mlModelGroup:({make_data_platform_urn(ML_PLATFORM)},delivery-risk,PROD)"


def model_urn() -> str:
    return f"urn:li:mlModel:({make_data_platform_urn(ML_PLATFORM)},delivery-risk-v2,PROD)"


def deployment_urn() -> str:
    return f"urn:li:mlModelDeployment:({make_data_platform_urn(ML_PLATFORM)},delivery-risk-prod,PROD)"


def ml_mcps() -> Iterable[MetadataChangeProposalWrapper]:
    owner = "mira.patel@example.com"
    for name, model, column, description in FEATURES:
        yield MetadataChangeProposalWrapper(
            entityUrn=feature_urn(name),
            aspect=MLFeaturePropertiesClass(description=description, dataType="CONTINUOUS",
                                             sources=[dataset_urn("ops_features", model)]),
        )
    yield MetadataChangeProposalWrapper(
        entityUrn=feature_table_urn(),
        aspect=MLFeatureTablePropertiesClass(
            description="Online feature set used to prioritize shipment and facility interventions.",
            mlFeatures=[feature_urn(f[0]) for f in FEATURES],
        ),
    )
    yield MetadataChangeProposalWrapper(entityUrn=feature_table_urn(), aspect=owner_aspect(owner))
    yield MetadataChangeProposalWrapper(
        entityUrn=model_group_urn(),
        aspect=MLModelGroupPropertiesClass(description="Operations risk models.", createdAt=EMIT_TIME_MS),
    )
    yield MetadataChangeProposalWrapper(entityUrn=model_group_urn(), aspect=owner_aspect(owner))
    yield MetadataChangeProposalWrapper(
        entityUrn=model_urn(),
        aspect=MLModelPropertiesClass(
            description="Ranks late shipments and overloaded facilities for the dispatch desk.",
            date=EMIT_TIME_MS, version=VersionTagClass(versionTag="2.0.0"), type="gradient_boosted_trees",
            groups=[model_group_urn()], mlFeatures=[feature_urn(f[0]) for f in FEATURES],
            deployments=[deployment_urn()], trainingMetrics=[], hyperParams=[],
        ),
    )
    yield MetadataChangeProposalWrapper(entityUrn=model_urn(), aspect=owner_aspect(owner))
    yield MetadataChangeProposalWrapper(
        entityUrn=deployment_urn(),
        aspect=MLModelDeploymentPropertiesClass(
            description="Live dispatch prioritization service.", createdAt=EMIT_TIME_MS,
            version=VersionTagClass(versionTag="2.0.0"),
        ),
    )
    yield MetadataChangeProposalWrapper(entityUrn=deployment_urn(), aspect=owner_aspect(owner))


def main() -> int:
    emitter = DatahubRestEmitter(
        gms_server=os.environ.get("DATAHUB_GMS_URL", "http://datahub-gms:8080"),
        token=os.environ.get("DATAHUB_GMS_TOKEN") or None,
    )
    emitter.test_connection()
    total = 0
    for factory in (people_mcps, dashboard_mcps, ml_mcps):
        for mcp in factory():
            emitter.emit(mcp)
            total += 1
    print(f"emitted {total} operations metadata proposals")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
