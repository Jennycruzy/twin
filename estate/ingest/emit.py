"""Emit the estate entities that dbt and Postgres ingestion cannot produce.

Three kinds of entity live outside the warehouse and outside the dbt project, and all
three matter to Twin:

  people      dbt only carries an email address in `meta.owner`. A paging list that reads
              "amara.chen@example.com" is not an incident response; one that reads
              "Amara Chen, ML Platform, sole owner of 7 assets" is. Twin resolves
              responders through these profiles.

  dashboards  the estate's terminal consumers. Without them, a failure timeline ends at
              a mart and cannot say which report is wrong this morning.

  ML branch   feature table, feature set, model group, model and deployment. This is the
              path that carries a warehouse failure all the way to a model serving live
              traffic, which is a different severity of outcome from a stale report.

Everything emitted here is idempotent: re-running overwrites the same aspects on the same
URNs, so `make estate` can be run repeatedly without accumulating duplicates.
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from dataclasses import dataclass, field
from typing import Iterable, Sequence

from datahub.emitter.mce_builder import make_data_platform_urn, make_dataset_urn
from datahub.emitter.mcp import MetadataChangeProposalWrapper
from datahub.emitter.rest_emitter import DatahubRestEmitter
from datahub.metadata.schema_classes import (
    AuditStampClass,
    ChangeAuditStampsClass,
    ChartInfoClass,
    ChartTypeClass,
    CorpUserInfoClass,
    DashboardInfoClass,
    EdgeClass,
    GlobalTagsClass,
    MLFeaturePropertiesClass,
    MLFeatureTablePropertiesClass,
    MLModelDeploymentPropertiesClass,
    MLModelGroupPropertiesClass,
    MLModelPropertiesClass,
    OwnerClass,
    OwnershipClass,
    OwnershipTypeClass,
    TagAssociationClass,
    VersionTagClass,
)

# The estate is built for PROD in a single Postgres warehouse; dbt is the transformation
# platform sitting over it.
WAREHOUSE_DB = os.environ.get("WAREHOUSE_DB", "warehouse")
ENV = "PROD"

BI_PLATFORM = "looker"
ML_PLATFORM = "mlflow"

# Fixed so re-emission produces identical aspects rather than a new timestamp each run.
EMIT_TIME_MS = 1_754_006_400_000  # 2025-08-01T00:00:00Z
AUDIT = AuditStampClass(time=EMIT_TIME_MS, actor="urn:li:corpuser:datahub")


def dataset_urn(schema: str, table: str, platform: str = "dbt") -> str:
    return make_dataset_urn(platform=platform, name=f"{WAREHOUSE_DB}.{schema}.{table}", env=ENV)


def user_urn(email: str) -> str:
    return f"urn:li:corpuser:{email}"


def owner_aspect(email: str | None) -> OwnershipClass:
    if not email:
        return OwnershipClass(owners=[], lastModified=AUDIT)
    return OwnershipClass(
        owners=[OwnerClass(owner=user_urn(email), type=OwnershipTypeClass.TECHNICAL_OWNER)],
        lastModified=AUDIT,
    )


def tags_aspect(tags: Sequence[str]) -> GlobalTagsClass:
    return GlobalTagsClass(tags=[TagAssociationClass(tag=f"urn:li:tag:{t}") for t in tags])


# --------------------------------------------------------------------------- people

@dataclass(frozen=True)
class Person:
    email: str
    full_name: str
    title: str
    team: str


PEOPLE: tuple[Person, ...] = (
    Person("priya.raghavan@example.com", "Priya Raghavan",
           "Staff Data Platform Engineer", "data-platform"),
    Person("marcus.webb@example.com", "Marcus Webb",
           "Senior Analytics Engineer", "analytics-engineering"),
    Person("dana.oyelaran@example.com", "Dana Oyelaran",
           "Analytics Lead, Finance", "finance-data"),
    Person("tomas.lindqvist@example.com", "Tomas Lindqvist",
           "Analytics Engineer, Growth", "growth-analytics"),
    Person("amara.chen@example.com", "Amara Chen",
           "ML Platform Engineer", "ml-platform"),
    # Consumers rather than owners. They appear in usage statistics and in the blast
    # radius of a failure, but they maintain nothing, which is why they are not part of
    # the ownership distribution.
    Person("rowan.beck@example.com", "Rowan Beck",
           "Finance Business Partner", "finance"),
    Person("ines.duarte@example.com", "Ines Duarte",
           "Commercial Analyst", "merchant-operations"),
)


def people_mcps() -> Iterable[MetadataChangeProposalWrapper]:
    for person in PEOPLE:
        yield MetadataChangeProposalWrapper(
            entityUrn=user_urn(person.email),
            aspect=CorpUserInfoClass(
                active=True,
                displayName=person.full_name,
                email=person.email,
                title=person.title,
                fullName=person.full_name,
                departmentName=person.team,
            ),
        )


# --------------------------------------------------------------------------- dashboards

@dataclass(frozen=True)
class Chart:
    key: str
    title: str
    description: str
    source: tuple[str, str]
    chart_type: str = ChartTypeClass.LINE


@dataclass(frozen=True)
class Dashboard:
    key: str
    title: str
    description: str
    owner: str | None
    tier: str
    charts: tuple[Chart, ...] = field(default_factory=tuple)


DASHBOARDS: tuple[Dashboard, ...] = (
    Dashboard(
        key="finance-revenue-review",
        title="Finance — Revenue Review",
        description=(
            "Reviewed by the CFO and the finance leadership team every Monday morning. "
            "Reports daily revenue on gross, net and settled bases, and the reconciliation "
            "gap between ordered and settled value per processor."
        ),
        owner="dana.oyelaran@example.com",
        tier="tier:tier1",
        charts=(
            Chart("revenue-trend", "Net revenue, daily",
                  "Net USD revenue by order date.", ("marts", "mart_revenue_daily")),
            Chart("authorisation-rate", "Authorisation rate",
                  "Settled payments as a share of attempts.", ("marts", "mart_revenue_daily")),
            Chart("reconciliation-gap", "Unreconciled value by processor",
                  "Ordered value less settled value, per processor, per day.",
                  ("marts", "mart_finance_reconciliation"), ChartTypeClass.BAR),
            Chart("dispute-exposure", "Open dispute exposure",
                  "Disputed value not yet resolved.",
                  ("marts", "mart_transaction_enriched"), ChartTypeClass.AREA),
        ),
    ),
    Dashboard(
        key="merchant-operations",
        title="Merchant Operations",
        description=(
            "Used by the merchant operations team to triage seller performance and "
            "fulfilment problems."
        ),
        owner="tomas.lindqvist@example.com",
        tier="tier:tier2",
        charts=(
            Chart("merchant-revenue", "Merchant revenue ranking",
                  "Net USD by merchant.", ("marts", "mart_merchant_scorecard"), ChartTypeClass.BAR),
            Chart("on-time-rate", "On-time delivery rate by carrier",
                  "Share of shipments meeting the carrier's promised transit days.",
                  ("marts", "mart_logistics_sla"), ChartTypeClass.BAR),
        ),
    ),
    Dashboard(
        key="growth-funnel",
        title="Growth Funnel",
        description="Daily acquisition and conversion funnel, reviewed by growth and marketing.",
        owner="tomas.lindqvist@example.com",
        tier="tier:tier2",
        charts=(
            Chart("funnel-conversion", "Session conversion funnel",
                  "Product view to cart to confirmation.",
                  ("marts", "mart_marketing_funnel"), ChartTypeClass.BAR),
            Chart("zero-result-search", "Zero-result searches",
                  "Searches returning no results, daily.", ("marts", "mart_marketing_funnel")),
        ),
    ),
    Dashboard(
        key="subscription-retention",
        title="Subscription & Retention",
        description="Subscription book, churn and customer value. Reviewed by finance and growth.",
        owner=None,
        tier="tier:tier2",
        charts=(
            Chart("mrr-by-plan", "Active MRR by plan",
                  "Monthly recurring revenue in USD.",
                  ("marts", "mart_subscription_health"), ChartTypeClass.BAR),
            Chart("customer-value", "Lifetime value distribution",
                  "Net lifetime USD per customer.",
                  ("marts", "mart_customer_360"), ChartTypeClass.HISTOGRAM),
        ),
    ),
)


def chart_urn(key: str) -> str:
    return f"urn:li:chart:({BI_PLATFORM},{key})"


def dashboard_urn(key: str) -> str:
    return f"urn:li:dashboard:({BI_PLATFORM},{key})"


def dashboard_mcps() -> Iterable[MetadataChangeProposalWrapper]:
    stamps = ChangeAuditStampsClass(created=AUDIT, lastModified=AUDIT)
    for dash in DASHBOARDS:
        for chart in dash.charts:
            schema, table = chart.source
            yield MetadataChangeProposalWrapper(
                entityUrn=chart_urn(chart.key),
                aspect=ChartInfoClass(
                    title=chart.title,
                    description=chart.description,
                    lastModified=stamps,
                    type=chart.chart_type,
                    inputEdges=[EdgeClass(destinationUrn=dataset_urn(schema, table))],
                ),
            )
            yield MetadataChangeProposalWrapper(
                entityUrn=chart_urn(chart.key), aspect=owner_aspect(dash.owner)
            )

        yield MetadataChangeProposalWrapper(
            entityUrn=dashboard_urn(dash.key),
            aspect=DashboardInfoClass(
                title=dash.title,
                description=dash.description,
                lastModified=stamps,
                chartEdges=[EdgeClass(destinationUrn=chart_urn(c.key)) for c in dash.charts],
                datasetEdges=[
                    EdgeClass(destinationUrn=dataset_urn(*src))
                    for src in sorted({c.source for c in dash.charts})
                ],
            ),
        )
        yield MetadataChangeProposalWrapper(
            entityUrn=dashboard_urn(dash.key), aspect=owner_aspect(dash.owner)
        )
        yield MetadataChangeProposalWrapper(
            entityUrn=dashboard_urn(dash.key), aspect=tags_aspect([dash.tier])
        )


# --------------------------------------------------------------------------- ML branch

FEATURE_TABLE_NAME = "fraud_features_v3"
MODEL_GROUP_NAME = "fraud_scorer"
MODEL_NAME = "fraud_scorer_v3"
DEPLOYMENT_NAME = "fraud-scoring-prod"

# Feature name -> (source model, source column, description). These are real columns of
# the ml.* tables the dbt project builds, so the feature-to-column path resolves.
FEATURES: tuple[tuple[str, str, str, str], ...] = (
    ("failed_login_rate", "feature_customer_risk", "failed_login_rate",
     "Failed logins as a share of the customer's sessions."),
    ("max_customers_sharing_device", "feature_customer_risk", "max_customers_sharing_device",
     "Largest number of distinct customers seen on any device this customer used."),
    ("min_device_trust", "feature_customer_risk", "min_device_trust",
     "Lowest device trust score across the customer's devices."),
    ("account_age_days", "feature_customer_risk", "account_age_days",
     "Days since signup."),
    ("peak_to_mean_attempts", "feature_txn_velocity", "peak_to_mean_attempts",
     "Busiest day's payment attempts over the customer's own daily mean."),
    ("decline_rate", "feature_txn_velocity", "decline_rate",
     "Declined payment attempts as a share of all attempts."),
    ("max_daily_amount_usd", "feature_txn_velocity", "max_daily_amount_usd",
     "Largest single-day payment value in USD."),
    ("merchant_dispute_rate", "feature_merchant_risk", "dispute_rate",
     "Disputes as a share of payments, for the merchant being transacted with."),
    ("merchant_disputed_value_share", "feature_merchant_risk", "disputed_value_share",
     "Disputed value as a share of the merchant's net revenue."),
)


def feature_urn(name: str) -> str:
    return f"urn:li:mlFeature:({FEATURE_TABLE_NAME},{name})"


def feature_table_urn() -> str:
    return f"urn:li:mlFeatureTable:({make_data_platform_urn(ML_PLATFORM)},{FEATURE_TABLE_NAME})"


def model_group_urn() -> str:
    return f"urn:li:mlModelGroup:({make_data_platform_urn(ML_PLATFORM)},{MODEL_GROUP_NAME},{ENV})"


def model_urn() -> str:
    return f"urn:li:mlModel:({make_data_platform_urn(ML_PLATFORM)},{MODEL_NAME},{ENV})"


def deployment_urn() -> str:
    return f"urn:li:mlModelDeployment:({make_data_platform_urn(ML_PLATFORM)},{DEPLOYMENT_NAME},{ENV})"


def ml_mcps() -> Iterable[MetadataChangeProposalWrapper]:
    owner = "amara.chen@example.com"

    for name, model, column, description in FEATURES:
        yield MetadataChangeProposalWrapper(
            entityUrn=feature_urn(name),
            aspect=MLFeaturePropertiesClass(
                description=description,
                dataType="CONTINUOUS",
                sources=[dataset_urn("ml", model)],
            ),
        )

    yield MetadataChangeProposalWrapper(
        entityUrn=feature_table_urn(),
        aspect=MLFeatureTablePropertiesClass(
            description=(
                "Feature set backing the production fraud scorer. Served online with a "
                "one-hour freshness target; a stale feature here is a wrong decision on a "
                "live transaction, not a late report."
            ),
            mlFeatures=[feature_urn(name) for name, _, _, _ in FEATURES],
        ),
    )
    yield MetadataChangeProposalWrapper(entityUrn=feature_table_urn(), aspect=owner_aspect(owner))

    yield MetadataChangeProposalWrapper(
        entityUrn=model_group_urn(),
        aspect=MLModelGroupPropertiesClass(
            description="Fraud scoring models. One version in production at a time.",
            createdAt=EMIT_TIME_MS,
        ),
    )
    yield MetadataChangeProposalWrapper(entityUrn=model_group_urn(), aspect=owner_aspect(owner))

    yield MetadataChangeProposalWrapper(
        entityUrn=model_urn(),
        aspect=MLModelPropertiesClass(
            description=(
                "Gradient-boosted fraud scorer. Scores every payment attempt at "
                "authorisation time and can decline a transaction outright."
            ),
            date=EMIT_TIME_MS,
            version=VersionTagClass(versionTag="3.1.0"),
            type="gradient_boosted_trees",
            groups=[model_group_urn()],
            mlFeatures=[feature_urn(name) for name, _, _, _ in FEATURES],
            deployments=[deployment_urn()],
            trainingMetrics=[],
            hyperParams=[],
        ),
    )
    yield MetadataChangeProposalWrapper(entityUrn=model_urn(), aspect=owner_aspect(owner))

    yield MetadataChangeProposalWrapper(
        entityUrn=deployment_urn(),
        aspect=MLModelDeploymentPropertiesClass(
            description=(
                "Live deployment of the fraud scorer, in the payment authorisation path. "
                "Serves every payment attempt on the platform."
            ),
            createdAt=EMIT_TIME_MS,
            version=VersionTagClass(versionTag="3.1.0"),
        ),
    )
    yield MetadataChangeProposalWrapper(entityUrn=deployment_urn(), aspect=owner_aspect(owner))


# --------------------------------------------------------------------------- entrypoint

EMITTERS = {
    "people": people_mcps,
    "dashboards": dashboard_mcps,
    "ml": ml_mcps,
}


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "groups",
        nargs="*",
        metavar="GROUP",
        help=f"Entity groups to emit ({', '.join(sorted(EMITTERS))}). Defaults to all.",
    )
    parser.add_argument(
        "--gms",
        default=os.environ.get("DATAHUB_GMS_URL", "http://datahub-gms:8080"),
    )
    args = parser.parse_args(argv)
    groups = args.groups or sorted(EMITTERS)
    unknown = [g for g in groups if g not in EMITTERS]
    if unknown:
        parser.error(f"unknown group(s): {', '.join(unknown)}. Choose from {', '.join(sorted(EMITTERS))}.")

    emitter = DatahubRestEmitter(gms_server=args.gms, token=os.environ.get("DATAHUB_GMS_TOKEN") or None)
    emitter.test_connection()

    started = time.monotonic()
    total = 0
    for group in groups:
        count = 0
        for mcp in EMITTERS[group]():
            emitter.emit(mcp)
            count += 1
        total += count
        print(f"  {group:<12} {count:>4} aspects")

    print(f"emitted {total} aspects in {time.monotonic() - started:.1f}s")
    return 0


if __name__ == "__main__":
    sys.exit(main())
