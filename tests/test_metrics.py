import math
import random

import pandas as pd
import pytest

from app.calc import metrics as m
from app.calc.metrics import Rules

R = Rules()  # defaults: window 30, excess 60, slow <3, critical 7, warning 14, buffer 7


# ----------------------------------------------------------- examples from the spec
def test_spec_example_dell_laptop_6_days_vs_10_day_lead_time():
    # "approximately 6 days of inventory ... supplier lead time is 10 days"
    ads = m.avg_daily_sales(units_sold=150, window_days=30)  # 5/day
    doi = m.days_of_inventory(quantity=30, avg_daily=ads)    # 6 days
    assert doi == pytest.approx(6.0)
    assert m.stockout_risk(doi, lead_time_days=10, rules=R) == "High"


def test_spec_example_what_if_demand_15_to_12_5_days():
    # "coverage would decrease from 15 days to approximately 12.5 days"
    res = m.what_if_demand(quantity=150, avg_daily=10, lead_time_days=5, demand_change_pct=20, rules=R)
    assert res["basis"] == "Simulated"
    assert res["baseline"]["days_of_inventory"] == pytest.approx(15.0)
    assert res["scenario"]["days_of_inventory"] == pytest.approx(12.5)


def test_spec_example_daily_sales_20_to_25():
    res = m.what_if_demand(quantity=500, avg_daily=20, lead_time_days=10, demand_change_pct=25, rules=R)
    assert res["baseline"]["days_of_inventory"] == pytest.approx(25.0)
    assert res["scenario"]["days_of_inventory"] == pytest.approx(20.0)


def test_spec_example_sales_up_22_percent():
    assert m.pct_change(122, 100) == pytest.approx(22.0)


# ----------------------------------------------------------------------- basics
def test_days_of_inventory_none_when_no_sales():
    assert m.days_of_inventory(50, 0) is None


def test_avg_daily_sales_rejects_bad_window():
    with pytest.raises(ValueError):
        m.avg_daily_sales(10, 0)


def test_inventory_value():
    assert m.inventory_value(12, 2500.0) == 30000.0


@pytest.mark.parametrize("qty,expected", [(4, "Low"), (5, "Healthy"), (15, "Healthy"), (16, "Excess")])
def test_stock_status_boundaries(qty, expected):
    assert m.stock_status(qty, reorder_level=5, max_level=15) == expected


@pytest.mark.parametrize("qty,reorder,max_level,expected", [
    (-1, 5, 15, "Invalid Stock Quantity"),
    (0, None, None, "Out of Stock"),
    (5, 5, 15, "At or Below Reorder Level"),
    (16, 5, 15, "Above Maximum Level"),
    (8, None, 15, "Reorder Level Unknown"),
])
def test_v1_inventory_availability_status(qty, reorder, max_level, expected):
    assert m.inventory_availability_status(qty, reorder, max_level) == expected


@pytest.mark.parametrize("units,expected", [
    (0, "No Recorded Sales"),
    (2, "Slow-moving Candidate"),
    (3, "Recorded Sales"),
    (-1, "Demand Unknown"),
])
def test_v1_demand_status_boundaries(units, expected):
    assert m.demand_status(units) == expected


@pytest.mark.parametrize("doi,expected", [(None, "No recent sales"), (6.9, "Critical"), (7, "Warning"),
                                          (13.9, "Warning"), (14, "OK")])
def test_coverage_band(doi, expected):
    assert m.coverage_band(doi, R) == expected


@pytest.mark.parametrize("doi,expected", [(None, "No demand"), (9.9, "High"), (10, "Medium"),
                                          (16.9, "Medium"), (17, "Low")])
def test_stockout_risk_boundaries(doi, expected):
    assert m.stockout_risk(doi, lead_time_days=10, rules=R) == expected


def test_missing_lead_time_is_not_low_risk():
    assert m.stockout_risk(2, lead_time_days=float("nan"), rules=R) == "Unknown lead time"
    frame = pd.DataFrame([{
        "quantity_on_hand": 10, "reorder_level": 5, "max_level": 20,
        "units_sold_window": 30, "lead_time_days": float("nan"),
    }])
    assert m.enrich_inventory(frame, R).iloc[0].stockout_risk == "Unknown lead time"


def test_v1_zero_recorded_sales_is_not_low_risk():
    frame = pd.DataFrame([{
        "quantity_on_hand": 10, "reorder_level": 5, "max_level": 20,
        "units_sold_window": 0, "lead_time_days": 10,
    }])
    row = m.enrich_inventory(frame, R).iloc[0]
    assert row.stockout_risk_v1 == "Not Assessable - No Recorded Demand"


def test_v1_threshold_quality_is_explicit():
    assert m.inventory_availability_status(8, None, 15) == "Reorder Level Unknown"
    assert m.inventory_availability_status(8, 5, None) == "Maximum Level Unknown"
    assert m.inventory_availability_status(8, 15, 5) == "Invalid Threshold Configuration"


def test_is_excess():
    assert m.is_excess(61, 100, R) is True
    assert m.is_excess(60, 100, R) is False
    assert m.is_excess(None, 10, R) is False     # no recorded demand is not confirmed excess
    assert m.is_excess(None, 0, R) is False      # nothing on hand


def test_is_slow_moving():
    assert m.is_slow_moving(2, R) is True
    assert m.is_slow_moving(3, R) is False


def test_pct_change_edge_cases():
    assert m.pct_change(0, 0) == 0.0
    assert m.pct_change(5, 0) is None
    assert m.pct_change(50, 100) == pytest.approx(-50.0)


# ----------------------------------------------------------------------- what-if
def test_what_if_lead_time_changes_risk_not_coverage():
    res = m.what_if_lead_time(quantity=120, avg_daily=6, lead_time_days=10, rules=R, extra_days=5)
    assert res["baseline"]["days_of_inventory"] == res["scenario"]["days_of_inventory"] == pytest.approx(20)
    assert res["baseline"]["risk"] == "Low"       # 20 >= 10 + 7
    assert res["scenario"]["risk"] == "Medium"    # 20 < 15 + 7 but >= 15


def test_what_if_lead_time_percent():
    res = m.what_if_lead_time(quantity=60, avg_daily=6, lead_time_days=10, rules=R, pct_increase=20)
    assert res["scenario"]["lead_time_days"] == pytest.approx(12.0)


def test_what_if_additional_sales_with_shortfall():
    res = m.what_if_additional_sales(quantity=80, extra_units=100)
    assert res["scenario"]["quantity_on_hand"] == 0
    assert res["scenario"]["shortfall_units"] == 20
    assert m.what_if_additional_sales(250, 100)["scenario"]["quantity_on_hand"] == 150


def test_inventory_needed_for_coverage():
    res = m.inventory_needed_for_coverage(avg_daily=3.2, target_days=30, quantity=70)
    assert res["required_units"] == 96 and res["gap_units"] == 26
    assert m.inventory_needed_for_coverage(1, 30, 500)["gap_units"] == 0


def test_demand_drop_below_zero_is_floored():
    res = m.what_if_demand(100, 5, 10, demand_change_pct=-150, rules=R)
    assert res["scenario"]["avg_daily_sales"] == 0
    assert res["scenario"]["days_of_inventory"] is None


# ------------------------------------------------------------------- rules loading
def test_rules_from_mapping_ignores_unknown_and_casts_window():
    r = Rules.from_mapping({"sales_window_days": 45.0, "excess_days_of_inventory": 90, "bogus": 1})
    assert r.sales_window_days == 45 and isinstance(r.sales_window_days, int)
    assert r.excess_days_of_inventory == 90 and r.safety_buffer_days == 7


def test_rules_reject_nonpositive_window():
    with pytest.raises(ValueError):
        Rules.from_mapping({"sales_window_days": 0})


# ------------------------------------------------ vectorised == scalar (consistency)
def _frame(n=500, seed=7):
    rnd = random.Random(seed)
    rows = []
    for _ in range(n):
        rl = rnd.randint(3, 20)
        rows.append({
            "quantity_on_hand": rnd.randint(0, 120),
            "reorder_level": rl, "max_level": rl * 3,
            "units_sold_window": rnd.choice([0, 0, 1, 2, 3, 5, 10, 30, 60]),
            "lead_time_days": rnd.randint(5, 18),
            "cost_price": 1000.0, "inventory_value": 0.0,
        })
    return pd.DataFrame(rows)


def test_vectorised_matches_scalar_functions():
    df = _frame()
    out = m.enrich_inventory(df, R)
    for row in out.itertuples():
        ads = m.avg_daily_sales(row.units_sold_window, R.sales_window_days)
        doi = m.days_of_inventory(row.quantity_on_hand, ads)
        assert row.stock_status == m.stock_status(row.quantity_on_hand, row.reorder_level, row.max_level)
        assert row.coverage_band == m.coverage_band(doi, R)
        assert row.stockout_risk == m.stockout_risk(doi, row.lead_time_days, R)
        assert row.is_excess == m.is_excess(doi, row.quantity_on_hand, R)
        assert row.is_slow_moving == m.is_slow_moving(row.units_sold_window, R)
        if doi is None:
            assert math.isnan(row.days_of_inventory)
        else:
            assert row.days_of_inventory == pytest.approx(doi)
    assert (out["basis"] == "Actual").all()


def test_v1_enrichment_exposes_independent_quality_and_status_columns():
    frame = pd.DataFrame([{
        "quantity_on_hand": 0, "reorder_level": None, "max_level": -1,
        "units_sold_window": 0, "lead_time_days": None,
    }])
    out = m.enrich_inventory(frame, R)
    row = out.iloc[0]
    assert row.inventory_status == "Out of Stock"
    assert "missing_reorder_level" in row.inventory_data_quality
    assert "negative_maximum_level" in row.inventory_data_quality
    assert row.demand_status == "No Recorded Sales"
    assert row.stockout_risk_v1 == "Out of Stock"
def test_enrich_scenario_matches_scalar_what_if():
    df = _frame(200, seed=11)
    out = m.enrich_inventory(df, R, demand_change_pct=20, lead_time_extra_days=5)
    assert (out["basis"] == "Simulated").all()
    for row in out.itertuples():
        ads = m.avg_daily_sales(row.units_sold_window, 30)
        res = m.what_if_lead_time(row.quantity_on_hand, ads * 1.2, row.lead_time_days, R, extra_days=5)
        assert row.stockout_risk == res["scenario"]["risk"]


def test_enrich_requires_columns_and_converts_decimals():
    from decimal import Decimal
    with pytest.raises(ValueError):
        m.enrich_inventory(pd.DataFrame({"quantity_on_hand": [1]}), R)
    df = _frame(3)
    df["cost_price"] = [Decimal("1000.00")] * 3
    df["inventory_value"] = [Decimal("0.00")] * 3
    out = m.enrich_inventory(df, R)
    assert out["cost_price"].dtype == float
