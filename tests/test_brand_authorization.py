from datetime import date

import pandas as pd
import pytest

from app.auth import AuthenticatedUser, AuthorizedScope
from app.calc.metrics import Rules
from app.context import UserContext, build_followups
from app.dispatch import DispatchResult
from app.llm.client import Usage
from app.llm.intent import ParsedIntent
from app.llm.schema import Filters, Intent, TimeWindow
from app.orchestrator import Orchestrator
from app.semantic.resolver import EntityResolver
from app.session import SessionMemory


BRANDS = ["Apple", "Samsung", "Sony", "LG", "Lenovo", "Dell", "HP", "Asus", "Canon", "OnePlus", "Xiaomi", "boAt"]
BM1 = tuple(BRANDS[:6])
BM2 = tuple(BRANDS[6:])


def _catalog():
    products = pd.DataFrame([
        {"product_id": i + 1, "sku": f"SKU-{i + 1:04d}", "product_name": f"{brand} Model {i + 1}",
         "brand": brand, "category": "Laptops", "model": f"M-{i + 1}", "subcategory": None}
        for i, brand in enumerate(BRANDS)
    ])
    stores = pd.DataFrame([
        {"store_id": 1, "store_name": "Central", "city": "Chennai", "region": "South"},
        {"store_id": 2, "store_name": "North", "city": "Delhi", "region": "North"},
    ])
    return products, stores


def _intent(filters):
    return Intent(
        capability="inventory_visibility", metrics=["quantity"], filters=filters,
        time=TimeWindow(kind="latest"), question_kind="lookup",
    )


def _orchestrator(products, stores, intent, captured):
    resolver = EntityResolver(products, stores)

    def parse(_question, _llm, context=""):
        return ParsedIntent(intent, Usage(prompt_tokens=1, completion_tokens=1, total_tokens=2), False)

    result = DispatchResult(
        df=pd.DataFrame([{"product_id": 7, "product_name": "authorized", "quantity_on_hand": 2}]),
        sql=[], params=[], notes=[], basis="Actual",
    )

    def dispatch(**kwargs):
        captured.append(kwargs)
        return result

    orchestrator = Orchestrator(
        llm=object(), queries=type("Q", (), {"as_of": lambda self: date(2026, 9, 30)})(),
        resolver=resolver, rules=Rules(), session_memory=SessionMemory(), dispatch_fn=dispatch,
    )
    return orchestrator, parse


@pytest.mark.parametrize("brand", BRANDS)
def test_each_assigned_brand_resolves_for_the_correct_brand_manager(monkeypatch, brand):
    products, stores = _catalog()
    captured = []
    intent = _intent(Filters(brands=[brand]))
    orchestrator, parse = _orchestrator(products, stores, intent, captured)
    monkeypatch.setattr("app.orchestrator.parse_intent", parse)
    owner_brands = BM1 if brand in BM1 else BM2
    context = AuthorizedScope(
        AuthenticatedUser(1, "BM-01" if owner_brands is BM1 else "BM-02", "Brand Manager", "BRAND_MANAGER"),
        None, (), (), owner_brands,
    ).context()

    response = orchestrator.handle(f"Show {brand} inventory across all stores", "brand-test", context)

    assert response.status == "ok"
    assert captured[-1]["product_ids"] == [int(products.loc[products.brand == brand, "product_id"].iloc[0])]


def test_bm02_unauthorized_brand_is_denied(monkeypatch):
    products, stores = _catalog()
    captured = []
    orchestrator, parse = _orchestrator(products, stores, _intent(Filters(brands=["Samsung"])), captured)
    monkeypatch.setattr("app.orchestrator.parse_intent", parse)
    context = AuthorizedScope(
        AuthenticatedUser(2, "BM-02", "Brand Manager", "BRAND_MANAGER"), None, (), (), BM2,
    ).context()

    response = orchestrator.handle("Show Samsung inventory across all stores", "brand-test", context)

    assert response.status == "out_of_scope"
    assert captured == []


def test_bm02_broad_query_is_limited_to_all_six_authorized_brands(monkeypatch):
    products, stores = _catalog()
    captured = []
    orchestrator, parse = _orchestrator(products, stores, _intent(Filters()), captured)
    monkeypatch.setattr("app.orchestrator.parse_intent", parse)
    context = AuthorizedScope(
        AuthenticatedUser(2, "BM-02", "Brand Manager", "BRAND_MANAGER"), None, (), (), BM2,
    ).context()

    response = orchestrator.handle("Show inventory across all stores", "brand-test", context)

    assert response.status == "ok"
    assert captured[-1]["product_ids"] == list(range(7, 13))


def test_mixed_authorized_and_unauthorized_brand_comparison_fails_closed(monkeypatch):
    products, stores = _catalog()
    captured = []
    orchestrator, parse = _orchestrator(products, stores, _intent(Filters(brands=["HP", "Samsung"])), captured)
    monkeypatch.setattr("app.orchestrator.parse_intent", parse)
    context = AuthorizedScope(
        AuthenticatedUser(2, "BM-02", "Brand Manager", "BRAND_MANAGER"), None, (), (), BM2,
    ).context()

    response = orchestrator.handle("Compare HP and Samsung inventory across all stores", "brand-test", context)

    assert response.status == "out_of_scope"
    assert captured == []


def test_comparison_subject_cannot_bypass_brand_scope(monkeypatch):
    products, stores = _catalog()
    captured = []
    intent = _intent(Filters(brands=["HP"]))
    intent = intent.model_copy(update={"comparison_subject": "Samsung", "comparison_mode": "subject_vs_scope"})
    orchestrator, parse = _orchestrator(products, stores, intent, captured)
    monkeypatch.setattr("app.orchestrator.parse_intent", parse)
    context = AuthorizedScope(
        AuthenticatedUser(2, "BM-02", "Brand Manager", "BRAND_MANAGER"), None, (), (), BM2,
    ).context()

    response = orchestrator.handle("Compare HP with Samsung across all stores", "brand-test", context)

    assert response.status == "out_of_scope"
    assert captured == []


def test_brand_manager_followup_keeps_authorized_brand_context():
    frame = pd.DataFrame([{"product_name": "HP Model", "brand": "HP", "store_name": "Central", "city": "Chennai"}])
    intent = _intent(Filters(brands=["HP"]))
    suggestions = build_followups(
        question="Show HP inventory in Chennai", intent=intent, dataframe=frame,
        response_type="inventory_visibility", persona="Brand Manager",
        authorized_scope=UserContext(authorized_brands=BM2),
    )
    assert any("HP" in suggestion for suggestion in suggestions)
    assert all("Samsung" not in suggestion for suggestion in suggestions)


def test_bm02_hp_store_and_timeline_clarifications_preserve_brand_scope(monkeypatch):
    products, stores = _catalog()
    stores = pd.DataFrame([
        {"store_id": 1, "store_name": "Anna Nagar", "city": "Chennai", "region": "South"},
        {"store_id": 2, "store_name": "T Nagar", "city": "Chennai", "region": "South"},
    ])
    captured = []
    intent = _intent(Filters(brands=["HP"], cities=["Chennai"]))
    intent = intent.model_copy(update={"time": TimeWindow()})
    orchestrator, parse = _orchestrator(products, stores, intent, captured)
    monkeypatch.setattr("app.orchestrator.parse_intent", parse)
    context = AuthorizedScope(
        AuthenticatedUser(2, "BM-02", "Brand Manager", "BRAND_MANAGER"), None, (), (), BM2,
    ).context()

    first = orchestrator.handle("Show HP inventory in Chennai", "flow", context)
    assert first.status == "clarification"
    store_option = next(item for item in first.clarification_options if item["label"] == "Anna Nagar")
    final = orchestrator.handle(store_option["token"], "flow", context)

    assert final.status == "ok"
    assert captured[-1]["product_ids"] == [7]
    assert captured[-1]["store_ids"] == [1]
    assert final.resolved_context["product_scope"]["product_ids"] == [7]


@pytest.mark.parametrize("role", ["PLANNER", "LEADERSHIP"])
def test_planner_and_leadership_keep_full_product_scope(monkeypatch, role):
    products, stores = _catalog()
    captured = []
    orchestrator, parse = _orchestrator(products, stores, _intent(Filters()), captured)
    monkeypatch.setattr("app.orchestrator.parse_intent", parse)
    context = AuthorizedScope(AuthenticatedUser(3, role, role, role), None, None).context()

    response = orchestrator.handle("Show inventory across all stores", "full-test", context)

    assert response.status == "ok"
    assert captured[-1]["product_ids"] is None


@pytest.mark.parametrize("store_id", [1, 2])
def test_store_manager_scope_remains_store_restricted(monkeypatch, store_id):
    products, stores = _catalog()
    captured = []
    orchestrator, parse = _orchestrator(products, stores, _intent(Filters()), captured)
    monkeypatch.setattr("app.orchestrator.parse_intent", parse)
    context = AuthorizedScope(
        AuthenticatedUser(10 + store_id, f"SM-0{store_id}", "Store Manager", "STORE_MANAGER"),
        (store_id,), None, ("South" if store_id == 1 else "North",), (),
    ).context()

    response = orchestrator.handle("Show inventory across all stores", "store-test", context)

    assert response.status == "ok"
    assert captured[-1]["store_ids"] == [store_id]


def test_store_manager_unauthorized_city_is_denied_before_clarification(monkeypatch):
    products, stores = _catalog()
    captured = []
    intent = _intent(Filters(categories=["Laptops"], cities=["Chennai"]))
    orchestrator, parse = _orchestrator(products, stores, intent, captured)
    monkeypatch.setattr("app.orchestrator.parse_intent", parse)
    context = AuthorizedScope(
        AuthenticatedUser(11, "SM-01", "North Store Manager", "STORE_MANAGER"),
        (99,), None, ("North",), (),
    ).context()

    response = orchestrator.handle("Show laptop inventory in Chennai", "store-denial", context)

    assert response.status == "out_of_scope"
    assert "outside" in response.error.casefold()
    assert response.clarification_options == []
    assert captured == []
