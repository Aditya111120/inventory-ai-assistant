"""Validated, deterministic chart metadata derived from analytical results."""

from __future__ import annotations

from datetime import date
import math
import re
from typing import Any, Literal

import pandas as pd
from pydantic import BaseModel, ConfigDict, Field, model_validator


VisualizationType = Literal["kpi", "bar", "line", "donut", "grouped_bar", "scatter"]


class VisualizationSeries(BaseModel):
    model_config = ConfigDict(extra="forbid")

    key: str = Field(min_length=1)
    label: str = Field(min_length=1)
    unit: str = ""
    currency: str | None = None


class VisualizationSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: VisualizationType
    title: str = Field(min_length=1)
    description: str = ""
    category_field: str | None = None
    date_field: str | None = None
    series: list[VisualizationSeries] = Field(min_length=1)
    data: list[dict[str, Any]] = Field(min_length=1)
    reporting_period: str | None = None
    basis: Literal["Actual", "Simulated"] = "Actual"
    scope: dict[str, Any] = Field(default_factory=dict)
    complete: bool = True
    limitation: str | None = None

    @model_validator(mode="after")
    def validate_chart_data(self):
        if len({item.key for item in self.series}) != len(self.series):
            raise ValueError("Visualization series keys must be unique")
        if any(item.unit == "currency" and not item.currency for item in self.series):
            raise ValueError("Currency series require an ISO currency code")
        if any(
            item.currency and not re.fullmatch(r"[A-Z]{3}", item.currency)
            for item in self.series
        ):
            raise ValueError("Currency codes must use three uppercase letters")
        if any(
            item.get("unit") == "currency" and not item.get("currency")
            for item in self.data
        ):
            raise ValueError("Currency data points require an ISO currency code")
        for point in self.data:
            if any(
                not isinstance(value, (str, int, float, bool, type(None)))
                for value in point.values()
            ):
                raise ValueError("Visualization point values must be scalar")
            if self.type == "kpi":
                if not isinstance(point.get("label"), str) or _clean_number(point.get("value")) is None:
                    raise ValueError("KPI points need a label and a finite numeric value")
            elif self.type == "donut":
                if (
                    not isinstance(point.get("label"), str)
                    or _clean_number(point.get("count")) is None
                    or float(point["count"]) < 0
                ):
                    raise ValueError("Donut points need a label and a non-negative count")
            elif self.type == "scatter":
                if _clean_number(point.get("x")) is None or _clean_number(point.get("y")) is None:
                    raise ValueError("Scatter points need two finite numeric values")
            elif self.type == "line":
                if (
                    not self.date_field
                    or not isinstance(point.get(self.date_field), str)
                    or _clean_number(point.get(self.series[0].key)) is None
                ):
                    raise ValueError("Line points need a date and a finite numeric value")
                try:
                    date.fromisoformat(point[self.date_field])
                except ValueError as exc:
                    raise ValueError("Line chart dates must use ISO format") from exc
            elif (
                not self.category_field
                or not isinstance(point.get(self.category_field), str)
                or any(_clean_number(point.get(item.key)) is None for item in self.series)
            ):
                raise ValueError("Categorical chart points need labels and finite numeric values")
        return self


SUPPORTED_CAPABILITIES = {
    "inventory_visibility",
    "inventory_health",
    "stockout_risk",
    "excess_slow_moving",
    "what_if",
    "executive_summary",
    "sales_trend",
    "historical_analysis",
}
GROUP_COLUMNS = {
    "product": ("product_name", "Products"),
    "brand": ("brand", "Brands"),
    "category": ("category", "Categories"),
    "subcategory": ("subcategory", "Subcategories"),
    "store": ("store_name", "Stores"),
    "city": ("city", "Cities"),
    "region": ("region", "Regions"),
}
METRICS = {
    "quantity": ("quantity_on_hand", "Inventory units", "units", None),
    "inventory_value": ("inventory_value", "Inventory value", "currency", "INR"),
    "sales_units": ("units", "Sales units", "units", None),
    "sales_revenue": ("revenue", "Sales revenue", "currency", "INR"),
    "sales_growth_pct": ("growth_pct", "Sales growth", "percent", None),
    "days_of_inventory": ("days_of_inventory", "Days of inventory", "days", None),
    "stockout_risk": ("stockout_risk_v1", "Stockout risk", "classification", None),
}


def _clean_number(value: Any) -> float | int | None:
    if value is None or pd.isna(value):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    if not math.isfinite(number):
        return None
    return int(number) if number.is_integer() else number


def _classification_column(capability: str, frame: pd.DataFrame) -> str | None:
        candidates = (
            ("inventory_status", "stock_status")
            if capability == "inventory_health"
            else ("stockout_risk_v1", "stockout_risk")
        )
        return next((column for column in candidates if column in frame), None)


def _classification_visualization(
        *,
        intent,
        frame: pd.DataFrame,
        basis: str,
        complete: bool,
        preference: str | None,
) -> dict[str, Any] | None:
        if not complete:
            return None
        capability = getattr(intent, "capability", "")
        status_column = _classification_column(capability, frame)
        if not status_column:
            return None

        statuses = frame[status_column].astype("string").str.strip().fillna("Unknown")
        statuses = statuses.mask(statuses.eq(""), "Unknown")
        common = {"intent": intent, "dataframe": frame, "basis": basis, "preference": preference}
        group_by = getattr(intent, "group_by", None)

        if not group_by:
            counts = statuses.value_counts(dropna=False)
            if not 2 <= len(counts) <= 6:
                return None
            return _spec(
                chart_type="donut",
                title="Inventory status distribution" if capability == "inventory_health" else "Stockout risk distribution",
                series=[{"key": "count", "label": "Positions", "unit": "positions"}],
                data=[{"label": str(label), "count": int(value)} for label, value in counts.items()],
                category_field="label",
                description="Share of all classified positions in the complete authorized result.",
                **common,
            )

        group_column = GROUP_COLUMNS.get(group_by, (None, None))[0]
        if not group_column or group_column not in frame:
            return None
        group_labels = frame[group_column].astype("string").str.strip()
        if group_labels.isna().any() or group_labels.eq("").any():
            return None

        counts = (
            pd.DataFrame({"label": group_labels, "classification": statuses})
            .groupby(["label", "classification"], sort=False, dropna=False)
            .size()
            .unstack(fill_value=0)
        )
        canonical_order = ("High", "Medium", "Low", "No demand", "Out of Stock", "Unknown")
        ordered_statuses = [status for status in canonical_order if status in counts.columns]
        ordered_statuses.extend(sorted(str(status) for status in counts.columns if status not in ordered_statuses))
        counts = counts.reindex(columns=ordered_statuses, fill_value=0)

        goal = getattr(intent, "visualization_goal", "automatic")
        if goal == "ranking" or (goal == "automatic" and getattr(intent, "question_kind", "") == "ranking"):
            values = counts["High"] if "High" in counts else pd.Series(0, index=counts.index)
            ranked = values.rename("value").reset_index()
            ranked = ranked.sort_values(
                ["value", "label"], ascending=[False, True], kind="stable"
            )
            limit = max(1, int(getattr(intent, "limit", None) or 10))
            points = [
                {"label": str(row.label), "value": int(row.value)}
                for row in ranked.head(limit).itertuples(index=False)
            ]
            top_limited = len(ranked) > limit
            return _spec(
                chart_type="bar",
                title=f"High-risk positions by {GROUP_COLUMNS[group_by][1].lower()}",
                series=[{"key": "value", "label": "High-risk positions", "unit": "positions"}],
                data=points,
                category_field="label",
                complete=not top_limited,
                limitation=f"Top {limit} of {len(ranked)} groups." if top_limited else None,
                description="Sorted by High-risk position count, highest first.",
                **common,
            )

        counts["_total"] = counts.sum(axis=1)
        counts = counts.sort_values(
            ["_total"], ascending=False, kind="stable"
        ).drop(columns="_total")
        top_limited = len(counts) > 10
        counts = counts.head(10)
        series = [
            {"key": f"class_{index}", "label": str(status), "unit": "positions"}
            for index, status in enumerate(ordered_statuses)
        ]
        data = []
        for label, row in counts.iterrows():
            data.append({
                "label": str(label),
                **{
                    f"class_{index}": int(row[status])
                    for index, status in enumerate(ordered_statuses)
                },
            })
        chart_type = "grouped_bar" if len(series) > 1 else "bar"
        return _spec(
            chart_type=chart_type,
            title=f"Stockout risk by {GROUP_COLUMNS[group_by][1].lower()}" if capability == "stockout_risk" else f"Inventory status by {GROUP_COLUMNS[group_by][1].lower()}",
            series=series,
            data=data,
            category_field="label",
            complete=not top_limited,
            limitation=f"Top 10 of {len(counts)} groups." if top_limited else None,
            description="Classification counts by the requested group.",
            **common,
        )


def _relationship_visualization(
        *, intent, frame: pd.DataFrame, basis: str, complete: bool, preference: str | None
) -> dict[str, Any] | None:
        if not complete:
            return None
        metrics = list(getattr(intent, "metrics", []) or [])
        if len(metrics) < 2:
            return None
        numeric_columns = []
        for metric in metrics:
            column = METRICS.get(metric, (None,))[0]
            if column and column in frame:
                values = pd.to_numeric(frame[column], errors="coerce")
                if values.notna().sum() >= 3 and values.notna().all():
                    numeric_columns.append((metric, column))
        if len(numeric_columns) < 2:
            return None
        (x_metric, x_column), (y_metric, y_column) = numeric_columns[:2]
        label_column = next(
            (column for column in ("product_name", "store_name") if column in frame),
            None,
        )
        if not label_column:
            return None
        _, x_label, x_unit, x_currency = METRICS[x_metric]
        _, y_label, y_unit, y_currency = METRICS[y_metric]
        data = []
        for _, row in frame.iterrows():
            label = row[label_column]
            x_value = _clean_number(row[x_column])
            y_value = _clean_number(row[y_column])
            if pd.isna(label) or x_value is None or y_value is None:
                return None
            data.append({"label": str(label), "x": x_value, "y": y_value})
        return _spec(
            chart_type="scatter",
            title=f"{x_label} vs {y_label}",
            series=[
                {"key": "x", "label": x_label, "unit": x_unit, **({"currency": x_currency} if x_currency else {})},
                {"key": "y", "label": y_label, "unit": y_unit, **({"currency": y_currency} if y_currency else {})},
            ],
            data=data[:100],
            complete=len(data) <= 100,
            limitation=f"First 100 of {len(data)} entities." if len(data) > 100 else None,
            description="Each point is one returned entity at the same analytical grain.",
            intent=intent,
            dataframe=frame,
            basis=basis,
            preference=preference,
        )


def _trend_visualization(
        *, intent, frame: pd.DataFrame, basis: str, complete: bool, preference: str | None
) -> dict[str, Any] | None:
        if not complete:
            return None
        metrics = list(getattr(intent, "metrics", []) or [])
        metric = next((item for item in metrics if item in METRICS and METRICS[item][0] in frame), None)
        if metric is None:
            metric = next(
                (candidate for candidate in ("sales_units", "quantity", "inventory_value", "sales_revenue")
                 if METRICS[candidate][0] in frame),
                None,
            )
        if metric is None:
            return None
        value_column, label, unit, currency = METRICS[metric]
        date_column = next(
            (column for column in ("date", "sale_date", "sales_date", "month", "snapshot_date", "inventory_snapshot_date")
             if column in frame),
            None,
        )
        if date_column is None:
            return None
        dates = pd.to_datetime(frame[date_column], errors="coerce")
        values = pd.to_numeric(frame[value_column], errors="coerce")
        if dates.isna().any() or values.isna().any():
            return None
        by_date = pd.DataFrame({"date": dates.dt.date.astype(str), "value": values})
        by_date = by_date.groupby("date", as_index=False, sort=True)["value"].sum()
        if len(by_date) < 2:
            return None
        return _spec(
            chart_type="line",
            title=f"{label} trend",
            series=[{"key": "value", "label": label, "unit": unit, **({"currency": currency} if currency else {})}],
            data=[{"date": row.date, "value": _clean_number(row.value)} for row in by_date.itertuples(index=False)],
            date_field="date",
            description="Chronological observations from the authorized analytical result.",
            intent=intent,
            dataframe=frame,
            basis=basis,
            preference=preference,
        )


def _historical_comparison_visualization(
        *, intent, frame: pd.DataFrame, basis: str, complete: bool, preference: str | None
) -> dict[str, Any] | None:
        needed = {"qty_before", "qty_now", "snapshot_date_before", "snapshot_date"}
        if not complete or not needed.issubset(frame.columns):
            return None
        before_dates = pd.to_datetime(frame["snapshot_date_before"], errors="coerce")
        current_dates = pd.to_datetime(frame["snapshot_date"], errors="coerce")
        before_values = pd.to_numeric(frame["qty_before"], errors="coerce")
        current_values = pd.to_numeric(frame["qty_now"], errors="coerce")
        if (
            before_dates.isna().any()
            or current_dates.isna().any()
            or before_values.isna().any()
            or current_values.isna().any()
            or before_dates.nunique() != 1
            or current_dates.nunique() != 1
        ):
            return None
        before_date = before_dates.iloc[0].date().isoformat()
        current_date = current_dates.iloc[0].date().isoformat()
        if before_date >= current_date:
            return None
        group_by = getattr(intent, "group_by", None)
        group_column = GROUP_COLUMNS.get(group_by, (None, None))[0] if group_by else None
        if group_column and group_column in frame:
            labels = frame[group_column].astype("string").str.strip()
            if labels.isna().any() or labels.eq("").any():
                return None
            grouped = pd.DataFrame({
                "label": labels,
                "earlier": before_values,
                "current": current_values,
            }).groupby("label", as_index=False, sort=True)[["earlier", "current"]].sum()
            points = [
                {"label": row.label, "earlier": _clean_number(row.earlier), "current": _clean_number(row.current)}
                for row in grouped.itertuples(index=False)
            ]
        else:
            points = [{
                "label": "Total inventory",
                "earlier": _clean_number(before_values.sum()),
                "current": _clean_number(current_values.sum()),
            }]
        if not points or any(point["earlier"] is None or point["current"] is None for point in points):
            return None
        if len(points) > 10:
            points = points[:10]
            limitation = f"First 10 of {len(frame)} returned groups."
        else:
            limitation = None
        return _spec(
            chart_type="grouped_bar",
            title="Inventory: earlier vs latest snapshot",
            series=[
                {"key": "earlier", "label": before_date, "unit": "units"},
                {"key": "current", "label": current_date, "unit": "units"},
            ],
            data=points,
            category_field="label",
            complete=limitation is None,
            limitation=limitation,
            description="Two-snapshot comparison; not a continuous time series.",
            intent=intent,
            dataframe=frame,
            basis=basis,
            preference=preference,
        )


def select_visualization(
        *,
        intent,
        dataframe: pd.DataFrame,
        basis: str,
        presentation: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
        """Choose a deterministic chart from analytical intent and returned results."""
        capability = getattr(intent, "capability", "")
        if capability not in SUPPORTED_CAPABILITIES or dataframe is None or dataframe.empty:
            return None
        if basis not in {"Actual", "Simulated"}:
            return None
        preference = getattr(intent, "visualization_preference", None)
        supported_types = {"kpi", "bar", "line", "donut", "grouped_bar", "scatter"}
        if preference and preference not in supported_types:
            return None

        presentation = presentation or {}
        matching_rows = (presentation.get("result_counts") or {}).get("matching_result_rows")
        complete = matching_rows is None or int(matching_rows) <= len(dataframe)
        goal = getattr(intent, "visualization_goal", "automatic") or "automatic"
        question_kind = getattr(intent, "question_kind", "lookup")
        metrics = list(getattr(intent, "metrics", []) or [])
        if goal == "automatic":
            if question_kind == "ranking":
                goal = "ranking"
            elif question_kind == "comparison":
                goal = "comparison"

        frame = dataframe.copy()
        if goal == "detailed_records":
            return None
        if goal == "relationship":
            return _relationship_visualization(
                intent=intent, frame=frame, basis=basis,
                complete=complete, preference=preference,
            )
        if goal == "trend":
            result = _trend_visualization(
                intent=intent, frame=frame, basis=basis,
                complete=complete, preference=preference,
            )
            if result is not None:
                return result
            if capability == "historical_analysis":
                legacy_intent = intent.model_copy(update={"visualization_goal": "trend"})
                return _select_visualization_legacy(
                    intent=legacy_intent, dataframe=frame, basis=basis, presentation=presentation,
                )
            return None
        if goal == "ranking":
            if capability in {"stockout_risk", "inventory_health"}:
                if not getattr(intent, "group_by", None):
                    return None
                return _classification_visualization(
                    intent=intent, frame=frame, basis=basis, complete=complete,
                    preference=preference,
                )
            if not getattr(intent, "group_by", None) or not complete:
                return None
            return _select_visualization_legacy(
                intent=intent, dataframe=frame, basis=basis, presentation=presentation,
            )
        if goal == "distribution":
            if capability not in {"stockout_risk", "inventory_health"}:
                return None
            return _classification_visualization(
                intent=intent, frame=frame, basis=basis, complete=complete,
                preference=preference,
            )
        if goal == "comparison":
            if capability in {"stockout_risk", "inventory_health"}:
                return _classification_visualization(
                    intent=intent, frame=frame, basis=basis, complete=complete,
                    preference=preference,
                )
            if capability == "historical_analysis":
                return _historical_comparison_visualization(
                    intent=intent, frame=frame, basis=basis,
                    complete=complete, preference=preference,
                )
            if capability == "what_if":
                return _select_visualization_legacy(
                    intent=intent, dataframe=frame, basis=basis, presentation=presentation,
                )
            if capability == "sales_trend" and "units_prev" in frame.columns:
                return _select_visualization_legacy(
                    intent=intent, dataframe=frame, basis=basis, presentation=presentation,
                )
            if len(metrics) >= 2:
                return _relationship_visualization(
                    intent=intent, frame=frame, basis=basis,
                    complete=complete, preference=preference,
                )
            if getattr(intent, "group_by", None) and complete:
                return _select_visualization_legacy(
                    intent=intent, dataframe=frame, basis=basis, presentation=presentation,
                )
            return None
        if goal == "kpi":
            if getattr(intent, "group_by", None):
                return None
            return _select_visualization_legacy(
                intent=intent, dataframe=frame, basis=basis, presentation=presentation,
            )
        if goal != "automatic":
            return None

        group_by = getattr(intent, "group_by", None)
        if capability in {"stockout_risk", "inventory_health"} and group_by:
            return _classification_visualization(
                intent=intent, frame=frame, basis=basis, complete=complete,
                preference=preference,
            )
        if capability == "historical_analysis":
            return _historical_comparison_visualization(
                intent=intent, frame=frame, basis=basis,
                complete=complete, preference=preference,
            )
        return _select_visualization_legacy(
            intent=intent, dataframe=frame, basis=basis, presentation=presentation,
        )


def _plain_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    clean: list[dict[str, Any]] = []
    for row in rows:
        point: dict[str, Any] = {}
        for key, value in row.items():
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                point[key] = _clean_number(value)
            elif value is None or isinstance(value, (str, bool)):
                point[key] = value
            elif hasattr(value, "isoformat"):
                point[key] = value.isoformat()
            else:
                return []
        clean.append(point)
    return clean


def _period(intent, dataframe: pd.DataFrame) -> str | None:
    time = getattr(intent, "time", None)
    if time is None:
        return None
    kind = getattr(time, "kind", "none")
    if kind == "month":
        month, year = getattr(time, "month", None), getattr(time, "year", None)
        return f"{year:04d}-{month:02d}" if month and year else None
    if kind == "exact_date":
        return str(getattr(time, "date", None) or "") or None
    if kind == "date_range":
        start, end = getattr(time, "start_date", None), getattr(time, "end_date", None)
        return f"{start} to {end}" if start and end else None
    if kind == "latest" and "inventory_snapshot_date" in dataframe:
        dates = dataframe["inventory_snapshot_date"].dropna().astype(str).unique()
        return dates[0] if len(dates) == 1 else None
    return None


def _requested_scope(intent) -> dict[str, list[str]]:
    filters = getattr(intent, "filters", None)
    if filters is None:
        return {}
    names = {
        "products": "products",
        "brands": "brands",
        "categories": "categories",
        "subcategories": "subcategories",
        "stores": "stores",
        "cities": "cities",
        "regions": "regions",
    }
    return {
        label: [str(value) for value in (getattr(filters, name, None) or [])]
        for name, label in names.items()
        if getattr(filters, name, None)
    }


def _spec(
    *,
    chart_type: VisualizationType,
    title: str,
    series: list[dict[str, Any]],
    data: list[dict[str, Any]],
    intent,
    dataframe: pd.DataFrame,
    basis: str,
    category_field: str | None = None,
    date_field: str | None = None,
    complete: bool = True,
    limitation: str | None = None,
    description: str = "",
    preference: str | None = None,
) -> dict[str, Any] | None:
    if preference and preference != chart_type:
        return None
    rows = _plain_rows(data)
    if not rows:
        return None
    try:
        return VisualizationSpec(
            type=chart_type,
            title=title,
            description=description,
            category_field=category_field,
            date_field=date_field,
            series=series,
            data=rows,
            reporting_period=_period(intent, dataframe),
            basis=basis,
            scope=_requested_scope(intent),
            complete=complete,
            limitation=limitation,
        ).model_dump(mode="json")
    except (TypeError, ValueError):
        return None


def _select_visualization_legacy(
    *,
    intent,
    dataframe: pd.DataFrame,
    basis: str,
    presentation: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    """Select a chart only when its required fields and result grain are present."""
    capability = getattr(intent, "capability", "")
    if capability not in SUPPORTED_CAPABILITIES or dataframe is None or dataframe.empty:
        return None
    if basis not in {"Actual", "Simulated"}:
        return None

    frame = dataframe.copy()
    metrics = list(getattr(intent, "metrics", []) or [])
    group_by = getattr(intent, "group_by", None)
    presentation = presentation or {}
    result_counts = dict(presentation.get("result_counts") or {})
    matching_rows = result_counts.get("matching_result_rows")
    complete = matching_rows is None or int(matching_rows) <= len(frame)
    limitation = None if complete else f"Based on {len(frame)} returned rows; {matching_rows} matching rows were reported."
    common = {
        "intent": intent,
        "dataframe": frame,
        "basis": basis,
        "preference": getattr(intent, "visualization_preference", None),
    }

    if (
        capability in {"inventory_health", "stockout_risk"}
        and complete
        and not group_by
        and getattr(intent, "question_kind", "lookup") not in {"ranking", "comparison"}
        and getattr(intent, "visualization_goal", "automatic") in {"automatic", "distribution"}
    ):
        status_column = (
            "inventory_status" if capability == "inventory_health" else "stockout_risk_v1"
        )
        if status_column in frame.columns:
            counts = frame[status_column].fillna("Unclassified").astype(str).value_counts()
            if 2 <= len(counts) <= 6:
                donut_data = [{"label": label, "count": int(value)} for label, value in counts.items()]
                return _spec(
                    chart_type="donut",
                    title="Inventory status distribution" if capability == "inventory_health" else "Stockout risk distribution",
                    series=[{"key": "count", "label": "Positions", "unit": "positions"}],
                    data=donut_data,
                    category_field="label",
                    description="Share of all returned classified positions.",
                    **common,
                )

    if (
        capability == "historical_analysis"
        and getattr(intent, "visualization_goal", "automatic") in {"automatic", "trend"}
        and {
        "qty_before", "qty_now", "snapshot_date_before", "snapshot_date",
        }.issubset(frame.columns)
    ):
        before = pd.to_datetime(frame["snapshot_date_before"], errors="coerce")
        now = pd.to_datetime(frame["snapshot_date"], errors="coerce")
        if before.notna().all() and now.notna().all():
            dates = [before.iloc[0].date().isoformat(), now.iloc[0].date().isoformat()]
            values = [
                _clean_number(pd.to_numeric(frame["qty_before"], errors="coerce").sum()),
                _clean_number(pd.to_numeric(frame["qty_now"], errors="coerce").sum()),
            ]
            if all(value is not None for value in values) and dates[0] < dates[1]:
                return _spec(
                    chart_type="line",
                    title="Inventory across available snapshots",
                    series=[{"key": "quantity", "label": "Inventory units", "unit": "units"}],
                    data=[{"date": dates[0], "quantity": values[0]}, {"date": dates[1], "quantity": values[1]}],
                    date_field="date",
                    description="Two available weekly snapshots; this is a comparison, not a continuous time series.",
                    **common,
                )

    if (
        not group_by
        and getattr(intent, "question_kind", "lookup") not in {"ranking", "comparison"}
        and getattr(intent, "visualization_goal", "automatic") in {"automatic", "kpi"}
        and capability in {"sales_trend", "executive_summary", "inventory_health", "stockout_risk"}
        and presentation.get("kpi_metrics")
    ):
        kpis = []
        for metric in presentation["kpi_metrics"]:
            value = _clean_number(metric.get("value"))
            key, label = metric.get("key"), metric.get("label")
            if value is not None and key and label:
                is_currency = "value" in key or "revenue" in key
                kpis.append({
                    "label": str(label),
                    "value": value,
                    "unit": "currency" if is_currency else "count",
                    "currency": "INR" if is_currency else None,
                })
        if kpis:
            return _spec(
                chart_type="kpi",
                title="Analytical summary",
                series=[{"key": "value", "label": "Value", "unit": "count"}],
                data=kpis,
                description="Deterministic totals from the authorized analytical result.",
                **common,
            )

    if (
        not group_by
        and getattr(intent, "question_kind", "lookup") not in {"ranking", "comparison"}
        and getattr(intent, "visualization_goal", "automatic") in {"automatic", "kpi"}
    ):
        count_metrics = {
            "quantity": ("quantity_on_hand_total", "Inventory units", "units", None),
            "inventory_value": ("inventory_value_total", "Inventory value", "currency", "INR"),
            "sales_units": ("sales_units_total", "Sales units", "units", None),
            "sales_revenue": ("sales_revenue_total", "Sales revenue", "currency", "INR"),
        }
        allowed = (
            {"quantity", "inventory_value"}
            if capability in {"inventory_visibility", "inventory_health", "stockout_risk", "excess_slow_moving"}
            else {"sales_units", "sales_revenue"}
            if capability == "sales_trend"
            else set()
        )
        kpis = []
        for metric in metrics:
            if metric not in allowed:
                continue
            count_key, label, unit, currency = count_metrics[metric]
            value = _clean_number(result_counts.get(count_key))
            if value is not None:
                kpis.append({
                    "label": label,
                    "value": value,
                    "unit": unit,
                    **({"currency": currency} if currency else {}),
                })
        if kpis:
            return _spec(
                chart_type="kpi",
                title="Analytical summary",
                series=[{"key": "value", "label": "Value", "unit": "count"}],
                data=kpis,
                **common,
            )

    if (
        capability == "sales_trend"
        and getattr(intent, "visualization_goal", "automatic") in {"automatic", "kpi"}
        and not group_by
        and len(frame) == 1
        and not {"product_name", "brand", "category", "store_name", "city"}.intersection(frame.columns)
    ):
        kpis = []
        for metric in metrics:
            column = METRICS.get(metric, (None,))[0]
            if column and column in frame:
                value = _clean_number(frame.iloc[0][column])
                if value is not None:
                    label = METRICS[metric][1]
                    unit = METRICS[metric][2]
                    currency = METRICS[metric][3]
                    kpis.append({
                        "label": label,
                        "value": value,
                        "unit": unit,
                        **({"currency": currency} if currency else {}),
                    })
        if kpis:
            return _spec(
                chart_type="kpi",
                title="Sales summary",
                series=[{"key": "value", "label": "Value", "unit": "count"}],
                data=kpis,
                **common,
            )

    if capability == "what_if" and getattr(intent, "visualization_goal", "automatic") in {"automatic", "comparison"} and {
        "actual_days_of_inventory", "simulated_days_of_inventory",
    }.issubset(frame.columns):
        label_column = next((column for column in ("product_name", "store_name") if column in frame), None)
        if label_column:
            points = []
            for _, row in frame.iterrows():
                label = str(row[label_column] or "").strip()
                actual = _clean_number(row["actual_days_of_inventory"])
                simulated = _clean_number(row["simulated_days_of_inventory"])
                if label and actual is not None and simulated is not None:
                    points.append({"label": label, "actual": actual, "simulated": simulated})
            points.sort(key=lambda point: point["label"].casefold())
            top_limited = len(points) > 10
            points = points[:10]
            return _spec(
                chart_type="grouped_bar",
                title="Actual vs Simulated inventory coverage",
                series=[
                    {"key": "actual", "label": "Actual", "unit": "days"},
                    {"key": "simulated", "label": "Simulated", "unit": "days"},
                ],
                data=points,
                category_field="label",
                complete=complete and not top_limited,
                limitation=(
                    f"Top 10 of {len(frame)} returned entities."
                    if top_limited else limitation
                ),
                **common,
            )

    if capability == "sales_trend" and getattr(intent, "question_kind", "") == "comparison":
        grouped_columns = {
            "units": ("units_prev", "Sales units"),
            "revenue": ("revenue_prev", "Sales revenue"),
        }
        metric_column = next(
            (column for column in ("units", "revenue") if column in frame and grouped_columns[column][0] in frame),
            None,
        )
        group_column = next(
            (column for column in ("brand", "category", "store_name", "city", "product_name") if column in frame),
            None,
        )
        if metric_column and group_column:
            previous_column, label = grouped_columns[metric_column]
            points = []
            for _, row in frame.iterrows():
                name = str(row[group_column] or "").strip()
                current, previous = _clean_number(row[metric_column]), _clean_number(row[previous_column])
                if name and current is not None and previous is not None:
                    points.append({"label": name, "current": current, "previous": previous})
            points.sort(key=lambda item: item["label"].casefold())
            return _spec(
                chart_type="grouped_bar",
                title=f"{label}: current vs comparison period",
                series=[
                    {"key": "current", "label": "Current period", "unit": "currency" if metric_column == "revenue" else "units", "currency": "INR" if metric_column == "revenue" else None},
                    {"key": "previous", "label": "Comparison period", "unit": "currency" if metric_column == "revenue" else "units", "currency": "INR" if metric_column == "revenue" else None},
                ],
                data=points[:10],
                category_field="label",
                complete=complete and len(points) <= 10,
                limitation=limitation if len(points) <= 10 else f"Top 10 of {len(points)} returned groups.",
                **common,
            )

    if len(metrics) >= 2 and {"product_name", "store_name"}.intersection(frame.columns):
        numeric_columns = []
        for metric in metrics:
            column = METRICS.get(metric, (None,))[0]
            if column and column in frame and pd.to_numeric(frame[column], errors="coerce").notna().sum() >= 3:
                numeric_columns.append(column)
        if len(numeric_columns) >= 2:
            x_column, y_column = numeric_columns[:2]
            x_metric = next(
                metric for metric in metrics
                if METRICS.get(metric, (None,))[0] == x_column
            )
            y_metric = next(
                metric for metric in metrics
                if METRICS.get(metric, (None,))[0] == y_column
            )
            _, x_label, x_unit, x_currency = METRICS[x_metric]
            _, y_label, y_unit, y_currency = METRICS[y_metric]
            points = []
            label_column = "product_name" if "product_name" in frame else "store_name"
            for _, row in frame.iterrows():
                x_value, y_value = _clean_number(row[x_column]), _clean_number(row[y_column])
                if x_value is not None and y_value is not None:
                    points.append({"label": str(row[label_column]), "x": x_value, "y": y_value})
            if len(points) >= 3:
                return _spec(
                    chart_type="scatter",
                    title=f"{x_label} vs {y_label}",
                    series=[
                        {
                            "key": "x", "label": x_label, "unit": x_unit,
                            **({"currency": x_currency} if x_currency else {}),
                        },
                        {
                            "key": "y", "label": y_label, "unit": y_unit,
                            **({"currency": y_currency} if y_currency else {}),
                        },
                    ],
                    data=points[:100],
                    description="Each point is one returned product or store entity.",
                    complete=complete and len(points) <= 100,
                    limitation=limitation if len(points) <= 100 else f"First 100 of {len(points)} returned entities.",
                    **common,
                )

    if group_by in GROUP_COLUMNS and complete:
        category_column, plural = GROUP_COLUMNS[group_by]
        metric_name = next((metric for metric in metrics if metric in METRICS), None)
        if metric_name is None:
            metric_name = {
                "sales_trend": "sales_units",
                "historical_analysis": "quantity",
                "executive_summary": "inventory_value",
            }.get(capability)
        if metric_name:
            value_column, label, unit, currency = METRICS[metric_name]
            if category_column in frame and value_column in frame:
                values = pd.to_numeric(frame[value_column], errors="coerce")
                grouped = pd.DataFrame({
                    "label": frame[category_column].astype("string"),
                    "value": values,
                }).dropna(subset=["label", "value"])
                grouped = grouped[grouped["label"].str.strip().ne("")]
                if not grouped.empty:
                    grouped = grouped.groupby("label", as_index=False, sort=False)["value"].sum()
                    grouped = grouped.sort_values(
                        ["value", "label"], ascending=[False, True], kind="stable"
                    )
                    points = [
                        {"label": str(row.label), "value": _clean_number(row.value)}
                        for row in grouped.head(10).itertuples(index=False)
                    ]
                    top_limited = len(grouped) > 10
                    return _spec(
                        chart_type="bar",
                        title=f"{label} by {plural.lower()}",
                        series=[{"key": "value", "label": label, "unit": unit, "currency": currency}],
                        data=points,
                        category_field="label",
                        complete=complete and not top_limited,
                        limitation=(
                            f"Top 10 of {len(grouped)} returned groups."
                            if top_limited else limitation
                        ),
                        description="Sorted by value, highest first.",
                        **common,
                    )

    return None
