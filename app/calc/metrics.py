"""Deterministic calculation layer.

The LLM never computes or estimates numbers. Every figure shown to the user comes from
these pure functions, driven by thresholds from dbo.BusinessRules.

Conventions
-----------
* "Actual" results use real data; anything produced by a what_if_* function (or
  enrich_inventory with a scenario) is tagged basis="Simulated".
* days_of_inventory is None (scalar) / NaN (vectorised) when there are no recent sales.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, fields
from typing import Mapping, Optional

import numpy as np
import pandas as pd

ACTUAL = "Actual"
SIMULATED = "Simulated"


# --------------------------------------------------------------------------- rules
@dataclass(frozen=True)
class Rules:
    """Business thresholds. Defaults mirror the seed rows in dbo.BusinessRules."""

    sales_window_days: int = 30
    excess_days_of_inventory: float = 60
    slow_moving_units_window: float = 3
    stockout_critical_days: float = 7
    stockout_warning_days: float = 14
    safety_buffer_days: float = 7

    @classmethod
    def from_mapping(cls, m: Mapping[str, float]) -> "Rules":
        known = {f.name for f in fields(cls)}
        kwargs = {k: v for k, v in m.items() if k in known}
        for key, value in kwargs.items():
            if not math.isfinite(float(value)):
                raise ValueError(f"{key} must be finite")
            if key == "sales_window_days" and float(value) <= 0:
                raise ValueError("sales_window_days must be positive")
            if key != "sales_window_days" and float(value) < 0:
                raise ValueError(f"{key} must be nonnegative")
        if "sales_window_days" in kwargs:
            kwargs["sales_window_days"] = int(kwargs["sales_window_days"])
        rules = cls(**kwargs)
        if rules.sales_window_days <= 0:
            raise ValueError("sales_window_days must be positive")
        return rules

    def applied(self) -> dict[str, float]:
        """Machine-readable active global rule evidence."""
        return {
            "sales_window_days": self.sales_window_days,
            "excess_days_of_inventory": self.excess_days_of_inventory,
            "slow_moving_units_window": self.slow_moving_units_window,
            "stockout_critical_days": self.stockout_critical_days,
            "stockout_warning_days": self.stockout_warning_days,
            "safety_buffer_days": self.safety_buffer_days,
        }

    def resolve(self, scope_type: str = "global", scope_key=None, overrides=None) -> "Rules":
        """V1 rule-resolution seam; only global rules are active today."""
        if scope_type not in {"global", "product-store", "product", "subcategory", "category"}:
            raise ValueError(f"unsupported rule scope: {scope_type}")
        if scope_type != "global" and overrides:
            raise ValueError("scoped rule overrides are not active in Business Rules V1")
        return self


# --------------------------------------------------------------------- scalar metrics
def avg_daily_sales(units_sold: float, window_days: int) -> float:
    if window_days <= 0:
        raise ValueError("window_days must be positive")
    return units_sold / window_days


def days_of_inventory(quantity: float, avg_daily: float) -> Optional[float]:
    """Current inventory / average daily sales. None when there are no sales."""
    if avg_daily <= 0:
        return None
    return quantity / avg_daily


def inventory_value(quantity: float, unit_cost: float) -> float:
    return quantity * unit_cost


def stock_status(quantity: float, reorder_level: float, max_level: float) -> str:
    """Low (< minimum) / Healthy (within range) / Excess (> maximum)."""
    if quantity < reorder_level:
        return "Low"
    if quantity > max_level:
        return "Excess"
    return "Healthy"


def inventory_availability_status(quantity, reorder_level=None, max_level=None) -> str:
    """V1 availability status, independent from threshold data quality."""
    if quantity is None or not math.isfinite(float(quantity)):
        return "Invalid Stock Quantity"
    quantity = float(quantity)
    if quantity < 0:
        return "Invalid Stock Quantity"
    if quantity == 0:
        return "Out of Stock"
    def valid_threshold(value):
        try:
            return value is not None and math.isfinite(float(value)) and float(value) >= 0
        except (TypeError, ValueError):
            return False

    reorder_valid = valid_threshold(reorder_level)
    max_valid = valid_threshold(max_level)
    if reorder_valid and max_valid and float(max_level) < float(reorder_level):
        return "Invalid Threshold Configuration"
    if reorder_valid and quantity <= float(reorder_level):
        return "At or Below Reorder Level"
    if max_valid and quantity > float(max_level):
        return "Above Maximum Level"
    if not reorder_valid:
        return "Reorder Level Unknown"
    if not max_valid:
        return "Maximum Level Unknown"
    return "Within Stock Limits"


def demand_status(units) -> str:
    if units is None or not math.isfinite(float(units)) or float(units) < 0:
        return "Demand Unknown"
    units = float(units)
    if units == 0:
        return "No Recorded Sales"
    if units <= 2:
        return "Slow-moving Candidate"
    return "Recorded Sales"


def coverage_band(doi: Optional[float], rules: Rules) -> str:
    if doi is None:
        return "No recent sales"
    if doi < rules.stockout_critical_days:
        return "Critical"
    if doi < rules.stockout_warning_days:
        return "Warning"
    return "OK"


def stockout_risk(doi: Optional[float], lead_time_days: float, rules: Rules) -> str:
    """High: coverage shorter than supplier lead time.
    Medium: covers lead time but not lead time + safety buffer. Low: otherwise."""
    if doi is None:
        return "No demand"
    if lead_time_days is None or not math.isfinite(float(lead_time_days)) or float(lead_time_days) < 0:
        return "Unknown lead time"
    if doi < lead_time_days:
        return "High"
    if doi < lead_time_days + rules.safety_buffer_days:
        return "Medium"
    return "Low"


def is_excess(doi: Optional[float], quantity: float, rules: Rules) -> bool:
    """Legacy compatibility flag; only confirmed DOI excess is true."""
    if quantity <= 0:
        return False
    if doi is None:
        return False
    return doi > rules.excess_days_of_inventory


def is_slow_moving(units_in_window: float, rules: Rules) -> bool:
    return units_in_window < rules.slow_moving_units_window


def pct_change(current: float, previous: float) -> Optional[float]:
    """Percentage change. None when undefined (previous == 0 and current > 0)."""
    if previous == 0:
        return 0.0 if current == 0 else None
    return (current - previous) / previous * 100.0


# ------------------------------------------------------------------------- what-if
def what_if_demand(
    quantity: float, avg_daily: float, lead_time_days: float, demand_change_pct: float, rules: Rules
) -> dict:
    """What happens to coverage and risk if daily demand changes by demand_change_pct %."""
    new_avg = max(avg_daily * (1 + demand_change_pct / 100.0), 0.0)
    base_doi = days_of_inventory(quantity, avg_daily)
    new_doi = days_of_inventory(quantity, new_avg)
    return {
        "basis": SIMULATED,
        "assumption": f"daily demand {demand_change_pct:+g}%",
        "baseline": {"avg_daily_sales": avg_daily, "days_of_inventory": base_doi,
                     "risk": stockout_risk(base_doi, lead_time_days, rules)},
        "scenario": {"avg_daily_sales": new_avg, "days_of_inventory": new_doi,
                     "risk": stockout_risk(new_doi, lead_time_days, rules)},
    }


def what_if_lead_time(
    quantity: float, avg_daily: float, lead_time_days: float, rules: Rules,
    extra_days: float = 0.0, pct_increase: float = 0.0,
) -> dict:
    """What happens to stockout risk if supplier lead time grows (by days and/or percent)."""
    new_lead = lead_time_days * (1 + pct_increase / 100.0) + extra_days
    doi = days_of_inventory(quantity, avg_daily)
    return {
        "basis": SIMULATED,
        "assumption": f"lead time +{extra_days:g} days, +{pct_increase:g}%",
        "baseline": {"lead_time_days": lead_time_days, "days_of_inventory": doi,
                     "risk": stockout_risk(doi, lead_time_days, rules)},
        "scenario": {"lead_time_days": new_lead, "days_of_inventory": doi,
                     "risk": stockout_risk(doi, new_lead, rules)},
    }


def what_if_additional_sales(quantity: float, extra_units: float) -> dict:
    """Remaining inventory if extra units are sold; reports any shortfall."""
    remaining = quantity - extra_units
    return {
        "basis": SIMULATED,
        "assumption": f"{extra_units:g} additional units sold",
        "baseline": {"quantity_on_hand": quantity},
        "scenario": {"quantity_on_hand": max(remaining, 0.0), "shortfall_units": max(-remaining, 0.0)},
    }


def inventory_needed_for_coverage(avg_daily: float, target_days: float, quantity: float) -> dict:
    """Units required to hold target_days of coverage, and the gap versus current stock."""
    required = math.ceil(avg_daily * target_days)
    return {
        "basis": SIMULATED,
        "assumption": f"{target_days:g} days of coverage",
        "required_units": required,
        "current_units": quantity,
        "gap_units": max(required - quantity, 0),
    }


# --------------------------------------------------------------- vectorised enrichment
def enrich_inventory(
    df: pd.DataFrame,
    rules: Rules,
    demand_change_pct: float = 0.0,
    lead_time_extra_days: float = 0.0,
    lead_time_pct: float = 0.0,
) -> pd.DataFrame:
    """Add computed columns to rows shaped like dbo.vw_InventoryPosition.

    Adds: avg_daily_sales, days_of_inventory, stock_status, coverage_band,
    stockout_risk, is_excess, is_slow_moving, basis.
    With any scenario parameter non-zero, demand/lead time are adjusted and basis='Simulated'.
    stock_status compares on-hand with the stored reorder/max levels and is not scenario-dependent.
    """
    required = {
        "quantity_on_hand",
        "reorder_level",
        "max_level",
        "units_sold_window",
        "lead_time_days",
    }
    missing = required - set(df.columns)
    if missing:
        raise ValueError(f"missing columns: {sorted(missing)}")

    out = df.copy()
    for col in ("cost_price", "inventory_value"):
        if col in out.columns:
            out[col] = out[col].astype(float)

    qty = out["quantity_on_hand"].astype(float)
    units = out["units_sold_window"].astype(float)
    factor = max(1 + demand_change_pct / 100.0, 0.0)
    window_days = int(out.attrs.get("window_days", rules.sales_window_days))
    if window_days <= 0:
        raise ValueError("window_days must be positive")
    ads = units / window_days * factor
    lead = out["lead_time_days"].astype(float) * (1 + lead_time_pct / 100.0) + lead_time_extra_days
    doi = qty / ads.where(ads > 0)  # NaN where no sales

    out["avg_daily_sales"] = ads
    out["days_of_inventory"] = doi

    reorder = pd.to_numeric(out["reorder_level"], errors="coerce")
    maximum = pd.to_numeric(out["max_level"], errors="coerce")
    out["inventory_status"] = [
        inventory_availability_status(q, r, m)
        for q, r, m in zip(qty, reorder, maximum)
    ]
    quality = []
    for r, m in zip(reorder, maximum):
        issues = []
        if pd.isna(r): issues.append("missing_reorder_level")
        if pd.isna(m): issues.append("missing_maximum_level")
        if pd.notna(r) and r < 0: issues.append("negative_reorder_level")
        if pd.notna(m) and m < 0: issues.append("negative_maximum_level")
        if pd.notna(r) and pd.notna(m) and m < r: issues.append("maximum_below_reorder")
        quality.append(";".join(issues) if issues else None)
    out["inventory_data_quality"] = quality
    out["stock_status"] = np.select(
        [qty < reorder, qty > maximum], ["Low", "Excess"], default="Healthy"
    )
    out["coverage_band"] = np.select(
        [doi.isna(), doi < rules.stockout_critical_days, doi < rules.stockout_warning_days],
        ["No recent sales", "Critical", "Warning"],
        default="OK",
    )
    lead_missing = lead.isna() | (lead < 0)
    out["stockout_risk"] = np.select(
        [doi.isna(), lead_missing, doi < lead, doi < lead + rules.safety_buffer_days],
        ["No demand", "Unknown lead time", "High", "Medium"],
        default="Low",
    )
    out["stockout_risk_v1"] = np.select(
        [qty < 0, qty == 0, units.isna() | (units < 0) | (units == 0), lead_missing, doi < lead, doi < lead + rules.safety_buffer_days],
        ["Not Assessable", "Out of Stock", "Not Assessable - No Recorded Demand", "Unknown", "High", "Medium"],
        default="Low",
    )
    if "supplier_rule" in out.columns:
        out["supplier_data_quality"] = np.where(
            out["supplier_rule"].isin(["Invalid supplier record", "No valid primary supplier"]),
            out["supplier_rule"],
            None,
        )
    out["demand_status"] = [demand_status(v) for v in units]
    out["demand_data_quality"] = np.where(units.isna() | (units < 0), "invalid_sales_quantity", None)
    out["excess_by_max_level"] = (qty > 0) & maximum.notna() & (qty > maximum)
    out["excess_coverage_candidate"] = (qty > 0) & doi.notna() & (doi > rules.excess_days_of_inventory)
    out["no_recorded_sales_review"] = (qty > 0) & units.eq(0)
    # Backward-compatible legacy flag; V1 exposes the two independent flags
    # above so no-recorded-sales is not silently presented as confirmed excess.
    out["is_excess"] = (qty > 0) & doi.notna() & (doi > rules.excess_days_of_inventory)
    out["is_slow_moving"] = units.between(0, 2, inclusive="both")
    scenario = bool(demand_change_pct or lead_time_extra_days or lead_time_pct)
    out["basis"] = SIMULATED if scenario else ACTUAL
    return out
