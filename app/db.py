"""Thin pyodbc helper. Always parameterised; returns pandas DataFrames."""
from __future__ import annotations

from typing import Sequence

import pandas as pd
import pyodbc

from app.config import Settings


def connect(settings: Settings, timeout: int = 10) -> pyodbc.Connection:
    return pyodbc.connect(settings.connection_string(), timeout=timeout)


def query(conn: pyodbc.Connection, sql: str, params: Sequence = ()) -> pd.DataFrame:
    cur = conn.cursor()
    try:
        cur.execute(sql, params)
        columns = [c[0] for c in cur.description]
        rows = [tuple(r) for r in cur.fetchall()]
    finally:
        cur.close()
    return pd.DataFrame.from_records(rows, columns=columns)
