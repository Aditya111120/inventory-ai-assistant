from __future__ import annotations

import gc
from datetime import date
from decimal import Decimal
from pathlib import Path
from uuid import uuid4

import pandas as pd
import pytest
from fastapi.testclient import TestClient

from app.api import create_app
from app.history import ConversationStore
from app.auth import AuthenticatedUser, AuthorizedScope
from app.llm.schema import Filters, Intent, TimeWindow
from app.session import SessionMemory


class FakeResponse:
    def __init__(
        self,
        *,
        session_id="session-test",
        status="ok",
        narrative="Lenovo Laptop has 32.45 days of inventory.",
        data=None,
        interpretation=None,
        trust_panel=None,
        basis="Actual",
        usage=None,
        error=None,
        narration_verified=True,
        narration_regenerated=False,
        narration_fallback=False,
        followups=None,
        visualization=None,
    ):
        self.session_id = session_id
        self.status = status
        self.narrative = narrative
        self.data = (
            data
            if data is not None
            else pd.DataFrame(
                [
                    {
                        "product_id": 92,
                        "product_name": "Lenovo Laptop LEN-LAP-4",
                        "quantity_on_hand": 212,
                        "days_of_inventory": 32.44898,
                    }
                ]
            )
        )
        self.interpretation = interpretation or [
            "'Lenovo' -> Lenovo: 12 products"
        ]
        self.trust_panel = trust_panel or {
            "interpretation": {
                "capability": "inventory_visibility",
            },
            "query": {
                "sql": ["SELECT ..."],
                "params": [[]],
                "row_count": 1,
            },
            "calculations": [],
            "rules_applied": {
                "sales_window_days": 30,
            },
            "basis": basis,
        }
        self.basis = basis
        self.usage = usage
        self.error = error
        self.narration_verified = narration_verified
        self.narration_regenerated = narration_regenerated
        self.narration_fallback = narration_fallback
        self.followups = followups or []
        self.visualization = visualization


class FakeOrchestrator:
    def __init__(self, response=None):
        self.response = response or FakeResponse()
        self.calls = []

    def handle(self, *, question, session_id):
        self.calls.append(
            {
                "question": question,
                "session_id": session_id,
            }
        )
        return self.response


class SequentialFakeOrchestrator:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def handle(self, *, question, session_id):
        self.calls.append((question, session_id))
        return self.responses.pop(0)


class FakeAuthService:
    def __init__(self):
        self.revoked = []
        self.user = AuthenticatedUser(7, "SM-01", "North Manager", "STORE_MANAGER")

    def user_for_token(self, token):
        return self.user if token == "valid-token" else None

    def scope(self, user):
        return AuthorizedScope(user, (1, 2), None, ("North",), ())

    def login(self, username, password):
        return self.user, "issued-token"

    def logout(self, token):
        self.revoked.append(token)


class FailingLogoutAuthService(FakeAuthService):
    def logout(self, token):
        raise RuntimeError("database unavailable")


class ContinuityAuthService(FakeAuthService):
    def user_for_token(self, token):
        return self.user if token in {"valid-token", "issued-token"} else None


def test_health_endpoint():
    fake = FakeOrchestrator()
    client = TestClient(create_app(lambda: fake))

    response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {
        "status": "ok",
        "service": "inventory-ai-assistant",
    }


def test_logout_revokes_server_session_and_me_exposes_account_scope():
    fake = FakeOrchestrator()
    fake.auth_service = FakeAuthService()
    client = TestClient(create_app(lambda: fake))
    client.cookies.set("inventory_session", "valid-token")

    me = client.get("/auth/me")
    assert me.status_code == 200
    assert me.json()["account_id"] == "SM-01"
    assert me.json()["regions"] == ["North"]
    assert me.json()["brands"] == []
    assert me.json()["full_access"] is False

    logout = client.post("/auth/logout")
    assert logout.status_code == 200
    assert fake.auth_service.revoked == ["valid-token"]
    assert "inventory_session" in logout.headers.get("set-cookie", "")


def test_logout_does_not_claim_success_when_session_revocation_fails():
    fake = FakeOrchestrator()
    fake.auth_service = FailingLogoutAuthService()
    client = TestClient(create_app(lambda: fake), raise_server_exceptions=False)
    client.cookies.set("inventory_session", "valid-token")

    response = client.post("/auth/logout")

    assert response.status_code == 500
    assert '"status":"ok"' not in response.text


def test_login_cookie_secure_is_false_for_configured_local_development(monkeypatch):
    monkeypatch.setenv("APP_ENV", "development")
    monkeypatch.delenv("AUTH_COOKIE_SECURE", raising=False)
    fake = FakeOrchestrator()
    fake.auth_service = FakeAuthService()
    client = TestClient(create_app(lambda: fake))

    response = client.post(
        "/auth/login",
        json={"username": "SM-01", "password": "password"},
    )

    assert response.status_code == 200
    cookie = response.headers["set-cookie"]
    assert "inventory_session=issued-token" in cookie
    assert "httponly" in cookie.casefold()
    assert "samesite=lax" in cookie.casefold()
    assert "path=/" in cookie.casefold()
    assert "secure" not in cookie.casefold()


def test_login_cookie_secure_defaults_true_for_production_even_over_http(monkeypatch):
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.delenv("AUTH_COOKIE_SECURE", raising=False)
    fake = FakeOrchestrator()
    fake.auth_service = FakeAuthService()
    client = TestClient(create_app(lambda: fake))

    response = client.post(
        "/auth/login",
        json={"username": "SM-01", "password": "password"},
    )

    assert response.status_code == 200
    cookie = response.headers["set-cookie"]
    assert "secure" in cookie.casefold()


def test_login_cookie_is_secure_for_https_even_in_development(monkeypatch):
    monkeypatch.setenv("APP_ENV", "development")
    monkeypatch.delenv("AUTH_COOKIE_SECURE", raising=False)
    fake = FakeOrchestrator()
    fake.auth_service = FakeAuthService()
    client = TestClient(create_app(lambda: fake), base_url="https://testserver")

    response = client.post("/auth/login", json={"username": "SM-01", "password": "password"})

    assert response.status_code == 200
    assert "secure" in response.headers["set-cookie"].casefold()


def test_non_loopback_http_cannot_disable_secure_cookie(monkeypatch):
    monkeypatch.setenv("APP_ENV", "development")
    monkeypatch.setenv("AUTH_COOKIE_SECURE", "false")
    fake = FakeOrchestrator()
    fake.auth_service = FakeAuthService()
    client = TestClient(create_app(lambda: fake), base_url="http://example.test")

    response = client.post("/auth/login", json={"username": "SM-01", "password": "password"})

    assert response.status_code == 200
    assert "secure" in response.headers["set-cookie"].casefold()


def test_local_login_cookie_supports_me_and_logout_session_continuity(monkeypatch):
    monkeypatch.setenv("APP_ENV", "development")
    monkeypatch.setenv("AUTH_COOKIE_SECURE", "true")
    fake = FakeOrchestrator()
    fake.auth_service = ContinuityAuthService()
    client = TestClient(create_app(lambda: fake), base_url="http://127.0.0.1:8000")

    login = client.post("/auth/login", json={"username": "SM-01", "password": "password"})
    assert login.status_code == 200
    cookie = login.headers["set-cookie"].casefold()
    assert "secure" not in cookie
    assert "httponly" in cookie
    assert "samesite=lax" in cookie
    assert client.get("/auth/me").status_code == 200
    assert client.post("/auth/logout").status_code == 200
    assert client.get("/auth/me").status_code == 401


def test_chat_generates_session_id_when_missing():
    fake = FakeOrchestrator()
    client = TestClient(create_app(lambda: fake))

    response = client.post(
        "/chat",
        json={"question": "Show Lenovo inventory"},
    )

    assert response.status_code == 200

    body = response.json()

    assert body["session_id"].startswith("session-")
    assert body["status"] == "ok"
    assert fake.calls[0]["question"] == "Show Lenovo inventory"
    assert fake.calls[0]["session_id"] == body["session_id"]


def test_chat_preserves_supplied_session_id():
    fake = FakeOrchestrator()
    client = TestClient(create_app(lambda: fake))

    response = client.post(
        "/chat",
        json={
            "question": "How many days of inventory?",
            "session_id": "session-abc",
        },
    )

    assert response.status_code == 200

    body = response.json()

    assert body["session_id"] == "session-abc"
    assert fake.calls[0]["session_id"] == "session-abc"


def test_chat_returns_table_and_trust_panel():
    fake = FakeOrchestrator()
    client = TestClient(create_app(lambda: fake))

    response = client.post(
        "/chat",
        json={
            "question": "How many days of inventory?",
            "session_id": "session-abc",
        },
    )

    assert response.status_code == 200

    body = response.json()

    assert len(body["table"]) == 1
    assert body["table"][0]["product_id"] == 92
    assert body["table"][0]["quantity_on_hand"] == 212
    assert body["trust_panel"]["query"]["row_count"] == 1
    assert body["trust_panel"]["basis"] == "Actual"
    assert body["response"]["display_columns"] == [
        "product_id", "product_name", "quantity_on_hand", "days_of_inventory"
    ]
    assert body["response"]["display_rows"] == body["table"]
    assert body["response"]["has_more"] is False


def test_chat_response_passes_validated_visualization_metadata_at_both_levels():
    fake = FakeOrchestrator()
    fake.response.visualization = {
        "type": "bar",
        "title": "Inventory value by category",
        "description": "Sorted by value, highest first.",
        "category_field": "label",
        "series": [{"key": "value", "label": "Inventory value", "unit": "currency", "currency": "INR"}],
        "data": [{"label": "Laptops", "value": 1200}],
        "basis": "Actual",
        "complete": True,
    }
    client = TestClient(create_app(lambda: fake))

    response = client.post("/chat", json={"question": "Show inventory value by category"})

    assert response.status_code == 200
    body = response.json()
    assert body["visualization"]["type"] == "bar"
    assert body["response"]["visualization"] == body["visualization"]
    assert body["visualization"]["data"][0] == {"label": "Laptops", "value": 1200}


def test_invalid_visualization_metadata_does_not_fail_analytical_chat():
    fake = FakeOrchestrator()
    fake.response.visualization = {"type": "unsupported", "data": []}
    client = TestClient(create_app(lambda: fake))

    response = client.post("/chat", json={"question": "Show inventory"})

    assert response.status_code == 200
    body = response.json()
    assert body["narrative"] == fake.response.narrative
    assert body["visualization"] is None
    assert body["response"]["visualization"] is None


def test_chat_restores_last_successful_structured_context_from_history():
    store = ConversationStore(f"test-context-{uuid4().hex}.sqlite3")
    store.ensure_conversation("demo-sai", "Sai", "Leadership", "resume-context")
    prior_intent = Intent(
        capability="sales_trend",
        metrics=["sales_revenue"],
        filters=Filters(brands=["Example"], cities=["Pune"]),
        time=TimeWindow(kind="month", month=9, year=2026),
    )
    store.add_message(
        "resume-context",
        "assistant",
        "Prior answer",
        {
            "status": "ok",
            "intent": prior_intent.model_dump(),
            "resolved_context": {
                "product_scope": {"product_ids": [4, 5]},
                "location_scope": {"store_ids": [12]},
            },
            "basis": "Simulated",
        },
    )

    class ContextInspectingOrchestrator(FakeOrchestrator):
        def __init__(self):
            super().__init__()
            self.sessions = SessionMemory()
            self.context_before_handle = None

        def handle(self, *, question, session_id):
            state = self.sessions.get(session_id)
            self.context_before_handle = (
                state.last_intent,
                state.last_product_ids,
                state.last_store_ids,
                state.last_basis,
            )
            return super().handle(question=question, session_id=session_id)

    orchestrator = ContextInspectingOrchestrator()
    client = TestClient(
        create_app(lambda: orchestrator, conversation_store=store)
    )

    response = client.post(
        "/chat",
        json={
            "question": "What about another month?",
            "session_id": "resume-context",
            "conversation_id": "resume-context",
        },
    )

    assert response.status_code == 200
    restored_intent, product_ids, store_ids, basis = orchestrator.context_before_handle
    assert restored_intent["capability"] == "sales_trend"
    assert restored_intent["metrics"] == ["sales_revenue"]
    assert restored_intent["filters"]["brands"] == ["Example"]
    assert restored_intent["time"]["month"] == 9
    assert product_ids == [4, 5]
    assert store_ids == [12]
    assert basis == "Simulated"


def test_chat_returns_narration_metadata():
    fake = FakeOrchestrator(
        FakeResponse(
            narration_verified=True,
            narration_regenerated=True,
            narration_fallback=False,
        )
    )
    client = TestClient(create_app(lambda: fake))

    response = client.post(
        "/chat",
        json={"question": "How many days of inventory?"},
    )

    assert response.status_code == 200

    body = response.json()

    assert body["narration_verified"] is True
    assert body["narration_regenerated"] is True
    assert body["narration_fallback"] is False


def test_simulated_response_is_preserved():
    fake = FakeOrchestrator(
        FakeResponse(
            basis="Simulated",
            trust_panel={
                "interpretation": {},
                "query": {
                    "sql": ["SELECT ..."],
                    "params": [[]],
                    "row_count": 1,
                },
                "calculations": [
                    {
                        "metric": "what_if_days_of_inventory",
                        "result": 27.04,
                        "basis": "Simulated",
                    }
                ],
                "rules_applied": {
                    "sales_window_days": 30,
                },
                "basis": "Simulated",
            },
        )
    )
    client = TestClient(create_app(lambda: fake))

    response = client.post(
        "/chat",
        json={"question": "What if demand increases by 20%?"},
    )

    assert response.status_code == 200

    body = response.json()

    assert body["basis"] == "Simulated"
    assert body["trust_panel"]["basis"] == "Simulated"
    assert body["trust_panel"]["calculations"][0]["basis"] == "Simulated"


def test_clarification_response_is_returned_as_conversation_result():
    fake = FakeOrchestrator(
        FakeResponse(
            status="clarification",
            narrative="",
            data=pd.DataFrame(),
            interpretation=[],
            trust_panel={},
            error="Could you specify the product?",
            narration_verified=False,
        )
    )
    client = TestClient(create_app(lambda: fake))

    response = client.post(
        "/chat",
        json={
            "question": "Which one?",
            "session_id": "session-abc",
        },
    )

    assert response.status_code == 200

    body = response.json()

    assert body["status"] == "clarification"
    assert body["error"] == "Could you specify the product?"
    assert body["table"] == []


def test_successful_followups_are_returned_at_top_level_and_nested_response():
    fake = FakeOrchestrator(FakeResponse(followups=["Show inventory by store in Hyderabad", "Show products below reorder level"]))
    client = TestClient(create_app(lambda: fake))
    body = client.post(
        "/chat",
        json={"question": "Show laptop inventory in June 2026", "session_id": "session-followups"},
    ).json()
    assert body["followups"] == body["response"]["followups"]
    assert len(body["followups"]) == 2


def test_empty_question_is_rejected():
    fake = FakeOrchestrator()
    client = TestClient(create_app(lambda: fake))

    response = client.post("/chat", json={"question": ""})

    assert response.status_code == 422


def test_question_over_500_characters_is_rejected():
    fake = FakeOrchestrator()
    client = TestClient(create_app(lambda: fake))

    response = client.post("/chat", json={"question": "x" * 501})

    assert response.status_code == 422


def test_error_status_from_orchestrator_is_preserved():
    fake = FakeOrchestrator(
        FakeResponse(
            status="llm_unavailable",
            narrative="",
            data=pd.DataFrame(),
            interpretation=[],
            trust_panel={},
            error="The AI service is busy or unavailable.",
            narration_verified=False,
        )
    )
    client = TestClient(create_app(lambda: fake))

    response = client.post(
        "/chat",
        json={"question": "Show inventory"},
    )

    assert response.status_code == 200

    body = response.json()

    assert body["status"] == "llm_unavailable"
    assert body["error"] == "The AI service is busy or unavailable."


@pytest.fixture
def ephemeral_conversation_store(request):
    path = Path(__file__).with_name(f".test-history-{uuid4().hex}.sqlite3")
    store = ConversationStore(path)

    def remove_history_file():
        gc.collect()
        path.unlink(missing_ok=True)

    request.addfinalizer(remove_history_file)
    return store


def test_consecutive_chat_responses_and_history_remain_separate(ephemeral_conversation_store):
    store = ephemeral_conversation_store
    orchestrator = SequentialFakeOrchestrator([
        FakeResponse(
            narrative="iPhone 16 has 25 units on hand across all stores.",
            data=pd.DataFrame([{
                "product_id": 1,
                "sku": "SKU-0001",
                "product_name": "iPhone 16",
                "quantity_on_hand": 25,
                "inventory_value": 1000.0,
            }]),
        ),
        FakeResponse(
            narrative="Chennai has 2 at-risk product-store positions.",
            data=pd.DataFrame([{
                "product_id": 1,
                "sku": "SKU-0001",
                "product_name": "iPhone 16",
                "store_id": 10,
                "store_name": "Chennai Central",
                "city": "Chennai",
                "quantity_on_hand": 5,
                "stockout_risk": "High",
            }]),
        ),
    ])
    client = TestClient(
        create_app(lambda: orchestrator, conversation_store=store)
    )

    first = client.post(
        "/chat",
        json={
            "question": "Show iPhone 16 inventory",
            "conversation_id": "conversation-isolated",
        },
    ).json()
    second = client.post(
        "/chat",
        json={
            "question": "Show Chennai stockout risk",
            "conversation_id": "conversation-isolated",
        },
    ).json()

    assert first["narrative"] == "iPhone 16 has 25 units on hand across all stores."
    assert "stockout" not in first["narrative"].casefold()
    assert first["table"][0]["inventory_value"] == 1000.0
    assert first["narrative"] not in first["table"][0].values()
    assert second["narrative"] == "Chennai has 2 at-risk product-store positions."
    assert second["table"][0]["store_name"] == "Chennai Central"
    assert second["table"][0]["city"] == "Chennai"

    history = client.get(
        "/conversations/demo-sai/conversation-isolated"
    ).json()["messages"]
    assistant_messages = [
        message
        for message in history
        if message["role"] == "assistant"
    ]
    assert [message["content"] for message in assistant_messages] == [first["narrative"], second["narrative"]]
    assert assistant_messages[0]["metadata"]["trust_panel"]["query"]["row_count"] == 1
    assert assistant_messages[0]["metadata"]["table"][0]["product_name"] == "iPhone 16"
    recents = client.get("/conversations/demo-sai").json()["conversations"]
    recent = next(item for item in recents if item["conversation_id"] == "conversation-isolated")
    assert recent["title"] == "Show iPhone 16 inventory"
    assert recent["first_question"] == "Show iPhone 16 inventory"
    assert recent["updated_at"]


def test_chat_persists_and_restores_decimal_evidence_with_json_safe_values(
    ephemeral_conversation_store,
):
    store = ephemeral_conversation_store
    revenue = Decimal("1234567890.123456789")
    fake = FakeOrchestrator(FakeResponse(
        narrative="Revenue evidence is available.",
        data=pd.DataFrame([{
            "product_name": "Example Product",
            "revenue": revenue,
            "sale_date": date(2026, 10, 11),
        }]),
        trust_panel={
            "calculations": [{
                "metric": "sales_revenue",
                "values": {"revenue": revenue},
                "result": revenue,
            }],
            "basis": "Actual",
        },
    ))
    client = TestClient(
        create_app(lambda: fake, conversation_store=store)
    )

    response = client.post(
        "/chat",
        json={
            "question": "Show example product revenue",
            "conversation_id": "decimal-evidence",
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["table"][0]["revenue"] == "1234567890.123456789"
    assert body["table"][0]["sale_date"] == "2026-10-11"
    assert (
        body["trust_panel"]["calculations"][0]["values"]["revenue"]
        == "1234567890.123456789"
    )
    assert (
        body["trust_panel"]["calculations"][0]["result"]
        == "1234567890.123456789"
    )

    history = client.get(
        "/conversations/demo-sai/decimal-evidence"
    )
    assert history.status_code == 200
    assistant_message = next(
        message for message in history.json()["messages"]
        if message["role"] == "assistant"
    )
    restored = assistant_message["metadata"]
    assert restored["table"] == body["table"]
    assert restored["trust_panel"] == body["trust_panel"]
