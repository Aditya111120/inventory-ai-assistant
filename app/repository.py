"""Data access for the calculation layer. SELECT-only, against views/tables."""
from __future__ import annotations

import pandas as pd

from app.calc.metrics import Rules
from app.db import query


def load_rules(conn) -> Rules:
    df = query(conn, "SELECT rule_key, rule_value FROM dbo.BusinessRules")
    return Rules.from_mapping({r.rule_key: float(r.rule_value) for r in df.itertuples()})


def load_inventory_position(conn) -> pd.DataFrame:
    """Raw inventory positions. NOTE: units_sold_30d is a fixed 30-day window in the view;
    later, query templates will parameterise the window from BusinessRules."""
    return query(conn, "SELECT * FROM dbo.vw_InventoryPosition")


def load_catalog(conn) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Product and store dimension tables (small) for the entity resolver."""
    products = query(
        conn,
        "SELECT p.product_id, p.sku, p.product_name, p.brand, p.category, p.model, "
        "ps.subcategory_name AS subcategory "
        "FROM dbo.Products p "
        "LEFT JOIN dbo.ProductSubcategories ps "
        "ON ps.subcategory_id = p.subcategory_id AND ps.category = p.category "
        "WHERE p.is_active = 1",
    )
    stores = query(
        conn,
        "SELECT store_id, store_name, city, region FROM dbo.Stores",
    )
    return products, stores


def _scope_filter(column: str, values) -> tuple[list[str], list]:
    if values is None:
        return [], []
    ids = list(values)
    if not ids:
        return ["1 = 0"], []
    return [f"{column} IN ({','.join('?' for _ in ids)})"], ids


class ExploreRepository:
    """Read-only, authorization-scoped data for the Explore Data view."""

    def __init__(self, run, conn, schema: str = "dbo."):
        self.run = run
        self.conn = conn
        self.schema = schema

    def _query(self, sql: str, params: list) -> pd.DataFrame:
        return self.run(self.conn, sql, params)

    @staticmethod
    def _product_scope(scope, product_alias: str = "p"):
        conditions, params = _scope_filter(
            f"{product_alias}.product_id",
            scope.allowed_product_ids,
        )
        if scope.allowed_brands:
            brand_conditions, brand_params = _scope_filter(
                f"{product_alias}.brand",
                scope.allowed_brands,
            )
            conditions.extend(brand_conditions)
            params.extend(brand_params)
        return conditions, params

    @staticmethod
    def _store_scope(scope, store_alias: str = "st"):
        store_conditions, store_params = _scope_filter(
            f"{store_alias}.store_id",
            scope.allowed_store_ids,
        )
        return store_conditions, store_params

    @classmethod
    def _scope_conditions(cls, scope, product_alias: str = "p", store_alias: str = "st"):
        product_conditions, product_params = cls._product_scope(scope, product_alias)
        store_conditions, store_params = cls._store_scope(scope, store_alias)
        return product_conditions + store_conditions, product_params + store_params

    def products(
        self,
        scope,
        *,
        search: str = "",
        brand: str | None = None,
        category: str | None = None,
        subcategory: str | None = None,
        page: int = 1,
        page_size: int = 25,
    ) -> dict:
        conditions, params = self._product_scope(scope)
        if search:
            conditions.append(
                "(LOWER(COALESCE(p.sku, '')) LIKE ? "
                "OR LOWER(COALESCE(p.product_name, '')) LIKE ? "
                "OR LOWER(COALESCE(p.brand, '')) LIKE ?)"
            )
            pattern = f"%{search.casefold()}%"
            params.extend([pattern, pattern, pattern])
        if brand:
            conditions.append("LOWER(p.brand) = ?")
            params.append(brand.casefold())
        if category:
            conditions.append("LOWER(p.category) = ?")
            params.append(category.casefold())
        if subcategory:
            conditions.append("LOWER(ps.subcategory_name) = ?")
            params.append(subcategory.casefold())
        where = f" WHERE {' AND '.join(conditions)}" if conditions else ""
        join = (
            f" LEFT JOIN {self.schema}ProductSubcategories ps "
            "ON ps.subcategory_id = p.subcategory_id AND ps.category = p.category"
        )
        count = self._query(
            f"SELECT COUNT_BIG(*) AS total FROM {self.schema}Products p{join}{where}",
            params,
        )
        total = int(count.iloc[0]["total"]) if not count.empty else 0
        rows = self._query(
            "SELECT p.product_id, p.sku, p.product_name, p.brand, p.category, "
            "ps.subcategory_name AS subcategory, p.is_active "
            f"FROM {self.schema}Products p{join}{where} "
            "ORDER BY p.product_name, p.product_id "
            "OFFSET ? ROWS FETCH NEXT ? ROWS ONLY",
            params + [(page - 1) * page_size, page_size],
        )
        return {
            "items": rows.to_dict(orient="records"),
            "total": total,
            "page": page,
            "page_size": page_size,
            "pages": (total + page_size - 1) // page_size,
        }

    def filters(self, scope) -> dict:
        conditions, params = self._product_scope(scope)
        where = f" WHERE {' AND '.join(conditions)}" if conditions else ""
        join = (
            f" LEFT JOIN {self.schema}ProductSubcategories ps "
            "ON ps.subcategory_id = p.subcategory_id AND ps.category = p.category"
        )
        frame = self._query(
            "SELECT DISTINCT p.brand, p.category, ps.subcategory_name AS subcategory "
            f"FROM {self.schema}Products p{join}{where} "
            "ORDER BY p.brand, p.category, ps.subcategory_name",
            params,
        )
        return {
            "brands": sorted(frame["brand"].dropna().astype(str).unique().tolist()) if "brand" in frame else [],
            "categories": sorted(frame["category"].dropna().astype(str).unique().tolist()) if "category" in frame else [],
            "subcategories": sorted(frame["subcategory"].dropna().astype(str).unique().tolist()) if "subcategory" in frame else [],
        }

    def locations(self, scope) -> list[dict]:
        conditions, params = self._store_scope(scope)
        where = f" WHERE {' AND '.join(conditions)}" if conditions else ""
        rows = self._query(
            "SELECT st.store_id, st.store_name, st.city, st.region "
            f"FROM {self.schema}Stores st{where} "
            "ORDER BY st.region, st.city, st.store_name",
            params,
        )
        return rows.to_dict(orient="records")

    def availability(self, scope) -> dict:
        product_conditions, product_params = self._product_scope(scope)
        product_where = (
            f" WHERE {' AND '.join(product_conditions)}"
            if product_conditions else ""
        )
        catalog = self._query(
            f"SELECT COUNT_BIG(*) AS total FROM {self.schema}Products p{product_where}",
            product_params,
        )
        position_conditions, position_params = self._scope_conditions(scope)
        position_where = (
            f" WHERE {' AND '.join(position_conditions)}"
            if position_conditions else ""
        )
        counts = self._query(
            "SELECT COUNT(DISTINCT CASE WHEN i.product_id IS NOT NULL THEN p.product_id END) AS inventory_products, "
            "COUNT(DISTINCT CASE WHEN i.quantity_on_hand > 0 THEN p.product_id END) AS positive_stock_products "
            f"FROM {self.schema}Products p "
            f"LEFT JOIN {self.schema}Inventory i ON i.product_id = p.product_id "
            f"LEFT JOIN {self.schema}Stores st ON st.store_id = i.store_id"
            f"{position_where}",
            position_params,
        )
        coverage = {}
        for label, table, date_column, alias, join_key in (
            ("inventory", "Inventory", "as_of_date", "i", "i.store_id"),
            ("sales", "Sales", "sale_date", "s", "s.store_id"),
            ("snapshots", "InventorySnapshotWeekly", "snapshot_date", "w", "w.store_id"),
        ):
            conditions, params = self._scope_conditions(
                scope,
                product_alias="p",
                store_alias="st",
            )
            table_where = f" WHERE {' AND '.join(conditions)}" if conditions else ""
            date_row = self._query(
                f"SELECT MIN({alias}.{date_column}) AS first_date, MAX({alias}.{date_column}) AS last_date "
                f"FROM {self.schema}{table} {alias} "
                f"JOIN {self.schema}Products p ON p.product_id = {alias}.product_id "
                f"JOIN {self.schema}Stores st ON st.store_id = {join_key}"
                f"{table_where}",
                params,
            )
            values = date_row.iloc[0].to_dict() if not date_row.empty else {}
            coverage[label] = {
                "first_date": values.get("first_date"),
                "last_date": values.get("last_date"),
            }
        return {
            "catalog_products": int(catalog.iloc[0]["total"]) if not catalog.empty else 0,
            "inventory_products": int(counts.iloc[0]["inventory_products"]) if not counts.empty else 0,
            "positive_stock_products": int(counts.iloc[0]["positive_stock_products"]) if not counts.empty else 0,
            "coverage": coverage,
        }
