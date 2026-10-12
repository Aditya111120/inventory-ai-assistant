"""
Deterministic answer builder.

Business answers are derived from the calculated DataFrame and Intent.
The LLM is not used to decide winners, calculate numbers, or change scope.
"""

from __future__ import annotations

import re
from typing import Any

import pandas as pd


def _number(value: Any) -> str:
    if value is None:
        return "—"

    try:
        if pd.isna(value):
            return "—"
    except (TypeError, ValueError):
        pass

    if isinstance(value, float) and value.is_integer():
        return f"{int(value):,}"

    if isinstance(value, float):
        return f"{value:,.2f}"

    if isinstance(value, int):
        return f"{value:,}"

    try:
        number = float(value)
        if number.is_integer():
            return f"{int(number):,}"
        return f"{number:,.2f}"
    except (TypeError, ValueError):
        return str(value)


def _inr(value: Any) -> str:
    """Format a monetary value without exposing raw floating-point noise."""
    try:
        if value is None or pd.isna(value):
            return "INR —"
        return f"INR {float(value):,.2f}"
    except (TypeError, ValueError):
        return "INR —"


def _name(row: pd.Series) -> str:
    for column in (
        "product_name",
        "product",
        "store_name",
        "brand",
        "category",
    ):
        if column in row.index and pd.notna(row[column]):
            return str(row[column])

    return "The selected item"


def _metric_label(metric: str) -> str:
    return {
        "quantity": "inventory",
        "inventory_value": "inventory value",
        "days_of_inventory": "days of inventory",
        "stockout_risk": "stockout risk",
        "stock_status": "stock status",
        "sales_units": "sales",
        "sales_revenue": "sales revenue",
        "sales_growth_pct": "sales growth",
        "inventory_growth_pct": "inventory growth",
        "excess_value": "excess value",
    }.get(metric, metric.replace("_", " "))


def _requested_subject(intent, default: str = "Inventory") -> str:
    """Return a human label from explicit filters without inventing identity."""
    filters = getattr(intent, "filters", None)
    for field in ("products", "brands", "categories", "subcategories"):
        values = [str(value).strip() for value in (getattr(filters, field, []) or [])]
        if len(values) == 1 and values[0]:
            return values[0]
    return default


def _value_column(metric: str) -> str | None:
    return {
        "quantity": "quantity_on_hand",
        "inventory_value": "inventory_value",
        "days_of_inventory": "days_of_inventory",
        "stockout_risk": "stockout_risk",
        "stock_status": "stock_status",
        "sales_units": "units",
        "sales_revenue": "revenue",
        "sales_growth_pct": "growth_pct",
        "inventory_growth_pct": "inventory_growth_pct",
        "excess_value": "excess_value",
    }.get(metric)


def _question_requests_why(question: str) -> bool:
    q = question.casefold().strip()

    return any(
        re.search(pattern, q)
        for pattern in (
            r"\bwhy\b",
            r"\bhow come\b",
            r"\bhow did\b",
            r"\bexplain\b",
        )
    )


def complete_narrative_context(intent, df: pd.DataFrame, narrative: str) -> str:
    """Add missing human-readable scope/date context from resolved evidence."""
    if not narrative or df is None or df.empty:
        return narrative

    filters = getattr(intent, "filters", None)
    scope_parts: list[str] = []
    for field, label in (
        ("products", "product"), ("subcategories", "subcategory"),
        ("categories", "category"), ("brands", "brand"),
        ("stores", "store"), ("cities", "city"), ("regions", "region"),
    ):
        values = list(getattr(filters, field, []) or []) if filters else []
        if values:
            scope_parts.extend(f"{label} {value}" for value in values)
    if not scope_parts:
        scope_parts.append("all authorized stores")
    scope = ", ".join(scope_parts)

    time = getattr(intent, "time", None)
    period = None
    kind = getattr(time, "kind", "none")
    if kind == "month" and getattr(time, "month", None) and getattr(time, "year", None):
        import calendar
        period = f"{calendar.month_name[int(time.month)]} {int(time.year)}"
    elif kind == "exact_date" and getattr(time, "date", None):
        period = str(time.date)
    elif kind == "date_range" and getattr(time, "start_date", None) and getattr(time, "end_date", None):
        period = f"{time.start_date} to {time.end_date}"
    elif kind in {"last_n_days", "last_n_months"} and getattr(time, "n", None):
        unit = "days" if kind == "last_n_days" else "months"
        period = f"the last {int(time.n)} {unit}"
    elif kind in {"latest", "none"}:
        date_column = next((c for c in ("inventory_snapshot_date", "snapshot_date") if c in df.columns), None)
        dates = df[date_column].dropna().astype(str).unique().tolist() if date_column else []
        if len(dates) == 1:
            period = f"the inventory reading dated {dates[0]}"

    additions = []
    # Avoid duplicate context while ensuring the narrative is self-contained.
    missing_scope = [value for value in scope_parts if value.casefold() not in narrative.casefold()]
    if missing_scope:
        additions.append(f"This covers {', '.join(missing_scope)}")
    period_present = bool(period and period.casefold() in narrative.casefold())
    if period and not period_present:
        date_match = re.search(r"\d{4}-\d{2}-\d{2}", period)
        period_present = bool(date_match and date_match.group(0) in narrative)
    if period and not period_present:
        additions.append(f"for {period}")
    if not additions:
        return narrative
    context = " ".join(additions) + "."
    return f"{narrative.rstrip()} {context}"


def validate_narrative_evidence(intent, df: pd.DataFrame, narrative: str) -> list[str]:
    """Return factual-contract violations for user-facing deterministic claims."""
    errors: list[str] = []
    if df is None or df.empty:
        return errors
    if not narrative.strip():
        return ["narrative is empty despite a non-empty result"]

    time = getattr(intent, "time", None)
    kind = getattr(time, "kind", "none")
    if kind == "month" and getattr(time, "month", None) and getattr(time, "year", None):
        import calendar
        expected_period = f"{calendar.month_name[int(time.month)]} {int(time.year)}"
        if expected_period.casefold() not in narrative.casefold():
            errors.append(f"requested period {expected_period} is absent")

    capability = getattr(intent, "capability", "")
    question_kind = getattr(intent, "question_kind", "lookup")
    metrics = list(getattr(intent, "metrics", []) or [])
    primary_metric = getattr(getattr(intent, "sort", None), "by", None) or (metrics[0] if metrics else None)
    metric_terms = {
        "inventory_value": ("inventory value",),
        "stockout_risk": ("risk",),
        "stock_status": ("stock",),
        "sales_units": ("sales", "unit"),
        "sales_revenue": ("revenue",),
        "sales_growth_pct": ("sales growth",),
        "inventory_growth_pct": ("inventory growth",),
        "excess_value": ("excess value",),
    }.get(primary_metric, ())
    metric_missing = bool(metric_terms) and not all(term in narrative.casefold() for term in metric_terms)
    if primary_metric == "quantity":
        metric_missing = not any(term in narrative.casefold() for term in ("inventory", "unit"))
    elif primary_metric == "sales_units":
        metric_missing = not (
            ("sales" in narrative.casefold() and "unit" in narrative.casefold())
            or ("sold" in narrative.casefold() and "unit" in narrative.casefold())
        )
    if primary_metric == "days_of_inventory":
        metric_missing = not any(term in narrative.casefold() for term in ("coverage", "days of inventory"))
    if metric_missing:
        errors.append(f"requested {primary_metric} metric is not clearly stated")

    if capability == "historical_analysis":
        for column in ("snapshot_date_before", "snapshot_date"):
            if column in df:
                for date in df[column].dropna().astype(str).unique().tolist():
                    if date not in narrative:
                        errors.append(f"historical snapshot date {date} is missing")
        for column in ("qty_before", "qty_now"):
            if column in df:
                total = pd.to_numeric(df[column], errors="coerce").sum(min_count=1)
                if pd.notna(total) and _number(total) not in narrative:
                    errors.append(f"historical total for {column} is missing")

    if capability == "executive_summary":
        counts = dict(df.attrs.get("result_counts", {}))
        summary_fields = (
            ("quantity_on_hand_total", "quantity_on_hand", "units"),
            ("inventory_value_total", "inventory_value", "INR"),
            ("sales_units_total", "units", "units"),
            ("sales_revenue_total", "revenue", "INR"),
        )
        for count_key, column, _kind in summary_fields:
            value = counts.get(count_key)
            if value is None and column in df:
                value = pd.to_numeric(df[column], errors="coerce").sum(min_count=1)
            if value is not None and pd.notna(value):
                formatted = _inr(value) if _kind == "INR" else _number(value)
                if formatted.casefold() not in narrative.casefold():
                    errors.append(f"executive summary total {count_key} does not match evidence")

    if question_kind == "comparison":
        group = getattr(intent, "group_by", None)
        if isinstance(group, (list, tuple)):
            group = group[0] if len(group) == 1 else None
        group_column = {
            "brand": "brand", "category": "category", "store": "store_name",
            "city": "city", "region": "region", "product": "product_name",
        }.get(group)
        if group_column and group_column in df:
            group_label = {
                "brand": "brands", "category": "categories", "store": "stores",
                "city": "cities", "region": "regions", "product": "products",
            }.get(group, "groups")
            if group_label.casefold() not in narrative.casefold():
                errors.append(f"comparison narrative does not name the requested {group} grouping")
            metric_column = _value_column(primary_metric or "")
            if metric_column and metric_column in df:
                values = pd.to_numeric(df[metric_column], errors="coerce")
                valid = df.loc[values.notna(), [group_column]].copy()
                valid["_value"] = values[values.notna()].to_numpy()
                if len(valid) >= 2:
                    high = valid.sort_values("_value", ascending=False, kind="stable").iloc[0]
                    low = valid.sort_values("_value", ascending=True, kind="stable").iloc[0]
                    formatter = _inr if metric_column in {"revenue", "inventory_value"} else _number
                    comparison_metric = {
                        "sales_revenue": "revenue", "sales_units": "sales units",
                        "quantity": "inventory units", "inventory_value": "inventory value",
                    }.get(primary_metric, _metric_label(primary_metric or "metric"))
                    expected = (
                        f"{high[group_column]} leads in {comparison_metric} among {group_label} "
                        f"with {formatter(high['_value'])}, compared with {low[group_column]} at {formatter(low['_value'])}."
                    )
                    if expected.casefold() not in narrative.casefold():
                        errors.append("comparison leaders or values do not match the grouped result")

    if question_kind == "ranking" and capability == "stockout_risk":
        risk_col = "stockout_risk_v1" if "stockout_risk_v1" in df.columns else "stockout_risk"
        limit = max(1, min(int(getattr(intent, "limit", None) or 1), len(df)))
        risk_order = {"High": 0, "Medium": 1} if getattr(getattr(intent, "sort", None), "direction", "desc") == "desc" else {"Medium": 0, "High": 1}
        ranked = df.copy()
        ranked["_risk_order"] = ranked[risk_col].map(risk_order).fillna(2) if risk_col in ranked else 2
        sort_columns = ["_risk_order"]
        ascending = [True]
        if "days_of_inventory" in ranked:
            sort_columns.append("days_of_inventory")
            ascending.append(getattr(getattr(intent, "sort", None), "direction", "desc") == "desc")
        for col in ("product_id", "store_id"):
            if col in ranked:
                sort_columns.append(col)
                ascending.append(True)
        ranked = ranked.sort_values(sort_columns, ascending=ascending, kind="stable", na_position="last")
        if f"{limit} " not in narrative:
            errors.append("ranking narrative does not identify the requested displayed count")
        narrative_positions: list[int] = []
        for _, row in ranked.head(1).iterrows():
            name = str(row.get("product_name", "")).strip()
            position = narrative.casefold().find(name.casefold()) if name else -1
            if name and position < 0:
                errors.append(f"ranked product {name} is missing from the narrative")
            elif position >= 0:
                narrative_positions.append(position)
            risk = row.get(risk_col)
            coverage = row.get("days_of_inventory")
            expected = name
            if pd.notna(row.get("store_name")):
                expected += f" at {row['store_name']}"
                if pd.notna(row.get("city")):
                    expected += f", {row['city']}"
            if pd.notna(risk):
                expected += f" ({risk} risk"
            if pd.notna(coverage):
                expected += f"; {_number(coverage)} days of coverage"
            if pd.notna(row.get("lead_time_days")):
                expected += f"; {_number(row['lead_time_days'])}-day supplier lead time"
            if pd.notna(risk) or pd.notna(coverage):
                expected += ")"
            if expected and expected.casefold() not in narrative.casefold():
                errors.append(f"ranked evidence for {name or 'a product'} does not match its identity, V1 risk, or coverage")
        if narrative_positions != sorted(narrative_positions):
            errors.append("ranked product-store positions are not narrated in calculated order")
    elif question_kind == "ranking" and getattr(intent, "sort", None) is not None:
        group = getattr(intent, "group_by", None)
        if isinstance(group, (list, tuple)):
            group = group[0] if len(group) == 1 else None
        group_column = {
            "product": "product_name", "brand": "brand", "category": "category",
            "subcategory": "subcategory", "store": "store_name", "city": "city",
            "region": "region",
        }.get(group)
        sort_column = _value_column(getattr(intent.sort, "by", None))
        if group_column and group_column in df and sort_column and sort_column in df:
            descending = getattr(intent.sort, "direction", "desc") == "desc"
            ranked = df.sort_values(sort_column, ascending=not descending, kind="stable", na_position="last")
            limit = max(1, min(int(getattr(intent, "limit", None) or 1), len(ranked)))
            if limit > 1 and f"{limit} " not in narrative:
                errors.append("ranking narrative does not identify the requested top-N count")
            formatter = _inr if sort_column in {"revenue", "inventory_value"} else _number
            narrative_positions: list[int] = []
            for _, row in ranked.head(1 if limit > 1 else limit).iterrows():
                name = str(row.get(group_column, "")).strip()
                value = row.get(sort_column)
                position = narrative.casefold().find(name.casefold()) if name else -1
                if name and position < 0:
                    errors.append(f"ranked {group} {name} is missing from the narrative")
                elif position >= 0:
                    narrative_positions.append(position)
                if limit > 1 and pd.notna(value) and formatter(value).casefold() not in narrative.casefold():
                    errors.append(f"ranked metric value for {name or group} is missing")
            if narrative_positions != sorted(narrative_positions):
                errors.append(f"ranked {group} results are not narrated in calculated order")
    elif capability == "what_if" and getattr(intent, "scenario", None) is not None and len(df) == 1:
        scenario_value = _number(getattr(intent.scenario, "value", None))
        if scenario_value not in narrative:
            errors.append("scenario value is missing from the narrative")
        if getattr(intent.scenario, "kind", "") == "demand_change_pct":
            row = df.iloc[0]
            actual = row.get("actual_days_of_inventory")
            simulated = row.get("simulated_days_of_inventory", row.get("days_of_inventory"))
            if pd.notna(actual) and _number(actual) not in narrative:
                errors.append("actual coverage is missing from the what-if narrative")
            if pd.notna(simulated) and _number(simulated) not in narrative:
                errors.append("simulated coverage is missing from the what-if narrative")
            if "simulation" not in narrative.casefold() and "simulated" not in narrative.casefold():
                errors.append("what-if result is not identified as a simulation")
    return errors


def build_subject_comparison_answer(
    *,
    subject_name: str,
    subject_value: float,
    winner_name: str,
    winner_value: float,
    metric_label: str,
) -> str:
    if winner_name == subject_name:
        return (
            f"Yes. {subject_name} has the highest {metric_label} "
            f"with {_number(subject_value)}."
        )

    return (
        f"No. {subject_name} has {_number(subject_value)} "
        f"{metric_label}, while {winner_name} is highest "
        f"at {_number(winner_value)}."
    )


def build_inventory_explanation(
    df: pd.DataFrame,
    subject_name: str | None = None,
) -> str:
    if (
        df is None
        or df.empty
        or "quantity_on_hand" not in df.columns
    ):
        return ""

    working = df.copy()

    working["quantity_on_hand"] = pd.to_numeric(
        working["quantity_on_hand"],
        errors="coerce",
    )

    working = working.dropna(
        subset=["quantity_on_hand"]
    )

    if working.empty:
        return ""

    ordered = working.sort_values(
        "quantity_on_hand",
        ascending=True,
        kind="stable",
    ).reset_index(drop=True)

    winner = ordered.iloc[0]

    winner_name = _name(winner)
    winner_value = float(winner["quantity_on_hand"])

    subject_row = None

    if subject_name:

        # Prefer exact product-name match.
        if "product_name" in ordered.columns:

            matches = ordered[
                ordered["product_name"]
                .astype(str)
                .str.casefold()
                == subject_name.casefold()
            ]

            if not matches.empty:
                subject_row = matches.iloc[0]

        # Fallback to SKU/model.
        if subject_row is None:

            for column in ("sku", "model", "product"):

                if column not in ordered.columns:
                    continue

                matches = ordered[
                    ordered[column]
                    .astype(str)
                    .str.casefold()
                    == subject_name.casefold()
                ]

                if not matches.empty:
                    subject_row = matches.iloc[0]
                    break

    if subject_row is None:
        subject_row = winner

    resolved_subject_name = _name(subject_row)
    subject_value = float(
        subject_row["quantity_on_hand"]
    )

    # User premise was wrong.
    if subject_value != winner_value:

        return (
            f"{resolved_subject_name} does not have the lowest inventory. "
            f"It has {_number(subject_value)} units on hand, while "
            f"{winner_name} has the lowest at "
            f"{_number(winner_value)} units."
        )

    # Subject really is the lowest.
    if len(ordered) > 1:

        second = ordered.iloc[1]

        return (
            f"{resolved_subject_name} has the lowest inventory because "
            f"it has {_number(subject_value)} units on hand, which is "
            f"the lowest among the {len(ordered)} products in the "
            f"comparison. The next-lowest is {_name(second)} at "
            f"{_number(second['quantity_on_hand'])} units."
        )

    return (
        f"{resolved_subject_name} has the lowest inventory with "
        f"{_number(subject_value)} units on hand."
    )


def _build_ranking_answer(
    intent,
    df: pd.DataFrame,
) -> str:

    sort = getattr(intent, "sort", None)

    if sort is None:
        return ""

    sort_by = getattr(sort, "by", None)
    direction = getattr(sort, "direction", None)

    column = _value_column(sort_by)

    is_stockout_ranking = getattr(intent, "capability", "") == "stockout_risk"
    has_v1_risk = "stockout_risk_v1" in df.columns
    if column is None or (column not in df.columns and not (is_stockout_ranking and has_v1_risk)):
        return ""

    if is_stockout_ranking:
        risk_column = "stockout_risk_v1" if "stockout_risk_v1" in df.columns else "stockout_risk"
        if risk_column not in df.columns:
            return "The requested stockout ranking is unavailable because V1 risk classifications are missing."
        ranked = df.copy()
        descending = str(direction or "desc").casefold().startswith("desc")
        risk_order = {"High": 0, "Medium": 1} if descending else {"Medium": 0, "High": 1}
        ranked["_risk_rank"] = ranked[risk_column].map(risk_order).fillna(2)
        sort_columns = ["_risk_rank"]
        ascending = [True]
        if "days_of_inventory" in ranked.columns:
            sort_columns.append("days_of_inventory")
            ascending.append(descending)
        tie_breakers = [column for column in ("product_id", "store_id") if column in ranked]
        sort_columns.extend(tie_breakers)
        ascending.extend([True] * len(tie_breakers))
        ranked = ranked.sort_values(sort_columns, ascending=ascending, na_position="last", kind="stable")
        count = max(1, min(int(getattr(intent, "limit", None) or 1), len(ranked)))
        rows = []
        for _, row in ranked.head(1).iterrows():
            identity = str(row.get("product_name") or "Product")
            if pd.notna(row.get("store_name")):
                identity += f" at {row['store_name']}"
                if pd.notna(row.get("city")):
                    identity += f", {row['city']}"
            risk = row.get(risk_column)
            coverage = row.get("days_of_inventory")
            detail_parts = [f"{risk} risk"]
            if coverage is not None and pd.notna(coverage):
                detail_parts.append(f"{_number(coverage)} days of coverage")
            lead_time = row.get("lead_time_days")
            if lead_time is not None and pd.notna(lead_time):
                detail_parts.append(f"{_number(lead_time)}-day supplier lead time")
            rows.append(f"{identity} ({'; '.join(detail_parts)})")
        if not rows:
            return "No positions meet the approved High or Medium stockout-risk criteria in this scope."
        counts = dict(df.attrs.get("result_counts", {}))
        matched = int(counts.get("matching_result_rows", len(ranked)))
        ranking_label = "highest-risk" if descending else "lowest-risk"
        eligible_note = "" if descending else " within the eligible High/Medium set"
        return (
            f"The {ranking_label} position{eligible_note} is "
            f"{rows[0]} among {matched} qualifying positions; "
            f"{count} results are shown in the table."
        )

    ascending = direction == "asc"

    ordered = (
        df.sort_values(
            column,
            ascending=ascending,
            kind="stable",
            na_position="last",
        )
        .reset_index(drop=True)
    )

    if ordered.empty:
        return ""

    group_by = getattr(intent, "group_by", None)
    if isinstance(group_by, (list, tuple)):
        group_by = group_by[0] if len(group_by) == 1 else None
    group_column = {
        "product": "product_name", "brand": "brand", "category": "category",
        "subcategory": "subcategory", "store": "store_name", "city": "city",
        "region": "region",
    }.get(group_by)
    if not group_column or group_column not in ordered.columns or ordered[group_column].dropna().empty:
        metric_value = ordered.iloc[0][column]
        if sort_by == "sales_units":
            return f"Across the matching records, {_number(metric_value)} units were sold; the grouped winner's identity is unavailable."
        if sort_by == "sales_revenue":
            return f"Across the matching records, sales revenue was {_inr(metric_value)}; the grouped winner's identity is unavailable."
        return "The ranking metric is available, but its result does not contain the requested group identity."

    adjective = "lowest" if ascending else "highest"
    top_n = max(1, min(int(getattr(intent, "limit", None) or 1), len(ordered)))
    winners = ordered.head(top_n)
    label = {
        "product": "product", "brand": "brand", "category": "category",
        "subcategory": "subcategory", "store": "store", "city": "city",
        "region": "region",
    }.get(group_by, "group")
    if top_n > 1:
        metric_label = {
            "quantity": "inventory units", "sales_units": "sales units",
            "sales_revenue": "sales revenue", "inventory_value": "inventory value",
            "days_of_inventory": "days of inventory",
        }.get(sort_by, _metric_label(sort_by))
        formatter = _inr if sort_by in {"sales_revenue", "inventory_value"} else _number
        direction = "highest" if not ascending else "lowest"
        winner = winners.iloc[0]
        winner_name = str(winner[group_column])
        location = ""
        if (
            group_column != "store_name"
            and "store_name" in winner.index
            and pd.notna(winner.get("store_name"))
        ):
            location = f" at {winner['store_name']}"
            if "city" in winner.index and pd.notna(winner.get("city")):
                location += f", {winner['city']}"
        value = formatter(winner[column])
        if sort_by == "stockout_risk":
            risk_column = (
                "stockout_risk_v1"
                if "stockout_risk_v1" in winner.index
                else "stockout_risk"
            )
            risk = winner.get(risk_column)
            doi = winner.get("days_of_inventory")
            detail = f"{risk} risk"
            if doi is not None and pd.notna(doi):
                detail += f"; {_number(doi)} days of coverage"
            value = detail
        return (
            f"The {direction} {label} by {metric_label} is "
            f"{winner_name}{location} ({value}), among "
            f"{len(ordered)} returned results."
        )

    winner = winners.iloc[0]
    winner_name = str(winner[group_column])
    winner_value = winner[column]

    if sort_by == "quantity":
        subject = f"{winner_name} has the {adjective} inventory" if group_by == "product" else f"{winner_name} has the {adjective} inventory among {label}s"
        return (
            f"{subject}, "
            f"with {_number(winner_value)} units on hand."
        )

    if sort_by == "sales_units":
        subject = f"{winner_name} has the {adjective} sales" if group_by == "product" else f"{winner_name} has the {adjective} sales among {label}s"
        return (
            f"{subject}, "
            f"with {_number(winner_value)} units sold."
        )

    if sort_by == "sales_revenue":
        subject = f"{winner_name} has the {adjective} sales revenue" if group_by == "product" else f"{winner_name} has the {adjective} sales revenue among {label}s"
        return (
            f"{subject}, "
            f"at {_inr(winner_value)}."
        )

    if sort_by == "days_of_inventory":
        return (
            f"{winner_name} has the {adjective} days of inventory, "
            f"at {_number(winner_value)} days."
        )

    return (
        f"{winner_name} has the {adjective} "
        f"{_metric_label(sort_by)}, "
        f"with {_number(winner_value)}."
    )


def _build_direct_metric_answer(
    intent,
    df: pd.DataFrame,
) -> str:

    metrics = list(
        getattr(intent, "metrics", []) or []
    )

    if not metrics or df.empty:
        return ""

    metric = metrics[0]
    row = df.iloc[0]
    name = _name(row)
    location = ""
    store = row.get("store_name")
    city = row.get("city")
    if store is not None and pd.notna(store):
        location = f" at {store}"
        if city is not None and pd.notna(city):
            location += f", {city}"
    elif city is not None and pd.notna(city):
        location = f" across {city} stores"
    elif "quantity_on_hand" in df.columns:
        location = " across all stores"

    if metric == "days_of_inventory":

        value = row.get(
            "days_of_inventory"
        )

        if value is None or pd.isna(value):
            return (
                f"{name} has no recent sales, "
                "so days of inventory cannot be calculated."
            )

        return (
            f"{name} has "
            f"{_number(value)} days of inventory coverage."
        )

    if metric == "stockout_risk":

        value = row.get("stockout_risk_v1", row.get("stockout_risk"))

        return (
            f"{name} has a "
            f"{str(value).lower()} stockout risk."
        )

    if metric == "stock_status":

        value = row.get("inventory_status", row.get("stock_status"))

        return (
            f"{name} currently has a "
            f"{str(value).lower()} stock status."
        )

    if metric == "quantity":

        value = row.get(
            "quantity_on_hand"
        )

        if value is not None:
            return (
                f"{name} currently has {_number(value)} units on hand"
                f"{location}."
            )

    if metric == "inventory_value":

        value = row.get(
            "inventory_value"
        )

        if value is not None:
            return (
                f"{name} has an inventory value of {_number(value)}"
                f"{location}."
            )

    if metric == "sales_units":

        value = row.get("units")

        if value is not None:
            return (
                f"{name} recorded "
                f"{_number(value)} units sold."
            )

    if metric == "sales_revenue":

        value = row.get("revenue")

        if value is not None:
            return (
                f"{name} generated "
                f"{_number(value)} in sales revenue."
            )

    return ""


def _build_sales_answer(intent, df: pd.DataFrame, rules=None) -> str:
    """Use aggregate numbers plus the resolved request identity."""
    if df.empty or "units" not in df.columns:
        return ""
    import calendar
    time = getattr(intent, "time", None)
    kind = getattr(time, "kind", "none")
    if kind == "month" and getattr(time, "year", None) and getattr(time, "month", None):
        period = f"in {calendar.month_name[int(time.month)]} {int(time.year)}"
    elif kind == "exact_date" and getattr(time, "date", None):
        period = f"on {time.date}"
    elif kind == "date_range" and getattr(time, "start_date", None) and getattr(time, "end_date", None):
        period = f"from {time.start_date} to {time.end_date}"
    else:
        period = (
            f"over the approved last {int(rules.sales_window_days)}-day sales window"
            if rules is not None and getattr(rules, "sales_window_days", None)
            else "during the selected period"
        )
    comparison_period = None
    if getattr(time, "comparison_month", None) and getattr(time, "comparison_year", None):
        comparison_period = f"{calendar.month_name[int(time.comparison_month)]} {int(time.comparison_year)}"
    filters = getattr(intent, "filters", None)
    def names_from(column, attr):
        names = df[column].dropna().astype(str).unique().tolist() if column in df.columns else []
        values = list(getattr(filters, attr, []) or []) if filters else []
        return names[0] if len(names) == 1 else (values[0] if len(values) == 1 else None)
    product = names_from("product_name", "products") or names_from("brand", "brands") or names_from("category", "categories") or names_from("subcategory", "subcategories")
    store = names_from("store_name", "stores")
    city = names_from("city", "cities")
    location = f"at {store}" if store else (f"in {city}" if city else "across the authorized stores")
    units = pd.to_numeric(df["units"], errors="coerce").sum(min_count=1)
    revenue = pd.to_numeric(df["revenue"], errors="coerce").sum(min_count=1) if "revenue" in df.columns else None
    requested_metrics = list(getattr(intent, "metrics", []) or [])
    primary_metric = requested_metrics[0] if requested_metrics else "sales_units"
    if "units_prev" in df.columns or "revenue_prev" in df.columns:
        if primary_metric == "sales_revenue" and "revenue_prev" in df.columns:
            current_revenue = pd.to_numeric(df["revenue"], errors="coerce").sum(min_count=1)
            previous_revenue = pd.to_numeric(df["revenue_prev"], errors="coerce").sum(min_count=1)
            revenue_change = current_revenue - previous_revenue if pd.notna(current_revenue) and pd.notna(previous_revenue) else None
            revenue_pct = (revenue_change / previous_revenue * 100) if revenue_change is not None and previous_revenue else None
            prior_label = comparison_period or "the comparison period"
            text = f"Sales revenue was {_inr(previous_revenue)} in {prior_label} and {_inr(current_revenue)} {period} {location}."
            if revenue_change is not None:
                text += f" That is a change of {_inr(revenue_change)}" + (f" ({revenue_pct:+.1f}%)." if revenue_pct is not None else ".")
            if "units_prev" in df.columns:
                prior_units = pd.to_numeric(df["units_prev"], errors="coerce").sum(min_count=1)
                if pd.notna(units) and pd.notna(prior_units):
                    text += f" Supporting sales volume was {_number(prior_units)} versus {_number(units)} units."
        else:
            prior = pd.to_numeric(df["units_prev"], errors="coerce").sum(min_count=1)
            change = units - prior if pd.notna(units) and pd.notna(prior) else None
            pct = (change / prior * 100) if change is not None and prior else None
            text = f"Sales were {_number(prior)} units in the comparison period and {_number(units)} units {location} {period}."
            if change is not None:
                text += f" That is a change of {_number(change)} units" + (f" ({pct:+.1f}%)." if pct is not None else ".")
            if "revenue" in df and "revenue_prev" in df:
                current_revenue = pd.to_numeric(df["revenue"], errors="coerce").sum(min_count=1)
                previous_revenue = pd.to_numeric(df["revenue_prev"], errors="coerce").sum(min_count=1)
                text += f" Supporting revenue was {_inr(previous_revenue)} versus {_inr(current_revenue)}."
        return text
    requested_value = revenue if primary_metric == "sales_revenue" else units
    if requested_value is None or pd.isna(requested_value):
        return f"I couldn't find recorded sales for {product or 'these products'} {location} {period}."
    subject = product or _requested_subject(intent, "The matching products")
    if primary_metric == "sales_revenue" and revenue is not None and pd.notna(revenue):
        answer = f"{subject} generated {_inr(revenue)} in sales revenue {location} {period}."
        answer += f" Recorded volume was {_number(units)} units."
    else:
        answer = f"{subject} sold {_number(units)} units {location} {period}."
        if revenue is not None and pd.notna(revenue):
            answer += f" Recorded revenue was {_inr(revenue)}."
    return answer


def _build_group_comparison_answer(intent, df: pd.DataFrame) -> str:
    """Explain grouped comparison from complete deterministic result columns."""
    group = getattr(intent, "group_by", None)
    if isinstance(group, (list, tuple)):
        group = group[0] if len(group) == 1 else None
    group_column = {
        "brand": "brand", "category": "category", "store": "store_name",
        "city": "city", "region": "region", "product": "product_name",
    }.get(group)
    if not group_column or group_column not in df.columns:
        return ""
    metric = next(iter(getattr(intent, "metrics", []) or []), "")
    if getattr(intent, "capability", "") == "sales_trend" and ("units_prev" in df.columns or "revenue_prev" in df.columns):
        current_col = "revenue" if metric == "sales_revenue" else "units"
        previous_col = current_col + "_prev"
        if current_col in df.columns and previous_col in df.columns:
            current = pd.to_numeric(df[current_col], errors="coerce").sum()
            previous = pd.to_numeric(df[previous_col], errors="coerce").sum()
            delta = current - previous
            pct = delta / previous * 100 if previous else None
            label = "revenue" if current_col == "revenue" else "sales units"
            formatter = _inr if current_col == "revenue" else _number
            return f"Across {group_column} groups, {label} changed from {formatter(previous)} to {formatter(current)}; a difference of {formatter(delta)}" + (f" ({pct:+.1f}%)." if pct is not None else ".")
    if "qty_now" in df.columns and "qty_before" in df.columns:
        current = pd.to_numeric(df["qty_now"], errors="coerce").sum()
        prior = pd.to_numeric(df["qty_before"], errors="coerce").sum()
        delta = current - prior
        pct = delta / prior * 100 if prior else None
        dates = []
        for column in ("snapshot_date_before", "snapshot_date"):
            if column in df.columns:
                vals = df[column].dropna().astype(str).unique().tolist()
                if vals:
                    dates.append(vals[0])
        period = f" between the weekly snapshots {dates[0]} and {dates[-1]}" if len(dates) == 2 else " between the available weekly snapshots"
        return f"Inventory changed by {_number(delta)} units{period}; total was {_number(prior)} and is now {_number(current)}" + (f" ({pct:+.1f}%)." if pct is not None else ".")
    if metric == "inventory_value":
        column, label = "inventory_value", "inventory value"
    elif metric == "sales_revenue" or (not metric and "revenue" in df.columns and getattr(intent, "capability", "") == "sales_trend"):
        column, label = "revenue", "revenue"
    elif metric == "sales_units" or (not metric and "units" in df.columns and getattr(intent, "capability", "") == "sales_trend"):
        column, label = "units", "sales units"
    elif metric == "stockout_risk":
        column, label = "stockout_risk_v1", "high-risk positions"
        if column not in df.columns:
            return ""
        data = df.assign(_metric=df[column].eq("High").astype(int)).groupby(group_column, dropna=False)["_metric"].sum().reset_index()
    else:
        column, label = "quantity_on_hand", "inventory units"
    if "data" not in locals():
        if column not in df.columns:
            return ""
        data = df.groupby(group_column, dropna=False)[column].sum(min_count=1).reset_index()
    data = data.dropna(subset=["_metric"] if "_metric" in data else [column])
    if len(data) < 2:
        return ""
    value_column = "_metric" if "_metric" in data else column
    data = data.sort_values(value_column, ascending=False, kind="stable")
    high, low = data.iloc[0], data.iloc[-1]
    difference = float(high[value_column]) - float(low[value_column])
    percent = difference / float(low[value_column]) * 100 if float(low[value_column]) else None
    group_label = {
        "brand": "brands", "category": "categories", "store": "stores",
        "city": "cities", "region": "regions", "product": "products",
    }.get(group, "groups")
    result = f"{high[group_column]} leads in {label} among {group_label} with {_inr(high[value_column]) if label in {'revenue', 'inventory value'} else _number(high[value_column])}, compared with {low[group_column]} at {_inr(low[value_column]) if label in {'revenue', 'inventory value'} else _number(low[value_column])}."
    result += f" The difference is {_inr(difference) if label in {'revenue', 'inventory value'} else _number(difference)}"
    result += f" ({percent:.1f}% higher)." if percent is not None else "."
    if metric == "inventory_value":
        result += (
            " This is an Actual result."
        )
    return result


def _inventory_scope_label(intent, df: pd.DataFrame) -> str:
    stores = (
        df["store_name"].dropna().astype(str).unique().tolist()
        if "store_name" in df.columns else []
    )
    cities = (
        df["city"].dropna().astype(str).unique().tolist()
        if "city" in df.columns else []
    )
    filters = getattr(intent, "filters", None)
    requested_stores = list(getattr(filters, "stores", []) or []) if filters else []
    if len(stores) == 1 and (requested_stores or len(df) == 1):
        return f"at {stores[0]}" + (f", {cities[0]}" if len(cities) == 1 else "")
    if len(cities) == 1:
        return f"in {cities[0]}"
    if "store_id" in df.columns:
        store_count = int(df["store_id"].nunique(dropna=True))
        if store_count:
            return f"across {store_count} authorized stores"
    return "in the authorized scope"


def _sales_observation_window(df: pd.DataFrame, rules=None) -> str:
    attrs = df.attrs
    start = attrs.get("sales_window_start_exclusive")
    end = attrs.get("sales_window_end_inclusive")
    if start and end:
        return f"the sales window after {start} through {end}"
    days = attrs.get("window_days") or getattr(rules, "sales_window_days", None)
    return f"the approved {int(days)}-day sales window" if days else "the selected sales window"


def _inventory_snapshot_text(df: pd.DataFrame) -> str:
    for column in ("inventory_snapshot_date", "snapshot_date"):
        if column in df.columns:
            dates = df[column].dropna().astype(str).unique().tolist()
            if len(dates) == 1:
                return f" Inventory snapshot as of {dates[0]}."
    return ""


def _build_inventory_classification_answer(intent, df: pd.DataFrame, rules=None) -> str:
    classification = getattr(intent, "inventory_classification", None)
    if classification not in {
        "slow_moving_candidate",
        "no_recorded_sales",
        "excess_by_max_level",
        "excess_coverage_candidate",
    }:
        return ""

    counts = dict(df.attrs.get("result_counts", {}))
    positions = int(counts.get("product_store_positions", len(df)))
    products = int(counts.get(
        "distinct_products",
        df["product_id"].nunique(dropna=True) if "product_id" in df else len(df),
    ))
    scope = _inventory_scope_label(intent, df)

    if classification in {"slow_moving_candidate", "no_recorded_sales"}:
        window = _sales_observation_window(df, rules)
        label = (
            "slow-moving candidates (1–2 recorded sales units)"
            if classification == "slow_moving_candidate"
            else "positions with no recorded sales (0 units)"
        )
        if df.empty:
            if classification == "slow_moving_candidate":
                return (
                    f"No product-store positions met the 1–2 recorded sales-unit "
                    f"slow-moving rule {scope} during {window}. Zero-sales "
                    "positions are classified separately as No Recorded Sales."
                )
            return (
                f"No product-store positions with zero recorded sales were found "
                f"{scope} during {window}. Slow-moving candidates with 1–2 "
                "recorded units are a separate classification."
            )

        lead = (
            f"Found {positions} {label} across {products} distinct products "
            f"{scope} during {window}."
        )
        if not df.empty:
            row = df.iloc[0]
            identity = str(row.get("product_name") or "A qualifying product")
            location = ""
            if pd.notna(row.get("store_name")):
                location = f" at {row['store_name']}"
                if pd.notna(row.get("city")):
                    location += f", {row['city']}"
            units = row.get("units_sold_window")
            if units is not None and pd.notna(units):
                lead += (
                    f" {identity}{location} recorded "
                    f"{_number(units)} sales units."
                )
        return f"{lead} Actual inventory and recorded sales."

    if df.empty:
        if classification == "excess_coverage_candidate":
            threshold = getattr(rules, "excess_days_of_inventory", None)
            rule = f"DOI greater than {threshold:g} days" if threshold is not None else "the configured excess-coverage rule"
        else:
            rule = "quantity on hand above the configured maximum level"
        return f"No product-store positions met {rule} in the authorized scope."

    if classification == "excess_coverage_candidate":
        threshold = getattr(rules, "excess_days_of_inventory", None)
        condition = (
            f"days of inventory greater than {threshold:g}"
            if threshold is not None else "the configured excess-coverage threshold"
        )
    else:
        condition = "quantity on hand above the configured maximum level"
    return (
        f"Found {positions} product-store positions across {products} products "
        f"{scope} with {condition}. The result table shows the qualifying "
        f"quantities, thresholds, and supporting inventory evidence."
        f"{_inventory_snapshot_text(df)} This is an Actual result."
    )


def _build_lookup_answer(
    intent,
    df: pd.DataFrame,
) -> str:

    if df.empty:
        return ""

    row_count = len(df)

    metrics = list(
        getattr(intent, "metrics", []) or []
    )

    primary_metric = (
        metrics[0]
        if metrics
        else None
    )

    if (
        primary_metric == "quantity"
        and "quantity_on_hand" in df.columns
    ):
        counts = dict(df.attrs.get("result_counts", {}))
        total_quantity = counts.get(
            "quantity_on_hand_total",
            pd.to_numeric(df["quantity_on_hand"], errors="coerce").sum(),
        )
        positions = counts.get(
            "product_store_positions",
            (
                df[["product_id", "store_id"]].drop_duplicates().shape[0]
                if {"product_id", "store_id"}.issubset(df.columns)
                else counts.get("matching_result_rows", row_count)
            ),
        )
        products = counts.get(
            "distinct_products",
            df["product_id"].nunique(dropna=True)
            if "product_id" in df.columns
            else 0,
        )
        stores = counts.get(
            "stores",
            df["store_id"].nunique(dropna=True)
            if "store_id" in df.columns
            else 0,
        )
        city_names = (
            df["city"].dropna().astype(str).unique().tolist()
            if "city" in df.columns
            else []
        )
        filters = getattr(intent, "filters", None)
        requested_stores = list(getattr(filters, "stores", []) or []) if filters else []
        result_store_names = (
            df["store_name"].dropna().astype(str).unique().tolist()
            if "store_name" in df.columns else []
        )
        if len(requested_stores) == 1 and len(result_store_names) == 1:
            scope = f"at {result_store_names[0]}"
            if len(city_names) == 1:
                scope += f", {city_names[0]}"
        elif len(city_names) == 1:
            scope = f"in {city_names[0]}"
        elif stores:
            scope = f"across {stores} stores"
        else:
            scope = "across all authorized stores"
        product_names = (
            df["product_name"].dropna().astype(str).unique().tolist()
            if "product_name" in df.columns
            else []
        )
        subject = f"{product_names[0]}: " if len(product_names) == 1 else ""
        subject_label = product_names[0] if len(product_names) == 1 else _requested_subject(intent, "Inventory")
        answer = f"{subject_label}: {_number(total_quantity)} units are available {scope}."
        if len(product_names) > 1:
            answer += f" This includes {int(products or len(product_names))} products."
        if "inventory_snapshot_date" in df.columns:
            dates = df["inventory_snapshot_date"].dropna().astype(str).unique().tolist()
            if len(dates) == 1:
                answer += f" Stock recorded on {dates[0]}."
        if {"quantity_on_hand", "reorder_level"}.issubset(df.columns):
            quantity = pd.to_numeric(df["quantity_on_hand"], errors="coerce")
            reorder = pd.to_numeric(df["reorder_level"], errors="coerce")
            below = int((quantity < reorder).fillna(False).sum())
            equal = int((quantity == reorder).fillna(False).sum())
            details = []
            if below:
                details.append(f"{below} below reorder level")
            if equal:
                details.append(f"{equal} exactly at reorder level")
            if details:
                answer += " Stock levels need attention: " + " and ".join(details) + "."
        return answer

    # A lookup should not pretend the first row is
    # the answer to a multi-row request.
    if row_count > 1:
        if {"product_id", "store_id"}.issubset(df.columns):
            label = "product-store positions"
        elif "product_id" in df.columns:
            label = "product totals"
        elif "store_id" in df.columns:
            label = "store totals"
        else:
            label = "result rows"
        return f"Found {row_count} matching {label}. The table shows the results."

    # Single-row lookup can safely use direct metric wording.
    return _build_direct_metric_answer(
        intent,
        df,
    )


def _build_what_if_answer(
    intent,
    df: pd.DataFrame,
) -> str:

    scenario = getattr(
        intent,
        "scenario",
        None,
    )

    if (
        scenario is None
        or df.empty
        or "days_of_inventory" not in df.columns
    ):
        return ""

    scenario_kind = getattr(
        scenario,
        "kind",
        "",
    )

    scenario_value = getattr(
        scenario,
        "value",
        None,
    )

    # A group scenario must not select an arbitrary first product or average
    # DOI across positions. Report the supported scope-level result and leave
    # per-position values to the table/Trust Panel.
    if len(df) > 1:
        scope = "product-store positions"
        if "product_id" in df.columns and "store_id" in df.columns:
            count = int(df[["product_id", "store_id"]].drop_duplicates().shape[0])
        else:
            count = len(df)
        if scenario_kind == "demand_change_pct":
            direction = "increase" if float(scenario_value or 0) >= 0 else "decrease"
            return (
                f"Demand is simulated to {direction} by {_number(abs(float(scenario_value or 0)))}% for "
                f"the selected group across {count} {scope}. Inventory quantity "
                "is unchanged and coverage is recalculated per position; "
                "no single DOI is reported for the group."
            )
        return (
            f"The selected scenario applies across {count} {scope}. "
            "Results are shown per position so no unsupported group-level risk "
            "or coverage value is inferred."
        )

    row = df.iloc[0]

    name = _name(row)
    location = ""
    if pd.notna(row.get("store_name")):
        location = f" at {row['store_name']}"
        if pd.notna(row.get("city")):
            location += f", {row['city']}"

    if scenario_kind == "demand_change_pct":
        quantity = row.get("quantity_on_hand")
        units = row.get("units_sold_window")
        window = df.attrs.get("window_days", 30)
        if pd.notna(row.get("actual_days_of_inventory")):
            baseline = float(row["actual_days_of_inventory"])
        else:
            baseline = None
        try:
            if baseline is None:
                baseline = float(quantity) / (float(units) / float(window)) if float(units) > 0 else None
            simulated = float(row.get("simulated_days_of_inventory", row["days_of_inventory"]))
        except (TypeError, ValueError, KeyError, ZeroDivisionError):
            baseline = simulated = None
        if baseline is not None and pd.notna(baseline) and simulated is not None and pd.notna(simulated):
            return (f"{name}{location} has {_number(quantity)} units on hand. Actual coverage is about {_number(baseline)} days at the recent sales rate; with demand changed by {_number(scenario_value)}%, simulated coverage is about {_number(simulated)} days ({_number(simulated - baseline)} days). This is a simulation, not a guaranteed forecast.")
        if units is not None and pd.notna(units) and float(units) <= 0:
            return (f"{name}{location} has no recorded sales in the approved {window}-day window, so actual and simulated days of inventory cannot be calculated. The demand scenario is retained, but no sales or inventory records are changed.")
        if units is None or pd.isna(units):
            simulated = row.get("simulated_days_of_inventory", row.get("days_of_inventory"))
            if simulated is not None and pd.notna(simulated):
                return (f"With demand changed by {_number(scenario_value)}%, {name}{location} would have about {_number(simulated)} days of inventory coverage. This is a simulation; the supporting sales basis is not available in this result.")
            return (f"The demand scenario for {name}{location} is simulated, but days of inventory cannot be calculated because the sales basis is missing.")
        if max(1 + float(scenario_value) / 100.0, 0.0) == 0:
            return (f"{name}{location} would have zero modeled daily sales after this demand change, so simulated days of inventory are undefined. Actual stock remains {_number(quantity)} units; this is a simulation, not a forecast.")
        return (
            f"With demand changed by "
            f"{_number(scenario_value)}%, "
            f"{name}{location} would have "
            f"{_number(row.get('simulated_days_of_inventory', row.get('days_of_inventory')))} "
            f"days of inventory coverage."
        )

    if scenario_kind == "lead_time_extra_days":

        risk = row.get("stockout_risk_v1", row.get("stockout_risk"))

        if risk is not None:

            return (
                f"With supplier lead time increased by "
                f"{_number(scenario_value)} days, "
                f"{name} would have a "
                f"{str(risk).lower()} stockout risk."
            )

    if scenario_kind == "lead_time_pct":

        risk = row.get("stockout_risk_v1", row.get("stockout_risk"))

        if risk is not None:

            return (
                f"With supplier lead time increased by "
                f"{_number(scenario_value)}%, "
                f"{name} would have a "
                f"{str(risk).lower()} stockout risk."
            )

    return ""


def _build_why_answer(
    question: str,
    intent,
    df: pd.DataFrame,
) -> str:

    metrics = list(
        getattr(intent, "metrics", []) or []
    )

    primary_metric = (
        metrics[0]
        if metrics
        else None
    )

    if primary_metric == "quantity":

        subject_name = getattr(
            intent,
            "comparison_subject",
            None,
        )

        # We deliberately prefer the semantic field from the
        # intent and only fall back to text extraction for backward
        # compatibility with the existing intent schema/tests.
        if not subject_name:

            question_text = question.casefold()

            if "product_name" in df.columns:

                for _, row in df.iterrows():

                    candidate = str(
                        row.get(
                            "product_name",
                            "",
                        )
                    ).strip()

                    if (
                        candidate
                        and candidate.casefold()
                        in question_text
                    ):
                        subject_name = candidate
                        break

        return build_inventory_explanation(
            df,
            subject_name=subject_name,
        )

    if (
        primary_metric == "sales_units"
        and "units" in df.columns
    ):

        ordered = (
            df.sort_values(
                "units",
                ascending=False,
                kind="stable",
            )
            .reset_index(drop=True)
        )

        winner = ordered.iloc[0]

        sentence = (
            f"{_name(winner)} has the highest sales because "
            f"it recorded {_number(winner['units'])} units sold"
        )

        if len(ordered) > 1:

            second = ordered.iloc[1]

            sentence += (
                f", ahead of {_name(second)} at "
                f"{_number(second['units'])} units"
            )

        return sentence + "."

    if (
        primary_metric == "days_of_inventory"
        and "days_of_inventory" in df.columns
    ):

        row = df.iloc[0]

        return (
            f"{_name(row)} has "
            f"{_number(row['days_of_inventory'])} "
            "days of inventory coverage."
        )

    return ""


def _build_answer_unabridged(
    *,
    question: str,
    intent,
    df: pd.DataFrame,
    basis: str = "Actual",
    rules=None,
) -> str:
    """
    Build a deterministic answer from the result and intent.

    IMPORTANT:
    - Ranking questions use ranking logic.
    - Lookup questions do not automatically collapse to row 1.
    - Explanation questions use comparison evidence.
    - What-if questions use simulated evidence.
    """

    if df is None:
        return ""

    capability = getattr(intent, "capability", "")
    if df.empty:
        classification_answer = _build_inventory_classification_answer(intent, df, rules)
        if classification_answer:
            return classification_answer
        if capability == "stockout_risk":
            return "No products in the current scope are classified as High or Medium stockout risk. Low risk and No demand are excluded from this at-risk result."
        if capability == "inventory_health":
            return "No inventory exceptions were found in the current scope."
        return ""

    question_kind = getattr(
        intent,
        "question_kind",
        "lookup",
    )

    capability = getattr(intent, "capability", "")

    classification_answer = _build_inventory_classification_answer(intent, df, rules)
    if classification_answer:
        return classification_answer

    if getattr(intent, "question_kind", "") == "comparison":
        comparison_answer = _build_group_comparison_answer(intent, df)
        if comparison_answer:
            return comparison_answer

    if capability == "historical_analysis" and {"qty_now", "qty_before"}.issubset(df.columns):
        now = pd.to_numeric(df["qty_now"], errors="coerce").fillna(0).sum()
        before = pd.to_numeric(df["qty_before"], errors="coerce").fillna(0).sum()
        change = now - before
        pct = (change / before * 100) if before else None
        period = ""
        before_dates = df["snapshot_date_before"].dropna().astype(str).unique().tolist() if "snapshot_date_before" in df.columns else []
        current = df["snapshot_date"].dropna().astype(str).unique().tolist() if "snapshot_date" in df.columns else []
        if before_dates and current:
            period = f" between the available weekly snapshots {before_dates[0]} and {current[0]}"
        pct_text = f" ({pct:+.1f}%)" if pct is not None else ""
        return (
            f"Inventory changed from {_number(before)} units to {_number(now)} units{period}, "
            f"a change of {_number(change)} units{pct_text}."
        )

    if capability == "executive_summary":
        units = pd.to_numeric(df.get("quantity_on_hand", pd.Series(dtype=float)), errors="coerce").sum()
        value = pd.to_numeric(df.get("inventory_value", pd.Series(dtype=float)), errors="coerce").sum()
        counts = dict(df.attrs.get("result_counts", {}))
        products = int(counts.get("distinct_products", df["product_id"].nunique() if "product_id" in df.columns else len(df)))
        stores = int(counts.get("stores", df["store_id"].nunique() if "store_id" in df.columns else 0))
        units = counts.get("quantity_on_hand_total", units)
        value = counts.get("inventory_value_total", value)
        sales_units = counts.get("sales_units_total", pd.to_numeric(df.get("units", pd.Series(dtype=float)), errors="coerce").sum())
        sales_revenue = counts.get("sales_revenue_total", pd.to_numeric(df.get("revenue", pd.Series(dtype=float)), errors="coerce").sum())
        low = int(counts.get("below_reorder_positions", 0))
        high = int(counts.get("high_risk_positions", 0))
        medium = int(counts.get("medium_risk_positions", 0))
        snap = df["snapshot_date"].dropna().astype(str).unique().tolist() if "snapshot_date" in df.columns else []
        date_text = f" Stock is as of {snap[0]}." if len(snap) == 1 else ""
        sales_start = counts.get("sales_window_start_exclusive")
        sales_end = counts.get("sales_window_end_inclusive")
        sales_period = f" from {sales_start} through {sales_end}" if sales_start and sales_end else ""
        return (
            f"Across {products} products and {stores} stores, inventory is "
            f"{_number(units)} units worth {_inr(value)}.{date_text} "
            f"Sales{sales_period} were {_number(sales_units)} units and {_inr(sales_revenue)}. "
            f"The current inventory includes {low} positions at or below reorder and "
            f"{high} high-risk plus {medium} medium-risk positions."
        )

    if (
        capability == "stockout_risk"
        and question_kind != "ranking"
        and ("stockout_risk_v1" in df.columns or "stockout_risk" in df.columns)
    ):
        risk_column = "stockout_risk_v1" if "stockout_risk_v1" in df.columns else "stockout_risk"
        counts = dict(df.attrs.get("result_counts", {}))
        high = int(counts.get(
            "high_risk_positions",
            df[risk_column].eq("High").sum(),
        ))
        medium = int(counts.get(
            "medium_risk_positions",
            df[risk_column].eq("Medium").sum(),
        ))
        first = df.iloc[0]
        products = int(counts.get(
            "distinct_products",
            df["product_id"].nunique(dropna=True)
            if "product_id" in df.columns
            else len(df),
        ))
        stores = int(counts.get(
            "stores",
            df["store_id"].nunique(dropna=True)
            if "store_id" in df.columns
            else 0,
        ))
        city_names = (
            df["city"].dropna().astype(str).unique().tolist()
            if "city" in df.columns
            else []
        )
        scope = f"in {city_names[0]}" if len(city_names) == 1 else "in the current scope"
        store_phrase = f" and {stores} stores" if stores else ""
        location = ""
        if "store_name" in first.index and pd.notna(first.get("store_name")):
            location = f" at {first.get('store_name')}"
            if pd.notna(first.get("city")):
                location += f", {first.get('city')}"
        risk = str(first.get(risk_column, "Unknown"))
        quantity = _number(first.get("quantity_on_hand"))
        doi = _number(first.get("days_of_inventory"))
        lead_time = _number(first.get("lead_time_days"))
        location_scope = f"in {city_names[0]}" if len(city_names) == 1 else "in the authorized scope"
        high_text = f"{high} High-risk {'position' if high == 1 else 'positions'}"
        medium_text = f"{medium} Medium-risk {'position' if medium == 1 else 'positions'}"
        product_text = f"{products} {'product' if products == 1 else 'products'}"
        store_text = f"{stores} {'store' if stores == 1 else 'stores'}"
        window = _sales_observation_window(df, rules)
        return (
            f"Across the qualifying positions {location_scope}, {high_text} and "
            f"{medium_text} qualify among {product_text} across {store_text}. "
            f"{_name(first)}{location} has {quantity} units on hand, {doi} days "
            f"of coverage, and {lead_time} days of supplier lead time. "
            f"Business Rules V1 uses coverage against supplier lead time and "
            f"sales observed during {window}; basis: Actual."
        )

    if capability == "inventory_health":
        reasons = df.get("attention_reason")
        if reasons is not None:
            status_series = df.get("inventory_status", df.get("stock_status", pd.Series(dtype=str)))
            risk_series = df.get("stockout_risk_v1", df.get("stockout_risk", pd.Series(dtype=str)))
            low = int(status_series.isin(["At or Below Reorder Level", "Low"]).sum())
            risk = int(risk_series.isin(["High", "Medium"]).sum())
            slow = int(df.get("is_slow_moving", pd.Series(dtype=bool)).astype(bool).sum())
            counts = dict(df.attrs.get("result_counts", {}))
            positions = int(counts.get("product_store_positions", len(df)))
            products = int(counts.get(
                "distinct_products",
                df["product_id"].nunique(dropna=True) if "product_id" in df.columns else len(df),
            ))
            stores = int(counts.get(
                "stores",
                df["store_id"].nunique(dropna=True) if "store_id" in df.columns else 0,
            ))
            scope = "in the current scope"
            cities = df["city"].dropna().astype(str).unique().tolist() if "city" in df.columns else []
            if len(cities) == 1:
                scope = f"in {cities[0]}"
            return (
                f"I found {products} products that need attention {scope}. "
                f"{low} stock records are at or below their reorder level, "
                f"{risk} have medium or high stockout risk, and {slow} are slow-moving. "
                "These groups can overlap; see the table for the affected products and stores."
                f"{_inventory_snapshot_text(df)} These are Actual inventory observations."
            )

    # ---------------------------------------------------------
    # Compound questions
    # ---------------------------------------------------------
    #
    # The orchestrator should handle true compound questions
    # as multiple sub-questions. We do not invent a fake
    # single answer here.
    #
    if question_kind == "compound":
        return ""

    # ---------------------------------------------------------
    # Explanation
    # ---------------------------------------------------------

    if (
        question_kind == "explanation"
        or _question_requests_why(question)
    ):

        answer = _build_why_answer(
            question,
            intent,
            df,
        )

        if answer:
            return answer

    # ---------------------------------------------------------
    # Scenario / What-if
    # ---------------------------------------------------------

    if (
        question_kind == "scenario"
        or capability == "what_if"
    ):

        answer = _build_what_if_answer(
            intent,
            df,
        )

        if answer:
            return answer

    if capability == "sales_trend" and question_kind != "ranking" and getattr(intent, "sort", None) is None:
        answer = _build_sales_answer(intent, df, rules)
        if answer:
            return answer

    # ---------------------------------------------------------
    # Ranking
    # ---------------------------------------------------------

    if (
        question_kind == "ranking"
        or getattr(intent, "sort", None) is not None
    ):

        answer = _build_ranking_answer(
            intent,
            df,
        )

        if answer:
            if capability == "stockout_risk":
                risk_column = "stockout_risk_v1" if "stockout_risk_v1" in df else "stockout_risk"
                if risk_column in df.columns and df[risk_column].isin(["High", "Medium"]).any():
                    answer += (
                        " High/Medium risk follows Business Rules V1 using coverage "
                        "against supplier lead time and sales observed during "
                        f"{_sales_observation_window(df, rules)}. Basis: Actual."
                    )
                else:
                    answer += f" Risk uses observed sales during {_sales_observation_window(df, rules)}. Basis: Actual."
            return answer

    # ---------------------------------------------------------
    # Lookup
    # ---------------------------------------------------------

    if question_kind == "lookup":
        if list(getattr(intent, "metrics", []) or [])[:1] == ["inventory_value"] and "inventory_value" in df.columns:
            counts = dict(df.attrs.get("result_counts", {}))
            value = counts.get(
                "inventory_value_total",
                pd.to_numeric(df["inventory_value"], errors="coerce").sum(min_count=1),
            )
            positions = counts.get(
                "product_store_positions",
                len(df),
            )
            return (
                f"Inventory value is {_inr(value)} across {positions} "
                f"product-store positions {_inventory_scope_label(intent, df)}. "
                f"Basis: {basis}."
            )
        answer = _build_lookup_answer(
            intent,
            df,
        )

        if answer:
            return answer

    # ---------------------------------------------------------
    # Fallback direct metric
    # ---------------------------------------------------------

    return _build_direct_metric_answer(
        intent,
        df,
    )


def concise_leadership_answer(answer: str) -> str:
    """Keep the user-facing response focused while retaining result caveats."""
    text = str(answer or "").strip()
    if not text:
        return ""

    sentences = [
        sentence.strip()
        for sentence in re.split(r"(?<=[.!?])\s+", text)
        if sentence.strip()
    ]
    sentences = [
        sentence for sentence in sentences
        if not re.match(
            r"^(?:examples?:|the table shows\b)",
            sentence,
            re.IGNORECASE,
        )
    ]
    if len(sentences) <= 3 and len(text.split()) <= 60:
        return " ".join(sentences)

    caveat = re.compile(
        r"\b(?:actual|simulated|simulation|not a forecast|"
        r"business rules v1|observed sales|no recorded sales|zero-sales|"
        r"weekly snapshot|not an exact|insufficient|unavailable)\b",
        re.IGNORECASE,
    )
    selected = []
    if sentences:
        selected.append(sentences[0])
    selected.extend(
        sentence for sentence in sentences[1:]
        if caveat.search(sentence) and sentence not in selected
    )
    selected = selected[:3]
    for sentence in sentences[1:]:
        if len(selected) == 3:
            break
        if sentence not in selected:
            selected.append(sentence)

    selected.sort(key=sentences.index)
    concise = []
    words = 0
    for index, sentence in enumerate(selected):
        count = len(sentence.split())
        essential = bool(caveat.search(sentence))
        if concise and words + count > 60 and not essential:
            continue
        concise.append(sentence)
        words += count
    return " ".join(concise)


def build_answer(
    *,
    question: str,
    intent,
    df: pd.DataFrame,
    basis: str = "Actual",
    rules=None,
) -> str:
    """Build a concise, deterministic Leadership answer from calculated evidence."""
    answer = _build_answer_unabridged(
        question=question,
        intent=intent,
        df=df,
        basis=basis,
        rules=rules,
    )
    return concise_leadership_answer(answer)
