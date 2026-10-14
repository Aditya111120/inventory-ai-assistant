"""Deterministic capability dispatcher.

Takes a validated Intent plus resolved entity IDs and routes the request
through the appropriate query/calculation pipeline.

The LLM is not involved here.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
import math
from typing import Optional

import pandas as pd
from app.calc.metrics import Rules, enrich_inventory
from app.semantic.queries import Queries, QueryResult, restrict, window_for


INVENTORY_CAPABILITIES = {
    "inventory_visibility",
    "inventory_health",
    "stockout_risk",
    "excess_slow_moving",
    "what_if",
    "executive_summary",
}

SALES_METRIC_COLUMNS = {
    "sales_units": "units",
    "sales_revenue": "revenue",
    "sales_growth_pct": "growth_pct",
}


@dataclass
class DispatchResult:
    df: pd.DataFrame
    sql: list[str]
    params: list[list]
    notes: list[str]
    basis: str


def _scenario_params(intent) -> dict:
    """Translate supported what-if scenarios into enrich_inventory arguments."""

    params = {
        "demand_change_pct": 0.0,
        "lead_time_extra_days": 0.0,
        "lead_time_pct": 0.0,
    }

    scenario = getattr(intent, "scenario", None)
    if scenario is None:
        if getattr(intent, "capability", "") == "what_if":
            raise ValueError("A what-if request needs a supported scenario, such as a demand or lead-time change.")
        return params

    if not math.isfinite(float(scenario.value)):
        raise ValueError("Scenario values must be finite numbers.")

    if scenario.kind == "demand_change_pct":
        if float(scenario.value) < -100:
            raise ValueError("A demand decrease cannot exceed 100%.")
        params["demand_change_pct"] = scenario.value

    elif scenario.kind == "lead_time_extra_days":
        params["lead_time_extra_days"] = scenario.value

    elif scenario.kind == "lead_time_pct":
        params["lead_time_pct"] = scenario.value
    else:
        raise ValueError(
            f"The what-if scenario '{scenario.kind}' is not supported yet; no simulation was run."
        )

    return params


def _finalize_sales(df, intent):
    """Apply deterministic sort and limit to sales results."""

    out = df.copy()
    notes = []
    counts = dict(out.attrs.get("result_counts", {}))
    counts["matching_result_rows"] = len(out)
    for column, key in (("units", "sales_units_total"), ("revenue", "sales_revenue_total")):
        if column in out.columns:
            counts[key] = float(pd.to_numeric(out[column], errors="coerce").sum())

    sort = getattr(intent, "sort", None)

    if sort is not None:
        metric = getattr(sort, "by", None)

        if metric:
            column = SALES_METRIC_COLUMNS.get(metric)

            if column is None:
                raise ValueError(
                    f"sales sort metric not allowed: {metric}"
                )

            if column not in out.columns:
                raise ValueError(
                    f"sales sort metric {metric} is not available for this result"
                )

            ascending = (
                str(getattr(sort, "direction", "asc"))
                .lower()
                .startswith("asc")
            )

            out = out.sort_values(
                column,
                ascending=ascending,
                na_position="last",
            )

    limit = getattr(intent, "limit", None)

    if limit is not None:
        limit = min(int(limit), 500)

        if len(out) > limit:
            notes.append(
                f"Showing top {limit} of {len(out)} rows"
            )

        out = out.head(limit)

    out = out.reset_index(drop=True)
    out.attrs["result_counts"] = counts
    return out, notes


def _inventory_dispatch(
    queries: Queries,
    intent,
    rules: Rules,
    as_of: date,
    product_ids: Optional[list[int]],
    store_ids: Optional[list[int]],
    prev_product_ids: Optional[list[int]],
    prev_store_ids: Optional[list[int]],
    excess_rule: str,
) -> DispatchResult:

    historical_kind = getattr(getattr(intent, "time", None), "kind", "none")
    if historical_kind in {"exact_date", "month", "date_range"}:
        snapshot = queries.inventory_snapshot(
            intent=intent,
            as_of=as_of,
            product_ids=product_ids,
            store_ids=store_ids,
            prev_product_ids=prev_product_ids,
            prev_store_ids=prev_store_ids,
        )
        if snapshot.df.empty:
            return DispatchResult(
                df=snapshot.df,
                sql=snapshot.sql,
                params=snapshot.params,
                notes=snapshot.notes,
                basis="Actual",
            )
        frame = snapshot.df.copy()
        frame.attrs["result_counts"] = {
            "matching_result_rows": len(frame),
            "distinct_products": int(frame["product_id"].nunique()) if "product_id" in frame else 0,
            "product_store_positions": int(frame[["product_id", "store_id"]].drop_duplicates().shape[0]) if {"product_id", "store_id"}.issubset(frame) else len(frame),
            "stores": int(frame["store_id"].nunique()) if "store_id" in frame else 0,
        }
        return DispatchResult(
            df=frame,
            sql=snapshot.sql,
            params=snapshot.params,
            notes=snapshot.notes + ["Only snapshot-backed quantity is available; current reorder, DOI, and risk metrics were not substituted."],
            basis="Actual",
        )

    # Fail closed before SQL execution: a scenario capability without a
    # supported, finite scenario must never fall through as an actual result.
    scenario_params = _scenario_params(intent)

    raw = queries.inventory_position(
        intent=intent,
        rules=rules,
        as_of=as_of,
        product_ids=product_ids,
        store_ids=store_ids,
        prev_product_ids=prev_product_ids,
        prev_store_ids=prev_store_ids,
    )

    if intent.capability == "executive_summary":
        # Build the contract from the complete authorized product-store grain
        # before any preview limit is applied. Rules are evaluated per position.
        full = enrich_inventory(raw.df.copy(), rules)
        risk_col = "stockout_risk_v1" if "stockout_risk_v1" in full else "stockout_risk"
        status_col = "inventory_status" if "inventory_status" in full else "stock_status"
        full["_below_reorder"] = full[status_col].isin(["At or Below Reorder Level", "Low"])
        full["_high_risk"] = full[risk_col].eq("High")
        full["_medium_risk"] = full[risk_col].eq("Medium")
        group_col = "category" if "category" in full else None
        keys = [group_col] if group_col else []
        aggregations = {
            "quantity_on_hand": ("quantity_on_hand", "sum"),
            "inventory_value": ("inventory_value", "sum"),
            "positions": ("product_id", "size"),
            "distinct_products": ("product_id", "nunique"),
            "below_reorder_positions": ("_below_reorder", "sum"),
            "high_risk_positions": ("_high_risk", "sum"),
            "medium_risk_positions": ("_medium_risk", "sum"),
            "snapshot_date": ("inventory_snapshot_date", "max"),
        }
        summary = full.groupby(keys, dropna=False, as_index=False).agg(**aggregations) if keys else pd.DataFrame([{
            name: full[column].sum() if op == "sum" else full[column].nunique() if op == "nunique" else full[column].max() if op == "max" else len(full)
            for name, (column, op) in aggregations.items()
        }])
        full_counts = {
            "matching_result_rows": int(len(full)),
            "distinct_products": int(full["product_id"].nunique()),
            "product_store_positions": int(full[["product_id", "store_id"]].drop_duplicates().shape[0]),
            "stores": int(full["store_id"].nunique()),
            "quantity_on_hand_total": float(pd.to_numeric(full["quantity_on_hand"], errors="coerce").sum()),
            "inventory_value_total": float(pd.to_numeric(full["inventory_value"], errors="coerce").sum()),
            "below_reorder_positions": int(full["_below_reorder"].sum()),
            "high_risk_positions": int(full["_high_risk"].sum()),
            "medium_risk_positions": int(full["_medium_risk"].sum()),
        }
        # Join a complete category-level sales aggregate using the same
        # authorized product/store scope and the established sales window.
        sales_intent = intent.model_copy(update={
            "capability": "sales_trend", "group_by": "category",
            "metrics": ["sales_units", "sales_revenue"], "sort": None,
            "limit": None,
        })
        sales = queries.sales(
            sales_intent, rules, as_of,
            restrict(product_ids, prev_product_ids),
            restrict(store_ids, prev_store_ids),
        )
        if group_col and not sales.df.empty and "category" in sales.df.columns:
            summary = summary.merge(sales.df[["category", "units", "revenue"]], on="category", how="left")
        else:
            summary["units"] = float(pd.to_numeric(sales.df.get("units", pd.Series(dtype=float)), errors="coerce").sum())
            summary["revenue"] = float(pd.to_numeric(sales.df.get("revenue", pd.Series(dtype=float)), errors="coerce").sum())
        summary[["units", "revenue"]] = summary[["units", "revenue"]].fillna(0)
        full_counts["sales_units_total"] = float(pd.to_numeric(sales.df.get("units", pd.Series(dtype=float)), errors="coerce").sum())
        full_counts["sales_revenue_total"] = float(pd.to_numeric(sales.df.get("revenue", pd.Series(dtype=float)), errors="coerce").sum())
        full_counts["sales_window_start_exclusive"] = window_for(sales_intent, rules, as_of)[0].isoformat()
        full_counts["sales_window_end_inclusive"] = window_for(sales_intent, rules, as_of)[1].isoformat()
        summary.attrs["result_counts"] = full_counts
        summary.attrs["window_days"] = raw.df.attrs.get("window_days")
        return DispatchResult(summary, raw.sql + sales.sql, raw.params + sales.params, raw.notes + sales.notes + ["Leadership summary: complete authorized product-store inventory evaluated with Business Rules V1 before category aggregation."], "Actual")

    aggregated = queries.aggregate_inventory(raw.df, intent)

    enriched = enrich_inventory(
        aggregated,
        rules,
        **scenario_params,
    )
    if intent.capability == "what_if" and any(scenario_params.values()):
        actual = enrich_inventory(aggregated, rules)
        enriched["actual_days_of_inventory"] = actual["days_of_inventory"].to_numpy()
        enriched["simulated_days_of_inventory"] = enriched["days_of_inventory"]
        enriched["coverage_change_days"] = enriched["simulated_days_of_inventory"] - enriched["actual_days_of_inventory"]
        enriched["actual_stockout_risk"] = actual["stockout_risk_v1"].to_numpy()
        enriched["simulated_stockout_risk"] = enriched["stockout_risk_v1"]

    final_intent = (
        intent.model_copy(update={"limit": None})
        if getattr(intent, "question_kind", "") == "comparison"
        and getattr(intent, "group_by", None)
        else intent
    )
    final_df, final_notes = queries.finalize(
        enriched,
        final_intent,
        rules,
        excess_rule=excess_rule,
    )
    counts = dict(final_df.attrs.get("result_counts", {}))
    counts["inventory_positions_evaluated"] = len(raw.df)
    final_df.attrs["result_counts"] = counts

    notes = list(raw.notes) + list(final_notes)

    basis = (
        str(final_df["basis"].iloc[0])
        if not final_df.empty and "basis" in final_df.columns
        else (
            "Simulated"
            if intent.capability == "what_if" and any(scenario_params.values())
            else "Actual"
        )
    )

    return DispatchResult(
        df=final_df,
        sql=raw.sql,
        params=raw.params,
        notes=notes,
        basis=basis,
    )


def _sales_dispatch(
    queries: Queries,
    intent,
    rules: Rules,
    as_of: date,
    product_ids: Optional[list[int]],
    store_ids: Optional[list[int]],
    prev_product_ids: Optional[list[int]],
    prev_store_ids: Optional[list[int]],
) -> DispatchResult:

    result = queries.sales(
        intent=intent,
        rules=rules,
        as_of=as_of,
        product_ids=product_ids,
        store_ids=store_ids,
        prev_product_ids=prev_product_ids,
        prev_store_ids=prev_store_ids,
    )

    final_intent = (
        intent.model_copy(update={"limit": None})
        if getattr(intent, "question_kind", "") == "comparison"
        and getattr(intent, "group_by", None)
        else intent
    )
    final_df, final_notes = _finalize_sales(result.df, final_intent)

    return DispatchResult(
        df=final_df,
        sql=result.sql,
        params=result.params,
        notes=list(result.notes) + final_notes,
        basis="Actual",
    )


def _historical_dispatch(
    queries: Queries, intent, as_of: date, product_ids, store_ids,
    prev_product_ids=None, prev_store_ids=None,
) -> DispatchResult:
    """Run the existing weekly-snapshot trend query through the normal contract."""
    target = as_of
    time = getattr(intent, "time", None)
    if getattr(time, "kind", "none") == "month" and getattr(time, "year", None) and getattr(time, "month", None):
        import calendar
        target = date(int(time.year), int(time.month), calendar.monthrange(int(time.year), int(time.month))[1])
    elif getattr(time, "kind", "none") == "exact_date" and getattr(time, "date", None):
        target = date.fromisoformat(str(time.date))
    elif getattr(time, "kind", "none") == "date_range" and getattr(time, "end_date", None):
        target = date.fromisoformat(str(time.end_date))
    previous_target = None
    comparison_month = getattr(time, "comparison_month", None)
    comparison_year = getattr(time, "comparison_year", None)
    if comparison_month and comparison_year:
        import calendar
        previous_target = date(
            int(comparison_year), int(comparison_month),
            calendar.monthrange(int(comparison_year), int(comparison_month))[1],
        )
    elif getattr(time, "compare_to_previous", False):
        if getattr(time, "kind", "none") == "month" and getattr(time, "year", None) and getattr(time, "month", None):
            import calendar
            current_first = date(int(time.year), int(time.month), 1)
            previous_end = current_first - timedelta(days=1)
            previous_target = previous_end
        elif getattr(time, "kind", "none") == "date_range" and getattr(time, "start_date", None):
            start = date.fromisoformat(str(time.start_date))
            previous_target = start - timedelta(days=1)
    group = getattr(intent, "group_by", None) or "category"
    groups = [group] if isinstance(group, str) else list(group)
    product_ids = restrict(product_ids, prev_product_ids)
    store_ids = restrict(store_ids, prev_store_ids)
    weeks = int(getattr(time, "n", None) or 4)
    result = queries.inventory_trend(target, weeks, product_ids, store_ids, tuple(groups), previous_as_of=previous_target)
    frame = result.df.copy()
    if not frame.empty:
        frame.attrs.update(result.df.attrs)
        frame.attrs["result_counts"] = {
            "matching_result_rows": len(frame),
            "quantity_now_total": float(pd.to_numeric(frame.get("qty_now"), errors="coerce").sum()),
            "quantity_before_total": float(pd.to_numeric(frame.get("qty_before"), errors="coerce").sum()),
            "quantity_change_total": float(pd.to_numeric(frame.get("qty_change"), errors="coerce").sum()),
        }
    return DispatchResult(frame, result.sql, result.params, result.notes, "Actual")


def dispatch(
    queries: Queries,
    intent,
    rules: Rules,
    as_of: date,
    product_ids: Optional[list[int]] = None,
    store_ids: Optional[list[int]] = None,
    prev_product_ids: Optional[list[int]] = None,
    prev_store_ids: Optional[list[int]] = None,
    *,
    excess_rule: str = "max_level",
) -> DispatchResult:
    """Route a validated intent to its deterministic execution pipeline."""

    capability = intent.capability

    if capability in INVENTORY_CAPABILITIES:
        return _inventory_dispatch(
            queries=queries,
            intent=intent,
            rules=rules,
            as_of=as_of,
            product_ids=product_ids,
            store_ids=store_ids,
            prev_product_ids=prev_product_ids,
            prev_store_ids=prev_store_ids,
            excess_rule=excess_rule,
        )

    if capability == "historical_analysis":
        return _historical_dispatch(queries, intent, as_of, product_ids, store_ids, prev_product_ids, prev_store_ids)

    if capability == "sales_trend":
        return _sales_dispatch(
            queries=queries,
            intent=intent,
            rules=rules,
            as_of=as_of,
            product_ids=product_ids,
            store_ids=store_ids,
            prev_product_ids=prev_product_ids,
            prev_store_ids=prev_store_ids,
        )

    if capability == "clarification":
        raise ValueError("clarification intents must be handled before dispatch")

    if capability == "out_of_scope":
        raise ValueError("out_of_scope intents must be handled before dispatch")

    raise ValueError(f"unsupported capability: {capability}")
