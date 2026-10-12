from dataclasses import dataclass
from typing import Any

import pandas as pd


@dataclass
class AnswerFacts:
    """Single deterministic fact contract shared by answer/API/trust layers."""

    capability: str
    question_kind: str
    requested_scope: dict[str, Any]
    resolved_scope: dict[str, Any]
    time_scope: dict[str, Any]
    basis: str
    metrics: dict[str, Any]
    classifications: dict[str, Any]
    data_quality: dict[str, Any]
    counts: dict[str, Any]
    notes: list[str]

    def as_dict(self) -> dict[str, Any]:
        return {
            "capability": self.capability,
            "question_kind": self.question_kind,
            "requested_scope": self.requested_scope,
            "resolved_scope": self.resolved_scope,
            "time_scope": self.time_scope,
            "basis": self.basis,
            "metrics": self.metrics,
            "classifications": self.classifications,
            "data_quality": self.data_quality,
            "counts": self.counts,
            "notes": self.notes,
        }


def build_answer_facts(*, intent, result, resolved_context: dict[str, Any], counts: dict[str, Any]) -> AnswerFacts:
    """Extract verified values without recalculating business metrics."""
    frame = result.df if isinstance(result.df, pd.DataFrame) else pd.DataFrame()
    metrics: dict[str, Any] = {}
    def plain(value):
        try:
            if hasattr(value, "item"):
                return value.item()
        except Exception:
            pass
        return value
    for column in (
        "quantity_on_hand", "inventory_value", "units", "revenue",
        "avg_daily_sales", "days_of_inventory", "lead_time_days",
    ):
        if column in frame.columns:
            values = frame[column].dropna().tolist()
            if values:
                metrics[column] = plain(values[0]) if len(values) == 1 else [plain(value) for value in values[:5]]
    if getattr(intent, "capability", "") == "historical_analysis":
        for source, target in (("qty_before", "quantity_before_total"), ("qty_now", "quantity_now_total"), ("qty_change", "quantity_change_total")):
            if source in frame:
                metrics[target] = plain(pd.to_numeric(frame[source], errors="coerce").sum())
        for source, target in (("snapshot_date_before", "snapshot_date_before"), ("snapshot_date", "snapshot_date")):
            if source in frame:
                dates = frame[source].dropna().astype(str).unique().tolist()
                if dates:
                    metrics[target] = dates[0]
    if getattr(intent, "capability", "") == "executive_summary":
        for key in ("quantity_on_hand_total", "inventory_value_total", "sales_units_total", "sales_revenue_total", "below_reorder_positions", "high_risk_positions", "medium_risk_positions", "sales_window_start_exclusive", "sales_window_end_inclusive"):
            if key in counts:
                metrics[key] = plain(counts[key])
    group_by = getattr(intent, "group_by", None)
    if getattr(intent, "question_kind", "") == "comparison" and group_by:
        group_column = {"store": "store_name", "product": "product_name"}.get(group_by, group_by)
        if group_column in frame:
            numeric = [c for c in ("quantity_on_hand", "inventory_value", "units", "revenue", "qty_now", "qty_before", "qty_change", "units_change", "growth_pct", "revenue_change", "revenue_growth_pct") if c in frame]
            metrics["comparison_results"] = frame[[group_column] + numeric].to_dict(orient="records")
    classifications = {}
    for column in ("inventory_status", "stockout_risk_v1", "demand_status", "excess_by_max_level", "excess_coverage_candidate", "no_recorded_sales_review"):
        if column in frame.columns:
            classifications[column] = frame[column].dropna().astype(str).value_counts().to_dict()
    quality = {}
    for column in ("inventory_data_quality", "demand_data_quality", "supplier_data_quality"):
        if column in frame.columns:
            quality[column] = frame[column].dropna().astype(str).value_counts().to_dict()
    filters = getattr(intent, "filters", None)
    requested = {
        "products": list(getattr(filters, "products", []) or []) if filters else [],
        "brands": list(getattr(filters, "brands", []) or []) if filters else [],
        "categories": list(getattr(filters, "categories", []) or []) if filters else [],
        "subcategories": list(getattr(filters, "subcategories", []) or []) if filters else [],
        "stores": list(getattr(filters, "stores", []) or []) if filters else [],
        "cities": list(getattr(filters, "cities", []) or []) if filters else [],
        "regions": list(getattr(filters, "regions", []) or []) if filters else [],
    }
    time = getattr(intent, "time", None)
    return AnswerFacts(
        capability=getattr(intent, "capability", ""),
        question_kind=getattr(intent, "question_kind", "lookup"),
        requested_scope=requested,
        resolved_scope=resolved_context,
        time_scope=time.model_dump() if hasattr(time, "model_dump") else {},
        basis=result.basis,
        metrics=metrics,
        classifications=classifications,
        data_quality=quality,
        counts=counts,
        notes=list(result.notes),
    )


@dataclass
class Evidence:
    question_kind: str
    capability: str
    dataframe: pd.DataFrame
    narrative: str
    basis: str
    sql: list[str]
    params: list[list]
    notes: list[str]


def compose_single(
    *,
    intent,
    result,
    narrative,
) -> Evidence:
    return Evidence(
        question_kind=intent.question_kind,
        capability=intent.capability,
        dataframe=result.df,
        narrative=narrative,
        basis=result.basis,
        sql=result.sql,
        params=result.params,
        notes=result.notes,
    )


def compose_compound(evidence: list[Evidence]) -> dict[str, Any]:
    narrative = "\n\n".join(
        item.narrative
        for item in evidence
        if item.narrative
    )

    return {
        "narrative": narrative,
        "sections": evidence,
    }
