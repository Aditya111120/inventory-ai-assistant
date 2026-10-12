"""Read-only LD-01 authenticated API and SQL reconciliation matrix.

Credentials are read only from process environment. The script never loads an
application .env file, writes database rows, or runs other demo accounts.
"""
from __future__ import annotations

import argparse
import calendar
import json
import os
import re
import sys
import uuid
from dataclasses import asdict, dataclass, field
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import requests


@dataclass
class Case:
    name: str
    question: str
    expected: str
    status: str = "BLOCKED"
    actual: str = ""
    root_cause: str = ""


@dataclass
class Report:
    status: str = "BLOCKED"
    target: dict[str, str] = field(default_factory=dict)
    identity: dict[str, Any] = field(default_factory=dict)
    cases: list[Case] = field(default_factory=list)
    excluded_account_security: list[Case] = field(default_factory=list)
    blockers: list[str] = field(default_factory=list)

    def as_dict(self):
        return {"status": self.status, "target": self.target, "identity": self.identity,
                "cases": [asdict(c) for c in self.cases],
                "excluded_account_security": [asdict(c) for c in self.excluded_account_security],
                "blockers": self.blockers}


MATRIX = (
    ("inventory_totals", "Show total inventory units across all stores", "Complete authorized inventory units reconcile to SQL"),
    ("inventory_value", "Show total inventory value across all stores", "Complete authorized inventory value reconciles to SQL"),
    ("category_ranking", "Rank categories by inventory value", "Ranking winner and value reconcile to SQL"),
    ("brand_ranking", "Rank brands by inventory value", "Ranking winner and value reconcile to SQL"),
    ("store_revenue", "Rank stores by sales revenue", "Store revenue ranking reconciles to SQL"),
    ("sales_units", "Show total sales units across all stores", "Sales total reconciles to SQL and the configured sales window"),
    ("sales_by_category", "Show sales units by category", "Category sales totals reconcile to SQL"),
    ("sales_by_brand", "Show sales revenue by brand", "Brand sales totals reconcile to SQL"),
    ("sales_by_city", "Show sales revenue by city", "City sales totals reconcile to SQL"),
    ("risk", "Show stockout risk across all stores", "V1 risk positions reconcile to read-only source inputs"),
    ("zero_stock", "Show zero stock positions across all stores", "Zero-stock position count reconciles to SQL"),
    ("historical", "Show historical inventory trend by category", "Weekly snapshot dates and totals reconcile to SQL"),
    ("comparison", "Compare categories by inventory value", "Category values and comparison math reconcile to SQL"),
    ("period_comparison", "Compare sales units by category across two available calendar months", "Both full-month totals and deterministic deltas reconcile to SQL"),
    ("what_if", "What if demand increases by 20% for a catalog product at an authorized store?", "Simulation retains actual scope and computes from verified data"),
    ("executive_summary", "Give me an executive summary", "Inventory, sales, rules, dates, table and Trust Panel reconcile"),
    ("clarification", "Compare categories", "Clarifies the missing metric with supported choices"),
    ("context_isolation", "Show sales units by category", "Explicit scope is identical after unrelated context"),
)
EXCLUDED_ACCOUNTS = ("SM-01", "SM-02", "SM-03", "SM-04", "BM-01", "BM-02", "PL-01")


def _below_reorder_predicate(alias: str = "i") -> str:
    """SQL equivalent of V1 At or Below Reorder Level classification."""
    return (
        f"{alias}.quantity_on_hand>0 AND {alias}.reorder_level>=0 "
        f"AND NOT ({alias}.max_level IS NOT NULL AND {alias}.max_level>=0 "
        f"AND {alias}.max_level<{alias}.reorder_level) "
        f"AND {alias}.quantity_on_hand<={alias}.reorder_level"
    )


class ReadOnlySql:
    def __init__(self, server: str, database: str):
        self.server, self.database, self.conn = server, database, None

    def connect(self):
        try:
            import pyodbc
        except ImportError as exc:
            raise RuntimeError("pyodbc is not installed") from exc
        driver = os.getenv("PHASE1_DB_DRIVER", "ODBC Driver 18 for SQL Server")
        encrypt = os.getenv("PHASE1_DB_ENCRYPT", "optional")
        trust = os.getenv("PHASE1_DB_TRUST_SERVER_CERT", "yes")
        auth = os.getenv("PHASE1_DB_AUTH", os.getenv("DB_AUTH", "sql")).casefold()
        parts = [f"DRIVER={{{driver}}}", f"SERVER={self.server}", f"DATABASE={self.database}"]
        if auth == "sql":
            user, password = os.getenv("PHASE1_DB_USER") or os.getenv("DB_USER"), os.getenv("PHASE1_DB_PASSWORD") or os.getenv("DB_PASSWORD")
            if not user or not password:
                raise RuntimeError("SQL auth requires PHASE1_DB_USER and PHASE1_DB_PASSWORD")
            parts.extend([f"UID={user}", f"PWD={password}"])
        else:
            parts.append("Trusted_Connection=yes")
        parts.extend([f"Encrypt={encrypt}", f"TrustServerCertificate={trust}"])
        self.conn = pyodbc.connect(";".join(parts), autocommit=True, timeout=10)

    def query(self, sql, params=()):
        cur = self.conn.cursor()
        try:
            cur.execute(sql, params)
            names = [x[0] for x in cur.description or ()]
            return [dict(zip(names, row)) for row in cur.fetchall()]
        finally:
            cur.close()

    def one(self, sql, params=()):
        rows = self.query(sql, params)
        return rows[0] if rows else {}


def _payload_rows(payload: dict[str, Any]) -> list[dict[str, Any]]:
    if not isinstance(payload, dict):
        return []
    response = payload.get("response") or {}
    return response.get("all_rows") or response.get("table") or payload.get("table") or []


def _safe_error(error: Exception) -> str:
    """Remove any driver-echoed connection credentials from diagnostics."""
    value = str(error)
    return re.sub(r"(?i)(PWD|PASSWORD|UID|USER)\s*=\s*[^;\s]+", r"\1=<redacted>", value)


def _response_counts(payload: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(payload, dict):
        return {}
    response = payload.get("response") or {}
    answer_facts = payload.get("answer_facts") or response.get("answer_facts") or {}
    if not isinstance(answer_facts, dict):
        answer_facts = {}
    return response.get("scope") or response.get("result_counts") or answer_facts.get("counts", {}) or {}


class Runner:
    def __init__(self, api_url: str, sql: ReadOnlySql, password: str):
        self.api_url, self.sql, self.password = api_url.rstrip("/"), sql, password
        self.report = Report(target={"server": sql.server, "database": sql.database, "api_url": self.api_url})
        self.client = requests.Session()
        self.conversation = f"ld01-acceptance-{uuid.uuid4().hex}"

    def _login(self):
        response = self.client.post(f"{self.api_url}/auth/login", json={"username": "LD-01", "password": self.password}, timeout=30)
        if response.status_code != 200:
            raise RuntimeError(f"LD-01 login failed (HTTP {response.status_code})")
        data = response.json()
        if data.get("username") != "LD-01" or str(data.get("role", "")).casefold() != "leadership":
            raise RuntimeError("Authenticated identity or role did not match LD-01 Leadership")

    def _chat(self, question: str, conversation: str | None = None):
        response = self.client.post(f"{self.api_url}/chat", json={"question": question, "conversation_id": conversation or self.conversation, "session_id": conversation or self.conversation}, timeout=90)
        if response.status_code >= 400:
            raise RuntimeError(f"API returned HTTP {response.status_code}")
        data = response.json()
        if not isinstance(data, dict):
            raise RuntimeError("API returned a non-object chat response")
        return data

    def _reference_window(self):
        as_of = self.sql.one("SELECT MAX(sale_date) AS d FROM dbo.Sales")
        rule = self.sql.one("SELECT rule_value FROM dbo.BusinessRules WHERE rule_key='sales_window_days'")
        if not as_of.get("d") or not rule.get("rule_value"):
            raise RuntimeError("Could not read approved sales as-of date and sales_window_days rule")
        end = as_of["d"]
        if isinstance(end, str):
            end = date.fromisoformat(end[:10])
        days = int(float(rule["rule_value"]))
        return end - timedelta(days=days), end

    def _reference(self, name: str):
        if name in {"inventory_totals", "inventory_value", "executive_summary"}:
            below_reorder = _below_reorder_predicate("i")
            inventory = self.sql.one(f"""SELECT COUNT(DISTINCT i.product_id) AS products, COUNT(DISTINCT i.store_id) AS stores,
                COUNT(*) AS positions, SUM(i.quantity_on_hand) AS units,
                SUM(i.quantity_on_hand*p.cost_price) AS inventory_value,
                SUM(CASE WHEN {below_reorder} THEN 1 ELSE 0 END) AS below_reorder_positions,
                MAX(i.as_of_date) AS inventory_date
                FROM dbo.Inventory i JOIN dbo.Products p ON p.product_id=i.product_id WHERE p.is_active=1""")
            if name != "executive_summary": return inventory
            start, end = self._reference_window()
            sales = self.sql.one("SELECT SUM(quantity) AS units,SUM(revenue) AS revenue FROM dbo.Sales WHERE sale_date>? AND sale_date<=?", (start.isoformat(), end.isoformat()))
            risk_rows = self._reference("risk")
            rules = {row["rule_key"]: float(row["rule_value"]) for row in Runner._rules_cache}
            high = medium = 0
            for row in risk_rows:
                qty, sold, lead = float(row["quantity_on_hand"] or 0), float(row["units_sold"] or 0), row["lead_time_days"]
                if qty <= 0 or sold <= 0 or lead is None: continue
                doi = qty / (sold / rules["sales_window_days"])
                if doi < float(lead): high += 1
                elif doi < float(lead) + rules["safety_buffer_days"]: medium += 1
            return {**inventory, "sales_units": sales.get("units"), "sales_revenue": sales.get("revenue"), "sales_start": start.isoformat(), "sales_end": end.isoformat(), "high_risk_positions": high, "medium_risk_positions": medium}
        if name in {"category_ranking", "brand_ranking"}:
            dim = "category" if name == "category_ranking" else "brand"
            return self.sql.query(f"SELECT p.{dim} AS group_name, SUM(i.quantity_on_hand*p.cost_price) AS metric FROM dbo.Inventory i JOIN dbo.Products p ON p.product_id=i.product_id WHERE p.is_active=1 GROUP BY p.{dim} ORDER BY metric DESC")
        if name in {"store_revenue", "sales_units", "sales_by_category", "sales_by_brand", "sales_by_city", "context_isolation"}:
            start, end = self._reference_window()
            dim = {"store_revenue": "st.store_name", "sales_by_category": "p.category", "sales_by_brand": "p.brand", "sales_by_city": "st.city"}.get(name)
            if name == "sales_units" or name == "context_isolation":
                return self.sql.one("SELECT SUM(sa.quantity) AS units, SUM(sa.revenue) AS revenue FROM dbo.Sales sa WHERE sa.sale_date>? AND sa.sale_date<=?", (start.isoformat(), end.isoformat())) | {"start": start.isoformat(), "end": end.isoformat()}
            return self.sql.query(f"SELECT {dim} AS group_name, SUM(sa.quantity) AS units, SUM(sa.revenue) AS revenue FROM dbo.Sales sa JOIN dbo.Products p ON p.product_id=sa.product_id JOIN dbo.Stores st ON st.store_id=sa.store_id WHERE sa.sale_date>? AND sa.sale_date<=? GROUP BY {dim} ORDER BY revenue DESC", (start.isoformat(), end.isoformat()))
        if name == "period_comparison":
            periods = self.sql.query("SELECT DISTINCT YEAR(sale_date) AS y, MONTH(sale_date) AS m FROM dbo.Sales ORDER BY y DESC, m DESC")
            if len(periods) < 2:
                return {"available": False}
            # Use the two latest distinct months that actually contain sales.
            newer, older = periods[0], periods[1]
            newer_start = date(int(newer["y"]), int(newer["m"]), 1)
            older_start = date(int(older["y"]), int(older["m"]), 1)
            newer_end = date(int(newer["y"]), int(newer["m"]), calendar.monthrange(int(newer["y"]), int(newer["m"]))[1])
            older_end = date(int(older["y"]), int(older["m"]), calendar.monthrange(int(older["y"]), int(older["m"]))[1])
            newer_start_exclusive = newer_start - timedelta(days=1)
            older_start_exclusive = older_start - timedelta(days=1)
            rows = self.sql.query("""SELECT p.category AS group_name,
                SUM(CASE WHEN sa.sale_date>? AND sa.sale_date<=? THEN sa.quantity ELSE 0 END) AS units,
                SUM(CASE WHEN sa.sale_date>? AND sa.sale_date<=? THEN sa.quantity ELSE 0 END) AS units_prev
                FROM dbo.Sales sa JOIN dbo.Products p ON p.product_id=sa.product_id
                WHERE (sa.sale_date>? AND sa.sale_date<=?) OR (sa.sale_date>? AND sa.sale_date<=?)
                GROUP BY p.category ORDER BY p.category""",
                (newer_start_exclusive.isoformat(), newer_end.isoformat(), older_start_exclusive.isoformat(), older_end.isoformat(), newer_start_exclusive.isoformat(), newer_end.isoformat(), older_start_exclusive.isoformat(), older_end.isoformat()))
            return {
                "available": True,
                "periods": ((int(newer["m"]), int(newer["y"])), (int(older["m"]), int(older["y"]))),
                "rows": rows,
                "expected_params": [
                    [newer_start_exclusive.isoformat(), newer_end.isoformat()],
                    [older_start_exclusive.isoformat(), older_end.isoformat()],
                ],
            }
        if name == "zero_stock":
            return self.sql.one("SELECT COUNT(*) AS positions, COUNT(DISTINCT i.product_id) AS products FROM dbo.Inventory i JOIN dbo.Products p ON p.product_id=i.product_id WHERE p.is_active=1 AND i.quantity_on_hand=0")
        if name == "historical":
            sales_as_of = self.sql.one("SELECT MAX(sale_date) AS d FROM dbo.Sales").get("d")
            if not sales_as_of:
                return {}
            if isinstance(sales_as_of, str): sales_as_of = date.fromisoformat(sales_as_of[:10])
            current = self.sql.one("SELECT MAX(snapshot_date) AS d FROM dbo.InventorySnapshotWeekly WHERE snapshot_date<=?", (sales_as_of.isoformat(),)).get("d")
            if not current: return {}
            if isinstance(current, str): current = date.fromisoformat(current[:10])
            earlier_target = sales_as_of - timedelta(weeks=4)
            prior = self.sql.one("SELECT MAX(snapshot_date) AS d FROM dbo.InventorySnapshotWeekly WHERE snapshot_date<=?", (earlier_target.isoformat(),)).get("d")
            if isinstance(prior, str): prior = date.fromisoformat(prior[:10])
            totals = self.sql.one("SELECT SUM(quantity_on_hand) AS units FROM dbo.InventorySnapshotWeekly WHERE snapshot_date=?", (current.isoformat(),))
            prior_totals = self.sql.one("SELECT SUM(quantity_on_hand) AS units FROM dbo.InventorySnapshotWeekly WHERE snapshot_date=?", (prior.isoformat(),)) if prior else {"units": None}
            return {"snapshot_date": current.isoformat(), "snapshot_date_before": prior.isoformat() if prior else None, "qty_now": totals.get("units"), "qty_before": prior_totals.get("units")}
        if name == "comparison":
            return self.sql.query("SELECT p.category AS group_name, SUM(i.quantity_on_hand*p.cost_price) AS metric FROM dbo.Inventory i JOIN dbo.Products p ON p.product_id=i.product_id WHERE p.is_active=1 GROUP BY p.category ORDER BY metric DESC")
        if name == "risk":
            start, end = self._reference_window()
            return self.sql.query("""SELECT i.product_id,i.store_id,i.quantity_on_hand,i.reorder_level,i.max_level,
                COALESCE(s.units,0) AS units_sold, sp.lead_time_days
                FROM dbo.Inventory i JOIN dbo.Products p ON p.product_id=i.product_id
                LEFT JOIN (SELECT product_id,store_id,SUM(quantity) units FROM dbo.Sales
                  WHERE sale_date>? AND sale_date<=?
                  GROUP BY product_id,store_id) s ON s.product_id=i.product_id AND s.store_id=i.store_id
                LEFT JOIN (SELECT product_id,MAX(lead_time_days) lead_time_days FROM dbo.SupplierProducts WHERE is_primary=1 GROUP BY product_id) sp ON sp.product_id=i.product_id
                WHERE p.is_active=1""", (start.isoformat(), end.isoformat()))
        if name == "what_if":
            start, end = self._reference_window()
            return self.sql.one("""SELECT TOP 1 p.product_id,st.store_id,p.product_name,st.store_name,i.quantity_on_hand,
                SUM(sa.quantity) AS units_sold
                FROM dbo.Inventory i JOIN dbo.Products p ON p.product_id=i.product_id
                JOIN dbo.Stores st ON st.store_id=i.store_id
                JOIN dbo.Sales sa ON sa.product_id=i.product_id AND sa.store_id=i.store_id
                WHERE p.is_active=1 AND i.quantity_on_hand>0 AND sa.sale_date>? AND sa.sale_date<=?
                  AND (SELECT COUNT_BIG(*) FROM dbo.Stores st2 WHERE st2.store_name=st.store_name)=1
                GROUP BY p.product_id,st.store_id,p.product_name,st.store_name,i.quantity_on_hand
                HAVING SUM(sa.quantity)>0 ORDER BY p.product_name,st.store_name""", (start.isoformat(), end.isoformat())) | {"start": start.isoformat(), "end": end.isoformat(), "window_days": (end-start).days, "demand_change_pct": 20}
        return None

    @staticmethod
    def _assert_numeric(name, payload, reference):
        status = payload.get("status") if isinstance(payload, dict) else None
        if status is not None and status != "ok":
            detail = payload.get("error") if isinstance(payload, dict) else None
            raise AssertionError(
                f"API did not return a successful result (status={status!r}): {detail or 'no error detail'}"
            )
        counts = _response_counts(payload)
        facts = payload.get("answer_facts") or (payload.get("response") or {}).get("answer_facts") or {}
        metrics = facts.get("metrics", {})
        if name == "inventory_totals":
            actual = counts.get("quantity_on_hand_total")
            expected = reference.get("units")
        elif name == "inventory_value":
            actual, expected = counts.get("inventory_value_total"), reference.get("inventory_value")
        elif name == "zero_stock":
            actual = counts.get("product_store_positions")
            expected = reference.get("positions")
        elif name == "executive_summary":
            for field, key in (("quantity_on_hand_total", "units"), ("inventory_value_total", "inventory_value"), ("sales_units_total", "sales_units"), ("sales_revenue_total", "sales_revenue"), ("below_reorder_positions", "below_reorder_positions"), ("high_risk_positions", "high_risk_positions"), ("medium_risk_positions", "medium_risk_positions")):
                if not _close(metrics.get(field), reference.get(key)):
                    raise AssertionError(f"executive summary {field} mismatch")
            if metrics.get("sales_window_start_exclusive") != reference.get("sales_start") or metrics.get("sales_window_end_inclusive") != reference.get("sales_end"):
                raise AssertionError("executive summary sales window does not match SQL reference")
            return
        elif name in {"sales_units", "context_isolation"}:
            rows = _payload_rows(payload)
            actual = sum(float(r.get("units") or 0) for r in rows) if rows else metrics.get("units")
            expected = reference.get("units")
        elif name == "period_comparison":
            if not reference.get("available"):
                raise AssertionError("fewer than two sales months are available for period comparison")
            api_rows = _payload_rows(payload)
            if not api_rows:
                raise AssertionError("period comparison returned no grouped rows")
            response_payload = payload.get("response") or {}
            facts = payload.get("answer_facts") or response_payload.get("answer_facts") or {}
            time_scope = facts.get("time_scope") or {}
            trust_query = ((payload.get("trust_panel") or {}).get("query") or {})
            if reference.get("expected_params") and trust_query.get("params") != reference["expected_params"]:
                raise AssertionError(f"effective SQL period parameters differ: API={trust_query.get('params')!r}, SQL reference windows={reference['expected_params']!r}")
            first, second = reference.get("periods", (None, None))
            if first and second and (
                time_scope.get("kind") != "month"
                or int(time_scope.get("month") or 0) != first[0]
                or int(time_scope.get("year") or 0) != first[1]
                or int(time_scope.get("comparison_month") or 0) != second[0]
                or int(time_scope.get("comparison_year") or 0) != second[1]
            ):
                raise AssertionError("answer facts do not preserve both selected calendar months")
            if {str(row.get("category", "")).casefold() for row in api_rows} != {str(row["group_name"]).casefold() for row in reference["rows"]}:
                raise AssertionError("period comparison group set differs from SQL reference")
            for ref in reference["rows"]:
                candidate = next((row for row in api_rows if str(row.get("category", "")).casefold() == str(ref["group_name"]).casefold()), None)
                if candidate is None or not _close(candidate.get("units"), ref.get("units")) or not _close(candidate.get("units_prev"), ref.get("units_prev")):
                    raise AssertionError(f"calendar-month comparison mismatch for category {ref.get('group_name')}")
                expected_change = float(ref.get("units") or 0) - float(ref.get("units_prev") or 0)
                if not _close(candidate.get("units_change"), expected_change):
                    raise AssertionError(f"period delta mismatch for category {ref.get('group_name')}")
            return
        elif name == "historical":
            for field in ("snapshot_date", "snapshot_date_before"):
                if field in reference and reference[field] and metrics.get(field) != reference[field]:
                    raise AssertionError(f"{field} mismatch: API={metrics.get(field)!r}, SQL={reference[field]!r}")
            if not _close(metrics.get("quantity_now_total"), reference.get("qty_now")) or not _close(metrics.get("quantity_before_total"), reference.get("qty_before")):
                raise AssertionError("historical snapshot totals differ from SQL")
            return
        elif name == "what_if":
            if not reference or not reference.get("product_name"):
                raise AssertionError("no product-store with positive stock and demand is available for the scenario check")
            rows = _payload_rows(payload)
            if not rows or not any(str(row.get("product_name", "")).casefold() == str(reference["product_name"]).casefold() and str(row.get("store_name", "")).casefold() == str(reference["store_name"]).casefold() for row in rows):
                raise AssertionError("what-if response did not retain the SQL-selected product/store")
            resolved = payload.get("resolved_context") or (payload.get("response") or {}).get("resolved_context") or {}
            products = (resolved.get("product_scope") or {}).get("product_ids") or []
            stores = (resolved.get("location_scope") or {}).get("store_ids") or []
            if [int(value) for value in products] != [int(reference["product_id"])] or [int(value) for value in stores] != [int(reference["store_id"])]:
                raise AssertionError("effective product/store IDs differ from the SQL-selected scenario target")
            api_intent = payload.get("intent") or (payload.get("response") or {}).get("intent") or {}
            scenario = api_intent.get("scenario") or {}
            demand_change_pct = float(reference.get("demand_change_pct", 20))
            if scenario.get("kind") != "demand_change_pct" or not _close(scenario.get("value"), demand_change_pct):
                raise AssertionError("API intent did not preserve the requested demand-change scenario")
            expected_actual = float(reference["quantity_on_hand"]) / (float(reference["units_sold"]) / int(reference["window_days"]))
            simulated_daily_sales = (float(reference["units_sold"]) / int(reference["window_days"])) * max(1 + demand_change_pct / 100.0, 0.0)
            expected_sim = float(reference["quantity_on_hand"]) / simulated_daily_sales if simulated_daily_sales > 0 else None
            row = next(row for row in rows if str(row.get("product_name", "")).casefold() == str(reference["product_name"]).casefold() and str(row.get("store_name", "")).casefold() == str(reference["store_name"]).casefold())
            if not _close(row.get("actual_days_of_inventory"), expected_actual) or not _close(row.get("simulated_days_of_inventory"), expected_sim):
                raise AssertionError("actual/simulated coverage differs from SQL source calculation")
            if payload.get("basis") != "Simulated":
                raise AssertionError("scenario response is not marked Simulated")
            calculations = (payload.get("trust_panel") or {}).get("calculations") or []
            scenario_fact = next((item for item in calculations if item.get("metric") == "what_if_days_of_inventory"), None)
            if scenario_fact is None or not _close(scenario_fact.get("result"), expected_sim):
                raise AssertionError("what-if Trust Panel does not reconcile to the simulated coverage result")
            comparison_fact = next((item for item in calculations if item.get("metric") == "what_if_comparison"), None)
            if comparison_fact is None or not _close((comparison_fact.get("result") or {}).get("actual_days_of_inventory"), expected_actual) or not _close((comparison_fact.get("result") or {}).get("simulated_days_of_inventory"), expected_sim):
                raise AssertionError("what-if actual/simulated Trust Panel comparison does not reconcile")
            trust_params = ((payload.get("trust_panel") or {}).get("query") or {}).get("params") or []
            if not trust_params or len(trust_params[0]) < 4 or [int(value) for value in trust_params[0][-2:]] != [int(reference["product_id"]), int(reference["store_id"])]:
                raise AssertionError("what-if SQL parameters do not retain the selected product/store filters")
            return
        elif name in {"category_ranking", "brand_ranking", "store_revenue", "sales_by_category", "sales_by_brand", "sales_by_city", "comparison"}:
            ref_rows = reference or []
            api_rows = _payload_rows(payload)
            if not ref_rows or not api_rows:
                raise AssertionError("expected non-empty grouped SQL and API rows")
            key = {"category_ranking": "category", "sales_by_category": "category", "comparison": "category", "brand_ranking": "brand", "sales_by_brand": "brand", "store_revenue": "store_name", "sales_by_city": "city"}[name]
            fields = ("inventory_value",) if name in {"category_ranking", "brand_ranking", "comparison"} else ("units", "revenue")
            for ref in ref_rows:
                dimension = ref.get("group_name")
                candidate = next((row for row in api_rows if str(row.get(key, "")).casefold() == str(dimension).casefold()), None)
                if candidate is None:
                    raise AssertionError(f"SQL group {dimension!r} absent from API result table")
                for field in fields:
                    expected = ref.get("metric", ref.get(field))
                    if not _close(candidate.get(field), expected):
                        raise AssertionError(f"{field} mismatch for {dimension}: API={candidate.get(field)!r}, SQL={expected!r}")
            return
        elif name == "risk":
            rows = reference or []
            rules = {row["rule_key"]: float(row["rule_value"]) for row in Runner._rules_cache}
            risk_counts = {"High": 0, "Medium": 0}
            for row in rows:
                qty, sold, lead = float(row["quantity_on_hand"] or 0), float(row["units_sold"] or 0), row["lead_time_days"]
                if qty <= 0 or sold <= 0 or lead is None: continue
                doi = qty / (sold / rules["sales_window_days"])
                if doi < float(lead): risk_counts["High"] += 1
                elif doi < float(lead) + rules["safety_buffer_days"]: risk_counts["Medium"] += 1
            actual_high = int(counts.get("high_risk_positions", 0))
            actual_med = int(counts.get("medium_risk_positions", 0))
            if (actual_high, actual_med) != (risk_counts["High"], risk_counts["Medium"]):
                raise AssertionError(f"V1 risk counts differ; API={(actual_high, actual_med)}, SQL-input calculation={(risk_counts['High'], risk_counts['Medium'])}")
            return
        else:
            return
        if not _close(actual, expected):
            raise AssertionError(f"numerical mismatch: API={actual!r}, SQL={expected!r}")

    def run(self):
        self.report.cases = [Case(name, question, expected) for name, question, expected in MATRIX]
        self.report.excluded_account_security = [Case(account, "POST /auth/login with that account", "Leadership-only demo rejects excluded demo account") for account in EXCLUDED_ACCOUNTS]
        if self.sql.database.casefold() == "inventoryai" and os.getenv("PHASE1_ALLOW_PRODUCTION") != "yes":
            self.report.blockers.append("InventoryAI target needs PHASE1_ALLOW_PRODUCTION=yes")
            for case in self.report.cases + self.report.excluded_account_security:
                case.root_cause = self.report.blockers[-1]
            return self.report
        try:
            self.sql.connect()
            self.report.identity = self.sql.one("SELECT @@SERVERNAME AS server_name, DB_NAME() AS database_name, DATABASEPROPERTYEX(DB_NAME(),'Status') AS database_status")
            identity = self.report.identity
            if str(identity.get("server_name", "")).casefold() != self.sql.server.casefold() or str(identity.get("database_name", "")).casefold() != self.sql.database.casefold() or str(identity.get("database_status", "")).casefold() != "online":
                raise RuntimeError("SQL server/database identity or online status mismatch")
            Runner._rules_cache = self.sql.query("SELECT rule_key,rule_value FROM dbo.BusinessRules")
            self._login()
            health = requests.get(f"{self.api_url}/health", timeout=15)
            if health.status_code != 200:
                raise RuntimeError(f"API health returned HTTP {health.status_code}")
        except Exception as exc:
            self.report.blockers.append(_safe_error(exc))
            for case in self.report.cases:
                case.status = "BLOCKED"
                case.root_cause = _safe_error(exc)
            for case in self.report.excluded_account_security:
                case.status = "BLOCKED"
                case.root_cause = _safe_error(exc)
            self.report.status = "BLOCKED"
            return self.report
        for case in self.report.cases:
            try:
                if case.name == "context_isolation":
                    conversation_a = f"ld01-context-a-{uuid.uuid4().hex}"
                    conversation_b = f"ld01-context-b-{uuid.uuid4().hex}"
                    self._chat("Show total inventory value across all stores", conversation_a)
                    payload = self._chat(case.question, conversation_a)
                    second = self._chat(case.question, conversation_b)
                    if payload.get("intent", {}).get("filters") != second.get("intent", {}).get("filters") or payload.get("resolved_context") != second.get("resolved_context"):
                        raise AssertionError("same explicit request resolved differently after unrelated context")
                    reference = self._reference(case.name)
                    case_conversation = conversation_a
                else:
                    reference = self._reference(case.name)
                    if case.name == "period_comparison":
                        if not reference.get("available"):
                            case.status, case.actual, case.root_cause = "BLOCKED", "not run", "Fewer than two distinct sales months are available"
                            continue
                        first, second = reference["periods"]
                        question = (f"Compare sales units by category in {calendar.month_name[first[0]]} {first[1]} "
                                    f"versus {calendar.month_name[second[0]]} {second[1]}")
                    else:
                        question = case.question
                    if case.name == "what_if":
                        if not reference or not reference.get("product_name"):
                            case.status, case.actual, case.root_cause = "BLOCKED", "not run", "No product-store with positive stock and sales in the approved window"
                            continue
                        question = f"What if demand increases by {reference['demand_change_pct']}% for {reference['product_name']} at {reference['store_name']}?"
                    else:
                        question = question
                    case.question = question if case.name == "what_if" else case.question
                    case_conversation = f"{self.conversation}-{case.name}-{uuid.uuid4().hex}"
                    payload = self._chat(question, case_conversation)
                self._assert_numeric(case.name, payload, reference)
                if case.name == "clarification" and (payload.get("status") != "clarification" or payload.get("clarification_kind") != "comparison_metric" or not payload.get("clarification_options")):
                    raise AssertionError("ambiguous comparison did not offer metric options")
                if case.name == "clarification":
                    options = payload.get("clarification_options") or (payload.get("response") or {}).get("clarification_options") or []
                    selected = next((item for item in options if isinstance(item, dict)), None)
                    if selected is None:
                        raise AssertionError("comparison clarification returned no selectable metric option")
                    resumed = self._chat(selected.get("token") or selected.get("label"), case_conversation)
                    if resumed.get("status") not in {"ok", "no_results"}:
                        raise AssertionError("metric clarification choice did not resume the comparison")
                case.status, case.actual = "PASS", f"status={payload.get('status')}; response={payload.get('response_type')}"
            except Exception as exc:
                case.status, case.actual, case.root_cause = "FAIL", "API/SQL check failed", str(exc)
        for case in self.report.excluded_account_security:
            account = case.name

            env_name = f"PHASE1_PASSWORD_{account.replace('-', '_')}"
            password = os.getenv(env_name)

            if not password:
                case.status = "BLOCKED"
                case.actual = "not attempted"
                case.root_cause = f"Missing process credential for {account}"
                continue

            session = requests.Session()

            try:
                response = session.post(
                    f"{self.api_url}/auth/login",
                    json={
                        "username": account,
                        "password": password,
                    },
                    timeout=30,
                )

                if response.status_code in {401, 403}:
                    case.status = "PASS"
                    case.actual = f"HTTP {response.status_code}"
                    case.root_cause = ""
                else:
                    case.status = "FAIL"
                    case.actual = f"HTTP {response.status_code}"
                    case.root_cause = (
                        "Excluded account was not rejected by the demo gate"
                    )

            except Exception as exc:
                case.status = "BLOCKED"
                case.actual = "not attempted"
                case.root_cause = _safe_error(exc)

            finally:
                session.close()
        statuses = [c.status for c in self.report.cases + self.report.excluded_account_security]
        self.report.status = "PASS" if statuses and all(status == "PASS" for status in statuses) else "FAIL" if "FAIL" in statuses else "BLOCKED"
        return self.report


def _close(actual, expected, tolerance=0.02):
    if actual is None or expected is None:
        return actual is expected
    try:
        return abs(float(actual) - float(expected)) <= tolerance
    except (TypeError, ValueError):
        return str(actual) == str(expected)


def write_report(report: Report, path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report.as_dict(), indent=2, default=str), encoding="utf-8")
    lines = ["# LD-01 Acceptance Report", "", f"Status: **{report.status}**", "", f"Target: `{report.target.get('server')}` / `{report.target.get('database')}`", "", "## LD-01 capability matrix", "", "| Case | Question | Status | Actual | Root cause |", "|---|---|---|---|---|"]
    for case in report.cases:
        lines.append(f"| {case.name} | {case.question.replace('|', '/') } | {case.status} | {case.actual} | {case.root_cause} |")
    lines.extend(["", "## Excluded-account security checks", "", "| Account | Check | Status | Actual | Root cause |", "|---|---|---|---|---|"])
    for case in report.excluded_account_security:
        lines.append(f"| {case.name} | {case.question} | {case.status} | {case.actual} | {case.root_cause} |")
    if report.blockers:
        lines.extend(["", "## Blockers", ""] + [f"- {item}" for item in report.blockers])
    path.with_suffix(".md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def main(argv=None):
    parser = argparse.ArgumentParser(description="LD-01 only authenticated API and read-only SQL acceptance matrix")
    parser.add_argument("--api-url", default=os.getenv("PHASE1_API_URL", "http://127.0.0.1:8000"))
    parser.add_argument("--expected-server", default=os.getenv("PHASE1_EXPECTED_SERVER"))
    parser.add_argument("--expected-database", default=os.getenv("PHASE1_EXPECTED_DATABASE"))
    parser.add_argument("--report", default="ld01_acceptance_report.json")
    args = parser.parse_args(argv)
    report = Report(target={"server": args.expected_server or "", "database": args.expected_database or "", "api_url": args.api_url}, cases=[Case(name, question, expected) for name, question, expected in MATRIX])
    password = os.getenv("PHASE1_PASSWORD_LD_01") or os.getenv("LD01_TEST_PASSWORD")
    if not args.expected_server or not args.expected_database:
        report.blockers.append("--expected-server and --expected-database are required")
    if args.expected_database and args.expected_database.casefold() == "inventoryai" and os.getenv("PHASE1_ALLOW_PRODUCTION") != "yes":
        report.blockers.append("InventoryAI target needs PHASE1_ALLOW_PRODUCTION=yes")
    if not password:
        report.blockers.append("Missing PHASE1_PASSWORD_LD_01 or LD01_TEST_PASSWORD")
    report.excluded_account_security = [Case(account, "POST /auth/login with that account", "Leadership-only demo rejects excluded account", root_cause="Not attempted by this LD-01-only matrix; run with securely supplied account credentials in the separate security checks") for account in EXCLUDED_ACCOUNTS]
    if report.blockers:
        for case in report.cases: case.root_cause = "; ".join(report.blockers)
        write_report(report, Path(args.report)); print("BLOCKED"); return 2
    sql = ReadOnlySql(args.expected_server, args.expected_database)
    report = Runner(args.api_url, sql, password).run()
    write_report(report, Path(args.report))
    print(report.status)
    return 0 if report.status == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
