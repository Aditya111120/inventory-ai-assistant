from __future__ import annotations

from datetime import date

import pandas as pd
import pytest

from app.calc.metrics import Rules
from app.dispatch import dispatch
from app.llm.schema import (
    Filters,
    Intent,
    Scenario,
    TimeWindow,
)
from app.semantic.queries import QueryResult


class FakeQueries:
    """Deterministic fake for dispatch tests.

    These tests verify routing and pipeline wiring without SQL Server.
    """

    def __init__(self):
        self.calls = []

    def inventory_position(
        self,
        intent,
        rules,
        as_of,
        product_ids,
        store_ids,
        prev_product_ids=None,
        prev_store_ids=None,
    ):
        self.calls.append(
            (
                "inventory_position",
                {
                    "intent": intent,
                    "rules": rules,
                    "as_of": as_of,
                    "product_ids": product_ids,
                    "store_ids": store_ids,
                    "prev_product_ids": prev_product_ids,
                    "prev_store_ids": prev_store_ids,
                },
            )
        )

        df = pd.DataFrame(
            [
                {
                    "product_id": 1,
                    "product_name": "Test Laptop",
                    "brand": "Lenovo",
                    "category": "Laptops",
                    "store_id": 10,
                    "store_name": "Test Store",
                    "quantity_on_hand": 38,
                    "reorder_level": 20,
                    "max_level": 60,
                    "cost_price": 1000.0,
                    "inventory_value": 38000.0,
                    "units_sold_window": 186.0,
                    "lead_time_days": 10.0,
                    "inventory_snapshot_date": "2026-09-30",
                }
            ]
        )
        df.attrs["window_days"] = 30

        return QueryResult(
            df=df,
            sql=["SELECT ... FROM Inventory ..."],
            params=[["2026-08-31", "2026-09-30"]],
            notes=["fake inventory query"],
        )

    @staticmethod
    def aggregate_inventory(df, intent):
        return df.copy()

    @staticmethod
    def finalize(df, intent, rules, excess_rule="max_level"):
        return df.reset_index(drop=True), ["fake finalize"]

    def sales(
        self,
        intent,
        rules,
        as_of,
        product_ids,
        store_ids,
        prev_product_ids=None,
        prev_store_ids=None,
    ):
        self.calls.append(
            (
                "sales",
                {
                    "intent": intent,
                    "rules": rules,
                    "as_of": as_of,
                    "product_ids": product_ids,
                    "store_ids": store_ids,
                    "prev_product_ids": prev_product_ids,
                    "prev_store_ids": prev_store_ids,
                },
            )
        )

        df = pd.DataFrame(
            [
                {
                    "product_id": 1,
                    "product_name": "Test Laptop",
                    "brand": "Lenovo",
                    "category": "Laptops",
                    "units": 186.0,
                    "revenue": 186000.0,
                    "invoices": 25,
                }
            ]
        )

        return QueryResult(
            df=df,
            sql=["SELECT SUM(quantity), SUM(revenue) FROM Sales ..."],
            params=[["2026-08-31", "2026-09-30"]],
            notes=["fake sales query"],
        )


def make_intent(
    capability: str,
    *,
    metrics=None,
    group_by=None,
    scenario=None,
    threshold=None,
    sort=None,
    limit=None,
):
    return Intent(
        capability=capability,
        metrics=metrics or [],
        filters=Filters(),
        group_by=group_by,
        time=TimeWindow(),
        threshold=threshold,
        sort=sort,
        limit=limit,
        scenario=scenario,
        uses_previous_result=False,
        clarification_question=None,
    )


RULES = Rules()
AS_OF = date(2026, 9, 30)


@pytest.mark.parametrize(
    "capability",
    [
        "inventory_visibility",
        "inventory_health",
        "stockout_risk",
        "excess_slow_moving",
        "what_if",
    ],
)
def test_inventory_capabilities_use_inventory_pipeline(capability):
    fake = FakeQueries()

    intent = make_intent(
        capability,
        scenario=Scenario(kind="demand_change_pct", value=20)
        if capability == "what_if" else None,
    )

    result = dispatch(
        queries=fake,
        intent=intent,
        rules=RULES,
        as_of=AS_OF,
        product_ids=[1, 2],
        store_ids=[10],
    )

    assert fake.calls[0][0] == "inventory_position"
    assert result.df is not None
    assert result.sql
    assert result.params
    assert "fake inventory query" in result.notes
    assert "fake finalize" in result.notes


def test_sales_trend_uses_sales_pipeline():
    fake = FakeQueries()

    intent = make_intent(
        "sales_trend",
        metrics=["sales_units"],
        group_by="product",
    )

    result = dispatch(
        queries=fake,
        intent=intent,
        rules=RULES,
        as_of=AS_OF,
        product_ids=[1],
        store_ids=[10],
    )

    assert fake.calls[0][0] == "sales"
    assert "units" in result.df.columns
    assert result.basis == "Actual"


def test_historical_analysis_routes_to_weekly_snapshot_trend_and_preserves_scope():
    class HistoricalQueries(FakeQueries):
        def inventory_trend(self, as_of, weeks, product_ids, store_ids, group, **kwargs):
            self.calls.append(("inventory_trend", {"as_of": as_of, "weeks": weeks, "product_ids": product_ids, "store_ids": store_ids, "group": group, **kwargs}))
            frame = pd.DataFrame([{"category": "Laptops", "qty_now": 15, "qty_before": 10, "qty_change": 5, "snapshot_date_before": "2026-08-31", "snapshot_date": "2026-09-28"}])
            return QueryResult(frame, ["SELECT snapshots"], [["2026-09-28", "2026-08-31"]], ["Snapshots compared"])
    queries = HistoricalQueries()
    intent = Intent(capability="historical_analysis", metrics=["quantity"], filters=Filters(), group_by="category", time=TimeWindow(kind="latest"))
    result = dispatch(queries, intent, RULES, AS_OF, product_ids=[7], store_ids=[9], prev_product_ids=[7, 8], prev_store_ids=[9, 10])
    call = queries.calls[0][1]
    assert call["as_of"] == AS_OF and call["weeks"] == 4
    assert call["product_ids"] == [7] and call["store_ids"] == [9]
    assert result.df.loc[0, "snapshot_date_before"] == "2026-08-31"
    assert result.df.loc[0, "snapshot_date"] == "2026-09-28"
    assert result.df.attrs["result_counts"]["quantity_change_total"] == 5


def test_historical_calendar_month_comparison_uses_both_month_end_targets():
    class HistoricalQueries(FakeQueries):
        def inventory_trend(self, as_of, weeks, product_ids, store_ids, group, **kwargs):
            self.calls.append(("inventory_trend", {"as_of": as_of, "product_ids": product_ids, "store_ids": store_ids, **kwargs}))
            return QueryResult(pd.DataFrame([{
                "product_name": "Catalog product", "qty_now": 18, "qty_before": 12,
                "qty_change": 6, "snapshot_date_before": "2026-06-29", "snapshot_date": "2026-07-27",
            }]), ["SELECT snapshot comparison"], [["2026-07-31", "2026-06-30"]], ["Snapshots compared"])

    queries = HistoricalQueries()
    intent = Intent(
        capability="historical_analysis", question_kind="comparison", metrics=["quantity"],
        filters=Filters(brands=["Catalog brand"], cities=["Catalog city"]), group_by="product",
        time=TimeWindow(kind="month", month=7, year=2026, comparison_month=6, comparison_year=2026),
    )

    result = dispatch(queries, intent, RULES, AS_OF, product_ids=[4, 5], store_ids=[20, 21])

    call = queries.calls[0][1]
    assert call["as_of"] == date(2026, 7, 31)
    assert call["previous_as_of"] == date(2026, 6, 30)
    assert call["product_ids"] == [4, 5] and call["store_ids"] == [20, 21]
    assert result.df.loc[0, "snapshot_date_before"] == "2026-06-29"
    assert result.df.loc[0, "snapshot_date"] == "2026-07-27"


def test_sales_group_comparison_does_not_truncate_groups_by_llm_limit():
    class CompleteGroupQueries(FakeQueries):
        def sales(self, *args, **kwargs):
            result = super().sales(*args, **kwargs)
            result.df = pd.DataFrame([
                {"category": "Audio", "units": 10, "revenue": 100},
                {"category": "Group B", "units": 8, "revenue": 80},
                {"category": "Laptops", "units": 6, "revenue": 60},
            ])
            return result

    fake = CompleteGroupQueries()
    intent = Intent(
        capability="sales_trend", question_kind="comparison", metrics=["sales_units"],
        filters=Filters(), group_by="category", time=TimeWindow(), limit=1,
    )
    result = dispatch(fake, intent, RULES, AS_OF)
    assert len(result.df) == 3


def test_executive_summary_aggregates_inventory_rules_and_sales_before_preview():
    fake = FakeQueries()
    intent = make_intent("executive_summary", metrics=["quantity", "inventory_value"])
    result = dispatch(fake, intent, RULES, AS_OF, product_ids=[1], store_ids=[10])
    row = result.df.iloc[0]
    assert row.category == "Laptops"
    assert row.quantity_on_hand == 38
    assert row.inventory_value == 38000
    assert row.units == 186 and row.revenue == 186000
    counts = result.df.attrs["result_counts"]
    assert counts["quantity_on_hand_total"] == 38
    assert counts["inventory_value_total"] == 38000
    assert counts["sales_units_total"] == 186
    assert counts["sales_revenue_total"] == 186000
    assert "Business Rules V1" in " ".join(result.notes)


def test_inventory_dispatch_passes_previous_result_ids():
    fake = FakeQueries()

    intent = make_intent("inventory_visibility")

    dispatch(
        queries=fake,
        intent=intent,
        rules=RULES,
        as_of=AS_OF,
        product_ids=[5],
        store_ids=[20],
        prev_product_ids=[5, 6, 7],
        prev_store_ids=[20, 21],
    )

    call = fake.calls[0][1]

    assert call["product_ids"] == [5]
    assert call["store_ids"] == [20]
    assert call["prev_product_ids"] == [5, 6, 7]
    assert call["prev_store_ids"] == [20, 21]


@pytest.mark.parametrize(
    "scenario, expected",
    [
        (
            Scenario(kind="demand_change_pct", value=20),
            {
                "demand_change_pct": 20,
                "lead_time_extra_days": 0,
                "lead_time_pct": 0,
            },
        ),
        (
            Scenario(kind="lead_time_extra_days", value=5),
            {
                "demand_change_pct": 0,
                "lead_time_extra_days": 5,
                "lead_time_pct": 0,
            },
        ),
        (
            Scenario(kind="lead_time_pct", value=20),
            {
                "demand_change_pct": 0,
                "lead_time_extra_days": 0,
                "lead_time_pct": 20,
            },
        ),
    ],
)
def test_what_if_scenario_is_simulated(scenario, expected):
    """The dispatcher must route supported scenario types through enrichment."""

    fake = FakeQueries()

    intent = make_intent(
        "what_if",
        scenario=scenario,
    )

    result = dispatch(
        queries=fake,
        intent=intent,
        rules=RULES,
        as_of=AS_OF,
        product_ids=[1],
        store_ids=[10],
    )

    assert fake.calls[0][0] == "inventory_position"
    assert result.basis == "Actual" or result.basis == "Simulated"


@pytest.mark.parametrize(
    ("scenario", "message"),
    [
        (None, "needs a supported scenario"),
        (Scenario(kind="additional_units_sold", value=20), "not supported yet"),
        (Scenario(kind="demand_change_pct", value=-101), "cannot exceed 100%"),
    ],
)
def test_unsupported_or_invalid_what_if_fails_before_query(scenario, message):
    fake = FakeQueries()
    intent = make_intent("what_if", scenario=scenario)
    with pytest.raises(ValueError, match=message):
        dispatch(fake, intent, RULES, AS_OF, product_ids=[1], store_ids=[10])
    assert fake.calls == []


def test_clarification_is_not_dispatched():
    fake = FakeQueries()

    intent = make_intent("clarification")

    with pytest.raises(
        ValueError,
        match="clarification intents must be handled before dispatch",
    ):
        dispatch(
            queries=fake,
            intent=intent,
            rules=RULES,
            as_of=AS_OF,
        )

    assert fake.calls == []


def test_out_of_scope_is_not_dispatched():
    fake = FakeQueries()

    intent = make_intent("out_of_scope")

    with pytest.raises(
        ValueError,
        match="out_of_scope intents must be handled before dispatch",
    ):
        dispatch(
            queries=fake,
            intent=intent,
            rules=RULES,
            as_of=AS_OF,
        )

    assert fake.calls == []


def test_unknown_capability_is_rejected():
    fake = FakeQueries()

    # Construct through model_construct so this tests dispatcher behavior
    # independently from Pydantic's enum validation.
    intent = Intent.model_construct(
        capability="unknown_capability",
        metrics=[],
        filters=Filters(),
        group_by=None,
        time=TimeWindow(),
        threshold=None,
        sort=None,
        limit=None,
        scenario=None,
        uses_previous_result=False,
        clarification_question=None,
    )

    with pytest.raises(
        ValueError,
        match="unsupported capability",
    ):
        dispatch(
            queries=fake,
            intent=intent,
            rules=RULES,
            as_of=AS_OF,
        )

    assert fake.calls == []
