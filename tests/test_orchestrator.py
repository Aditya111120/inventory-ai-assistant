from __future__ import annotations

from datetime import date

import pandas as pd
import pytest

from app.calc.metrics import Rules
from app.context import UserContext
from app.dispatch import DispatchResult
from app.llm.client import Usage
from app.llm.schema import Filters, Intent, Sort, SubQuestion, TimeWindow
from app.orchestrator import Orchestrator
from app.orchestrator import _presentation, _explicitly_requests_multiple_capabilities, _calculation_payload, _leadership_defaults, _catalog_brand_comparison, _apply_followup_context, _has_explicit_time, _has_unsupported_inventory_sales_tradeoff, _dispatch_result_contract_issue
from app.session import SessionMemory


class FakeLLM:
    def __init__(self):
        self.calls = []

    def complete_json(self, messages, name, schema):
        self.calls.append({"messages": messages, "name": name, "schema": schema})
        raise AssertionError("parse_intent should be monkeypatched in these unit tests")


class FakeQueries:
    def __init__(self):
        self.as_of_calls = 0

    def as_of(self):
        self.as_of_calls += 1
        return date(2026, 9, 30)


class FakeResolution:
    def __init__(
        self,
        *,
        product_ids=None,
        store_ids=None,
        unresolved=None,
        empty_reason="",
        explanations=None,
    ):
        self.product_ids = product_ids
        self.store_ids = store_ids
        self.unresolved = unresolved or []
        self.empty_reason = empty_reason
        self.explanations = explanations or []

    @property
    def ok(self):
        return not self.unresolved

    @property
    def empty(self):
        return self.product_ids == [] or self.store_ids == []

    def explain(self):
        return self.explanations

    def clarification_question(self):
        return "Please clarify the requested entity."


class FakeResolver:
    def __init__(self, resolution):
        self.resolution = resolution
        self.calls = []

    def resolve(self, filters):
        self.calls.append(filters)
        return self.resolution

    def catalog_brand_mentions(self, text):
        labels = ["Apple", "Samsung"]
        return [label for label in labels if label.casefold() in text.casefold()]


class FakeNarratorResult:
    def __init__(
        self,
        text="Test laptop has 32.45 days of inventory coverage.",
    ):
        self.text = text
        self.verified = True
        self.regenerated = False
        self.used_fallback = False
        self.usage = Usage(
            prompt_tokens=20,
            completion_tokens=10,
            total_tokens=30,
        )


class FakeNarrator:
    def __init__(self, fail=False):
        self.calls = []
        self.fail = fail

    def generate(self, **kwargs):
        self.calls.append(kwargs)
        if self.fail:
            raise RuntimeError("narrator failed")
        return FakeNarratorResult()


def make_intent(
    capability,
    *,
    uses_previous_result=False,
    clarification_question=None,
):
    return Intent(
        capability=capability,
        metrics={
            "inventory_visibility": ["quantity"],
            "sales_trend": ["sales_units"],
            "stockout_risk": ["stockout_risk"],
            "what_if": ["days_of_inventory"],
            "inventory_health": ["stock_status"],
            "executive_summary": ["quantity", "inventory_value"],
        }.get(capability, []),
        filters=Filters(),
        group_by=None,
        time=TimeWindow(),
        threshold=None,
        sort=None,
        limit=None,
        scenario=None,
        uses_previous_result=uses_previous_result,
        clarification_question=clarification_question,
    )


def test_follow_up_highest_sales_uses_previous_result():
    intent = Intent(
        capability="sales_trend",
        metrics=["sales_units"],
        filters=Filters(),
        group_by="product",
        time=TimeWindow(),
        threshold=None,
        sort=Sort(by="sales_units", direction="desc"),
        limit=1,
        scenario=None,
        uses_previous_result=True,
        clarification_question=None,
    )

    assert intent.capability == "sales_trend"
    assert intent.metrics == ["sales_units"]
    assert intent.group_by == "product"
    assert intent.sort.by == "sales_units"
    assert intent.sort.direction == "desc"
    assert intent.limit == 1
    assert intent.uses_previous_result is True


def test_leadership_category_value_question_gets_deterministic_grouping():
    intent = make_intent("inventory_visibility")
    result = _leadership_defaults(
        "Which category has the highest inventory value?",
        intent,
        UserContext(persona="Leadership"),
    )
    assert result.group_by == "category"
    assert result.question_kind == "ranking"
    assert result.metrics == ["inventory_value"]
    assert result.sort.by == "inventory_value"


def test_leadership_sales_revenue_ranking_gets_requested_group_and_metric():
    intent = make_intent("sales_trend")
    result = _leadership_defaults(
        "Rank all stores by sales revenue",
        intent,
        UserContext(persona="Leadership"),
    )
    assert result.group_by == "store"
    assert result.metrics == ["sales_revenue"]
    assert result.sort.by == "sales_revenue"


@pytest.mark.parametrize(("question", "capability", "group", "metric"), [
    ("Show historical inventory trend by category", "historical_analysis", "category", "quantity"),
    ("Give me an executive summary", "executive_summary", "category", "quantity"),
    ("Compare brands by sales revenue", "sales_trend", "brand", "sales_revenue"),
    ("Compare cities by inventory value", "inventory_visibility", "city", "inventory_value"),
])
def test_leadership_capability_contract_is_deterministic(question, capability, group, metric):
    result = _leadership_defaults(question, make_intent(capability), UserContext(persona="Leadership"))
    assert result.capability == capability
    assert result.group_by == group
    assert metric in result.metrics


def test_leadership_metric_ambiguous_comparison_remains_without_metric_for_clarification():
    result = _leadership_defaults("Compare categories", make_intent("inventory_visibility"), UserContext(persona="Leadership"))
    assert result.question_kind == "comparison"
    assert result.group_by == "category"
    assert result.metrics == []


def test_generic_sales_brand_comparison_does_not_default_to_units():
    result = _leadership_defaults(
        "Compare Apple and Samsung sales",
        make_intent("sales_trend"),
        UserContext(persona="Leadership"),
    )
    assert result.capability == "sales_trend"
    assert result.question_kind == "comparison"
    assert result.metrics == []


def test_ambiguous_store_performance_requests_metric_clarification():
    result = _leadership_defaults(
        "Which store performed best?",
        make_intent("inventory_visibility"),
        UserContext(persona="Leadership"),
    )
    assert result.question_kind == "comparison"
    assert result.group_by == "store"
    assert result.metrics == []


def test_catalog_brand_comparison_replaces_product_grain_with_brand_grain():
    intent = Intent(
        capability="sales_trend", question_kind="comparison", metrics=["sales_revenue"],
        filters=Filters(products=["Apple", "Samsung"]), group_by="product",
    )
    result = _catalog_brand_comparison(
        "Compare Apple and Samsung sales revenue in September 2026.",
        intent,
        FakeResolver(FakeResolution()),
    )
    assert result.group_by == "brand"
    assert result.filters.brands == ["Apple", "Samsung"]
    assert result.filters.products == []
    assert result.comparison_subject is None


def test_entity_replacement_followup_preserves_metric_capability_and_period():
    previous = SessionMemory().get("replacement-followup")
    previous.last_capability = "sales_trend"
    previous.last_metric = "sales_revenue"
    previous.last_time = TimeWindow(kind="month", month=9, year=2026).model_dump()
    previous.last_store_ids = [11, 12]
    parsed = Intent(
        capability="inventory_visibility", metrics=["quantity"],
        filters=Filters(brands=["Apple"]), time=TimeWindow(),
    )

    result = _apply_followup_context("Now show Apple instead", parsed, previous)

    assert result.capability == "sales_trend"
    assert result.metrics == ["sales_revenue"]
    assert result.filters.brands == ["Apple"]
    assert result.time.kind == "month" and (result.time.month, result.time.year) == (9, 2026)
    assert result.uses_previous_result is True


def test_independent_stockout_request_does_not_inherit_historical_period():
    previous = SessionMemory().get("independent-stockout")
    previous.last_capability = "inventory_visibility"
    previous.last_time = TimeWindow(kind="month", month=6, year=2026).model_dump()
    parsed = Intent(
        capability="stockout_risk", metrics=["stockout_risk"],
        filters=Filters(), time=TimeWindow(kind="month", month=6, year=2026),
    )

    result = _apply_followup_context("Show stockout risk", parsed, previous)

    assert result.capability == "stockout_risk"
    assert result.time.kind == "none"


@pytest.mark.parametrize("period", ["September", "Sep", "September 2026", "2026-09-14"])
def test_explicit_period_is_not_mistaken_for_stale_context(period):
    assert _has_explicit_time(f"Show sales in {period}")


def test_stockout_risk_and_inventory_period_comparison_keep_requested_capability():
    leadership = UserContext(persona="Leadership")
    risk_intent = make_intent("inventory_visibility").model_copy(
        update={"filters": Filters(brands=["Samsung"], cities=["Chennai"])}
    )
    risk = _leadership_defaults(
        "Which five Samsung products in Chennai have the highest stockout risk and days of inventory coverage?",
        risk_intent, leadership,
    )
    assert risk.capability == "stockout_risk"
    assert risk.question_kind == "ranking" and risk.limit == 5
    assert risk.sort.by == "stockout_risk" and risk.sort.direction == "desc"
    assert risk.metrics == ["stockout_risk", "days_of_inventory"]
    assert risk.filters.brands == ["Samsung"]
    assert risk.filters.cities == ["Chennai"]
    default_risk = _leadership_defaults(
        "Show products with the highest stockout risk",
        make_intent("inventory_visibility"), leadership,
    )
    assert default_risk.limit == 5

    historical = Intent(
        capability="inventory_visibility", question_kind="comparison",
        metrics=["quantity"], filters=Filters(brands=["Samsung"], cities=["Chennai"]),
        time=TimeWindow(kind="month", month=7, year=2026, comparison_month=6, comparison_year=2026),
    )
    normalized = _leadership_defaults(
        "Compare Samsung inventory in Chennai between June 2026 and July 2026. How many units changed?",
        historical, leadership,
    )
    assert normalized.capability == "historical_analysis"
    assert normalized.filters.brands == ["Samsung"] and normalized.filters.cities == ["Chennai"]
    assert normalized.group_by == "product"
    assert normalized.time.comparison_month == 6 and normalized.time.month == 7


def test_stockout_ranking_presentation_keeps_position_identity_and_metrics():
    intent = Intent(
        capability="stockout_risk", question_kind="ranking",
        metrics=["stockout_risk", "days_of_inventory"], filters=Filters(brands=["Samsung"], cities=["Chennai"]),
        group_by="product", sort=Sort(by="stockout_risk", direction="desc"), limit=5,
    )
    frame = pd.DataFrame([{
        "product_id": 8, "product_name": "Catalog phone", "store_id": 4,
        "store_name": "Central", "city": "Chennai", "quantity_on_hand": 2,
        "avg_daily_sales": 1.0, "days_of_inventory": 2.0, "lead_time_days": 5,
        "stockout_risk_v1": "High", "demand_status": "Recent demand",
    }])

    view, display = _presentation(frame, intent, "Which products have highest stockout risk?")

    assert {"product_name", "store_name", "city", "days_of_inventory", "stockout_risk_v1"}.issubset(view.columns)
    assert "stockout_risk" not in view.columns
    assert view.iloc[0]["store_name"] == "Central"
    assert display["result_counts"]["matching_result_rows"] == 1


def test_result_contract_rejects_stockout_rows_without_risk_or_coverage():
    intent = Intent(capability="stockout_risk", metrics=["stockout_risk"])
    frame = pd.DataFrame([{
        "product_id": 1, "product_name": "Catalog product", "store_id": 1,
        "store_name": "Catalog store", "quantity_on_hand": 3,
    }])

    issue = _dispatch_result_contract_issue(intent, frame)

    assert issue is not None
    assert "risk classification" in issue


def test_inventory_value_low_sales_tradeoff_has_no_implicit_formula():
    assert _has_unsupported_inventory_sales_tradeoff(
        "Which five products have the highest inventory value but the lowest sales?"
    )
    assert not _has_unsupported_inventory_sales_tradeoff(
        "Which products have the highest inventory value?"
    )


def test_unsupported_inventory_sales_tradeoff_stops_before_query(monkeypatch, base_components):
    intent = make_intent("inventory_visibility")
    monkeypatch.setattr(
        "app.orchestrator.parse_intent",
        lambda question, llm, context="": parsed(intent),
    )
    orchestrator = Orchestrator(
        llm=base_components["llm"], queries=base_components["queries"],
        resolver=FakeResolver(base_components["resolution"]), rules=base_components["rules"],
        session_memory=base_components["sessions"],
        dispatch_fn=lambda **kwargs: pytest.fail("unsupported ranking must not dispatch"),
    )

    response = orchestrator.handle(
        "Which five products have the highest inventory value but the lowest sales in September 2026 across all stores?",
        "unsupported-cross-metric",
        UserContext(persona="Leadership"),
    )

    assert response.status == "out_of_scope"
    assert "no approved V1 formula" in response.error
    assert base_components["queries"].as_of_calls == 0


def test_leadership_particular_store_asks_for_a_catalog_store():
    resolver = FakeResolver(FakeResolution())
    resolver.store_options = lambda authorized: [
        {"store_name": "North", "city": "Pune"},
        {"store_name": "Central", "city": "Delhi"},
    ]
    orchestrator = Orchestrator(
        llm=FakeLLM(), queries=FakeQueries(), resolver=resolver, rules=Rules(),
        session_memory=SessionMemory(),
    )
    response = orchestrator._location_clarification(
        question="Show inventory at a particular store",
        intent=make_intent("inventory_visibility"),
        resolution=FakeResolution(), session_id="leadership-store-choice",
        previous_state=SessionMemory().get("leadership-store-choice"),
        user_context=UserContext(persona="Leadership", authorized_store_ids=(1, 2)),
        usage=Usage(calls=0),
    )
    assert response.status == "clarification"
    assert response.clarification_prompt == "Which store would you like to check?"
    assert {option["label"] for option in response.clarification_options} == {"North", "Central"}


def test_executive_summary_presentation_uses_aggregated_categories_and_scalar_kpis():
    frame = pd.DataFrame([
        {"category": "Laptops", "quantity_on_hand": 100, "inventory_value": 5000, "units": 40, "revenue": 8000, "snapshot_date": "2026-09-30"},
        {"category": "Audio", "quantity_on_hand": 50, "inventory_value": 2000, "units": 20, "revenue": 3000, "snapshot_date": "2026-09-30"},
    ])
    frame.attrs["result_counts"] = {"matching_result_rows": 200, "distinct_products": 150, "stores": 20, "quantity_on_hand_total": 150, "inventory_value_total": 7000, "sales_units_total": 60, "sales_revenue_total": 11000}
    intent = Intent(capability="executive_summary", metrics=["quantity", "inventory_value"], filters=Filters())
    view, display = _presentation(frame, intent, "Executive summary")
    assert len(view) == 2
    assert display["result_counts"]["matching_result_rows"] == 200
    assert {item["key"] for item in display["kpi_metrics"]} >= {"quantity_on_hand_total", "inventory_value_total", "sales_units_total", "sales_revenue_total"}


def test_ranking_presentation_keeps_evidence_but_starts_with_winner():
    intent = Intent(
        capability="inventory_visibility", metrics=["quantity"], filters=Filters(),
        sort=Sort(by="quantity", direction="asc"), question_kind="ranking",
    )
    view, display = _presentation(pd.DataFrame([
        {"product_name": "A", "quantity_on_hand": 10},
        {"product_name": "B", "quantity_on_hand": 20},
    ]), intent)
    assert len(view) == 2  # retained semantic evidence for View all
    assert display["type"] == "ranking"
    assert display["initial_display_count"] == 1
    assert display["has_more"] is True


def test_ranking_presentation_retains_grouping_dimension_and_metric():
    intent = Intent(
        capability="inventory_visibility",
        metrics=["inventory_value"],
        filters=Filters(),
        group_by="category",
        question_kind="ranking",
        sort=Sort(by="inventory_value", direction="desc"),
    )
    frame = pd.DataFrame([
        {"category": "Laptops", "inventory_value": 900.0},
        {"category": "Smartphones", "inventory_value": 700.0},
    ])

    view, display = _presentation(frame, intent)

    assert list(view.columns) == ["category", "inventory_value"]
    assert view.to_dict(orient="records") == [
        {"category": "Laptops", "inventory_value": 900.0},
        {"category": "Smartphones", "inventory_value": 700.0},
    ]
    assert display["type"] == "ranking"


@pytest.mark.parametrize(
    ("group_by", "group_column", "group_value"),
    [
        ("category", "category", "Laptops"),
        ("city", "city", "Chennai"),
    ],
)
def test_sales_presentation_retains_category_and_city_grouping(
    group_by, group_column, group_value
):
    intent = make_intent("sales_trend")
    intent.group_by = group_by
    frame = pd.DataFrame([{
        group_column: group_value,
        "brand": "Example",
        "store_name": "Central",
        "city": "Chennai",
        "region": "South",
        "product_name": "Example Product",
        "units": 24,
        "revenue": 1200.0,
        "units_prev": 20,
        "revenue_prev": 1000.0,
        "units_change": 4,
        "revenue_change": 200.0,
        "growth_pct": 20.0,
        "revenue_growth_pct": 20.0,
    }])

    view, display = _presentation(frame, intent)

    assert group_column in view.columns
    assert view.iloc[0][group_column] == group_value
    assert {
        "units", "revenue", "units_prev", "revenue_prev",
        "units_change", "revenue_change", "growth_pct",
        "revenue_growth_pct",
    }.issubset(view.columns)
    assert display["type"] == "sales"


def test_stockout_presentation_uses_business_columns_and_location():
    intent = make_intent("stockout_risk")
    frame = pd.DataFrame([{
        "product_id": 1,
        "sku": "SKU-0001",
        "product_name": "iPhone 16",
        "subcategory": "Smartphone",
        "store_id": 10,
        "store_name": "Chennai Central",
        "city": "Chennai",
        "region": "South",
        "quantity_on_hand": 12,
        "units_sold_window": 60,
        "avg_daily_sales": 2.0,
        "days_of_inventory": 6.0,
        "lead_time_days": 10,
        "stockout_risk": "High",
    }])

    view, _ = _presentation(frame, intent)

    assert {"product_name", "subcategory", "store_name", "city", "quantity_on_hand", "avg_daily_sales", "days_of_inventory", "lead_time_days"}.issubset(view.columns)
    assert not {"product_id", "sku", "store_id", "region", "stockout_risk"}.intersection(view.columns)


def test_inventory_narrative_does_not_add_unrequested_stockout_claim(monkeypatch):
    intent = make_intent("inventory_visibility")
    intent.metrics = ["quantity"]
    result = DispatchResult(
        df=pd.DataFrame([{
            "product_id": 1,
            "product_name": "iPhone 16",
            "quantity_on_hand": 25,
            "inventory_value": 1000,
            "stockout_risk": "High",
            "days_of_inventory": 6.0,
        }]),
        sql=[],
        params=[],
        notes=[],
        basis="Actual",
    )
    monkeypatch.setattr(
        "app.orchestrator.parse_intent",
        lambda question, llm, context="": parsed(intent),
    )
    orchestrator = Orchestrator(
        llm=FakeLLM(),
        queries=FakeQueries(),
        resolver=FakeResolver(FakeResolution()),
        rules=Rules(),
        session_memory=SessionMemory(),
        dispatch_fn=lambda **kwargs: result,
    )
    response = orchestrator.handle("Show iPhone 16 inventory", "session-1")

    assert "25 units are available" in response.narrative
    assert "stockout" not in response.narrative.casefold()


def test_single_capability_question_cannot_become_compound_from_parser_noise():
    assert not _explicitly_requests_multiple_capabilities("Show iPhone 16 inventory")
    assert _explicitly_requests_multiple_capabilities(
        "Show iPhone 16 inventory and stockout risk"
    )


def test_presentation_exposes_scope_counts_for_location_results():
    intent = make_intent("inventory_visibility")
    frame = pd.DataFrame([
        {"product_id": 1, "store_id": 10, "store_name": "Central", "city": "Chennai", "quantity_on_hand": 4},
        {"product_id": 1, "store_id": 11, "store_name": "North", "city": "Chennai", "quantity_on_hand": 6},
    ])
    frame.attrs["result_counts"] = {
        "matching_result_rows": 2,
        "distinct_products": 1,
        "product_store_positions": 2,
        "stores": 2,
    }
    _, display = _presentation(frame, intent)
    assert display["result_counts"]["distinct_products"] == 1
    assert display["result_counts"]["product_store_positions"] == 2
    assert display["result_counts"]["stores"] == 2


def test_trust_panel_prefers_v1_status_and_risk_labels():
    intent = make_intent("stockout_risk")
    frame = pd.DataFrame([{
        "quantity_on_hand": 0,
        "reorder_level": 5,
        "max_level": 15,
        "days_of_inventory": None,
        "lead_time_days": 10,
        "stock_status": "Low",
        "inventory_status": "Out of Stock",
        "stockout_risk": "No demand",
        "stockout_risk_v1": "Out of Stock",
    }])
    metrics = _calculation_payload(intent, frame, Rules(), "Actual")
    by_metric = {item["metric"]: item for item in metrics}
    assert by_metric["inventory_status"]["result"] == "Out of Stock"
    assert by_metric["stockout_risk_v1"]["result"] == "Out of Stock"


def test_inventory_visibility_shows_compact_preview_unless_detail_requested():
    intent = make_intent("inventory_visibility")
    frame = pd.DataFrame([
        {"product_id": i, "product_name": f"Laptop {i}", "store_id": 1, "store_name": "Central", "city": "Chennai", "quantity_on_hand": 2}
        for i in range(1, 9)
    ])
    _, summary = _presentation(frame, intent, "Show laptop inventory in Chennai")
    _, detail = _presentation(frame, intent, "Show detailed inventory records for laptops in Chennai")
    assert summary["summary_only"] is False
    assert summary["initial_display_count"] == 5
    assert detail["summary_only"] is False
    assert detail["initial_display_count"] == 5
    assert detail["has_more"] is True


def test_presentation_omits_all_null_subcategory_by_default():
    intent = make_intent("inventory_visibility")
    frame = pd.DataFrame([{"product_id": 1, "product_name": "Unclassified", "subcategory": None, "quantity_on_hand": 3}])
    view, _ = _presentation(frame, intent)
    assert "subcategory" not in view.columns


def test_presentation_keeps_mixed_subcategories_and_labels_missing():
    intent = make_intent("inventory_visibility")
    frame = pd.DataFrame([
        {"product_id": 1, "product_name": "Speaker", "subcategory": "Speaker", "quantity_on_hand": 3},
        {"product_id": 2, "product_name": "Unknown audio", "subcategory": None, "quantity_on_hand": 4},
    ])
    view, _ = _presentation(frame, intent)
    assert list(view["subcategory"]) == ["Speaker", "Unclassified"]


def test_presentation_shows_all_null_subcategory_when_explicitly_requested():
    intent = make_intent("inventory_visibility")
    intent.filters = Filters(subcategories=["without subcategory"])
    frame = pd.DataFrame([{"product_id": 1, "product_name": "Unknown audio", "subcategory": None, "quantity_on_hand": 4}])
    view, _ = _presentation(frame, intent)
    assert "subcategory" in view.columns
    assert view.iloc[0]["subcategory"] == "Unclassified"


def parsed(intent):
    from app.llm.intent import ParsedIntent

    return ParsedIntent(
        intent=intent,
        usage=Usage(prompt_tokens=10, completion_tokens=5, total_tokens=15),
        repaired=False,
    )


def make_dispatch_result():
    df = pd.DataFrame(
        [
            {
                "product_id": 1,
                "product_name": "Test Laptop",
                "quantity_on_hand": 38,
                "inventory_value": 3800,
                "units": 12,
                "revenue": 2400,
                "days_of_inventory": 6.1,
                "stockout_risk_v1": "Medium",
                "stockout_risk": "Medium",
                "inventory_status": "Healthy",
                "lead_time_days": 5,
                "inventory_snapshot_date": "2026-09-30",
                "avg_daily_sales": 2,
                "units_sold_window": 60,
                "actual_days_of_inventory": 19,
                "simulated_days_of_inventory": 6.1,
                "basis": "Actual",
            }
        ]
    )

    return DispatchResult(
        df=df,
        sql=["SELECT ..."],
        params=[["2026-08-31", "2026-09-30"]],
        notes=["test query"],
        basis="Actual",
    )


@pytest.fixture
def base_components():
    return {
        "llm": FakeLLM(),
        "queries": FakeQueries(),
        "rules": Rules(),
        "sessions": SessionMemory(),
        "resolution": FakeResolution(
            product_ids=[1],
            store_ids=[10],
            explanations=["'Lenovo' -> Lenovo: 12 products"],
        ),
    }


def test_successful_inventory_request(monkeypatch, base_components):
    intent = make_intent("inventory_visibility")
    monkeypatch.setattr(
        "app.orchestrator.parse_intent",
        lambda question, llm, context="": parsed(intent),
    )
    resolver = FakeResolver(base_components["resolution"])
    dispatch_calls = []

    def fake_dispatch(**kwargs):
        dispatch_calls.append(kwargs)
        return make_dispatch_result()

    orchestrator = Orchestrator(
        llm=base_components["llm"],
        queries=base_components["queries"],
        resolver=resolver,
        rules=base_components["rules"],
        session_memory=base_components["sessions"],
        dispatch_fn=fake_dispatch,
    )
    response = orchestrator.handle("Show me Lenovo inventory", "session-1")

    assert response.status == "ok"
    assert response.row_count == 1
    assert response.basis == "Actual"
    assert response.sql == ["SELECT ..."]
    assert len(dispatch_calls) == 1
    assert dispatch_calls[0]["product_ids"] == [1]
    assert dispatch_calls[0]["store_ids"] == [10]

    state = base_components["sessions"].get("session-1")
    assert state.last_product_ids == [1]
    assert state.last_store_ids == [10]
    assert "capability=inventory_visibility" in state.last_intent_summary


def test_clarification_intent_does_not_resolve_or_dispatch(monkeypatch, base_components):
    intent = make_intent("clarification", clarification_question="Which store do you mean?")
    monkeypatch.setattr(
        "app.orchestrator.parse_intent",
        lambda question, llm, context="": parsed(intent),
    )
    resolver = FakeResolver(base_components["resolution"])
    dispatch_called = False

    def fake_dispatch(**kwargs):
        nonlocal dispatch_called
        dispatch_called = True
        return make_dispatch_result()

    orchestrator = Orchestrator(
        llm=base_components["llm"],
        queries=base_components["queries"],
        resolver=resolver,
        rules=base_components["rules"],
        session_memory=base_components["sessions"],
        dispatch_fn=fake_dispatch,
    )
    response = orchestrator.handle("Show me something", "session-1")

    assert response.status == "clarification"
    assert response.error == "Which store do you mean?"
    assert resolver.calls == []
    assert dispatch_called is False


def test_compare_categories_returns_metric_clarification(monkeypatch, base_components):
    intent = make_intent("inventory_visibility")
    monkeypatch.setattr(
        "app.orchestrator.parse_intent",
        lambda question, llm, context="": parsed(intent),
    )
    orchestrator = Orchestrator(
        llm=base_components["llm"],
        queries=base_components["queries"],
        resolver=FakeResolver(base_components["resolution"]),
        rules=base_components["rules"],
        session_memory=base_components["sessions"],
        dispatch_fn=lambda **kwargs: pytest.fail("clarification must not dispatch"),
    )

    response = orchestrator.handle(
        "Compare categories",
        "leadership-compare-categories",
        UserContext(persona="Leadership"),
    )

    assert response.status == "clarification"
    assert response.clarification_kind == "comparison_metric"
    assert response.intent.group_by == "category"
    assert response.intent.metrics == []
    assert response.interpretation == []
    assert "compare across category" in response.error
    assert len(response.clarification_options) == 4


def test_compare_categories_metric_selection_resumes_pending_intent(monkeypatch, base_components):
    intent = make_intent("inventory_visibility")
    monkeypatch.setattr(
        "app.orchestrator.parse_intent",
        lambda question, llm, context="": parsed(intent),
    )
    dispatched = []

    def fake_dispatch(**kwargs):
        dispatched.append(kwargs["intent"])
        result = make_dispatch_result()
        result.df = pd.DataFrame([
            {"category": "Laptops", "units": 10, "revenue": 100},
            {"category": "Audio", "units": 8, "revenue": 80},
        ])
        return result

    orchestrator = Orchestrator(
        llm=base_components["llm"],
        queries=base_components["queries"],
        resolver=FakeResolver(base_components["resolution"]),
        rules=base_components["rules"],
        session_memory=base_components["sessions"],
        dispatch_fn=fake_dispatch,
    )
    first = orchestrator.handle("Compare categories", "compare-resume", UserContext(persona="Leadership"))
    selected = next(option for option in first.clarification_options if option["label"] == "Sales units")
    resumed = orchestrator.handle(selected["token"], "compare-resume", UserContext(persona="Leadership"))

    assert first.status == "clarification"
    assert resumed.status == "ok", (resumed.intent.model_dump(), resumed.clarification_kind, resumed.error)
    assert len(dispatched) == 1
    assert dispatched[0].question_kind == "comparison"
    assert dispatched[0].group_by == "category"
    assert dispatched[0].metrics == ["sales_units"]


def test_store_performance_asks_metric_then_ranks_authorized_stores(
    monkeypatch, base_components
):
    intent = make_intent("inventory_visibility")
    monkeypatch.setattr(
        "app.orchestrator.parse_intent",
        lambda question, llm, context="": parsed(intent),
    )
    resolution = FakeResolution(product_ids=[1], store_ids=[10, 11])
    resolver = FakeResolver(resolution)
    dispatched = []

    def fake_dispatch(**kwargs):
        dispatched.append(kwargs)
        result = make_dispatch_result()
        result.df = pd.DataFrame([
            {"store_id": 10, "store_name": "Central", "city": "Chennai", "units": 100, "revenue": 5000},
            {"store_id": 11, "store_name": "North", "city": "Delhi", "units": 80, "revenue": 4000},
        ])
        return result

    orchestrator = Orchestrator(
        llm=base_components["llm"],
        queries=base_components["queries"],
        resolver=resolver,
        rules=base_components["rules"],
        session_memory=base_components["sessions"],
        dispatch_fn=fake_dispatch,
    )
    first = orchestrator.handle(
        "Which store is performing best?",
        "store-performance",
        UserContext(persona="Leadership"),
    )

    assert first.status == "clarification"
    assert first.clarification_prompt == "How would you like to measure store performance?"
    assert {option["label"] for option in first.clarification_options} == {
        "Sales revenue", "Units sold", "Inventory value",
    }

    selected = next(
        option for option in first.clarification_options
        if option["label"] == "Sales revenue"
    )
    resumed = orchestrator.handle(
        selected["token"],
        "store-performance",
        UserContext(persona="Leadership"),
    )

    assert resumed.status == "ok", (resumed.clarification_kind, resumed.error)
    assert len(dispatched) == 1
    assert dispatched[0]["intent"].group_by == "store"
    assert dispatched[0]["intent"].question_kind == "ranking"
    assert dispatched[0]["intent"].metrics == ["sales_revenue"]
    assert dispatched[0]["intent"].sort.by == "sales_revenue"
    assert dispatched[0]["store_ids"] == [10, 11]


def test_sales_month_clarification_lists_available_periods_and_resumes(
    monkeypatch, base_components
):
    intent = make_intent("sales_trend")
    intent.time = TimeWindow(kind="month", month=9)
    monkeypatch.setattr(
        "app.orchestrator.parse_intent",
        lambda question, llm, context="": parsed(intent),
    )
    base_components["queries"].sales_periods = lambda: [
        (2026, 9), (2025, 9), (2026, 8),
    ]
    dispatched = []

    def fake_dispatch(**kwargs):
        dispatched.append(kwargs["intent"])
        return make_dispatch_result()

    orchestrator = Orchestrator(
        llm=base_components["llm"],
        queries=base_components["queries"],
        resolver=FakeResolver(base_components["resolution"]),
        rules=base_components["rules"],
        session_memory=base_components["sessions"],
        dispatch_fn=fake_dispatch,
    )
    first = orchestrator.handle(
        "Show sales in September",
        "sales-period",
        UserContext(persona="Leadership"),
    )
    labels = {option["label"] for option in first.clarification_options}

    assert first.status == "clarification"
    assert {"Latest available", "This month", "Last month"} <= labels
    assert {"September 2026", "September 2025"} <= labels
    assert "August 2026" not in labels

    selected = next(
        option for option in first.clarification_options
        if option["label"] == "September 2026"
    )
    resumed = orchestrator.handle(
        selected["token"],
        "sales-period",
        UserContext(persona="Leadership"),
    )

    assert resumed.status == "ok"
    assert len(dispatched) == 1
    assert dispatched[0].time.kind == "month"
    assert (dispatched[0].time.month, dispatched[0].time.year) == (9, 2026)


def test_unrelated_question_does_not_inherit_pending_filters(
    monkeypatch, base_components
):
    current_intent = make_intent("inventory_visibility")
    current_intent.filters = Filters(products=["Dell"])
    monkeypatch.setattr(
        "app.orchestrator.parse_intent",
        lambda question, llm, context="": parsed(current_intent),
    )
    sessions = base_components["sessions"]
    stale_intent = make_intent("inventory_visibility")
    stale_intent.filters = Filters(products=["HP"])
    sessions.set_pending("fresh-question", {
        "id": "clar-stale",
        "kind": "entity",
        "intent": stale_intent.model_dump(),
        "options": [{"token": "clar-stale-option", "label": "HP"}],
        "field": "products",
    })
    dispatched = []

    def fake_dispatch(**kwargs):
        dispatched.append(kwargs["intent"])
        return make_dispatch_result()

    orchestrator = Orchestrator(
        llm=base_components["llm"],
        queries=base_components["queries"],
        resolver=FakeResolver(base_components["resolution"]),
        rules=base_components["rules"],
        session_memory=sessions,
        dispatch_fn=fake_dispatch,
    )
    response = orchestrator.handle("Show Dell inventory", "fresh-question")

    assert response.status == "ok"
    assert dispatched[0].filters.products == ["Dell"]
    assert sessions.pending("fresh-question") is None


def test_out_of_scope_does_not_resolve_or_dispatch(monkeypatch, base_components):
    intent = make_intent("out_of_scope")
    monkeypatch.setattr(
        "app.orchestrator.parse_intent",
        lambda question, llm, context="": parsed(intent),
    )
    resolver = FakeResolver(base_components["resolution"])
    dispatch_called = False

    def fake_dispatch(**kwargs):
        nonlocal dispatch_called
        dispatch_called = True
        return make_dispatch_result()

    orchestrator = Orchestrator(
        llm=base_components["llm"],
        queries=base_components["queries"],
        resolver=resolver,
        rules=base_components["rules"],
        session_memory=base_components["sessions"],
        dispatch_fn=fake_dispatch,
    )
    response = orchestrator.handle("Delete all inventory", "session-1")

    assert response.status == "out_of_scope"
    assert resolver.calls == []
    assert dispatch_called is False


def test_unsupported_scope_comparison_fails_closed_before_dispatch(monkeypatch, base_components):
    intent = make_intent("inventory_visibility")
    intent = intent.model_copy(update={"question_kind": "comparison", "comparison_mode": "none"})
    monkeypatch.setattr(
        "app.orchestrator.parse_intent",
        lambda question, llm, context="": parsed(intent),
    )
    resolver = FakeResolver(base_components["resolution"])
    dispatch_called = False

    def fake_dispatch(**kwargs):
        nonlocal dispatch_called
        dispatch_called = True
        return make_dispatch_result()

    orchestrator = Orchestrator(
        llm=base_components["llm"], queries=base_components["queries"],
        resolver=resolver, rules=base_components["rules"],
        session_memory=base_components["sessions"], dispatch_fn=fake_dispatch,
    )
    response = orchestrator.handle("Compare Chennai and Delhi inventory", "comparison-test")
    assert response.status == "out_of_scope"
    assert dispatch_called is False


def test_unresolved_entity_returns_clarification(monkeypatch, base_components):
    intent = make_intent("inventory_visibility")
    monkeypatch.setattr(
        "app.orchestrator.parse_intent",
        lambda question, llm, context="": parsed(intent),
    )
    resolution = FakeResolution(
        unresolved=["unknown"],
        explanations=["Could not match brand 'Zebra'."],
    )
    resolver = FakeResolver(resolution)
    dispatch_called = False

    def fake_dispatch(**kwargs):
        nonlocal dispatch_called
        dispatch_called = True
        return make_dispatch_result()

    orchestrator = Orchestrator(
        llm=base_components["llm"],
        queries=base_components["queries"],
        resolver=resolver,
        rules=base_components["rules"],
        session_memory=base_components["sessions"],
        dispatch_fn=fake_dispatch,
    )
    response = orchestrator.handle("Show Zebra inventory", "session-1")

    assert response.status == "clarification"
    assert "Please clarify" in response.error
    assert dispatch_called is False


def test_empty_entity_match_returns_no_results(monkeypatch, base_components):
    intent = make_intent("inventory_visibility")
    monkeypatch.setattr(
        "app.orchestrator.parse_intent",
        lambda question, llm, context="": parsed(intent),
    )
    resolution = FakeResolution(
        product_ids=[],
        store_ids=None,
        empty_reason="No products match all requested filters.",
        explanations=["'Dell' -> Dell: 6 products"],
    )
    resolver = FakeResolver(resolution)
    orchestrator = Orchestrator(
        llm=base_components["llm"],
        queries=base_components["queries"],
        resolver=resolver,
        rules=base_components["rules"],
        session_memory=base_components["sessions"],
        dispatch_fn=lambda **kwargs: pytest.fail(
            "dispatch must not run when resolution is empty"
        ),
    )
    response = orchestrator.handle("Show Dell tablets", "session-1")

    assert response.status == "no_results"
    assert "No products match" in response.error


def test_previous_result_ids_are_passed_for_follow_up(monkeypatch, base_components):
    sessions = SessionMemory()
    sessions.update(
        "session-1",
        intent_summary="capability=inventory_visibility",
        product_ids=[1, 2, 3],
        store_ids=[10, 11],
    )
    intent = make_intent("stockout_risk", uses_previous_result=True)
    monkeypatch.setattr(
        "app.orchestrator.parse_intent",
        lambda question, llm, context="": parsed(intent),
    )
    resolver = FakeResolver(FakeResolution(explanations=[]))
    captured = {}

    def fake_dispatch(**kwargs):
        captured.update(kwargs)
        result = make_dispatch_result()
        result.df["store_id"] = 10
        result.df["store_name"] = "Store A"
        result.df["stockout_risk_v1"] = "High"
        return result

    orchestrator = Orchestrator(
        llm=base_components["llm"],
        queries=base_components["queries"],
        resolver=resolver,
        rules=base_components["rules"],
        session_memory=sessions,
        dispatch_fn=fake_dispatch,
    )
    response = orchestrator.handle(
        "What about those products' days of inventory?", "session-1"
    )

    assert response.status == "ok"
    assert captured["prev_product_ids"] == [1, 2, 3]
    assert captured["prev_store_ids"] == [10, 11]


def test_consecutive_unrelated_question_does_not_reuse_prior_scope(
    monkeypatch,
    base_components,
):
    sessions = SessionMemory()
    sessions.update(
        "session-1",
        intent_summary="capability=inventory_visibility",
        product_ids=[1],
        store_ids=[10],
    )
    intent = make_intent("stockout_risk", uses_previous_result=True)
    monkeypatch.setattr(
        "app.orchestrator.parse_intent",
        lambda question, llm, context="": parsed(intent),
    )
    resolver = FakeResolver(
        FakeResolution(product_ids=[99], store_ids=[50], explanations=[])
    )
    captured = {}

    def fake_dispatch(**kwargs):
        captured.update(kwargs)
        result = make_dispatch_result()
        result.df["store_id"] = 50
        result.df["store_name"] = "Chennai Central"
        result.df["stockout_risk_v1"] = "Medium"
        return result

    orchestrator = Orchestrator(
        llm=base_components["llm"],
        queries=base_components["queries"],
        resolver=resolver,
        rules=base_components["rules"],
        session_memory=sessions,
        dispatch_fn=fake_dispatch,
    )
    response = orchestrator.handle(
        "Which products have stockout risk in Chennai?",
        "session-1",
    )

    assert response.status == "ok"
    assert captured["product_ids"] == [99]
    assert captured["store_ids"] == [50]
    assert captured["prev_product_ids"] is None
    assert captured["prev_store_ids"] is None


def test_unrequested_compound_intent_is_reduced_to_one_capability(
    monkeypatch,
    base_components,
):
    intent = make_intent("inventory_visibility").model_copy(
        update={
            "question_kind": "compound",
            "sub_questions": [
                SubQuestion(
                    capability="stockout_risk",
                    metrics=["stockout_risk"],
                    filters=Filters(),
                )
            ],
        }
    )
    monkeypatch.setattr(
        "app.orchestrator.parse_intent",
        lambda question, llm, context="": parsed(intent),
    )
    dispatched = []

    def fake_dispatch(**kwargs):
        dispatched.append(kwargs["intent"].capability)
        return make_dispatch_result()

    orchestrator = Orchestrator(
        llm=base_components["llm"],
        queries=base_components["queries"],
        resolver=FakeResolver(base_components["resolution"]),
        rules=base_components["rules"],
        session_memory=base_components["sessions"],
        dispatch_fn=fake_dispatch,
    )
    response = orchestrator.handle(
        "Show iPhone 16 inventory",
        "session-1",
    )

    assert response.status == "ok"
    assert response.intent.question_kind == "lookup"
    assert response.intent.sub_questions == []
    assert dispatched == ["inventory_visibility"]


def test_previous_result_is_not_used_for_new_independent_question(monkeypatch, base_components):
    sessions = SessionMemory()
    sessions.update(
        "session-1",
        intent_summary="capability=inventory_visibility",
        product_ids=[1, 2, 3],
        store_ids=[10, 11],
    )
    intent = make_intent("inventory_visibility", uses_previous_result=False)
    monkeypatch.setattr(
        "app.orchestrator.parse_intent",
        lambda question, llm, context="": parsed(intent),
    )
    resolver = FakeResolver(
        FakeResolution(product_ids=[99], store_ids=[50], explanations=[])
    )
    captured = {}

    def fake_dispatch(**kwargs):
        captured.update(kwargs)
        return make_dispatch_result()

    orchestrator = Orchestrator(
        llm=base_components["llm"],
        queries=base_components["queries"],
        resolver=resolver,
        rules=base_components["rules"],
        session_memory=sessions,
        dispatch_fn=fake_dispatch,
    )
    response = orchestrator.handle("Show me televisions", "session-1")

    assert response.status == "ok"
    assert captured["prev_product_ids"] is None
    assert captured["prev_store_ids"] is None


def test_memory_narrows_to_ids_in_final_result(monkeypatch, base_components):
    sessions = SessionMemory()

    sessions.update(
        "session-1",
        intent_summary="capability=inventory_visibility",
        product_ids=[17, 42, 67, 92, 117, 142],
        store_ids=None,
    )

    intent = make_intent(
        "sales_trend",
        uses_previous_result=True,
    )

    monkeypatch.setattr(
        "app.orchestrator.parse_intent",
        lambda question, llm, context="": parsed(intent),
    )

    resolver = FakeResolver(
        FakeResolution(
            product_ids=None,
            store_ids=None,
            explanations=[],
        )
    )

    result = DispatchResult(
        df=pd.DataFrame(
            [
                {
                    "product_id": 92,
                    "product_name": "Lenovo Laptop LEN-LAP-4",
                    "units": 229,
                }
            ]
        ),
        sql=["SELECT ..."],
        params=[[]],
        notes=[],
        basis="Actual",
    )

    orchestrator = Orchestrator(
        llm=base_components["llm"],
        queries=base_components["queries"],
        resolver=resolver,
        rules=base_components["rules"],
        session_memory=sessions,
        dispatch_fn=lambda **kwargs: result,
    )

    response = orchestrator.handle(
        "Which one has the highest sales?",
        "session-1",
    )

    assert response.status == "ok"

    state = sessions.get("session-1")

    assert state.last_product_ids == [92]


def test_orchestrator_adds_narration_and_trust_panel(
    monkeypatch,
    base_components,
):
    intent = make_intent("inventory_visibility")
    monkeypatch.setattr(
        "app.orchestrator.parse_intent",
        lambda question, llm, context="": parsed(intent),
    )
    narrator = FakeNarrator()

    orchestrator = Orchestrator(
        llm=base_components["llm"],
        queries=base_components["queries"],
        resolver=FakeResolver(base_components["resolution"]),
        rules=base_components["rules"],
        session_memory=base_components["sessions"],
        narrator=narrator,
        dispatch_fn=lambda **kwargs: make_dispatch_result(),
    )
    response = orchestrator.handle("Show me Lenovo inventory", "session-1")

    assert response.status == "ok"
    assert response.narrative
    assert "38 units" in response.narrative
    assert response.narration_verified is False
    assert response.narration_fallback is False
    assert response.trust_panel
    assert "interpretation" in response.trust_panel
    assert "query" in response.trust_panel
    assert "calculations" in response.trust_panel
    assert "rules_applied" in response.trust_panel
    assert "basis" in response.trust_panel
    assert response.trust_panel["query"]["row_count"] == 1
    assert response.trust_panel["basis"] == "Actual"
    assert len(narrator.calls) == 0


def test_orchestrator_trust_panel_marks_what_if_simulated(
    monkeypatch,
    base_components,
):
    from app.llm.schema import Scenario

    intent = make_intent("what_if")
    intent.scenario = Scenario(
        kind="demand_change_pct",
        value=20,
    )

    monkeypatch.setattr(
        "app.orchestrator.parse_intent",
        lambda question, llm, context="": parsed(intent),
    )

    result = DispatchResult(
        df=pd.DataFrame(
            [
                {
                    "product_id": 92,
                    "product_name": "Lenovo Laptop LEN-LAP-4",
                    "store_id": 7,
                    "store_name": "Central",
                    "city": "Pune",
                    "quantity_on_hand": 212,
                    "reorder_level": 81,
                    "max_level": 243,
                    "cost_price": 50113.2,
                    "inventory_value": 10624000.0,
                    "units_sold_window": 196,
                    "lead_time_days": 13.0,
                    "avg_daily_sales": 7.84,
                    "days_of_inventory": 27.040816,
                    "actual_days_of_inventory": 27.040816,
                    "simulated_days_of_inventory": 27.040816,
                    "stock_status": "Healthy",
                    "stockout_risk": "Low",
                    "basis": "Simulated",
                }
            ]
        ),
        sql=["SELECT ..."],
        params=[[]],
        notes=[],
        basis="Simulated",
    )

    narrator = FakeNarrator()

    orchestrator = Orchestrator(
        llm=base_components["llm"],
        queries=base_components["queries"],
        resolver=FakeResolver(base_components["resolution"]),
        rules=base_components["rules"],
        session_memory=base_components["sessions"],
        narrator=narrator,
        dispatch_fn=lambda **kwargs: result,
    )

    response = orchestrator.handle(
        "What if demand increases by 20%?",
        "session-1",
    )

    assert response.status == "ok"
    assert response.basis == "Simulated"
    assert response.trust_panel["basis"] == "Simulated"

    simulated = [
        x
        for x in response.trust_panel["calculations"]
        if x["basis"] == "Simulated"
    ]

    assert simulated


def test_what_if_trust_panel_reports_each_product_store_without_averaging():
    from app.llm.schema import Scenario

    intent = Intent(
        capability="what_if", metrics=[], filters=Filters(),
        scenario=Scenario(kind="demand_change_pct", value=20),
    )
    frame = pd.DataFrame([
        {"product_id": 1, "product_name": "Product One", "store_id": 10,
         "store_name": "Store A", "city": "Delhi", "quantity_on_hand": 10,
         "units_sold_window": 5, "actual_days_of_inventory": 60,
         "simulated_days_of_inventory": 50, "coverage_change_days": -10,
         "actual_stockout_risk": "Low", "simulated_stockout_risk": "Low"},
        {"product_id": 1, "product_name": "Product One", "store_id": 11,
         "store_name": "Store B", "city": "Delhi", "quantity_on_hand": 2,
         "units_sold_window": 10, "actual_days_of_inventory": 6,
         "simulated_days_of_inventory": 5, "coverage_change_days": -1,
         "actual_stockout_risk": "High", "simulated_stockout_risk": "High"},
    ])

    calculations = _calculation_payload(intent, frame, Rules(), "Simulated")
    positions = next(item for item in calculations if item["metric"] == "what_if_position_results")

    assert [row["store_id"] for row in positions["result"]] == [10, 11]
    assert [row["simulated_days_of_inventory"] for row in positions["result"]] == [50, 5]
    assert positions["values"] == {"scenario_kind": "demand_change_pct", "scenario_value": 20}


def test_invalid_question_is_rejected():
    orchestrator = Orchestrator(
        llm=FakeLLM(),
        queries=FakeQueries(),
        resolver=FakeResolver(FakeResolution()),
        rules=Rules(),
        session_memory=SessionMemory(),
    )
    response = orchestrator.handle("", "session-1")

    assert response.status == "error"
    assert response.error == "Please enter a question."


def test_missing_session_id_is_rejected():
    orchestrator = Orchestrator(
        llm=FakeLLM(),
        queries=FakeQueries(),
        resolver=FakeResolver(FakeResolution()),
        rules=Rules(),
        session_memory=SessionMemory(),
    )

    with pytest.raises(ValueError, match="session_id is required"):
        orchestrator.handle("Show me inventory", "")


def test_narration_failure_does_not_destroy_result(monkeypatch, base_components):
    intent = make_intent("inventory_visibility")
    monkeypatch.setattr(
        "app.orchestrator.parse_intent",
        lambda question, llm, context="": parsed(intent),
    )

    narrator = FakeNarrator(fail=True)

    orchestrator = Orchestrator(
        llm=base_components["llm"],
        queries=base_components["queries"],
        resolver=FakeResolver(base_components["resolution"]),
        rules=base_components["rules"],
        session_memory=base_components["sessions"],
        narrator=narrator,
        dispatch_fn=lambda **kwargs: make_dispatch_result(),
    )
    response = orchestrator.handle("Show me inventory", "session-1")

    assert response.status == "ok"
    assert response.row_count == 1
    assert "38 units are available" in response.narrative
    assert narrator.calls == []


