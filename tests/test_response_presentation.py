from __future__ import annotations

import pandas as pd

from app.answer_builder import build_answer
from app.calc.metrics import Rules
from app.dispatch import DispatchResult
from app.llm.schema import Filters, Intent, Sort, TimeWindow
from app.orchestrator import _calculation_payload, _presentation, _trust_panel_payload


def intent(capability="inventory_visibility", *, stores=None, group_by=None):
    return Intent(capability=capability, metrics=[], filters=Filters(stores=stores or []), group_by=group_by, time=TimeWindow(), threshold=None, sort=None, limit=None, scenario=None, uses_previous_result=False)


def base_rows():
    return pd.DataFrame([{"product_id": 1, "sku": "SKU-1", "product_name": "Laptop A", "store_id": 10, "store_name": "Anna Nagar", "city": "Chennai", "quantity_on_hand": 4, "inventory_snapshot_date": "2026-09-28", "subcategory": None}])


def test_single_store_result_hides_repeated_location_and_counts():
    _, display = _presentation(base_rows(), intent(stores=["Anna Nagar"]), "Show laptop inventory in Anna Nagar")
    assert "store_name" not in display["columns"] and "city" not in display["columns"]
    assert display["single_result_supporting_only"] is True
    assert display["show_counts"] is False and display["kpi_metrics"] == []


def test_multi_store_result_keeps_store_column_and_scalar_kpis():
    rows = pd.concat([base_rows(), base_rows().assign(product_id=2, store_id=11, store_name="T Nagar")], ignore_index=True)
    _, display = _presentation(rows, intent(), "Show laptop inventory by store")
    assert "store_name" in display["columns"] and display["single_result"] is False
    assert display["kpi_metrics"] == []  # inventory totals belong in the answer, not duplicated cards
    assert all(not isinstance(item["value"], (list, dict)) for item in display["kpi_metrics"])


def test_shared_snapshot_date_is_shown_once_and_column_omitted():
    _, display = _presentation(base_rows(), intent(), "Show inventory")
    assert "inventory_snapshot_date" not in display["columns"]
    assert display["shared_snapshot_date"] == "2026-09-28"


def test_catalog_discovery_table_includes_requested_group_dimension():
    rows = pd.DataFrame([
        {"category": "Laptops", "quantity_on_hand": 18},
        {"category": "Audio", "quantity_on_hand": 12},
    ])
    request = Intent(
        capability="inventory_visibility",
        question_kind="lookup",
        metrics=["quantity"],
        filters=Filters(),
        group_by="category",
    )

    table, _ = _presentation(rows, request, "What categories do we carry?")

    assert table.columns.tolist() == ["category", "quantity_on_hand"]
    assert table["category"].tolist() == ["Laptops", "Audio"]


def test_varying_snapshot_dates_keep_date_column():
    rows = pd.concat([base_rows(), base_rows().assign(product_id=2, inventory_snapshot_date="2026-09-29")], ignore_index=True)
    _, display = _presentation(rows, intent(), "Show inventory")
    assert "inventory_snapshot_date" in display["columns"] and display["shared_snapshot_date"] is None


def test_default_tables_do_not_expose_legacy_duplicate_status_columns():
    rows = base_rows().assign(inventory_status="Within Range", stock_status="Within Range", stockout_risk_v1="Low", stockout_risk="Low")
    _, display = _presentation(rows, intent("stockout_risk"), "Show stockout risk")
    assert "stockout_risk_v1" in display["columns"]
    assert "stockout_risk" not in display["columns"] and "stock_status" not in display["columns"]


def test_stockout_top_n_narrative_table_and_counts_share_position_scope():
    query_result = pd.DataFrame([
        {"product_name": "Risk item A", "store_name": "Store North", "city": "Pune",
         "quantity_on_hand": 2, "days_of_inventory": 1.5, "lead_time_days": 4,
         "stockout_risk_v1": "High"},
        {"product_name": "Risk item B", "store_name": "Store South", "city": "Pune",
         "quantity_on_hand": 5, "days_of_inventory": 3, "lead_time_days": 5,
         "stockout_risk_v1": "Medium"},
    ])
    query_result.attrs["result_counts"] = {
        "matching_result_rows": 8,
        "distinct_products": 6,
        "product_store_positions": 8,
        "stores": 4,
    }
    request = Intent(
        capability="stockout_risk", question_kind="ranking",
        metrics=["stockout_risk", "days_of_inventory"], filters=Filters(),
        sort=Sort(by="stockout_risk", direction="desc"), group_by="product", limit=2,
    )

    table, display = _presentation(query_result, request, "Which two products have highest stockout risk?")
    narrative = build_answer(
        question="Which two products have highest stockout risk?",
        intent=request,
        df=query_result,
    )

    assert display["initial_display_count"] == 2
    assert display["available_result_count"] == 2
    assert display["result_counts"]["matching_result_rows"] == 8
    assert {"product_name", "store_name", "city", "quantity_on_hand", "stockout_risk_v1", "days_of_inventory", "lead_time_days"}.issubset(table.columns)
    assert "highest-risk position is" in narrative
    assert "8 qualifying positions" in narrative
    assert "2 results are shown in the table" in narrative
    assert table["product_name"].iloc[0] in narrative
    assert table["product_name"].iloc[1] not in narrative
    trust_calculations = _calculation_payload(request, query_result, Rules(), "Actual")
    trust_ranking = next(item for item in trust_calculations if item["metric"] == "stockout_risk_top_n_positions")
    assert trust_ranking["values"]["qualifying_positions"] == 8
    assert trust_ranking["values"]["qualifying_distinct_products"] == 6
    assert trust_ranking["values"]["displayed_positions"] == len(table) == 2
    assert [row["product_name"] for row in trust_ranking["result"]] == table["product_name"].tolist()
    assert [row["stockout_risk_v1"] for row in trust_ranking["result"]] == table["stockout_risk_v1"].tolist()
    assert [row["days_of_inventory"] for row in trust_ranking["result"]] == table["days_of_inventory"].tolist()
    trust = _trust_panel_payload(
        question="Which two products have highest stockout risk?",
        intent=request,
        interpretation=["catalog filters resolved"],
        result=DispatchResult(query_result, ["SELECT ..."], [[]], [], "Actual"),
        rules=Rules(),
    )
    assert trust["query"]["row_count"] == 8
    assert trust["query"]["returned_result_rows"] == len(table) == 2
    assert trust["query"]["result_counts"]["distinct_products"] == 6
    assert trust["calculations"] == trust_calculations
