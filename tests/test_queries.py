import sqlite3
from datetime import date

import pandas as pd
import pytest

from app.calc.metrics import Rules as BusinessRules, enrich_inventory
from app.dispatch import dispatch
from app.llm.schema import Filters, Intent, Scenario, Sort, TimeWindow
from app.semantic.queries import Queries, restrict, window_for


class R:
    sales_window_days = 30
    excess_days_of_inventory = 60


def run(conn, sql, params):
    return pd.read_sql_query(sql, conn, params=params)


@pytest.fixture
def q():
    c = sqlite3.connect(":memory:")
    c.executescript(
        """
        CREATE TABLE Products(product_id INT, sku TEXT, product_name TEXT, brand TEXT, category TEXT, model TEXT, cost_price REAL);
        CREATE TABLE Stores(store_id INT, store_name TEXT, city TEXT, region TEXT);
        CREATE TABLE SupplierProducts(supplier_id INT, product_id INT, lead_time_days INT, is_primary INT);
        CREATE TABLE Inventory(product_id INT, store_id INT, quantity_on_hand INT, reorder_level INT, max_level INT, as_of_date TEXT);
        CREATE TABLE Sales(sale_id INT, invoice_no TEXT, sale_date TEXT, product_id INT, store_id INT, quantity INT, revenue REAL);
        CREATE TABLE InventorySnapshotWeekly(product_id INT, store_id INT, snapshot_date TEXT, quantity_on_hand INT);
        INSERT INTO Products VALUES (1,'A','P1','Lenovo','Laptops','m',100),(2,'B','P2','Dell','Laptops','m',200);
        INSERT INTO Stores VALUES (1,'S1','Delhi','North'),(2,'S2','Pune','West');
        INSERT INTO SupplierProducts VALUES (1,1,10,1),(1,2,5,1);
        INSERT INTO Inventory VALUES (1,1,40,10,30,'2026-09-30'),(1,2,5,10,30,'2026-09-30'),(2,1,20,10,30,'2026-09-30'),(2,2,20,10,30,'2026-09-30');
        INSERT INTO Sales VALUES (1,'I1','2026-09-30',1,1,6,600),(2,'I1','2026-09-30',2,1,3,600),
                                 (3,'I2','2026-08-31',1,1,99,9900),
                                 (4,'I3','2026-08-20',1,1,10,1000);
        INSERT INTO InventorySnapshotWeekly VALUES (1,1,'2026-09-28',35),(1,2,'2026-09-28',7);
        """
    )
    return Queries(run, c, schema="")


def test_as_of(q):
    assert q.as_of() == date(2026, 9, 30)


def test_available_calendar_periods_come_from_sales_and_snapshot_data(q):
    assert q.sales_periods() == [(2026, 9), (2026, 8)]
    assert q.snapshot_periods() == [(2026, 9)]


def test_window_boundary_excludes_day_30_back(q):
    class I:
        time = None
        group_by = []

    r = q.inventory_position(I, R, date(2026, 9, 30), None, None)
    assert r.df.units_sold_window.sum() == 9


def test_sqlserver_inventory_query_left_joins_subcategories():
    captured = []
    def fake_run(conn, sql, params):
        captured.append(sql)
        return pd.DataFrame({"product_id": [1], "subcategory": [None], "quantity_on_hand": [2]})
    query = Queries(fake_run, object(), schema="dbo.")
    class I:
        time = None
        group_by = []
    query.inventory_position(I, R, date(2026, 9, 30), None, None)
    assert "LEFT JOIN dbo.ProductSubcategories ps" in captured[0]
    assert "ps.subcategory_id = p.subcategory_id" in captured[0]


def test_inventory_query_keeps_products_without_primary_supplier():
    captured = []
    def fake_run(conn, sql, params):
        captured.append(sql)
        return pd.DataFrame({"product_id": [1], "quantity_on_hand": [2]})
    query = Queries(fake_run, object(), schema="dbo.")
    class I:
        time = None
        group_by = []
    query.inventory_position(I, R, date(2026, 9, 30), None, None)
    assert "LEFT JOIN (" in captured[0]
    assert "MIN(lead_time_days)" in captured[0]


def test_restrict_intersection():
    assert restrict([1, 2], [2, 3]) == [2]
    assert restrict([], [2, 3]) == [2, 3]
    assert restrict(None, None) is None
    assert restrict([1], [2]) == []


def test_empty_ids_return_nothing(q):
    class I:
        time = None
        group_by = []

    assert (
        q.inventory_position(
            I, R, date(2026, 9, 30), [], None, prev_product_ids=[1]
        ).row_count
        == 2
    )
    assert (
        q.inventory_position(
            I, R, date(2026, 9, 30), [1], None, prev_product_ids=[2]
        ).row_count
        == 0
    )


def test_group_by_not_allowlisted(q):
    class I:
        time = None
        group_by = ["x; DROP TABLE Sales"]

    with pytest.raises(ValueError):
        q.sales(I, R, date(2026, 9, 30), None, None)


def test_growth(q):
    class T:
        kind = "last_n_days"
        n = 30
        compare_to_previous = True

    class I:
        time = T
        group_by = ["category"]

    r = q.sales(I, R, date(2026, 9, 30), None, None)
    row = r.df.iloc[0]
    assert row.units == 9 and row.units_prev == 109
    assert row.growth_pct == -91.7


def test_explicit_calendar_month_comparison_uses_both_complete_month_windows(q):
    class T:
        kind = "month"
        month = 9
        year = 2026
        comparison_month = 8
        comparison_year = 2026
        comparison_start_date = None
        comparison_end_date = None
        compare_to_previous = False

    class I:
        time = T()
        group_by = ["category"]

    result = q.sales(I, R, date(2026, 9, 30), None, None)
    row = result.df.iloc[0]
    assert row.units == 9
    assert row.units_prev == 109
    assert row.units_change == -100
    assert any("Compared with comparison period" in note for note in result.notes)
    assert result.params == [
        ["2026-08-31", "2026-09-30"],
        ["2026-07-31", "2026-08-31"],
    ]


def test_window_supports_exact_date_month_and_range():
    class I:
        time = type("T", (), {"kind": "exact_date", "date": "2026-08-20"})()
    assert window_for(I, R, date(2026, 9, 30))[:2] == (date(2026, 8, 19), date(2026, 8, 20))
    I.time = type("T", (), {"kind": "month", "year": 2026, "month": 8})()
    assert window_for(I, R, date(2026, 9, 30))[:2] == (date(2026, 7, 31), date(2026, 8, 31))
    I.time = type("T", (), {"kind": "date_range", "start_date": "2026-08-01", "end_date": "2026-08-20"})()
    assert window_for(I, R, date(2026, 9, 30))[:2] == (date(2026, 7, 31), date(2026, 8, 20))


def test_historical_inventory_uses_weekly_snapshot_and_discloses_actual_date(q):
    class T:
        kind = "exact_date"
        date = "2026-09-30"
    class I:
        time = T()
        group_by = []
    result = q.inventory_snapshot(I, date(2026, 9, 30), None, None)
    assert result.df.quantity_on_hand.tolist() == [35, 7]
    assert any("weekly snapshot 2026-09-28" in note for note in result.notes)


def test_inventory_trend_returns_actual_weekly_snapshot_dates_and_deltas(q):
    q.conn.execute("INSERT INTO InventorySnapshotWeekly VALUES (1,1,'2026-08-31',30)")
    q.conn.execute("INSERT INTO InventorySnapshotWeekly VALUES (2,1,'2026-08-31',15)")
    result = q.inventory_trend(date(2026, 9, 30), 4, [1, 2], [1], ("category",))
    assert set(result.df.snapshot_date) == {"2026-09-28"}
    assert set(result.df.snapshot_date_before) == {"2026-08-31"}
    laptop = result.df.iloc[0]
    assert laptop.qty_now == 35 and laptop.qty_before == 45 and laptop.qty_change == -10


def test_exact_future_sales_date_returns_no_rows_without_substitution(q):
    class T:
        kind = "exact_date"
        date = "2030-01-01"
        compare_to_previous = False
    class I:
        time = T()
        group_by = ["city"]
    result = q.sales(I, R, date(2026, 9, 30), None, None)
    assert result.df.empty


def test_aggregate_to_product_level(q):
    class I:
        time = None
        group_by = []

    r = q.inventory_position(I, R, date(2026, 9, 30), None, None)
    agg = Queries.aggregate_inventory(r.df, I)
    assert len(agg) == 2 and agg.set_index("product_id").loc[1, "quantity_on_hand"] == 45
    assert "inventory_snapshot_date" in agg.columns


def test_stockout_finalize_excludes_low_and_no_demand_and_orders_high_first():
    class I:
        capability = "stockout_risk"
        sort = None
        threshold = None
        limit = None

    frame = pd.DataFrame([
        {"product_name": "Low", "stockout_risk": "Low", "days_of_inventory": 40, "quantity_on_hand": 10},
        {"product_name": "No demand", "stockout_risk": "No demand", "days_of_inventory": None, "quantity_on_hand": 20},
        {"product_name": "Medium", "stockout_risk": "Medium", "days_of_inventory": 12, "quantity_on_hand": 5},
        {"product_name": "High", "stockout_risk": "High", "days_of_inventory": 3, "quantity_on_hand": 2},
    ])
    out, notes = Queries.finalize(frame, I, R())
    assert out.product_name.tolist() == ["High", "Medium"]
    assert any("High and Medium" in note for note in notes)


def test_stockout_risk_sort_uses_v1_priority_and_doi_not_lexical_labels():
    intent = Intent(
        capability="stockout_risk", metrics=["stockout_risk"],
        sort=Sort(by="stockout_risk", direction="desc"), limit=5,
    )
    frame = pd.DataFrame([
        {"product_name": "Medium slower", "stockout_risk_v1": "Medium", "stockout_risk": "High", "days_of_inventory": 12},
        {"product_name": "High slower", "stockout_risk_v1": "High", "stockout_risk": "Low", "days_of_inventory": 7},
        {"product_name": "High sooner", "stockout_risk_v1": "High", "stockout_risk": "Medium", "days_of_inventory": 2},
    ])

    result, _ = Queries.finalize(frame, intent, BusinessRules())

    assert result.product_name.tolist() == ["High sooner", "High slower", "Medium slower"]


def test_stockout_v1_ranking_classifies_full_scope_then_stably_limits_requested_top_n():
    intent = Intent(
        capability="stockout_risk", question_kind="ranking",
        metrics=["stockout_risk", "days_of_inventory"], filters=Filters(),
        group_by="product", sort=Sort(by="stockout_risk", direction="desc"), limit=5,
    )
    # Deliberately shuffled as a SQL result may be; the result must not rely on
    # source order. Equal risk/DOI positions use product_id/store_id ordering.
    raw = pd.DataFrame([
        {"product_id": 6, "store_id": 1, "product_name": "Medium tie later", "brand": "Brand C", "city": "Pune", "quantity_on_hand": 16, "reorder_level": 3, "max_level": 30, "units_sold_window": 30, "lead_time_days": 12},
        {"product_id": 8, "store_id": 1, "product_name": "No sales", "brand": "Brand D", "city": "Pune", "quantity_on_hand": 20, "reorder_level": 3, "max_level": 30, "units_sold_window": 0, "lead_time_days": 12},
        {"product_id": 1, "store_id": 2, "product_name": "High tie second", "brand": "Brand A", "city": "Delhi", "quantity_on_hand": 2, "reorder_level": 3, "max_level": 30, "units_sold_window": 30, "lead_time_days": 5},
        {"product_id": 7, "store_id": 1, "product_name": "Zero stock", "brand": "Brand E", "city": "Pune", "quantity_on_hand": 0, "reorder_level": 3, "max_level": 30, "units_sold_window": 30, "lead_time_days": 5},
        {"product_id": 4, "store_id": 1, "product_name": "Low risk", "brand": "Brand B", "city": "Pune", "quantity_on_hand": 20, "reorder_level": 3, "max_level": 30, "units_sold_window": 30, "lead_time_days": 12},
        {"product_id": 3, "store_id": 1, "product_name": "High later DOI", "brand": "Brand B", "city": "Chennai", "quantity_on_hand": 4, "reorder_level": 3, "max_level": 30, "units_sold_window": 30, "lead_time_days": 5},
        {"product_id": 9, "store_id": 1, "product_name": "Missing lead", "brand": "Brand F", "city": "Pune", "quantity_on_hand": 2, "reorder_level": 3, "max_level": 30, "units_sold_window": 30, "lead_time_days": float("nan")},
        {"product_id": 5, "store_id": 1, "product_name": "Medium first DOI", "brand": "Brand C", "city": "Delhi", "quantity_on_hand": 15, "reorder_level": 3, "max_level": 30, "units_sold_window": 30, "lead_time_days": 12},
        {"product_id": 10, "store_id": 1, "product_name": "Medium later DOI A", "brand": "Brand G", "city": "Pune", "quantity_on_hand": 17, "reorder_level": 3, "max_level": 30, "units_sold_window": 30, "lead_time_days": 12},
        {"product_id": 11, "store_id": 1, "product_name": "Medium later DOI B", "brand": "Brand G", "city": "Delhi", "quantity_on_hand": 18, "reorder_level": 3, "max_level": 30, "units_sold_window": 30, "lead_time_days": 12},
        {"product_id": 1, "store_id": 1, "product_name": "High tie first", "brand": "Brand A", "city": "Pune", "quantity_on_hand": 2, "reorder_level": 3, "max_level": 30, "units_sold_window": 30, "lead_time_days": 5},
    ])
    raw.attrs["window_days"] = 30

    enriched = enrich_inventory(raw, BusinessRules())
    risk_by_identity = enriched.set_index(["product_id", "store_id"])["stockout_risk_v1"]
    assert risk_by_identity.loc[(7, 1)] == "Out of Stock"
    assert risk_by_identity.loc[(8, 1)] == "Not Assessable - No Recorded Demand"
    assert risk_by_identity.loc[(9, 1)] == "Unknown"
    expected = [(1, 1), (1, 2), (3, 1), (5, 1), (6, 1)]
    for limit in (1, 3, 5):
        result, _ = Queries.finalize(enriched, intent.model_copy(update={"limit": limit}), BusinessRules())
        assert list(zip(result["product_id"], result["store_id"])) == expected[:limit]
        assert result.attrs["result_counts"]["matching_result_rows"] == 7
        assert result.attrs["result_counts"]["distinct_products"] == 6
        assert result.attrs["result_counts"]["high_risk_positions"] == 3
        assert result.attrs["result_counts"]["medium_risk_positions"] == 4
    result, _ = Queries.finalize(enriched, intent, BusinessRules())
    assert result["product_name"].tolist() == [
        "High tie first", "High tie second", "High later DOI", "Medium first DOI", "Medium tie later",
    ]
    assert result["stockout_risk_v1"].tolist() == ["High", "High", "High", "Medium", "Medium"]
    assert result["days_of_inventory"].tolist() == [2.0, 2.0, 4.0, 15.0, 16.0]
    # Zero stock, zero demand, low risk and missing lead time are not silently
    # upgraded into High/Medium qualifying risk under Business Rules V1.
    assert "Zero stock" not in result["product_name"].tolist()
    assert "No sales" not in result["product_name"].tolist()
    assert "Low risk" not in result["product_name"].tolist()
    assert "Missing lead" not in result["product_name"].tolist()


def test_stockout_aggregation_keeps_store_level_product_positions(q):
    class I:
        capability = "stockout_risk"
        group_by = ["city"]
        filters = type("F", (), {"products": [], "brands": [], "categories": [], "stores": [], "cities": ["Delhi"], "regions": []})()

    raw = q.inventory_position(I, R, date(2026, 9, 30), None, None)
    aggregated = Queries.aggregate_inventory(raw.df, I)

    assert len(aggregated) == len(raw.df)
    assert {
        "product_id", "sku", "product_name", "store_id", "store_name", "city"
    }.issubset(aggregated.columns)


@pytest.mark.parametrize("demand_change_pct", [10, 20, 30, 50, -10])
def test_what_if_preserves_product_store_grain_and_reconciles_demand_change(q, demand_change_pct):
    intent = Intent(
        capability="what_if",
        filters=Filters(products=["P1"], stores=["S1"]),
        time=TimeWindow(),
        scenario=Scenario(kind="demand_change_pct", value=demand_change_pct),
    )
    result = dispatch(
        queries=q,
        intent=intent,
        rules=BusinessRules(),
        as_of=date(2026, 9, 30),
        product_ids=[1],
        store_ids=[1],
    )

    assert result.basis == "Simulated"
    assert len(result.df) == 1
    row = result.df.iloc[0]
    assert (int(row.product_id), int(row.store_id), row.product_name, row.store_name) == (1, 1, "P1", "S1")
    actual_doi = float(row.quantity_on_hand) / (float(row.units_sold_window) / 30)
    simulated_doi = actual_doi / max(1 + demand_change_pct / 100, 0)
    assert row.actual_days_of_inventory == pytest.approx(actual_doi)
    assert row.simulated_days_of_inventory == pytest.approx(simulated_doi)
    assert row.coverage_change_days == pytest.approx(simulated_doi - actual_doi)


def test_what_if_group_scope_keeps_each_position_including_zero_demand(q):
    intent = Intent(
        capability="what_if",
        filters=Filters(),
        time=TimeWindow(),
        scenario=Scenario(kind="demand_change_pct", value=20),
    )
    result = dispatch(
        queries=q,
        intent=intent,
        rules=BusinessRules(),
        as_of=date(2026, 9, 30),
        product_ids=[1, 2],
        store_ids=[1, 2],
    )

    assert set(map(tuple, result.df[["product_id", "store_id"]].astype(int).values)) == {
        (1, 1), (1, 2), (2, 1), (2, 2),
    }
    no_demand = result.df.loc[(result.df.product_id == 1) & (result.df.store_id == 2)].iloc[0]
    assert no_demand.units_sold_window == 0
    assert pd.isna(no_demand.actual_days_of_inventory)
    assert pd.isna(no_demand.simulated_days_of_inventory)
    assert result.basis == "Simulated"


def test_scoped_product_inventory_keeps_each_store_position(q):
    class F:
        products = ["P1"]
        brands = []
        categories = []
        stores = []
        cities = []
        regions = []
        subcategories = []

    class I:
        capability = "inventory_visibility"
        group_by = None
        filters = F()

    raw = q.inventory_position(I, R, date(2026, 9, 30), [1], None)
    aggregated = Queries.aggregate_inventory(raw.df, I)

    assert len(aggregated) == 2
    assert aggregated.store_id.tolist() == [1, 2]
    assert {"sku", "store_name", "city"}.issubset(aggregated.columns)


def test_zero_stock_threshold_filters_raw_positions_not_aggregated_totals():
    class I:
        capability = "inventory_visibility"
        sort = None
        limit = None
        threshold = type("T", (), {"field": "quantity", "op": "=", "value": 0})()

    frame = pd.DataFrame([
        {"product_id": 1, "store_id": 10, "quantity_on_hand": 0},
        {"product_id": 1, "store_id": 11, "quantity_on_hand": 4},
        {"product_id": 2, "store_id": 10, "quantity_on_hand": 0},
    ])
    out, _ = Queries.finalize(frame, I, R())
    assert out[["product_id", "store_id"]].to_records(index=False).tolist() == [(1, 10), (2, 10)]


def test_finalize_counts_matching_positions_before_display_limit():
    class I:
        capability = "stockout_risk"
        sort = None
        threshold = None
        limit = 1

    frame = pd.DataFrame([
        {"product_id": 1, "store_id": 10, "quantity_on_hand": 5, "days_of_inventory": 2, "stockout_risk": "High"},
        {"product_id": 1, "store_id": 11, "quantity_on_hand": 8, "days_of_inventory": 12, "stockout_risk": "Medium"},
        {"product_id": 2, "store_id": 10, "quantity_on_hand": 3, "days_of_inventory": 3, "stockout_risk": "High"},
    ])
    out, _ = Queries.finalize(frame, I, R())

    assert len(out) == 1
    assert out.attrs["result_counts"] == {
        "matching_result_rows": 3,
        "distinct_products": 2,
        "product_store_positions": 3,
        "stores": 2,
        "quantity_on_hand_total": 16.0,
        "high_risk_positions": 2,
        "medium_risk_positions": 1,
    }
