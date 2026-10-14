from pathlib import Path
import re

import pandas as pd
import pytest

from app.answer_builder import build_answer
from app.context import UserContext, build_followups
from app.llm.intent import _normalise
from app.llm.schema import Filters, Intent, TimeWindow
from app.orchestrator import _presentation, _time_window_issue


def test_catalog_fixture_covers_all_categories_and_assigned_subcategories():
    manifest = pd.read_csv(Path(__file__).parents[1] / "PRODUCT_SUBCATEGORY_COMPLETE_MANIFEST.csv")
    assert len(manifest) == 150
    assert manifest["category"].nunique() == 7
    load = (Path(__file__).parents[1] / "PRODUCT_SUBCATEGORY_150_PRODUCTION_LOAD_FIXED.sql").read_text(encoding="utf-8")
    rows = re.findall(r"\((\d+),N'[^']*',N'[^']*',N'([^']*)',N'([^']*)'\)", load)
    assert len(rows) == 150
    assert len({category for _, category, _ in rows}) == 7
    assert len({subcategory for _, _, subcategory in rows}) == 26


def test_historical_snapshot_date_is_normalized_for_display_once():
    intent = Intent(
        capability="inventory_visibility",
        filters=Filters(products=["MacBook Air M3"], stores=["Gachibowli"]),
        time=TimeWindow(kind="month", month=6, year=2026),
    )
    frame = pd.DataFrame([
        {"product_name": "MacBook Air M3", "store_name": "Gachibowli", "city": "Hyderabad",
         "quantity_on_hand": 8, "snapshot_date": "2026-06-29"},
        {"product_name": "MacBook Air M3", "store_name": "Gachibowli", "city": "Hyderabad",
         "quantity_on_hand": 9, "snapshot_date": "2026-06-29"},
    ])
    view, display = _presentation(frame, intent, "Show laptop inventory at Gachibowli in June 2026")
    assert display["shared_snapshot_date"] == "2026-06-29"
    assert "inventory_snapshot_date" not in display["columns"]


def test_from_to_sales_period_is_normalized_to_full_calendar_months():
    intent = Intent(
        capability="sales_trend", metrics=["sales_revenue"], filters=Filters(),
        time=TimeWindow(),
    )
    normalized = _normalise(
        intent,
        "How did total sales revenue change from August to September 2026?",
    )
    assert normalized.question_kind == "comparison"
    assert normalized.time.kind == "month"
    assert (normalized.time.month, normalized.time.year) == (9, 2026)
    assert (normalized.time.comparison_month, normalized.time.comparison_year) == (8, 2026)


def test_unambiguous_two_month_comparison_preserves_same_shared_year():
    intent = Intent(capability="sales_trend", metrics=["sales_revenue"], filters=Filters())
    normalized = _normalise(intent, "Compare revenue from May to June 2026")
    assert (normalized.time.month, normalized.time.year) == (6, 2026)
    assert (normalized.time.comparison_month, normalized.time.comparison_year) == (5, 2026)


def test_explicit_two_inventory_measures_are_preserved_for_scatter_intent():
    intent = Intent(
        capability="inventory_visibility",
        metrics=["inventory_value"],
        filters=Filters(),
    )
    normalized = _normalise(
        intent,
        "For each product, compare quantity on hand with inventory value.",
    )

    assert normalized.capability == "inventory_visibility"
    assert normalized.question_kind == "comparison"
    assert normalized.group_by == "product"
    assert normalized.metrics == ["quantity", "inventory_value"]


def test_explicit_demand_scenario_is_normalized_without_llm_capability_guess():
    intent = Intent(
        capability="inventory_visibility",
        metrics=["quantity"],
        filters=Filters(cities=["Connaught Place"]),
    )
    normalized = _normalise(
        intent,
        "For products in Connaught Place, compare actual and simulated days of "
        "inventory if demand increases by 20%.",
    )

    assert normalized.capability == "what_if"
    assert normalized.question_kind == "scenario"
    assert normalized.group_by == "product"
    assert normalized.metrics == ["days_of_inventory"]
    assert normalized.scenario.kind == "demand_change_pct"
    assert normalized.scenario.value == 20


def test_snapshot_question_is_normalized_to_historical_capability():
    intent = Intent(
        capability="inventory_visibility",
        metrics=["quantity"],
        filters=Filters(),
    )
    normalized = _normalise(
        intent,
        "Show inventory quantity across the latest two available snapshots.",
    )

    assert normalized.capability == "historical_analysis"
    assert normalized.question_kind == "comparison"
    assert normalized.time.kind == "latest"


def test_current_month_sales_comparison_sets_previous_period_window():
    intent = Intent(
        capability="sales_trend",
        metrics=["sales_units"],
        filters=Filters(),
        group_by="brand",
    )
    normalized = _normalise(
        intent,
        "Compare sales units by brand for the current month and the previous month.",
    )

    assert normalized.question_kind == "comparison"
    assert normalized.time.kind == "this_month"
    assert normalized.time.compare_to_previous is True


@pytest.mark.parametrize("question", [
    "Compare Samsung inventory between June 2026 and July 2026",
    "Compare Samsung inventory between June and July 2026",
])
def test_between_month_inventory_comparison_uses_later_month_as_current(question):
    intent = Intent(capability="inventory_visibility", metrics=["quantity"], filters=Filters())
    normalized = _normalise(intent, question)
    assert normalized.question_kind == "comparison"
    assert normalized.time.kind == "month"
    assert (normalized.time.month, normalized.time.year) == (7, 2026)
    assert (normalized.time.comparison_month, normalized.time.comparison_year) == (6, 2026)


def test_followups_keep_product_location_and_time_context():
    intent = Intent(
        capability="inventory_visibility",
        filters=Filters(products=["iPhone 16"], stores=["T Nagar"]),
        time=TimeWindow(kind="month", month=5, year=2026),
    )
    frame = pd.DataFrame([
        {"product_name": "iPhone 16", "store_name": "T Nagar", "city": "Chennai"}
    ])
    suggestions = build_followups(
        question="Show iPhone 16 inventory at T Nagar in May 2026",
        intent=intent,
        dataframe=frame,
        response_type="inventory_health",
        persona="Planner",
        authorized_scope=UserContext(authorized_store_ids=(7,)),
    )
    assert len(suggestions) == 3
    assert any("iPhone 16" in item and "May 2026" in item for item in suggestions)
    assert all("requested products" not in item.casefold() for item in suggestions)


def test_each_supported_answer_has_contextual_followups():
    intent = Intent(
        capability="what_if",
        filters=Filters(products=["iPhone 16"], stores=["T Nagar"]),
        time=TimeWindow(kind="latest"),
    )
    frame = pd.DataFrame([
        {"product_name": "iPhone 16", "store_name": "T Nagar", "city": "Chennai"}
    ])
    for response_type in ("inventory_visibility", "sales", "stockout_risk", "scenario"):
        suggestions = build_followups(
            question="Show iPhone 16 results at T Nagar",
            intent=intent,
            dataframe=frame,
            response_type=response_type,
            persona="Planner",
        )
        assert 2 <= len(suggestions) <= 3
        assert any("iPhone 16" in item or "T Nagar" in item for item in suggestions)


def test_business_answers_avoid_technical_scope_placeholders():
    intent = Intent(capability="sales_trend", filters=Filters(categories=["Laptops"]))
    frame = pd.DataFrame([{"units": 12, "revenue": 125000}])
    answer = build_answer(question="Show laptop sales", intent=intent, df=frame)
    lowered = answer.casefold()
    assert "requested products" not in lowered
    assert "selected scope" not in lowered
    assert "inr" in lowered


def test_month_year_normalization_is_stable_for_repeated_question():
    first = Intent(capability="sales_trend", filters=Filters(products=["iPhone 16"]), time=TimeWindow(kind="exact_date", date="2026-05-01"))
    second = Intent(capability="sales_trend", filters=Filters(products=["iPhone 16"]), time=TimeWindow(kind="exact_date", date="2026-05-01"))
    a = _normalise(first, "Show iPhone 16 sales in May 2026 at T Nagar")
    b = _normalise(second, "Show iPhone 16 sales in May 2026 at T Nagar")
    assert a.time.model_dump() == b.time.model_dump()


def test_month_clarification_distinguishes_missing_month_and_year():
    assert "month and year" in _time_window_issue(Intent(capability="inventory_visibility", time=TimeWindow(kind="month")))
    assert "year" in _time_window_issue(Intent(capability="inventory_visibility", time=TimeWindow(kind="month", month=6)))
    assert "month" in _time_window_issue(Intent(capability="inventory_visibility", time=TimeWindow(kind="month", year=2026)))


def test_historical_inventory_uses_inventory_visibility_response_type_and_followups():
    intent = Intent(
        capability="inventory_visibility",
        filters=Filters(products=["MacBook Air M3"], stores=["Gachibowli"]),
        time=TimeWindow(kind="month", month=6, year=2026),
    )
    frame = pd.DataFrame([{
        "product_name": "MacBook Air M3",
        "store_name": "Gachibowli",
        "city": "Hyderabad",
        "quantity_on_hand": 8,
        "snapshot_date": "2026-06-29",
    }])
    _, display = _presentation(frame, intent, "Show laptop inventory at Gachibowli in June 2026")
    assert display["type"] == "inventory_visibility"
    suggestions = build_followups(
        question="Show laptop inventory at Gachibowli in June 2026",
        intent=intent,
        dataframe=frame,
        response_type=display["type"],
        persona="Planner",
    )
    assert 2 <= len(suggestions) <= 3
    assert any("Gachibowli" in item or "MacBook Air M3" in item for item in suggestions)
