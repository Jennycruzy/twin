from pathlib import Path

from twin.target import CatalogScope, load_target


def dataset_urn(instance: str | None, path: str = "warehouse.raw.orders") -> str:
    platform = (
        "urn:li:dataPlatform:postgres"
        if instance is None
        else f"urn:li:dataPlatformInstance:(urn:li:dataPlatform:postgres,{instance})"
    )
    return f"urn:li:dataset:({platform},{path},PROD)"


def test_catalog_scope_separates_dataset_platform_instances():
    scope = CatalogScope("operations", "operations.warehouse.", ("urn:li:chart:(operations-",))

    assert scope.accepts({"urn": dataset_urn("operations", "operations.warehouse.raw.orders")})
    assert not scope.accepts({"urn": dataset_urn(None)})
    assert scope.accepts({"urn": "urn:li:chart:(operations-orders,ops)"})
    assert not scope.accepts({"urn": "urn:li:chart:(looker,revenue)"})


def test_empty_non_dataset_prefixes_preserve_the_current_estate_scope():
    scope = CatalogScope(None, "warehouse.")
    assert scope.accepts({"urn": dataset_urn(None)})
    assert scope.accepts({"urn": "urn:li:dashboard:(looker,revenue)"})


def test_commerce_target_keeps_existing_paths_and_source_contract():
    target = load_target("commerce")
    assert target.dbt_project == Path("estate/dbt")
    assert target.workload == Path("estate/ingest/queries/workload.yml")
    assert target.scenario_dir == Path("scenarios")
    assert target.cache_dir == Path(".twin")
    assert target.catalog.accepts({"urn": "urn:li:dashboard:(looker,revenue)"})
    assert not target.catalog.accepts({"urn": "urn:li:dashboard:(ops-looker,control-tower)"})
    assert target.source_layers == frozenset({"raw_pg", "raw_events"})
    assert target.model_schemas == frozenset({"staging", "intermediate", "marts", "ml"})


def test_operations_target_has_an_independent_runtime_contract():
    target = load_target("operations")
    assert target.catalog.dataset_platform_instance == "operations"
    assert target.catalog.dataset_path_prefix == "operations.warehouse."
    assert target.dbt_project == Path("operations_estate/dbt")
    assert target.scenario_dir == Path("operations_scenarios")
    assert target.source_layers == frozenset({"ops_erp", "ops_stream"})
    assert target.model_schemas == frozenset({"ops_staging", "ops_core", "ops_marts", "ops_features"})
