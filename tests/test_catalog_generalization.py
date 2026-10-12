"""Data-driven catalog coverage using the approved 150-row classification load."""

from __future__ import annotations

import re
from pathlib import Path

import pandas as pd
import pytest
from datetime import date

from app.llm.schema import Filters, Intent, Scenario, TimeWindow
from app.dispatch import dispatch
from app.semantic.queries import Queries, QueryResult
from app.answer_builder import build_answer
from app.orchestrator import _calculation_payload
from app.dispatch import DispatchResult
from app.response_composer import build_answer_facts
from app.semantic.resolver import EntityResolver
from app.orchestrator import _presentation
from app.calc.metrics import Rules
from app.semantic.queries import window_for


ROOT = Path(__file__).parents[1]
LOAD = ROOT / "PRODUCT_SUBCATEGORY_150_PRODUCTION_LOAD_FIXED.sql"
ROW_RE = re.compile(
    r"\((\d+),N'([^']*)',N'([^']*)',N'([^']*)',N'([^']*)'\),?"
)


@pytest.fixture(scope="module")
def catalog():
    rows = [m.groups() for m in ROW_RE.finditer(LOAD.read_text(encoding="utf-8"))]
    assert len(rows) == 150
    frame = pd.DataFrame(rows, columns=["product_id", "sku", "product_name", "category", "subcategory"])
    frame["product_id"] = frame["product_id"].astype(int)
    frame["model"] = frame["sku"]
    source = pd.read_csv(ROOT / "PRODUCT_SUBCATEGORY_COMPLETE_MANIFEST.csv")[["product_id", "brand"]]
    return frame.merge(source, on="product_id", validate="one_to_one")


@pytest.fixture(scope="module")
def resolver(catalog):
    stores = pd.DataFrame([
        (1, "Central", "Delhi", "North"), (2, "West", "Mumbai", "West"),
        (3, "South", "Bengaluru", "South"), (4, "T Nagar", "Chennai", "South"),
        (5, "Velachery", "Chennai", "South"), (6, "Salt Lake", "Kolkata", "East"),
    ], columns=["store_id", "store_name", "city", "region"])
    return EntityResolver(catalog, stores)


def test_approved_catalog_covers_all_categories_and_subcategories(catalog):
    assert catalog["product_id"].nunique() == 150
    assert catalog["category"].nunique() == 7
    assert catalog["subcategory"].nunique() == 26


@pytest.mark.parametrize("field", ["categories", "subcategories", "brands"])
def test_every_catalog_dimension_resolves_without_name_inference(resolver, catalog, field):
    source = {"categories": "category", "subcategories": "subcategory", "brands": "brand"}[field]
    values = sorted(catalog[source].dropna().unique())
    for value in values:
        result = resolver.resolve(Filters(**{field: [value]}))
        assert result.ok and result.product_ids, f"unresolved {field}: {value}"


def test_every_product_identity_resolves_to_its_single_catalog_row(resolver, catalog):
    for row in catalog.itertuples():
        result = resolver.resolve(Filters(products=[row.product_name]))
        assert result.ok and row.product_id in result.product_ids


def test_authorized_city_scope_is_consistent_for_single_and_multi_store_requests(resolver):
    city = resolver.resolve(Filters(cities=["Chennai"]))
    assert city.store_ids == [4, 5]
    one = resolver.resolve(Filters(stores=["T Nagar"]))
    assert one.store_ids == [4]
    assert resolver.city_store_options("Chennai", authorized_store_ids=[4]) == [
        {"store_id": 4, "store_name": "T Nagar", "city": "Chennai", "region": "South"}
    ]


@pytest.mark.parametrize(
    ("kind", "updates"),
    [
        ("month", {"month": 1, "year": 2025}),
        ("month", {"month": 6, "year": 2026}),
        ("exact_date", {"date": "2026-06-15"}),
        ("date_range", {"start_date": "2025-01-01", "end_date": "2025-03-31"}),
    ],
)
def test_supported_periods_are_normalized_without_capability_specific_shortcuts(kind, updates):
    intent = type("I", (), {"time": TimeWindow(kind=kind, **updates)})()
    start, end, label = window_for(intent, Rules(), date(2026, 9, 30))
    assert start < end and label


@pytest.mark.parametrize("capability", ["inventory_visibility", "inventory_health", "stockout_risk", "sales_trend", "what_if"])
def test_response_contract_remains_scope_aware_for_each_supported_capability(capability, catalog):
    row = catalog.iloc[[0]].copy()
    row["product_id"] = row["product_id"].astype(int)
    row["store_id"] = 4
    row["store_name"] = "T Nagar"
    row["city"] = "Chennai"
    row["quantity_on_hand"] = 12
    row["inventory_snapshot_date"] = "2026-09-30"
    intent = Intent(capability=capability, metrics=[], filters=Filters(products=[str(row.iloc[0].product_name)], stores=["T Nagar"]), time=TimeWindow())
    view, display = _presentation(row, intent, "Show results at T Nagar")
    facts = build_answer_facts(intent=intent, result=DispatchResult(df=row, sql=[], params=[], notes=[], basis="Actual"), resolved_context={"capability": capability}, counts={"distinct_products": 1, "stores": 1}).as_dict()
    assert display["columns"]
    assert facts["requested_scope"]["stores"] == ["T Nagar"]
    assert display["single_result"] is True


@pytest.mark.parametrize("demand_change_pct", [10, 20, 30, 50, -10])
def test_what_if_catalog_sweep_preserves_identity_and_calculation_contract(
    resolver, catalog, demand_change_pct,
):
    """Exercise every manifest product against known store names without SQL writes."""

    stores = resolver.stores.sort_values("store_id").reset_index(drop=True)
    rules = Rules()

    for index, product in enumerate(catalog.itertuples()):
        store = stores.iloc[index % len(stores)]
        resolved = resolver.resolve(Filters(products=[product.product_name], stores=[store.store_name]))
        assert resolved.ok
        assert resolved.product_ids == [int(product.product_id)]
        assert resolved.store_ids == [int(store.store_id)]

        quantity = 0 if index % 13 == 0 else 100
        units = 0 if index % 7 == 0 else 20
        lead_time = None if index % 9 == 0 else 8
        source = pd.DataFrame([{
            "product_id": int(product.product_id), "sku": product.sku,
            "product_name": product.product_name, "brand": product.brand,
            "category": product.category, "subcategory": product.subcategory,
            "store_id": int(store.store_id), "store_name": store.store_name,
            "city": store.city, "region": store.region,
            "quantity_on_hand": quantity, "reorder_level": 10, "max_level": 200,
            "cost_price": 10.0, "inventory_value": quantity * 10.0,
            "units_sold_window": units, "lead_time_days": lead_time,
            "inventory_snapshot_date": "2026-09-30",
        }])
        source.attrs["window_days"] = rules.sales_window_days

        class CatalogQueries:
            aggregate_inventory = staticmethod(Queries.aggregate_inventory)
            finalize = staticmethod(Queries.finalize)

            def inventory_position(self, intent, rules, as_of, product_ids, store_ids,
                                   prev_product_ids=None, prev_store_ids=None):
                assert product_ids == [int(product.product_id)]
                assert store_ids == [int(store.store_id)]
                return QueryResult(source.copy(), ["SELECT product-store inventory"],
                                   [["start", "end", int(product.product_id), int(store.store_id)]], [])

        intent = Intent(
            capability="what_if",
            metrics=[],
            filters=Filters(products=[product.product_name], stores=[store.store_name]),
            time=TimeWindow(),
            scenario=Scenario(kind="demand_change_pct", value=demand_change_pct),
        )
        result = dispatch(
            CatalogQueries(), intent, rules, date(2026, 9, 30),
            product_ids=resolved.product_ids, store_ids=resolved.store_ids,
        )
        assert result.basis == "Simulated"
        assert len(result.df) == 1
        output = result.df.iloc[0]
        assert (int(output.product_id), int(output.store_id)) == (
            int(product.product_id), int(store.store_id),
        )
        if units > 0:
            actual = quantity / (units / rules.sales_window_days)
            factor = max(1 + demand_change_pct / 100, 0)
            simulated = quantity / ((units / rules.sales_window_days) * factor) if factor else None
            assert output.actual_days_of_inventory == pytest.approx(actual)
            if simulated is None:
                assert pd.isna(output.simulated_days_of_inventory)
            else:
                assert output.simulated_days_of_inventory == pytest.approx(simulated)
        else:
            assert pd.isna(output.actual_days_of_inventory)
            assert pd.isna(output.simulated_days_of_inventory)
        if lead_time is None and quantity > 0 and units > 0:
            assert output.stockout_risk_v1 == "Unknown"

        facts = _calculation_payload(intent, result.df, rules, result.basis)
        comparison = next(item for item in facts if item["metric"] == "what_if_comparison")
        assert comparison["values"]["scenario_value"] == demand_change_pct
        assert comparison["values"]["product_id"] == int(product.product_id)
        assert comparison["values"]["store_id"] == int(store.store_id)
        answer = build_answer(
            question=f"What if demand changes by {demand_change_pct}%?",
            intent=intent, df=result.df, basis=result.basis, rules=rules,
        )
        assert product.product_name in answer
        assert store.store_name in answer

