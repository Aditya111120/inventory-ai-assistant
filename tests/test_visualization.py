import pandas as pd
import pytest
from pydantic import ValidationError

from app.llm.schema import Filters, Intent, TimeWindow
from app.visualization import VisualizationSpec, select_visualization


def make_intent(
    capability="inventory_visibility",
    *,
    metrics=None,
    group_by=None,
    question_kind="lookup",
    time=None,
    visualization_goal="automatic",
    visualization_preference=None,
):
    return Intent(
        capability=capability,
        question_kind=question_kind,
        metrics=metrics or [],
        filters=Filters(),
        group_by=group_by,
        time=time or TimeWindow(),
        visualization_goal=visualization_goal,
        visualization_preference=visualization_preference,
        threshold=None,
        sort=None,
        limit=None,
        scenario=None,
        uses_previous_result=False,
    )


def test_single_value_sales_response_selects_kpi_from_deterministic_aggregate():
    frame = pd.DataFrame([{"units": 145, "revenue": 43210.5}])
    spec = select_visualization(
        intent=make_intent("sales_trend", metrics=["sales_units", "sales_revenue"]),
        dataframe=frame,
        basis="Actual",
    )

    assert spec["type"] == "kpi"
    assert spec["data"] == [
        {"label": "Sales units", "value": 145, "unit": "units"},
        {"label": "Sales revenue", "value": 43210.5, "unit": "currency", "currency": "INR"},
    ]
    assert spec["data"][1]["currency"] == "INR"


def test_single_metric_kpi_uses_complete_dispatched_aggregate_counts():
    spec = select_visualization(
        intent=make_intent(metrics=["inventory_value"]),
        dataframe=pd.DataFrame([{"product_name": "Preview item", "inventory_value": 50}]),
        basis="Actual",
        presentation={
            "result_counts": {
                "inventory_value_total": 1250,
                "matching_result_rows": 30,
            },
        },
    )

    assert spec["type"] == "kpi"
    assert spec["data"] == [{
        "label": "Inventory value",
        "value": 1250,
        "unit": "currency",
        "currency": "INR",
    }]


def test_category_bars_are_aggregated_and_sorted_highest_first():
    frame = pd.DataFrame([
        {"category": "Audio", "inventory_value": 12},
        {"category": "Laptops", "inventory_value": 40},
        {"category": "Audio", "inventory_value": 8},
    ])
    spec = select_visualization(
        intent=make_intent(metrics=["inventory_value"], group_by="category"),
        dataframe=frame,
        basis="Actual",
    )

    assert spec["type"] == "bar"
    assert spec["data"] == [
        {"label": "Laptops", "value": 40},
        {"label": "Audio", "value": 20},
    ]
    assert spec["series"][0]["currency"] == "INR"


@pytest.mark.parametrize(
    "group_by,column",
    [
        ("product", "product_name"),
        ("brand", "brand"),
        ("category", "category"),
        ("city", "city"),
        ("store", "store_name"),
    ],
)
def test_supported_inventory_dimensions_produce_sorted_bars(group_by, column):
    frame = pd.DataFrame([
        {column: "Lower", "quantity_on_hand": 3},
        {column: "Higher", "quantity_on_hand": 8},
    ])
    spec = select_visualization(
        intent=make_intent(metrics=["quantity"], group_by=group_by),
        dataframe=frame,
        basis="Actual",
    )

    assert spec["type"] == "bar"
    assert spec["category_field"] == "label"
    assert spec["data"] == [
        {"label": "Higher", "value": 8},
        {"label": "Lower", "value": 3},
    ]


def test_brand_comparison_uses_current_and_previous_period_values():
    frame = pd.DataFrame([
        {"brand": "Brand B", "units": 5, "units_prev": 9},
        {"brand": "Brand A", "units": 12, "units_prev": 10},
    ])
    spec = select_visualization(
        intent=make_intent(
            "sales_trend",
            metrics=["sales_units"],
            group_by="brand",
            question_kind="comparison",
        ),
        dataframe=frame,
        basis="Actual",
    )

    assert spec["type"] == "grouped_bar"
    assert spec["data"] == [
        {"label": "Brand A", "current": 12, "previous": 10},
        {"label": "Brand B", "current": 5, "previous": 9},
    ]


def test_historical_inventory_two_snapshots_use_grouped_comparison():
    frame = pd.DataFrame([
        {
            "category": "Audio",
            "qty_before": 25,
            "qty_now": 20,
            "snapshot_date_before": "2026-08-31",
            "snapshot_date": "2026-09-28",
        },
        {
            "category": "Laptops",
            "qty_before": 15,
            "qty_now": 30,
            "snapshot_date_before": "2026-08-31",
            "snapshot_date": "2026-09-28",
        },
    ])
    spec = select_visualization(
        intent=make_intent(
            "historical_analysis",
            metrics=["quantity"],
            group_by="category",
            time=TimeWindow(kind="month", year=2026, month=9),
        ),
        dataframe=frame,
        basis="Actual",
    )

    assert spec["type"] == "grouped_bar"
    assert spec["series"][0]["label"] == "2026-08-31"
    assert spec["series"][1]["label"] == "2026-09-28"
    assert spec["data"] == [
        {"label": "Audio", "earlier": 25, "current": 20},
        {"label": "Laptops", "earlier": 15, "current": 30},
    ]
    assert "not a continuous time series" in spec["description"]


def test_inventory_status_donut_requires_complete_classification_scope():
    frame = pd.DataFrame({
        "inventory_status": ["Within Stock Limits", "Within Stock Limits", "Out of Stock"],
    })
    intent = make_intent("inventory_health")
    presentation = {
        "result_counts": {"matching_result_rows": 3},
        "kpi_metrics": [
            {"key": "distinct_products", "label": "Products", "value": 3},
            {"key": "quantity_on_hand", "label": "Inventory units", "value": 20},
        ],
    }

    complete = select_visualization(
        intent=intent,
        dataframe=frame,
        basis="Actual",
        presentation=presentation,
    )
    partial = select_visualization(
        intent=intent,
        dataframe=frame,
        basis="Actual",
        presentation={"result_counts": {"matching_result_rows": 8}},
    )

    assert complete["type"] == "donut"
    assert complete["data"][0] == {"label": "Within Stock Limits", "count": 2}
    assert partial is None


def test_actual_and_simulated_scenario_uses_only_backend_values():
    frame = pd.DataFrame([
        {"product_name": "Item A", "actual_days_of_inventory": 12, "simulated_days_of_inventory": 8},
        {"product_name": "Item B", "actual_days_of_inventory": 20, "simulated_days_of_inventory": 14},
    ])
    spec = select_visualization(
        intent=make_intent("what_if", metrics=["days_of_inventory"]),
        dataframe=frame,
        basis="Simulated",
    )

    assert spec["type"] == "grouped_bar"
    assert spec["basis"] == "Simulated"
    assert spec["data"][0] == {"label": "Item A", "actual": 12, "simulated": 8}


def test_authorized_subset_is_the_only_source_of_chart_points():
    authorized_products = ["Allowed A", "Allowed B"]
    frame = pd.DataFrame([
        {"product_name": "Allowed A", "quantity_on_hand": 8},
        {"product_name": "Allowed B", "quantity_on_hand": 4},
    ])
    intent = make_intent(metrics=["quantity"], group_by="product")
    intent.filters.products = authorized_products
    spec = select_visualization(intent=intent, dataframe=frame, basis="Actual")

    assert spec["scope"]["products"] == authorized_products
    assert {point["label"] for point in spec["data"]} == set(authorized_products)


def test_inventory_value_vs_sales_velocity_uses_scatter_at_product_grain():
    frame = pd.DataFrame([
        {"product_name": "A", "inventory_value": 20, "units": 2},
        {"product_name": "B", "inventory_value": 65, "units": 5},
        {"product_name": "C", "inventory_value": 100, "units": 8},
    ])
    spec = select_visualization(
        intent=make_intent(
            metrics=["inventory_value", "sales_units"],
            group_by="product",
            visualization_goal="relationship",
        ),
        dataframe=frame,
        basis="Actual",
    )

    assert spec["type"] == "scatter"
    assert spec["series"][0]["unit"] == "currency"
    assert spec["series"][0]["currency"] == "INR"
    assert spec["series"][1]["label"] == "Sales units"


@pytest.mark.parametrize(
    "frame,intent",
    [
        (pd.DataFrame(), make_intent()),
        (pd.DataFrame([{"category": "X", "inventory_value": float("nan")}]), make_intent(metrics=["inventory_value"], group_by="category")),
        (pd.DataFrame([{"units": 2, "sale_date": "2026-01-01"}]), make_intent("sales_trend", metrics=["sales_units"])),
    ],
)
def test_empty_missing_values_and_nonchronological_sales_do_not_create_misleading_chart(frame, intent):
    spec = select_visualization(intent=intent, dataframe=frame, basis="Actual")
    if intent.capability == "sales_trend" and not frame.empty:
        assert spec["type"] == "kpi"
        assert spec["type"] != "line"
    else:
        assert spec is None


def test_large_category_results_are_explicitly_limited_and_sorted():
    frame = pd.DataFrame([
        {"category": f"Category {index}", "units": index}
        for index in range(15)
    ])
    spec = select_visualization(
        intent=make_intent("sales_trend", metrics=["sales_units"], group_by="category"),
        dataframe=frame,
        basis="Actual",
    )

    assert spec["type"] == "bar"
    assert len(spec["data"]) == 10
    assert spec["data"][0]["label"] == "Category 14"
    assert spec["complete"] is False
    assert "Top 10" in spec["limitation"]


def test_paginated_grouped_result_does_not_create_a_misleading_chart():
    frame = pd.DataFrame([
        {"category": "Laptops", "inventory_value": 50},
        {"category": "Audio", "inventory_value": 20},
    ])
    spec = select_visualization(
        intent=make_intent(metrics=["inventory_value"], group_by="category"),
        dataframe=frame,
        basis="Actual",
        presentation={"result_counts": {"matching_result_rows": 150}},
    )

    assert spec is None


def test_stockout_risk_ranking_by_store_is_a_sorted_high_risk_bar():
    frame = pd.DataFrame([
        {"store_name": "Alpha", "stockout_risk_v1": "High"},
        {"store_name": "Alpha", "stockout_risk_v1": "High"},
        {"store_name": "Alpha", "stockout_risk_v1": "Medium"},
        {"store_name": "Beta", "stockout_risk_v1": "High"},
        {"store_name": "Gamma", "stockout_risk_v1": "Low"},
    ])
    spec = select_visualization(
        intent=make_intent(
            "stockout_risk", metrics=["stockout_risk"], group_by="store",
            question_kind="ranking", visualization_goal="ranking",
        ),
        dataframe=frame,
        basis="Actual",
        presentation={"result_counts": {"matching_result_rows": len(frame)}},
    )

    assert spec["type"] == "bar"
    assert spec["data"] == [
        {"label": "Alpha", "value": 2},
        {"label": "Beta", "value": 1},
        {"label": "Gamma", "value": 0},
    ]


def test_stockout_risk_distribution_is_donut_only_without_explicit_group():
    frame = pd.DataFrame({
        "stockout_risk_v1": ["High", "High", "Medium", "Low", None],
    })
    spec = select_visualization(
        intent=make_intent(
            "stockout_risk", metrics=["stockout_risk"],
            visualization_goal="distribution",
        ),
        dataframe=frame,
        basis="Actual",
        presentation={"result_counts": {"matching_result_rows": len(frame)}},
    )

    assert spec["type"] == "donut"
    assert {point["label"]: point["count"] for point in spec["data"]} == {
        "High": 2, "Medium": 1, "Low": 1, "Unknown": 1,
    }
    assert sum(point["count"] for point in spec["data"]) == len(frame)


def test_stockout_classifications_by_store_use_multiple_series_not_donut():
    frame = pd.DataFrame([
        {"store_name": "Alpha", "stockout_risk_v1": "High"},
        {"store_name": "Alpha", "stockout_risk_v1": "Medium"},
        {"store_name": "Beta", "stockout_risk_v1": "High"},
        {"store_name": "Beta", "stockout_risk_v1": "Low"},
    ])
    spec = select_visualization(
        intent=make_intent(
            "stockout_risk", metrics=["stockout_risk"], group_by="store",
            question_kind="comparison", visualization_goal="comparison",
        ),
        dataframe=frame,
        basis="Actual",
        presentation={"result_counts": {"matching_result_rows": len(frame)}},
    )

    assert spec["type"] == "grouped_bar"
    assert [series["label"] for series in spec["series"]] == ["High", "Medium", "Low"]
    assert spec["data"] == [
        {"label": "Alpha", "class_0": 1, "class_1": 1, "class_2": 0},
        {"label": "Beta", "class_0": 1, "class_1": 0, "class_2": 1},
    ]


def test_inventory_value_by_brand_is_a_bar_and_total_is_a_kpi():
    grouped = select_visualization(
        intent=make_intent(metrics=["inventory_value"], group_by="brand"),
        dataframe=pd.DataFrame([
            {"brand": "Brand B", "inventory_value": 20},
            {"brand": "Brand A", "inventory_value": 30},
        ]),
        basis="Actual",
    )
    total = select_visualization(
        intent=make_intent(metrics=["inventory_value"], visualization_goal="kpi"),
        dataframe=pd.DataFrame([{"inventory_value": 50}]),
        basis="Actual",
        presentation={"result_counts": {"inventory_value_total": 50}},
    )

    assert grouped["type"] == "bar"
    assert grouped["data"] == [
        {"label": "Brand A", "value": 30},
        {"label": "Brand B", "value": 20},
    ]
    assert total["type"] == "kpi"
    assert total["data"] == [{
        "label": "Inventory value", "value": 50, "unit": "currency", "currency": "INR",
    }]


def test_explicit_monthly_sales_trend_uses_sorted_valid_date_observations():
    frame = pd.DataFrame([
        {"month": "2026-03-01", "units": 20},
        {"month": "2026-01-01", "units": 10},
        {"month": "2026-02-01", "units": 15},
    ])
    spec = select_visualization(
        intent=make_intent(
            "sales_trend", metrics=["sales_units"], visualization_goal="trend",
        ),
        dataframe=frame,
        basis="Actual",
    )

    assert spec["type"] == "line"
    assert spec["data"] == [
        {"date": "2026-01-01", "value": 10},
        {"date": "2026-02-01", "value": 15},
        {"date": "2026-03-01", "value": 20},
    ]


def test_incomplete_classification_distribution_and_unsupported_preference_fall_back():
    frame = pd.DataFrame({
        "stockout_risk_v1": ["High", "Medium"],
    })
    incomplete = select_visualization(
        intent=make_intent(
            "stockout_risk", visualization_goal="distribution",
        ),
        dataframe=frame,
        basis="Actual",
        presentation={"result_counts": {"matching_result_rows": 12}},
    )
    unsupported = select_visualization(
        intent=make_intent(
            metrics=["inventory_value"], group_by="brand",
            visualization_preference="heatmap",
        ),
        dataframe=pd.DataFrame([{"brand": "A", "inventory_value": 10}]),
        basis="Actual",
    )

    assert incomplete is None
    assert unsupported is None


def test_intent_without_visualization_fields_keeps_automatic_behavior():
    intent = Intent.model_validate({
        "capability": "inventory_visibility",
        "metrics": ["inventory_value"],
        "group_by": "brand",
        "filters": {},
    })

    assert intent.visualization_goal == "automatic"
    assert intent.visualization_preference is None


def test_metadata_schema_rejects_invalid_visualization_type_and_missing_series():
    with pytest.raises(ValidationError):
        VisualizationSpec.model_validate({
            "type": "pie",
            "title": "Invalid",
            "series": [],
            "data": [],
            "basis": "Actual",
        })


def test_currency_metadata_requires_currency_code():
    with pytest.raises(ValidationError):
        VisualizationSpec.model_validate({
            "type": "bar",
            "title": "Revenue",
            "series": [{"key": "value", "label": "Revenue", "unit": "currency"}],
            "data": [{"label": "X", "value": 10}],
            "basis": "Actual",
        })


def test_line_metadata_rejects_invalid_or_missing_dates():
    with pytest.raises(ValidationError):
        VisualizationSpec.model_validate({
            "type": "line",
            "title": "Historical",
            "date_field": "date",
            "series": [{"key": "value", "label": "Value", "unit": "units"}],
            "data": [{"date": "not-a-date", "value": 10}],
            "basis": "Actual",
        })


def test_unsupported_capability_and_unknown_currency_data_fail_closed():
    frame = pd.DataFrame([{"category": "X", "revenue": 10}])
    assert select_visualization(
        intent=make_intent("clarification", metrics=["sales_revenue"], group_by="category"),
        dataframe=frame,
        basis="Actual",
    ) is None
    assert select_visualization(
        intent=make_intent("sales_trend", metrics=["sales_revenue"]),
        dataframe=frame,
        basis="Unknown",
    ) is None
