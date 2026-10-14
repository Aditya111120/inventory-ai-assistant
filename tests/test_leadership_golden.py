from __future__ import annotations

import sqlite3

import pandas as pd
import pytest

from app.answer_builder import validate_narrative_evidence
from app.calc.metrics import Rules
from app.context import UserContext
from app.llm.intent import ParsedIntent, _normalise
from app.llm.client import Usage
from app.llm.schema import Filters, Intent
from app.orchestrator import Orchestrator
from app.semantic.queries import Queries
from app.semantic.resolver import EntityResolver
from app.session import SessionMemory


@pytest.fixture
def sales_assistant(monkeypatch):
    connection = sqlite3.connect(":memory:")
    connection.executescript(
        """
        CREATE TABLE Products(
            product_id INTEGER, sku TEXT, product_name TEXT, brand TEXT,
            category TEXT, model TEXT
        );
        CREATE TABLE Stores(
            store_id INTEGER, store_name TEXT, city TEXT, region TEXT
        );
        CREATE TABLE Sales(
            sale_id INTEGER, invoice_no TEXT, sale_date TEXT, product_id INTEGER,
            store_id INTEGER, quantity INTEGER, revenue REAL
        );
        INSERT INTO Products VALUES
            (1, 'APL-1', 'Apple Device One', 'Apple', 'Smartphones', 'DeviceOne'),
            (2, 'APL-2', 'Apple Audio', 'Apple', 'Audio', 'Earbuds'),
            (3, 'SAM-1', 'Samsung Phone', 'Samsung', 'Smartphones', 'Phone');
        INSERT INTO Stores VALUES
            (10, 'Central', 'Chennai', 'South'),
            (11, 'North', 'Delhi', 'North');
        INSERT INTO Sales VALUES
            (1, 'AUG-A1', '2026-08-10', 1, 10, 10, 1000),
            (2, 'AUG-A2', '2026-08-11', 2, 11, 5, 250),
            (3, 'AUG-S1', '2026-08-12', 3, 10, 8, 800),
            (4, 'SEP-A1', '2026-09-10', 1, 10, 15, 1500),
            (5, 'SEP-A2', '2026-09-11', 2, 11, 7, 350),
            (6, 'SEP-S1', '2026-09-12', 3, 10, 12, 1200),
            (7, 'OCT-A1', '2026-10-10', 1, 10, 18, 1800),
            (8, 'OCT-A2', '2026-10-11', 2, 11, 9, 450),
            (9, 'OCT-S1', '2026-10-12', 3, 10, 14, 1400);
        """
    )

    def run(conn, sql, params):
        return pd.read_sql_query(sql, conn, params=params)

    queries = Queries(run, connection, schema="")
    products = pd.read_sql_query("SELECT * FROM Products", connection)
    stores = pd.read_sql_query("SELECT * FROM Stores", connection)
    resolver = EntityResolver(products, stores)
    sessions = SessionMemory()

    def parse(question, _llm, context=""):
        intent = Intent(
            capability="sales_trend",
            metrics=["sales_revenue"],
            filters=Filters(),
        )
        return ParsedIntent(
            intent=_normalise(intent, question),
            usage=Usage(calls=0),
            repaired=False,
        )

    monkeypatch.setattr("app.orchestrator.parse_intent", parse)
    assistant = Orchestrator(
        llm=object(),
        queries=queries,
        resolver=resolver,
        rules=Rules(),
        session_memory=sessions,
    )
    return assistant, sessions


@pytest.mark.parametrize(
    "question",
    [
        "How did Apple sales revenue change from August to September 2026?",
        "Show the change in Apple revenue from August to September 2026.",
    ],
)
def test_single_brand_calendar_period_comparison_returns_calculated_answer(
    sales_assistant, question
):
    assistant, _ = sales_assistant

    response = assistant.handle(
        question,
        "apple-period",
        UserContext(persona="Leadership", authorized_store_ids=(10, 11)),
    )

    assert response.status == "ok"
    assert response.intent.filters.brands == ["Apple"]
    assert response.intent.metrics == ["sales_revenue"]
    assert response.intent.time.month == 9
    assert response.intent.time.comparison_month == 8
    assert response.result_counts["sales_revenue_total"] == 1850
    assert response.data["revenue"].sum() == 1850
    assert response.data["revenue_prev"].sum() == 1250
    assert "INR 1,250.00 in August 2026" in response.narrative
    assert "INR 1,850.00 in September 2026" in response.narrative
    assert "INR 600.00 (+48.0%)" in response.narrative
    assert response.params == [
        ["2026-08-31", "2026-09-30", 1, 2, 10, 11],
        ["2026-07-31", "2026-08-31", 1, 2, 10, 11],
    ]


def test_period_comparison_retains_resolved_city_and_authorized_store_scope(
    sales_assistant,
):
    assistant, _ = sales_assistant

    response = assistant.handle(
        "How did Apple sales revenue change from August to September 2026 in Chennai?",
        "apple-city-period",
        UserContext(persona="Leadership", authorized_store_ids=(10,)),
    )

    assert response.status == "ok"
    assert response.intent.filters.cities == ["Chennai"]
    assert response.result_counts["sales_revenue_total"] == 1500
    assert response.data["revenue_prev"].sum() == 1000
    assert response.data["revenue"].sum() == 1500
    assert response.params == [
        ["2026-08-31", "2026-09-30", 1, 2, 10],
        ["2026-07-31", "2026-08-31", 1, 2, 10],
    ]


def test_product_period_comparison_preserves_product_filter_and_evidence(
    sales_assistant,
):
    assistant, _ = sales_assistant

    response = assistant.handle(
        "How did Apple Device One sales revenue change from August to September 2026?",
        "product-period",
        UserContext(persona="Leadership", authorized_store_ids=(10, 11)),
    )

    assert response.status == "ok"
    assert response.intent.filters.products == ["Apple Device One"]
    assert response.data["revenue_prev"].sum() == 1000
    assert response.data["revenue"].sum() == 1500
    assert "INR 1,000.00 in August 2026" in response.narrative
    assert "INR 1,500.00 in September 2026" in response.narrative


def test_category_period_comparison_uses_same_sql_totals_for_answer_and_table(
    sales_assistant,
):
    assistant, _ = sales_assistant

    response = assistant.handle(
        "Compare sales revenue by category from August to September 2026.",
        "category-period",
        UserContext(persona="Leadership", authorized_store_ids=(10, 11)),
    )

    assert response.status == "ok"
    assert response.intent.group_by == "category"
    assert response.intent.time.comparison_month == 8
    rows = response.data.set_index("category")
    assert rows.loc["Smartphones", "revenue_prev"] == 1800
    assert rows.loc["Smartphones", "revenue"] == 2700
    assert rows.loc["Audio", "revenue_prev"] == 250
    assert rows.loc["Audio", "revenue"] == 350
    assert "Across category groups, revenue changed from INR 2,050.00 to INR 3,050.00" in response.narrative
    assert response.result_counts["sales_revenue_total"] == 3050
    assert validate_narrative_evidence(
        response.intent, response.data, response.narrative
    ) == []
    assert response.visualization is not None


def test_category_period_comparison_natural_language_variant_uses_same_totals(
    sales_assistant,
):
    assistant, _ = sales_assistant

    response = assistant.handle(
        "How did sales revenue by category change from August to September 2026?",
        "category-period-variant",
        UserContext(persona="Leadership", authorized_store_ids=(10, 11)),
    )

    assert response.status == "ok"
    assert response.intent.group_by == "category"
    assert response.data["revenue_prev"].sum() == 2050
    assert response.data["revenue"].sum() == 3050
    assert "INR 2,050.00" in response.narrative
    assert "INR 3,050.00" in response.narrative


@pytest.mark.parametrize(
    "question",
    [
        "Compare Apple and Samsung sales revenue in September 2026.",
        "How does Apple revenue compare with Samsung in September 2026?",
    ],
)
def test_brand_versus_brand_comparison_is_catalog_scoped_and_calculated(
    sales_assistant, question
):
    assistant, _ = sales_assistant

    response = assistant.handle(
        question,
        "brand-comparison",
        UserContext(persona="Leadership", authorized_store_ids=(10, 11)),
    )

    assert response.status == "ok"
    assert response.intent.group_by == "brand"
    assert response.intent.filters.brands == ["Apple", "Samsung"]
    revenues = response.data.set_index("brand")["revenue"].to_dict()
    assert revenues == {"Apple": 1850, "Samsung": 1200}
    assert "Apple" in response.narrative and "Samsung" in response.narrative
    assert response.visualization is not None


def test_follow_up_period_change_keeps_prior_brand_and_updates_both_periods(
    sales_assistant,
):
    assistant, _ = sales_assistant

    first = assistant.handle(
        "How did Apple sales revenue change from August to September 2026?",
        "period-follow-up",
        UserContext(persona="Leadership", authorized_store_ids=(10, 11)),
    )
    second = assistant.handle(
        "What about revenue from September to October 2026?",
        "period-follow-up",
        UserContext(persona="Leadership", authorized_store_ids=(10, 11)),
    )

    assert first.status == "ok"
    assert second.status == "ok"
    assert second.intent.filters.brands == ["Apple"]
    assert second.intent.uses_previous_result is True
    assert second.intent.time.month == 10
    assert second.intent.time.comparison_month == 9
    assert second.data["revenue_prev"].sum() == 1850
    assert second.data["revenue"].sum() == 2250
    assert "INR 1,850.00" in second.narrative
    assert "INR 2,250.00" in second.narrative


def test_comparison_without_a_supported_dimension_or_period_remains_rejected(
    sales_assistant,
):
    assistant, _ = sales_assistant

    response = assistant.handle(
        "Compare sales revenue.",
        "unsupported-comparison",
        UserContext(persona="Leadership", authorized_store_ids=(10, 11)),
    )

    assert response.status == "out_of_scope"
    assert "comparison dimension" in response.error
