from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal
from uuid import uuid4

import numpy as np
import pandas as pd
import pytest

from app.context import UserContext, build_followups, effective_ids, persona_followups
from app.history import ConversationStore, normalize_json_value


def test_authorized_scope_intersects_requested_scope():
    assert effective_ids([1, 2], (2, 3)) == ([2], True)
    assert effective_ids(None, (2, 3)) == ([2, 3], False)


def test_persona_followups_are_specific():
    assert "Show stockout risk" in persona_followups("Planner", "lookup")
    assert "Compare categories" in persona_followups("Leadership", "lookup")


def test_dynamic_stockout_followups_use_verified_scope_labels():
    frame = pd.DataFrame([
        {"product_name": "iPhone 16", "store_name": "Chennai Central", "city": "Chennai", "stockout_risk": "High"}
    ])
    intent = type("Intent", (), {"filters": type("Filters", (), {"brands": [], "products": []})()})()
    suggestions = build_followups(
        question="Which products are at risk of stockout in Chennai?",
        intent=intent,
        dataframe=frame,
        response_type="stockout_risk",
        persona="Planner",
    )
    assert suggestions[0] == "Show low-stock products in Chennai"
    assert "iPhone 16" in suggestions[1]
    assert all("17" not in suggestion for suggestion in suggestions)


def test_dynamic_questions_produce_different_relevant_followups():
    frame = pd.DataFrame([{"product_name": "Samsung TV", "brand": "Samsung", "units": 100}])
    intent = type("Intent", (), {"filters": type("Filters", (), {"brands": ["Samsung"], "products": []})()})()
    sales = build_followups(question="How is Samsung performing?", intent=intent, dataframe=frame, response_type="sales", persona="Brand Manager")
    ranking = build_followups(question="Which products have the lowest inventory?", intent=intent, dataframe=frame, response_type="ranking", persona="Planner")
    assert sales != ranking
    assert any("Samsung" in item for item in sales)
    assert any("stockout" in item.casefold() or "sales" in item.casefold() for item in ranking)


def test_brand_manager_followups_use_only_authorized_brands():
    frame = pd.DataFrame([
        {"product_name": "Alpha Phone", "brand": "Alpha", "units": 10},
        {"product_name": "Beta Phone", "brand": "Beta", "units": 8},
        {"product_name": "Unauthorized Phone", "brand": "Gamma", "units": 7},
    ])
    intent = type("Intent", (), {"filters": type("Filters", (), {"brands": [], "products": []})()})()
    scope = UserContext(authorized_brands=("Alpha", "Beta"))
    suggestions = build_followups(
        question="Show authorized brand sales", intent=intent, dataframe=frame,
        response_type="sales", persona="Brand Manager", authorized_scope=scope,
    )
    assert suggestions
    assert all("Gamma" not in item for item in suggestions)
    assert any("Alpha" in item and "Beta" in item for item in suggestions)


def test_history_is_user_scoped_and_persistent():
    # Workspace-local fixture avoids a locked system Temp directory on Windows CI.
    path = f"test-history-{uuid4().hex}.sqlite3"
    store = ConversationStore(path)
    store.ensure_conversation("sai", "Sai", "Planner", "c-1")
    store.add_message("c-1", "user", "Which Lenovo laptops have the lowest inventory?")
    store.set_title_if_new("c-1", "Which Lenovo laptops have the lowest inventory?")
    store.add_message("c-1", "assistant", "Deterministic answer", {"status": "ok"})
    recent = store.recents("sai")[0]
    assert recent["title"] == "Which Lenovo laptops have the lowest inventory?"
    assert recent["first_question"] == recent["title"]
    assert recent["updated_at"]
    assert [m["role"] for m in store.messages("sai", "c-1")] == ["user", "assistant"]
    assert store.messages("other-user", "c-1") == []


def test_history_uses_first_meaningful_question_for_title():
    store = ConversationStore(f"test-history-{uuid4().hex}.sqlite3")
    store.ensure_conversation("sai", "Sai", "Leadership", "c-title")
    store.add_message("c-title", "user", "Hello!")
    store.set_title_if_new("c-title", "Hello!")
    assert store.recents("sai")[0]["title"] == "New conversation"

    store.add_message(
        "c-title",
        "user",
        "Which stores have the highest sales revenue this month?",
    )
    store.set_title_if_new(
        "c-title",
        "Which stores have the highest sales revenue this month?",
    )
    assert (
        store.recents("sai")[0]["title"]
        == "Which stores have the highest sales revenue this month?"
    )


def test_history_round_trips_decimal_dates_and_numpy_scalars():
    store = ConversationStore(f"test-history-{uuid4().hex}.sqlite3")
    store.ensure_conversation("sai", "Sai", "Planner", "c-json")
    metadata = {
        "revenue": Decimal("1234567890.123456789"),
        "business_date": date(2026, 10, 11),
        "created_at": datetime(2026, 10, 11, 7, 56, tzinfo=timezone.utc),
        "calculation": {"units": np.int64(12)},
    }

    store.add_message("c-json", "assistant", "Revenue details", metadata)

    restored = store.messages("sai", "c-json")[0]["metadata"]
    assert restored == {
        "revenue": "1234567890.123456789",
        "business_date": "2026-10-11",
        "created_at": "2026-10-11T07:56:00+00:00",
        "calculation": {"units": 12},
    }


def test_json_normalizer_rejects_unsupported_objects():
    with pytest.raises(TypeError, match="Unsupported JSON metadata value"):
        normalize_json_value({"unsupported": object()})


def test_history_rename_and_delete_are_owner_checked():
    path = f"test-history-{uuid4().hex}.sqlite3"
    store = ConversationStore(path)
    store.ensure_conversation("sai", "Sai", "Planner", "c-1")
    assert store.rename("other", "c-1", "No access") is False
    assert store.rename("sai", "c-1", "Renamed") is True
    assert store.recents("sai")[0]["title"] == "Renamed"
    assert store.delete("other", "c-1") is False
    assert store.delete("sai", "c-1") is True
