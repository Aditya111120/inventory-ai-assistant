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
