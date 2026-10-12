"""Compare query-layer results with oracle SQL on the real DB."""
import pandas as pd

from app.config import load_settings
from app.db import connect, query
from app.repository import load_rules
from app.semantic.queries import Queries


class I:
    time = None
    group_by = []


conn = connect(load_settings())
try:
    rules = load_rules(conn)
    q = Queries(query, conn)
    as_of = q.as_of()
    print("as_of:", as_of)

    r = q.inventory_position(I, rules, as_of, None, None)
    oracle = query(
        conn,
        "SELECT COUNT(*) n, SUM(units_sold_30d) u, "
        "SUM(inventory_value) v FROM dbo.vw_InventoryPosition",
        [],
    )
    print("rows        ", len(r.df), "vs view", int(oracle.n[0]))
    print("units 30d   ", int(r.df.units_sold_window.sum()), "vs view", int(oracle.u[0]))
    print(
        "inv value   ",
        round(float(r.df.inventory_value.sum()), 2),
        "vs view",
        float(oracle.v[0]),
    )
finally:
    conn.close()
