"""Query layer: Intent + Resolution + Rules + as_of -> DataFrames + SQL + params.

All SQL is parameterised (?). Column names come only from the allow-lists below.
`schema` is "dbo." for SQL Server and "" for the SQLite test fixtures.
"""
from __future__ import annotations

import calendar
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Callable, Optional, Sequence

import pandas as pd

MAX_ROWS = 500

GROUPS = {
    "product": ["p.product_id", "p.product_name"],
    "brand": ["p.brand"],
    "category": ["p.category"],
    "subcategory": ["ps.subcategory_name"],
    "store": ["st.store_id", "st.store_name"],
    "city": ["st.city"],
    "region": ["st.region"],
}
INVENTORY_GROUPS = {
    **GROUPS,
    "product": [
        "p.product_id", "p.sku", "p.product_name", "p.brand", "p.category",
        "p.model",
    ],
    "store": ["st.store_id", "st.store_name", "st.city", "st.region"],
}
OPS = {">": ">", ">=": ">=", "<": "<", "<=": "<=", "=": "==", "==": "==", "!=": "!="}
METRIC_COLUMNS = {
    "quantity": "quantity_on_hand",
    "inventory_value": "inventory_value",
    "stock_status": "stock_status",
    "days_of_inventory": "days_of_inventory",
    "stockout_risk": "stockout_risk",
    "sales_units": "units",
    "sales_revenue": "revenue",
    "sales_growth_pct": "growth_pct",
    "inventory_growth_pct": "inventory_growth_pct",
    "excess_value": "excess_value",
}


@dataclass
class QueryResult:
    df: pd.DataFrame
    sql: list[str] = field(default_factory=list)
    params: list[list] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    @property
    def row_count(self) -> int:
        return len(self.df)


def _ids(values) -> list[int]:
    return [int(v) for v in values]


def restrict(
    resolved: Optional[Sequence[int]], previous: Optional[Sequence[int]]
) -> Optional[list[int]]:
    """Follow-up rule: intersect previous result with newly stated filters.
    None = no restriction. Empty list = nothing matches.
    """
    if resolved is None or len(resolved) == 0:
        return None if previous is None else _ids(previous)
    if previous is None:
        return _ids(resolved)
    return sorted(set(_ids(resolved)) & set(_ids(previous)))


def _in_clause(col: str, ids: Optional[list[int]]):
    if ids is None:
        return "", []
    if not ids:
        return " AND 1 = 0", []
    return f" AND {col} IN ({','.join('?' * len(ids))})", ids


def _group_keys(intent) -> list[str]:
    g = getattr(intent, "group_by", None) or []
    if isinstance(g, str):
        g = [g]
    bad = [x for x in g if x not in GROUPS]
    if bad:
        raise ValueError(f"group_by not allowed: {bad}")
    return list(g)


def _months_back(d: date, k: int) -> date:
    m = d.month - 1 - k
    y, m = d.year + m // 12, m % 12 + 1
    return date(y, m, min(d.day, calendar.monthrange(y, m)[1]))


def window_for(intent, rules, as_of: date) -> tuple[date, date, str]:
    """Return (start_exclusive, end_inclusive, label). Computed in Python."""
    t = getattr(intent, "time", None)
    kind = getattr(t, "kind", "none") if t else "none"
    n = getattr(t, "n", None) if t else None
    def parse(value):
        try:
            return datetime.strptime(str(value), "%Y-%m-%d").date()
        except (TypeError, ValueError):
            raise ValueError(f"Invalid date value: {value!r}; expected YYYY-MM-DD")

    if kind == "latest":
        return as_of - timedelta(days=int(rules.sales_window_days)), as_of, f"latest available through {as_of.isoformat()}"
    if kind == "exact_date":
        d = parse(getattr(t, "date", None))
        return d - timedelta(days=1), d, f"exact date {d.isoformat()}"
    if kind == "month":
        year = getattr(t, "year", None)
        month = getattr(t, "month", None)
        if year is None or month is None or not 1 <= int(month) <= 12:
            raise ValueError("A month window requires year and month")
        first = date(int(year), int(month), 1)
        last = date(int(year), int(month), calendar.monthrange(int(year), int(month))[1])
        return first - timedelta(days=1), last, f"calendar month {first.strftime('%B %Y')}"
    if kind == "date_range":
        start = parse(getattr(t, "start_date", None))
        end = parse(getattr(t, "end_date", None))
        if end < start:
            raise ValueError("Date range end must not precede start")
        return start - timedelta(days=1), end, f"date range {start.isoformat()} to {end.isoformat()}"
    if kind == "last_n_days":
        return as_of - timedelta(days=int(n)), as_of, f"last {int(n)} days"
    if kind == "last_n_months":
        return _months_back(as_of, int(n)), as_of, f"last {int(n)} months"
    if kind == "this_month":
        first = as_of.replace(day=1)
        return first - timedelta(days=1), as_of, "this month to date"
    if kind == "last_month":
        first = as_of.replace(day=1)
        end = first - timedelta(days=1)
        return end.replace(day=1) - timedelta(days=1), end, "last month"
    w = int(rules.sales_window_days)
    return as_of - timedelta(days=w), as_of, f"last {w} days (default rule)"


class Queries:
    def __init__(
        self,
        run: Callable[[object, str, list], pd.DataFrame],
        conn,
        schema: str = "dbo.",
    ):
        self.run, self.conn, self.s = run, conn, schema
        self.subcategory_join = (
            f" LEFT JOIN {self.s}ProductSubcategories ps "
            "ON ps.subcategory_id = p.subcategory_id AND ps.category = p.category "
            if self.s else ""
        )
        self.subcategory_select = ", ps.subcategory_name AS subcategory" if self.s else ""

    def _q(self, res: QueryResult, sql: str, params: list) -> pd.DataFrame:
        res.sql.append(" ".join(sql.split()))
        res.params.append(params)
        return self.run(self.conn, sql, params)

    def as_of(self) -> date:
        df = self.run(self.conn, f"SELECT MAX(sale_date) AS d FROM {self.s}Sales", [])
        return pd.to_datetime(df["d"].iloc[0]).date()

    def snapshot_date_for(self, target: date) -> Optional[date]:
        """Return the latest weekly snapshot on or before ``target``."""
        df = self.run(
            self.conn,
            f"SELECT MAX(snapshot_date) AS d FROM {self.s}InventorySnapshotWeekly WHERE snapshot_date <= ?",
            [target.isoformat()],
        )
        if df.empty or pd.isna(df["d"].iloc[0]):
            return None
        return pd.to_datetime(df["d"].iloc[0]).date()

    def sales_years(self) -> list[int]:
        """Return years represented by sales, for deterministic month choices."""
        df = self.run(
            self.conn,
            f"SELECT DISTINCT YEAR(sale_date) AS year FROM {self.s}Sales ORDER BY year",
            [],
        )
        if df.empty:
            return []
        return sorted({int(value) for value in df["year"].dropna().tolist()})

    def snapshot_years(self) -> list[int]:
        """Return years represented by weekly inventory snapshots."""
        df = self.run(
            self.conn,
            f"SELECT DISTINCT YEAR(snapshot_date) AS year FROM {self.s}InventorySnapshotWeekly ORDER BY year",
            [],
        )
        if df.empty:
            return []
        return sorted({int(value) for value in df["year"].dropna().tolist()})

    def sales_periods(self) -> list[tuple[int, int]]:
        """Return calendar month/year pairs represented by sales records."""
        year_expr = "YEAR(sale_date)" if self.s else "CAST(strftime('%Y', sale_date) AS INTEGER)"
        month_expr = "MONTH(sale_date)" if self.s else "CAST(strftime('%m', sale_date) AS INTEGER)"
        df = self.run(
            self.conn,
            f"SELECT DISTINCT {year_expr} AS year, {month_expr} AS month "
            f"FROM {self.s}Sales WHERE sale_date IS NOT NULL "
            "ORDER BY year DESC, month DESC",
            [],
        )
        if df.empty:
            return []
        return [
            (int(row.year), int(row.month))
            for row in df.itertuples(index=False)
            if pd.notna(row.year) and pd.notna(row.month)
        ]

    def snapshot_periods(self) -> list[tuple[int, int]]:
        """Return calendar month/year pairs represented by inventory snapshots."""
        year_expr = (
            "YEAR(snapshot_date)"
            if self.s
            else "CAST(strftime('%Y', snapshot_date) AS INTEGER)"
        )
        month_expr = (
            "MONTH(snapshot_date)"
            if self.s
            else "CAST(strftime('%m', snapshot_date) AS INTEGER)"
        )
        df = self.run(
            self.conn,
            f"SELECT DISTINCT {year_expr} AS year, {month_expr} AS month "
            f"FROM {self.s}InventorySnapshotWeekly "
            "WHERE snapshot_date IS NOT NULL ORDER BY year DESC, month DESC",
            [],
        )
        if df.empty:
            return []
        return [
            (int(row.year), int(row.month))
            for row in df.itertuples(index=False)
            if pd.notna(row.year) and pd.notna(row.month)
        ]

    def inventory_position(
        self,
        intent,
        rules,
        as_of: date,
        product_ids: Optional[list[int]],
        store_ids: Optional[list[int]],
        prev_product_ids=None,
        prev_store_ids=None,
    ) -> QueryResult:
        """Raw product-store rows (pre-aggregation) with units sold in the window."""
        res = QueryResult(pd.DataFrame())
        pids, sids = restrict(product_ids, prev_product_ids), restrict(
            store_ids, prev_store_ids
        )
        start, end, label = window_for(intent, rules, as_of)
        res.notes.append(
            f"Sales window: {label} ({start} exclusive to {end} inclusive)"
        )

        pc, pp = _in_clause("i.product_id", pids)
        sc, sp_ = _in_clause("i.store_id", sids)
        sql = f"""
        SELECT i.product_id, p.sku, p.product_name, p.brand, p.category, p.model{self.subcategory_select},
               i.store_id, st.store_name, st.city, st.region,
               i.quantity_on_hand, i.reorder_level, i.max_level, p.cost_price,
               i.quantity_on_hand * p.cost_price AS inventory_value,
               COALESCE(s.units, 0) AS units_sold_window,
               sp.lead_time_days, sp.primary_supplier_count,
               CASE WHEN sp.primary_supplier_count IS NULL THEN 'No valid primary supplier'
                    WHEN sp.min_primary_lead_time IS NULL OR sp.min_primary_lead_time < 0
                         OR sp.max_primary_lead_time IS NULL OR sp.max_primary_lead_time < 0 THEN 'Invalid supplier record'
                    WHEN sp.min_primary_lead_time <> sp.max_primary_lead_time THEN 'Multiple primaries; maximum lead time selected'
                    WHEN sp.primary_supplier_count > 1 THEN 'Duplicate primaries; same lead time'
                    ELSE 'Single valid primary supplier' END AS supplier_rule,
               i.as_of_date AS inventory_snapshot_date
        FROM {self.s}Inventory i
        JOIN {self.s}Products p ON p.product_id = i.product_id
        {self.subcategory_join}
        JOIN {self.s}Stores st ON st.store_id = i.store_id
        LEFT JOIN (
            SELECT product_id,
                   CASE WHEN COUNT(*) = 0 THEN NULL
                        WHEN MIN(lead_time_days) <> MAX(lead_time_days) THEN MAX(lead_time_days)
                        ELSE MAX(lead_time_days) END AS lead_time_days,
                   COUNT(*) AS primary_supplier_count,
                   MIN(lead_time_days) AS min_primary_lead_time,
                   MAX(lead_time_days) AS max_primary_lead_time
            FROM {self.s}SupplierProducts
            WHERE is_primary = 1
            GROUP BY product_id
        ) sp ON sp.product_id = i.product_id
        LEFT JOIN (SELECT product_id, store_id, SUM(quantity) AS units
                   FROM {self.s}Sales WHERE sale_date > ? AND sale_date <= ?
                   GROUP BY product_id, store_id) s
               ON s.product_id = i.product_id AND s.store_id = i.store_id
        WHERE 1 = 1 {pc} {sc}"""
        params = [start.isoformat(), end.isoformat()] + pp + sp_
        res.df = self._q(res, sql, params)
        res.df.attrs["window_days"] = (end - start).days
        return res

    def inventory_snapshot(
        self, intent, as_of: date, product_ids, store_ids,
        prev_product_ids=None, prev_store_ids=None,
    ) -> QueryResult:
        """Read historical quantity from the weekly snapshot table only."""
        res = QueryResult(pd.DataFrame())
        t = getattr(intent, "time", None)
        kind = getattr(t, "kind", "none") if t else "none"
        if kind == "exact_date":
            target = datetime.strptime(str(t.date), "%Y-%m-%d").date()
        elif kind == "month":
            target = date(int(t.year), int(t.month), calendar.monthrange(int(t.year), int(t.month))[1])
        elif kind == "date_range":
            target = datetime.strptime(str(t.end_date), "%Y-%m-%d").date()
        else:
            raise ValueError("Historical snapshot requires an explicit date or period")
        pids, sids = restrict(product_ids, prev_product_ids), restrict(store_ids, prev_store_ids)
        pc, pp = _in_clause("w.product_id", pids)
        sc, sp = _in_clause("w.store_id", sids)
        d = self._q(res, f"SELECT MAX(snapshot_date) AS d FROM {self.s}InventorySnapshotWeekly WHERE snapshot_date <= ?", [target.isoformat()])
        if d.empty or pd.isna(d["d"].iloc[0]):
            res.notes.append(f"No weekly inventory snapshot is available on or before {target.isoformat()}")
            return res
        snap = pd.to_datetime(d["d"].iloc[0]).date()
        sql = f"""
        SELECT w.product_id, p.sku, p.product_name, p.brand, p.category, p.model{self.subcategory_select},
               w.store_id, st.store_name, st.city, st.region,
               w.quantity_on_hand, w.snapshot_date
        FROM {self.s}InventorySnapshotWeekly w
        JOIN {self.s}Products p ON p.product_id = w.product_id
        {self.subcategory_join}
        JOIN {self.s}Stores st ON st.store_id = w.store_id
        WHERE w.snapshot_date = ? {pc} {sc}"""
        frame = self._q(res, sql, [snap.isoformat()] + pp + sp)
        frame.columns = [c.split(".")[-1] for c in frame.columns]
        res.df = frame
        res.notes.append(f"Historical inventory basis: weekly snapshot {snap.isoformat()} (requested through {target.isoformat()})")
        return res

    @staticmethod
    def aggregate_inventory(df: pd.DataFrame, intent) -> pd.DataFrame:
        """
        Aggregate inventory at the semantic grain requested by the user.

        Rules:
        - no group_by -> product totals
        - group_by=store with no product scope -> store totals
        - product scoped + group_by=store -> product + store
        - explicit product + store -> product + store
        """

        g = _group_keys(intent)

        filters = getattr(intent, "filters", None)

        has_product_scope = bool(
            filters
            and (
                getattr(filters, "products", None)
                or getattr(filters, "brands", None)
                or getattr(filters, "categories", None)
            )
        )
        has_entity_scope = bool(
            filters
            and (
                getattr(filters, "products", None)
                or getattr(filters, "stores", None)
                or getattr(filters, "cities", None)
                or getattr(filters, "regions", None)
                or getattr(filters, "subcategories", None)
            )
        )

        if not g:
            g = ["product"]

        # Operational classifications and explicitly scoped inventory queries
        # must retain the source product-store positions. DOI/risk cannot be
        # meaningfully averaged across stores, and location/product identifiers
        # are required to explain and act on each result.
        capability = getattr(intent, "capability", "")
        if capability in {"stockout_risk", "inventory_health", "what_if"}:
            return df.copy()
        if capability == "inventory_visibility" and (has_entity_scope or getattr(intent, "threshold", None) is not None):
            return df.copy()

        if g == ["store"] and has_product_scope:
            g = ["product", "store"]

        if set(g) >= {"product", "store"}:
            return df.copy()

        columns = [
            column
            for key in g
            for column in INVENTORY_GROUPS[key]
        ]

        keys = []
        for column in columns:
            key = (
                "subcategory"
                if column == "ps.subcategory_name"
                else column.split(".")[1]
            )
            if key in df.columns:
                keys.append(key)

        aggregations = {
            "quantity_on_hand": ("quantity_on_hand", "sum"),
            "reorder_level": ("reorder_level", "sum"),
            "max_level": ("max_level", "sum"),
            "inventory_value": ("inventory_value", "sum"),
            "units_sold_window": ("units_sold_window", "sum"),
            "lead_time_days": ("lead_time_days", "mean"),
            "positions": ("product_id", "size"),
        }
        if "inventory_snapshot_date" in df.columns:
            aggregations["inventory_snapshot_date"] = ("inventory_snapshot_date", "max")
        if "subcategory" in df.columns:
            aggregations["subcategory"] = ("subcategory", "first")
        agg = (
            df.groupby(
                keys,
                as_index=False,
            )
            .agg(**aggregations)
        )

        if "lead_time_days" in agg.columns:
            agg["lead_time_days"] = (
                agg["lead_time_days"]
                .round(1)
            )

        agg.attrs.update(df.attrs)
        return agg

    @staticmethod
    def finalize(
        df: pd.DataFrame,
        intent,
        rules,
        excess_rule: str = "max_level",
    ) -> tuple[pd.DataFrame, list[str]]:
        """Run AFTER enrich_inventory. Adds both excess flags, applies threshold/sort/limit."""
        notes: list[str] = []
        df = df.copy()
        if "inventory_data_quality" in df.columns:
            quality_count = int(df["inventory_data_quality"].notna().sum())
            if quality_count:
                notes.append(f"Inventory data quality issues present in {quality_count} position(s)")
        if "demand_data_quality" in df.columns:
            demand_quality = int(df["demand_data_quality"].notna().sum())
            if demand_quality:
                notes.append(f"Demand data quality issues present in {demand_quality} position(s)")
        if "supplier_data_quality" in df.columns:
            supplier_quality = int(df["supplier_data_quality"].notna().sum())
            if supplier_quality:
                notes.append(f"Supplier data quality issues present in {supplier_quality} position(s)")
        if {"quantity_on_hand", "max_level"} <= set(df.columns):
            df["excess_by_max_level"] = df["quantity_on_hand"] > df["max_level"]
        if "days_of_inventory" in df.columns:
            df["excess_by_doi"] = (
                df["days_of_inventory"] > rules.excess_days_of_inventory
            )
        if getattr(intent, "capability", "") == "excess_slow_moving":
            col = (
                "excess_by_max_level" if excess_rule == "max_level" else "excess_by_doi"
            )
            other = (
                "excess_by_doi" if excess_rule == "max_level" else "excess_by_max_level"
            )
            df = df[df[col]]
            notes.append(f"Excess rule used: {col} (alternative also available: {other})")

        if getattr(intent, "capability", "") == "inventory_health":
            reasons = pd.Series("", index=df.index, dtype="object")
            def add_reason(mask, label):
                nonlocal reasons
                prior = reasons.copy()
                reasons = reasons.mask(mask & prior.eq(""), label)
                reasons = reasons.mask(mask & prior.ne(""), prior + "; " + label)
            status_col = "inventory_status" if "inventory_status" in df.columns else "stock_status"
            risk_col = "stockout_risk_v1" if "stockout_risk_v1" in df.columns else "stockout_risk"
            add_reason(df[status_col].eq("At or Below Reorder Level") | df[status_col].eq("Low"), "Low inventory")
            add_reason(df[risk_col].isin(["High", "Medium"]), "Stockout risk")
            excess_mask = (
                df.get("excess_by_max_level", pd.Series(False, index=df.index)).astype(bool)
                | df.get("excess_coverage_candidate", pd.Series(False, index=df.index)).astype(bool)
            )
            add_reason(excess_mask, "Excess inventory")
            add_reason(df.get("is_slow_moving", pd.Series(False, index=df.index)).astype(bool), "Slow-moving")
            add_reason(df[risk_col].isin(["No demand", "Not Assessable — No Recorded Demand"]), "No recent sales")
            df["attention_reason"] = reasons
            df = df[df["attention_reason"] != ""]
            notes.append("Inventory-health exceptions use low stock, stockout risk, excess, slow-moving, and no-demand rules")

        if getattr(intent, "capability", "") == "stockout_risk":
            # No demand is a distinct classification, not imminent stockout risk.
            risk_col = "stockout_risk_v1" if "stockout_risk_v1" in df.columns else "stockout_risk"
            unknown_lead = int(df[risk_col].isin(["Unknown", "Unknown lead time"]).sum())
            df = df[df[risk_col].isin(["High", "Medium"])].copy()
            if risk_col in df.columns:
                df["_risk_order"] = df[risk_col].map({"High": 0, "Medium": 1})
                # SQL row order is not a stable tie-break. Keep equal-risk,
                # equal-coverage positions deterministic at product-store grain.
                tie_breakers = [column for column in ("product_id", "store_id") if column in df]
                df = df.sort_values(
                    ["_risk_order", "days_of_inventory", *tie_breakers],
                    ascending=[True, True, *([True] * len(tie_breakers))],
                    na_position="last",
                    kind="stable",
                )
                df = df.drop(columns=["_risk_order"])
            notes.append("At-risk results include only High and Medium stockout classifications; Low and No demand are excluded")
            if unknown_lead:
                notes.append(f"Excluded {unknown_lead} position(s) with missing supplier lead time from risk ranking")

        th = getattr(intent, "threshold", None)
        if th is not None and getattr(th, "field", None):
            # Map validated intent metrics to operational result columns.
            # Never evaluate a zero-stock predicate on aggregated product totals.
            threshold_columns = {
                "quantity": "quantity_on_hand",
                "days_of_inventory": "days_of_inventory",
                "lead_time_days": "lead_time_days",
                "sales_units": "units_sold_window",
            }
            column = threshold_columns.get(th.field)
            if column is None or column not in df.columns:
                raise ValueError(f"threshold field not allowed: {th.field}")
            op = OPS.get(th.op)
            if op is None:
                raise ValueError(f"threshold op not allowed: {th.op}")
            v = th.value
            s = pd.to_numeric(df[column], errors="coerce")
            mask = {
                ">": s > v,
                ">=": s >= v,
                "<": s < v,
                "<=": s <= v,
                "==": s == v,
                "!=": s != v,
            }[op]
            df = df[mask.fillna(False)]
            notes.append(f"Threshold: {th.field} {th.op} {v}")

        so = getattr(intent, "sort", None)
        if so is not None:
            metric = getattr(so, "by", None)

            if metric:
                if metric == "stockout_risk" and getattr(intent, "capability", "") == "stockout_risk":
                    risk_col = "stockout_risk_v1" if "stockout_risk_v1" in df.columns else "stockout_risk"
                    if risk_col not in df.columns:
                        raise ValueError("stockout risk ranking requires a V1 risk classification")
                    descending = str(getattr(so, "direction", "desc")).lower().startswith("desc")
                    df = df.copy()
                    df["_risk_rank"] = df[risk_col].map({"High": 0, "Medium": 1}).fillna(2)
                    sort_columns = ["_risk_rank"]
                    ascending = [True]
                    if "days_of_inventory" in df.columns:
                        sort_columns.append("days_of_inventory")
                        ascending.append(descending)
                    tie_breakers = [column for column in ("product_id", "store_id") if column in df]
                    sort_columns.extend(tie_breakers)
                    ascending.extend([True] * len(tie_breakers))
                    if not descending:
                        # For an explicitly ascending request, reverse the
                        # classification order while retaining DOI as a tie-break.
                        df["_risk_rank"] = df["_risk_rank"].map({0: 1, 1: 0, 2: 2})
                    df = df.sort_values(sort_columns, ascending=ascending, na_position="last", kind="stable")
                    df = df.drop(columns=["_risk_rank"])
                    continue_sorting = False
                else:
                    continue_sorting = True
                field_ = METRIC_COLUMNS.get(metric)

                if field_ is None:
                    raise ValueError(f"sort metric not allowed: {metric}")

                if continue_sorting and field_ not in df.columns:
                    raise ValueError(
                        f"sort metric {metric} is not available for this result"
                    )

                if continue_sorting:
                    asc = str(
                        getattr(so, "direction", "asc")
                    ).lower().startswith("asc")

                    df = df.sort_values(
                        field_,
                        ascending=asc,
                        na_position="last",
                    )
        counts: dict[str, int | float] = {
            "matching_result_rows": len(df),
        }
        if "product_id" in df.columns:
            counts["distinct_products"] = int(df["product_id"].nunique(dropna=True))
        if {"product_id", "store_id"}.issubset(df.columns):
            counts["product_store_positions"] = int(
                df[["product_id", "store_id"]].drop_duplicates().shape[0]
            )
        elif "positions" in df.columns:
            counts["product_store_positions"] = int(
                pd.to_numeric(df["positions"], errors="coerce").fillna(0).sum()
            )
        if "store_id" in df.columns:
            counts["stores"] = int(df["store_id"].nunique(dropna=True))
        risk_col = "stockout_risk_v1" if "stockout_risk_v1" in df.columns else "stockout_risk"
        if risk_col in df.columns:
            counts["high_risk_positions"] = int(df[risk_col].eq("High").sum())
            counts["medium_risk_positions"] = int(df[risk_col].eq("Medium").sum())
        for column, count_name in (
            ("quantity_on_hand", "quantity_on_hand_total"),
            ("inventory_value", "inventory_value_total"),
        ):
            if column in df.columns:
                counts[count_name] = float(
                    pd.to_numeric(df[column], errors="coerce").sum()
                )

        limit = min(int(getattr(intent, "limit", None) or MAX_ROWS), MAX_ROWS)
        if len(df) > limit:
            notes.append(f"Showing top {limit} of {len(df)} rows")
        out = df.head(limit).reset_index(drop=True)
        out.attrs.update(df.attrs)
        out.attrs["result_counts"] = counts
        return out, notes

    def _sales_agg(self, res, start, end, group, pids, sids) -> pd.DataFrame:
        cols = [c for k in group for c in GROUPS[k]]
        sel = (", ".join(cols) + ", ") if cols else ""
        grp = f" GROUP BY {', '.join(cols)}" if cols else ""
        pc, pp = _in_clause("sa.product_id", pids)
        sc, sp_ = _in_clause("sa.store_id", sids)
        sql = f"""
        SELECT {sel}SUM(sa.quantity) AS units, SUM(sa.revenue) AS revenue,
               COUNT(DISTINCT sa.invoice_no) AS invoices,
               SUM(CASE WHEN sa.quantity < 0 THEN 1 ELSE 0 END) AS negative_sales_records
        FROM {self.s}Sales sa
        JOIN {self.s}Products p ON p.product_id = sa.product_id
        {self.subcategory_join}
        JOIN {self.s}Stores st ON st.store_id = sa.store_id
        WHERE sa.sale_date > ? AND sa.sale_date <= ? {pc} {sc}{grp}"""
        df = self._q(
            res,
            sql,
            [start.isoformat(), end.isoformat()] + pp + sp_,
        )
        df.columns = [c.split(".")[-1] for c in df.columns]
        return df

    def sales(
        self,
        intent,
        rules,
        as_of: date,
        product_ids,
        store_ids,
        prev_product_ids=None,
        prev_store_ids=None,
    ) -> QueryResult:
        res = QueryResult(pd.DataFrame())
        pids, sids = restrict(product_ids, prev_product_ids), restrict(
            store_ids, prev_store_ids
        )
        group = _group_keys(intent)
        start, end, label = window_for(intent, rules, as_of)
        res.notes.append(
            f"Sales window: {label} ({start} exclusive to {end} inclusive)"
        )
        cur = self._sales_agg(res, start, end, group, pids, sids)

        t = getattr(intent, "time", None)
        has_explicit_compare = bool(
            t is not None and (
                (getattr(t, "comparison_start_date", None) and getattr(t, "comparison_end_date", None))
                or (getattr(t, "comparison_month", None) and getattr(t, "comparison_year", None))
            )
        )
        if t is not None and (getattr(t, "compare_to_previous", False) or has_explicit_compare):
            if getattr(t, "comparison_start_date", None) and getattr(t, "comparison_end_date", None):
                pstart = datetime.strptime(str(t.comparison_start_date), "%Y-%m-%d").date() - timedelta(days=1)
                pend = datetime.strptime(str(t.comparison_end_date), "%Y-%m-%d").date()
            elif getattr(t, "comparison_month", None) and getattr(t, "comparison_year", None):
                pstart = date(int(t.comparison_year), int(t.comparison_month), 1) - timedelta(days=1)
                pend = date(int(t.comparison_year), int(t.comparison_month), calendar.monthrange(int(t.comparison_year), int(t.comparison_month))[1])
            else:
                span = end - start
                pstart, pend = start - span, start
            prev = self._sales_agg(res, pstart, pend, group, pids, sids)
            res.notes.append(
                f"Compared with comparison period ({pstart} exclusive to {pend} inclusive)"
            )
            keys = [c.split(".")[-1] for k in group for c in GROUPS[k]]
            if keys:
                df = cur.merge(prev, on=keys, how="outer", suffixes=("", "_prev"))
            else:
                df = pd.concat([cur, prev.add_suffix("_prev")], axis=1)
            for c in ("units", "revenue", "invoices", "negative_sales_records"):
                df[c] = df[c].fillna(0)
                df[c + "_prev"] = df[c + "_prev"].fillna(0)
            df["units_change"] = df["units"] - df["units_prev"]
            df["growth_pct"] = (
                (df["units"] - df["units_prev"]) / df["units_prev"] * 100
            ).where(df["units_prev"] > 0).round(1)
            df["revenue_change"] = df["revenue"] - df["revenue_prev"]
            df["revenue_growth_pct"] = (
                (df["revenue"] - df["revenue_prev"]) / df["revenue_prev"] * 100
            ).where(df["revenue_prev"] > 0).round(1)
            cur = df
        res.df = cur
        return res

    def inventory_trend(
        self,
        as_of: date,
        weeks: int,
        product_ids,
        store_ids,
        group=("category",),
        previous_as_of: date | None = None,
    ) -> QueryResult:
        """Weekly-snapshot comparison: latest snapshot <= as_of vs one `weeks` earlier."""
        res = QueryResult(pd.DataFrame())
        g = _group_keys(type("I", (), {"group_by": list(group)}))
        cols = [c for k in g for c in GROUPS[k]]
        sel = (", ".join(cols) + ", ") if cols else ""
        grp = f" GROUP BY {', '.join(cols)}" if cols else ""
        pc, pp = _in_clause("w.product_id", product_ids)
        sc, sp_ = (
            _in_clause("w.store_id", sids) if (sids := store_ids) is not None else ("", [])
        )
        out = {}
        previous_target = previous_as_of or (as_of - timedelta(weeks=weeks))
        for tag, target in (("now", as_of), ("before", previous_target)):
            d = self._q(
                res,
                f"SELECT MAX(snapshot_date) AS d FROM {self.s}InventorySnapshotWeekly WHERE snapshot_date <= ?",
                [target.isoformat()],
            )
            if d.empty or pd.isna(d["d"].iloc[0]):
                res.notes.append(f"No inventory snapshot is available on or before {target.isoformat()}")
                res.df = pd.DataFrame()
                return res
            snap = pd.to_datetime(d["d"].iloc[0]).date()
            sql = f"""
            SELECT {sel}SUM(w.quantity_on_hand) AS qty
            FROM {self.s}InventorySnapshotWeekly w
            JOIN {self.s}Products p ON p.product_id = w.product_id
            {self.subcategory_join}
            JOIN {self.s}Stores st ON st.store_id = w.store_id
            WHERE w.snapshot_date = ? {pc} {sc}{grp}"""
            df = self._q(res, sql, [snap.isoformat()] + pp + sp_)
            df.columns = [c.split(".")[-1] for c in df.columns]
            out[tag] = (snap, df)
        keys = [c.split(".")[-1] for c in cols]
        a, b = out["now"][1], out["before"][1]
        df = (
            a.merge(b, on=keys, how="outer", suffixes=("_now", "_before"))
            if keys
            else pd.concat([a.add_suffix("_now"), b.add_suffix("_before")], axis=1)
        )
        df["qty_change"] = df["qty_now"].fillna(0) - df["qty_before"].fillna(0)
        df["snapshot_date"] = out["now"][0].isoformat()
        df["snapshot_date_before"] = out["before"][0].isoformat()
        res.notes.append(f"Snapshots compared: {out['before'][0]} -> {out['now'][0]}")
        res.df = df
        return res
