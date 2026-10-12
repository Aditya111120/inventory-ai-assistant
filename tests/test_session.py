from __future__ import annotations

import pytest

from app.session import SessionMemory, SessionState


def test_get_creates_empty_session():
    memory = SessionMemory()

    state = memory.get("abc")

    assert isinstance(state, SessionState)
    assert state.last_intent_summary == ""
    assert state.last_product_ids is None
    assert state.last_store_ids is None


def test_get_returns_same_session_state():
    memory = SessionMemory()

    first = memory.get("abc")
    first.last_intent_summary = "inventory visibility"

    second = memory.get("abc")

    assert second is first
    assert second.last_intent_summary == "inventory visibility"


def test_sessions_are_isolated():
    memory = SessionMemory()

    memory.update(
        "session-a",
        intent_summary="laptops",
        product_ids=[1, 2],
        store_ids=[10],
    )

    memory.update(
        "session-b",
        intent_summary="televisions",
        product_ids=[20],
        store_ids=[30],
    )

    a = memory.get("session-a")
    b = memory.get("session-b")

    assert a.last_product_ids == [1, 2]
    assert a.last_store_ids == [10]
    assert b.last_product_ids == [20]
    assert b.last_store_ids == [30]


def test_update_copies_id_lists():
    memory = SessionMemory()

    products = [1, 2]
    stores = [10, 20]

    memory.update(
        "abc",
        intent_summary="inventory",
        product_ids=products,
        store_ids=stores,
    )

    products.append(999)
    stores.append(999)

    state = memory.get("abc")

    assert state.last_product_ids == [1, 2]
    assert state.last_store_ids == [10, 20]


def test_update_allows_none():
    memory = SessionMemory()

    state = memory.update(
        "abc",
        intent_summary="all inventory",
        product_ids=None,
        store_ids=None,
    )

    assert state.last_product_ids is None
    assert state.last_store_ids is None


def test_update_persists_complete_intent_and_basis_without_aliasing():
    from app.llm.schema import Filters, Intent, Sort, TimeWindow

    intent = Intent(
        capability="sales_trend",
        metrics=["sales_revenue", "sales_units"],
        filters=Filters(brands=["Example"], cities=["Pune"]),
        group_by="city",
        time=TimeWindow(kind="month", month=9, year=2026),
        sort=Sort(by="sales_revenue", direction="desc"),
        limit=5,
        question_kind="ranking",
    )
    memory = SessionMemory()
    state = memory.update(
        "structured",
        intent=intent,
        intent_summary="sales",
        product_ids=[1, 2],
        store_ids=[10],
        basis="Actual",
    )

    intent.filters.brands.append("Mutated after update")

    assert state.last_intent["capability"] == "sales_trend"
    assert state.last_intent["metrics"] == ["sales_revenue", "sales_units"]
    assert state.last_intent["filters"]["brands"] == ["Example"]
    assert state.last_intent["group_by"] == "city"
    assert state.last_intent["time"]["month"] == 9
    assert state.last_intent["sort"]["direction"] == "desc"
    assert state.last_intent["limit"] == 5
    assert state.last_basis == "Actual"


def test_previous_context_contains_summary_but_not_database_ids():
    memory = SessionMemory()

    memory.update(
        "abc",
        intent_summary="Which laptop models have the lowest inventory?",
        product_ids=[1, 2, 3],
        store_ids=[10, 11],
    )

    context = memory.previous_context("abc")

    assert "Which laptop models have the lowest inventory?" in context
    assert "previous_product_ids" not in context
    assert "previous_store_ids" not in context


def test_previous_context_does_not_expose_database_ids():
    memory = SessionMemory()

    memory.update(
        "session-1",
        intent_summary="capability=inventory_visibility; metric=quantity",
        product_ids=[17, 42, 67],
        store_ids=[1, 2],
    )

    context = memory.previous_context("session-1")

    assert "previous_product_ids" not in context
    assert "previous_store_ids" not in context
    assert "capability=inventory_visibility" in context
    assert "metric=quantity" in context


def test_previous_context_empty_for_new_session():
    memory = SessionMemory()

    assert memory.previous_context("new") == ""


def test_clear_session():
    memory = SessionMemory()

    memory.update(
        "abc",
        intent_summary="inventory",
        product_ids=[1],
        store_ids=[10],
    )

    memory.clear("abc")

    state = memory.get("abc")

    assert state.last_intent_summary == ""
    assert state.last_product_ids is None
    assert state.last_store_ids is None


def test_clear_all():
    memory = SessionMemory()

    memory.update(
        "a",
        intent_summary="inventory",
        product_ids=[1],
        store_ids=[10],
    )
    memory.update(
        "b",
        intent_summary="sales",
        product_ids=[2],
        store_ids=[20],
    )

    memory.clear_all()

    assert memory.previous_context("a") == ""
    assert memory.previous_context("b") == ""


def test_empty_session_id_rejected():
    memory = SessionMemory()

    with pytest.raises(ValueError, match="session_id is required"):
        memory.get("")
