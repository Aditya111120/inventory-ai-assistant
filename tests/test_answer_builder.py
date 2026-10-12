import pandas as pd

from app.answer_builder import (
    _build_ranking_answer,
    build_answer,
    build_subject_comparison_answer,
    concise_leadership_answer,
)
from app.calc.metrics import Rules
from app.llm.schema import Filters, Intent, Sort, TimeWindow


def make_intent(
    *,
    metrics,
    sort=None,
    capability="inventory_visibility",
):
    return Intent(
        capability=capability,
        metrics=metrics,
        filters=Filters(),
        sort=sort,
        group_by="product",
    )


def test_lowest_inventory_uses_quantity_not_doi():
    intent = make_intent(
        metrics=["quantity"],
        sort=Sort(
            by="quantity",
            direction="asc",
        ),
    )

    df = pd.DataFrame(
        [
            {
                "product_name": "LEN-LAP-3",
                "quantity_on_hand": 120,
                "days_of_inventory": 67.92,
            },
            {
                "product_name": "LEN-LAP-6",
                "quantity_on_hand": 123,
                "days_of_inventory": 73.80,
            },
        ]
    )

    answer = build_answer(
        question="Which Lenovo laptops have the lowest inventory?",
        intent=intent,
        df=df,
    )

    assert answer == (
        "LEN-LAP-3 has the lowest inventory, "
        "with 120 units on hand."
    )


def test_why_lowest_inventory_is_deterministic():
    intent = make_intent(
        metrics=["quantity"],
        sort=Sort(
            by="quantity",
            direction="asc",
        ),
    )

    df = pd.DataFrame(
        [
            {
                "product_name": "LEN-LAP-3",
                "quantity_on_hand": 120,
            },
            {
                "product_name": "LEN-LAP-6",
                "quantity_on_hand": 123,
            },
        ]
    )

    answer = build_answer(
        question="Why does LEN-LAP-3 have the lowest inventory?",
        intent=intent,
        df=df,
    )

    assert "120 units" in answer
    assert "123 units" in answer
    assert "LEN-LAP-3" in answer


def test_why_lowest_inventory_explains_comparison():
    intent = make_intent(
        metrics=["quantity"],
        sort=Sort(
            by="quantity",
            direction="asc",
        ),
    )

    df = pd.DataFrame(
        [
            {"product_name": "ThinkPad T14 Gen 5", "quantity_on_hand": 120},
            {"product_name": "IdeaPad Pro 5", "quantity_on_hand": 123},
            {"product_name": "IdeaPad Slim 5", "quantity_on_hand": 137},
        ]
    )

    answer = build_answer(
        question=(
            "Why does ThinkPad T14 Gen 5 "
            "have the lowest inventory?"
        ),
        intent=intent,
        df=df,
    )

    assert "ThinkPad T14 Gen 5" in answer
    assert "120 units" in answer
    assert "IdeaPad Pro 5" in answer
    assert "123 units" in answer
    assert "lowest" in answer.lower()


def test_why_named_product_not_actually_lowest():
    intent = make_intent(
        metrics=["quantity"],
        sort=Sort(
            by="quantity",
            direction="asc",
        ),
    )

    df = pd.DataFrame(
        [
            {"product_name": "ThinkPad T14 Gen 5", "quantity_on_hand": 150},
            {"product_name": "IdeaPad Pro 5", "quantity_on_hand": 120},
        ]
    )

    answer = build_answer(
        question=(
            "Why does ThinkPad T14 Gen 5 "
            "have the lowest inventory?"
        ),
        intent=intent,
        df=df,
    )

    assert "does not have the lowest inventory" in answer
    assert "120 units" in answer


def test_compound_question_does_not_repeat_question():
    intent = make_intent(
        metrics=["quantity"],
        sort=Sort(by="quantity", direction="asc"),
    )
    df = pd.DataFrame(
        [
            {"product_name": "ThinkPad T14 Gen 5", "quantity_on_hand": 120},
            {"product_name": "IdeaPad Pro 5", "quantity_on_hand": 123},
            {"product_name": "ThinkPad E14 Gen 6", "quantity_on_hand": 167},
        ]
    )

    answer = build_answer(
        question=(
            "Why does ThinkPad T14 Gen 5 have the lowest inventory "
            "and what should we do to increase sales?"
        ),
        intent=intent,
        df=df,
    )

    assert answer
    assert "ThinkPad T14 Gen 5" in answer
    assert "120 units" in answer
    assert "why does thinkpad t14 gen 5" not in answer.casefold()


def test_highest_sales_is_deterministic():
    intent = make_intent(
        capability="sales_trend",
        metrics=["sales_units"],
        sort=Sort(
            by="sales_units",
            direction="desc",
        ),
    )

    df = pd.DataFrame(
        [
            {
                "product_name": "Product A",
                "units": 229,
            },
            {
                "product_name": "Product B",
                "units": 180,
            },
        ]
    )

    answer = build_answer(
        question="Which product sold the most?",
        intent=intent,
        df=df,
    )

    assert answer == (
        "Product A has the highest sales, "
        "with 229 units sold."
    )


def test_what_if_is_marked_by_scenario():
    from app.llm.schema import Scenario

    intent = make_intent(
        capability="what_if",
        metrics=[],
    )

    intent.scenario = Scenario(
        kind="demand_change_pct",
        value=20,
    )

    df = pd.DataFrame(
        [
            {
                "product_name": "LEN-LAP-4",
                "days_of_inventory": 27.040816,
            }
        ]
    )

    answer = build_answer(
        question="What if demand increases by 20%?",
        intent=intent,
        df=df,
        basis="Simulated",
    )

    assert "20%" in answer
    assert "27.04" in answer


def test_multi_product_what_if_does_not_use_arbitrary_first_doi():
    from app.llm.schema import Scenario

    intent = make_intent(capability="what_if", metrics=[])
    intent.scenario = Scenario(kind="demand_change_pct", value=20)
    df = pd.DataFrame([
        {"product_id": 1, "store_id": 10, "product_name": "A", "days_of_inventory": 10},
        {"product_id": 2, "store_id": 10, "product_name": "B", "days_of_inventory": 20},
    ])
    answer = build_answer(question="What if demand increases by 20% for laptops?", intent=intent, df=df, basis="Simulated")
    assert "2 product-store positions" in answer
    assert "no single DOI" in answer
    assert "10 days" not in answer


def test_demand_what_if_names_the_resolved_product_and_location():
    from app.llm.schema import Scenario

    intent = make_intent(capability="what_if", metrics=[])
    intent.scenario = Scenario(kind="demand_change_pct", value=30)
    df = pd.DataFrame([{
        "product_id": 44, "store_id": 8, "product_name": "Catalog Earbuds",
        "store_name": "West Market", "city": "Mumbai", "quantity_on_hand": 60,
        "units_sold_window": 30, "actual_days_of_inventory": 60,
        "simulated_days_of_inventory": 46.153846, "days_of_inventory": 46.153846,
    }])
    answer = build_answer(question="What if demand increases?", intent=intent, df=df, basis="Simulated")

    assert "Catalog Earbuds" in answer
    assert "West Market, Mumbai" in answer
    assert "30%" in answer
    assert "60 days" in answer and "46.15 days" in answer


def test_demand_what_if_with_zero_sales_explains_undefined_coverage():
    from app.llm.schema import Scenario

    intent = make_intent(capability="what_if", metrics=[])
    intent.scenario = Scenario(kind="demand_change_pct", value=20)
    df = pd.DataFrame([{
        "product_name": "No Sales Product", "store_name": "Central", "city": "Delhi",
        "quantity_on_hand": 12, "units_sold_window": 0,
        "actual_days_of_inventory": float("nan"), "simulated_days_of_inventory": float("nan"),
        "days_of_inventory": float("nan"),
    }])
    answer = build_answer(question="What if demand increases by 20%?", intent=intent, df=df, basis="Simulated")
    assert "no recorded sales" in answer.casefold()
    assert "cannot be calculated" in answer.casefold()
    assert "Central, Delhi" in answer


def test_sales_aggregate_does_not_invent_top_product():
    intent = make_intent(capability="sales_trend", metrics=["sales_units"])
    df = pd.DataFrame([{"units": 14176, "revenue": 2500000}])
    answer = build_answer(question="How many sales were recorded?", intent=intent, df=df)
    assert "authorized stores" in answer
    assert "top matching product" not in answer.casefold()
    assert "14,176" in answer
    assert "INR 2,500,000.00" in answer


def test_sales_ranking_names_the_actual_winner():
    intent = make_intent(capability="sales_trend", metrics=["sales_units"])
    intent.question_kind = "ranking"
    intent.sort = Sort(by="sales_units", direction="desc")
    df = pd.DataFrame([
        {"product_name": "iPhone 16", "units": 165},
        {"product_name": "Laptop A", "units": 90},
    ])
    answer = build_answer(question="Which product sold the most?", intent=intent, df=df)
    assert "iPhone 16" in answer and "165" in answer


def test_store_ranking_names_group_winner_and_requested_top_three():
    intent = Intent(
        capability="sales_trend", question_kind="ranking", metrics=["sales_revenue"],
        filters=Filters(), group_by="store", sort=Sort(by="sales_revenue", direction="desc"), limit=3,
    )
    frame = pd.DataFrame([
        {"store_name": "Store North", "revenue": 3000},
        {"store_name": "Store Central", "revenue": 2000},
        {"store_name": "Store South", "revenue": 1000},
    ])
    answer = build_answer(question="Which three stores generated highest sales revenue?", intent=intent, df=frame)
    assert "highest store by sales revenue is Store North" in answer
    assert "INR 3,000.00" in answer
    assert "3 returned results" in answer
    assert "Store South" not in answer
    assert "no single product winner" not in answer.casefold()


def test_stockout_ranking_names_product_store_and_coverage_in_v1_order():
    intent = Intent(
        capability="stockout_risk", question_kind="ranking",
        metrics=["stockout_risk", "days_of_inventory"], filters=Filters(),
        group_by="product", sort=Sort(by="stockout_risk", direction="desc"), limit=2,
    )
    frame = pd.DataFrame([
        {"product_name": "Product B", "store_name": "Store 2", "city": "Pune", "stockout_risk_v1": "Medium", "days_of_inventory": 8},
        {"product_name": "Product A", "store_name": "Store 1", "city": "Delhi", "stockout_risk_v1": "High", "days_of_inventory": 3},
    ])

    answer = _build_ranking_answer(intent, frame)

    assert "Product A at Store 1, Delhi (High risk; 3 days of coverage)" in answer
    assert "2 qualifying positions" in answer
    assert "Product B" not in answer


def test_stockout_top_n_narrative_matches_returned_positions_and_total_matches():
    intent = Intent(
        capability="stockout_risk", question_kind="ranking",
        metrics=["stockout_risk", "days_of_inventory"], filters=Filters(),
        group_by="product", sort=Sort(by="stockout_risk", direction="desc"), limit=2,
    )
    frame = pd.DataFrame([
        {"product_name": "Catalog Product A", "store_name": "Store East", "city": "Pune",
         "stockout_risk_v1": "High", "days_of_inventory": 2, "lead_time_days": 5},
        {"product_name": "Catalog Product B", "store_name": "Store West", "city": "Delhi",
         "stockout_risk_v1": "Medium", "days_of_inventory": 8, "lead_time_days": 7},
    ])
    frame.attrs["result_counts"] = {
        "matching_result_rows": 9,
        "distinct_products": 7,
        "product_store_positions": 9,
        "stores": 4,
        "high_risk_positions": 3,
        "medium_risk_positions": 6,
    }

    answer = build_answer(
        question="Which two products have highest stockout risk and their coverage?",
        intent=intent,
        df=frame,
    )

    assert "highest-risk position is Catalog Product A" in answer
    assert "Catalog Product A at Store East, Pune (High risk; 2 days of coverage; 5-day supplier lead time)" in answer
    assert "2 results are shown in the table" in answer
    assert "Catalog Product B" not in answer
    assert "may run out before" not in answer
    assert "affecting 7 products across 4 stores" not in answer
    assert "Business Rules V1" in answer
    assert "sales observed during" in answer
    assert "Basis: Actual." in answer
    assert len(answer.split()) < 60


def test_revenue_period_comparison_leads_with_revenue_and_calendar_months():
    intent = Intent(
        capability="sales_trend", question_kind="comparison", metrics=["sales_revenue"],
        filters=Filters(), time=TimeWindow(kind="month", month=9, year=2026,
                                          comparison_month=8, comparison_year=2026),
    )
    frame = pd.DataFrame([{
        "units": 130, "units_prev": 100, "revenue": 2600,
        "revenue_prev": 2000,
    }])
    answer = build_answer(question="How did total sales revenue change from August to September 2026?", intent=intent, df=frame)
    assert "INR 2,000.00 in August 2026" in answer
    assert "INR 2,600.00 in September 2026" in answer
    assert "INR 600.00" in answer and "+30.0%" in answer
    assert answer.index("Sales revenue") < answer.index("Supporting sales volume")


def test_subject_comparison_answer_reports_winner():
    answer = build_subject_comparison_answer(
        subject_name="LEN-LAP-4",
        subject_value=180,
        winner_name="LEN-LAP-9",
        winner_value=229,
        metric_label="sales",
    )

    assert answer == (
        "No. LEN-LAP-4 has 180 sales, while "
        "LEN-LAP-9 is highest at 229."
    )


def test_subject_comparison_answer_confirms_subject_winner():
    answer = build_subject_comparison_answer(
        subject_name="LEN-LAP-4",
        subject_value=229,
        winner_name="LEN-LAP-4",
        winner_value=229,
        metric_label="sales",
    )

    assert answer == "Yes. LEN-LAP-4 has the highest sales with 229."


def test_group_comparison_reports_deterministic_metric_gap():
    frame = pd.DataFrame([{"category": "Laptops", "inventory_value": 900.0}, {"category": "Audio", "inventory_value": 600.0}])
    intent = Intent(capability="inventory_visibility", question_kind="comparison", group_by="category", metrics=["inventory_value"], filters=Filters())
    answer = build_answer(question="Compare categories by inventory value", intent=intent, df=frame)
    assert "Laptops leads" in answer
    assert "INR 900.00" in answer and "INR 600.00" in answer
    assert "INR 300.00" in answer and "50.0% higher" in answer
    assert "Actual result" in answer
    assert len(answer.split()) < 60


def test_inventory_value_lookup_explains_sum_of_quantity_times_unit_cost():
    intent = Intent(
        capability="inventory_visibility", question_kind="lookup",
        metrics=["inventory_value"], filters=Filters(),
    )
    frame = pd.DataFrame([
        {"product_id": 1, "store_id": 10, "quantity_on_hand": 2, "cost_price": 100, "inventory_value": 200, "inventory_snapshot_date": "2026-09-30"},
        {"product_id": 2, "store_id": 10, "quantity_on_hand": 3, "cost_price": 150, "inventory_value": 450, "inventory_snapshot_date": "2026-09-30"},
    ])
    frame.attrs["result_counts"] = {
        "inventory_value_total": 650,
        "product_store_positions": 2,
    }

    answer = build_answer(
        question="What is the inventory value?",
        intent=intent,
        df=frame,
        basis="Actual",
    )

    assert "INR 650.00" in answer
    assert "2 product-store positions" in answer
    assert "Basis: Actual." in answer
    assert "quantity on hand multiplied by unit cost" not in answer
    assert len(answer.split()) < 60


def test_slow_moving_answer_reports_store_units_and_actual_observation_window():
    intent = Intent(
        capability="excess_slow_moving", question_kind="ranking",
        inventory_classification="slow_moving_candidate",
        metrics=["sales_units"],
        filters=Filters(stores=["Chennai Central"]),
    )
    frame = pd.DataFrame([{
        "product_id": 1, "store_id": 10, "product_name": "Phone A",
        "store_name": "Chennai Central", "city": "Chennai",
        "units_sold_window": 2, "demand_status": "Slow-moving Candidate",
    }])
    frame.attrs.update({
        "sales_window_start_exclusive": "2026-08-31",
        "sales_window_end_inclusive": "2026-09-30",
        "result_counts": {
            "matching_result_rows": 1, "product_store_positions": 1,
            "distinct_products": 1,
        },
    })

    answer = build_answer(
        question="Show slow-moving candidates",
        intent=intent,
        df=frame,
        rules=Rules(),
    )

    assert "1 slow-moving candidates (1–2 recorded sales units)" in answer
    assert "Phone A at Chennai Central, Chennai recorded 2 sales units" in answer
    assert "after 2026-08-31 through 2026-09-30" in answer
    assert "Examples:" not in answer
    assert len(answer.split()) < 60
    assert len([part for part in answer.split(". ") if part]) <= 3


def test_concise_policy_keeps_result_and_simulation_caveat_without_table_examples():
    answer = concise_leadership_answer(
        "Lenovo leads by 12 units in the selected scope. "
        "Examples: Lenovo A, Lenovo B, and Lenovo C appear in the table. "
        "This is a simulation, not a forecast."
    )

    assert answer.startswith("Lenovo leads by 12 units")
    assert "Examples:" not in answer
    assert "simulation, not a forecast" in answer
    assert len(answer.split()) < 60
    assert answer.count(".") <= 2


def test_zero_sales_answer_is_separate_from_slow_moving_candidates():
    intent = Intent(
        capability="excess_slow_moving", question_kind="ranking",
        inventory_classification="no_recorded_sales",
        metrics=["sales_units"], filters=Filters(),
    )
    frame = pd.DataFrame([{
        "product_id": 1, "store_id": 10, "product_name": "Phone A",
        "store_name": "Central", "city": "Chennai", "units_sold_window": 0,
    }])
    frame.attrs["window_days"] = 30
    frame.attrs["result_counts"] = {
        "product_store_positions": 1, "distinct_products": 1,
    }

    answer = build_answer(
        question="Show products with no recorded sales",
        intent=intent,
        df=frame,
        rules=Rules(),
    )

    assert "positions with no recorded sales (0 units)" in answer
    assert "Slow-moving Candidate" not in answer


def test_empty_slow_moving_result_explains_zero_sales_is_separate():
    intent = Intent(
        capability="excess_slow_moving", question_kind="ranking",
        inventory_classification="slow_moving_candidate",
        metrics=["sales_units"], filters=Filters(),
    )
    frame = pd.DataFrame(columns=["product_id", "store_id"])
    frame.attrs.update({
        "sales_window_start_exclusive": "2026-08-31",
        "sales_window_end_inclusive": "2026-09-30",
        "result_counts": {"product_store_positions": 0, "distinct_products": 0},
    })

    answer = build_answer(
        question="Show slow-moving candidates",
        intent=intent,
        df=frame,
        rules=Rules(),
    )

    assert "No product-store positions met the 1–2 recorded sales-unit" in answer
    assert "Zero-sales positions are classified separately" in answer


def test_historical_analysis_explains_both_snapshot_dates():
    frame = pd.DataFrame([{"category": "Laptops", "qty_before": 100, "qty_now": 80, "qty_change": -20, "snapshot_date_before": "2026-08-31", "snapshot_date": "2026-09-28"}])
    intent = Intent(capability="historical_analysis", question_kind="lookup", metrics=["quantity"], filters=Filters())
    answer = build_answer(question="Show historical inventory trend", intent=intent, df=frame)
    assert "100 units" in answer and "80 units" in answer
    assert "2026-08-31" in answer and "2026-09-28" in answer


def test_stockout_answer_uses_position_counts_location_and_rule_values():
    intent = Intent(
        capability="stockout_risk",
        metrics=["stockout_risk"],
        filters=Filters(),
    )
    frame = pd.DataFrame([
        {
            "product_id": 1,
            "sku": "SKU-0001",
            "product_name": "iPhone 16",
            "store_id": 10,
            "store_name": "Chennai Central",
            "city": "Chennai",
            "quantity_on_hand": 12,
            "units_sold_window": 60,
            "avg_daily_sales": 2.0,
            "days_of_inventory": 6.0,
            "lead_time_days": 10,
            "stockout_risk": "High",
        },
        {
            "product_id": 1,
            "sku": "SKU-0001",
            "product_name": "iPhone 16",
            "store_id": 11,
            "store_name": "Chennai South",
            "city": "Chennai",
            "quantity_on_hand": 20,
            "units_sold_window": 30,
            "avg_daily_sales": 1.0,
            "days_of_inventory": 20.0,
            "lead_time_days": 10,
            "stockout_risk": "Medium",
        },
    ])
    frame.attrs["window_days"] = 30
    frame.attrs["result_counts"] = {
        "matching_result_rows": 2,
        "distinct_products": 1,
        "product_store_positions": 2,
        "stores": 2,
        "high_risk_positions": 1,
        "medium_risk_positions": 1,
    }

    answer = build_answer(
        question="Which products are at risk in Chennai?",
        intent=intent,
        df=frame,
        rules=Rules(safety_buffer_days=7),
    )

    assert "1 High-risk position and 1 Medium-risk position qualify" in answer
    assert "among 1 product across 2 stores" in answer
    assert "Chennai Central, Chennai" in answer
    assert "12 units on hand" in answer
    assert "6 days of coverage" in answer
    assert "10 days of supplier lead time" in answer
    assert "Business Rules V1 uses coverage against supplier lead time" in answer
    assert "sales observed during the approved 30-day sales window" in answer
    assert "may run out before" not in answer


def test_inventory_lookup_summary_is_scope_aware_and_counts_positions():
    intent = make_intent(metrics=["quantity"])
    frame = pd.DataFrame([
        {
            "product_id": 1,
            "product_name": "iPhone 16",
            "store_id": 10,
            "store_name": "Chennai Central",
            "city": "Chennai",
            "quantity_on_hand": 45,
        },
        {
            "product_id": 1,
            "product_name": "iPhone 16",
            "store_id": 11,
            "store_name": "Chennai South",
            "city": "Chennai",
            "quantity_on_hand": 55,
        },
    ])
    frame.attrs["result_counts"] = {
        "matching_result_rows": 2,
        "distinct_products": 1,
        "product_store_positions": 2,
        "stores": 2,
        "quantity_on_hand_total": 100.0,
    }

    answer = build_answer(
        question="Show iPhone 16 inventory in Chennai",
        intent=intent,
        df=frame,
    )

    assert "iPhone 16: 100 units are available in Chennai" in answer
    assert "product-store positions" not in answer
    assert "distinct product" not in answer
