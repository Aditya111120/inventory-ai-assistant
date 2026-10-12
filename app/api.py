"""FastAPI API layer for the Inventory AI Assistant."""

from __future__ import annotations

from dataclasses import asdict
from functools import lru_cache
from typing import Any
from uuid import uuid4
import inspect
import os

from fastapi import FastAPI, Cookie, HTTPException, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from app.config import load_settings
from app.db import connect, query
from app.dispatch import dispatch
from app.llm.client import GroqClient
from app.narration import Narrator
from app.orchestrator import Orchestrator
from app.repository import load_catalog, load_rules
from app.semantic.queries import Queries
from app.semantic.resolver import EntityResolver
from app.session import SessionMemory
from app.context import UserContext, role_policy
from app.history import ConversationStore, normalize_json_value
from app.auth import AuthService


class ChatRequest(BaseModel):
    question: str = Field(min_length=1, max_length=500)
    session_id: str | None = Field(default=None, min_length=1, max_length=100)
    conversation_id: str | None = Field(default=None, min_length=1, max_length=100)
    user_id: str = Field(default="demo-sai", min_length=1, max_length=100)
    display_name: str = Field(default="Sai", min_length=1, max_length=100)
    persona: str = Field(default="Planner")


class LoginRequest(BaseModel):
    username: str = Field(min_length=1, max_length=100)
    password: str = Field(min_length=1, max_length=500)


class ChatResponse(BaseModel):
    session_id: str
    status: str
    intent: dict[str, Any] | None = None
    limit: int | None = None
    narrative: str = ""
    table: list[dict[str, Any]] = Field(default_factory=list)
    interpretation: list[str] = Field(default_factory=list)
    trust_panel: dict[str, Any] = Field(default_factory=dict)
    basis: str = "Actual"
    usage: dict[str, Any] | None = None
    error: str | None = None
    narration_verified: bool = False
    narration_regenerated: bool = False
    narration_fallback: bool = False
    conversation_id: str | None = None
    user_context: dict[str, Any] = Field(default_factory=dict)
    response_type: str = "lookup"
    display: dict[str, Any] = Field(default_factory=dict)
    followups: list[str] = Field(default_factory=list)
    response: dict[str, Any] = Field(default_factory=dict)
    clarification_kind: str | None = None
    clarification_prompt: str | None = None
    clarification_options: list[dict[str, Any]] = Field(default_factory=list)
    pending_clarification_id: str | None = None
    resolved_context: dict[str, Any] = Field(default_factory=dict)
    answer_facts: dict[str, Any] | None = None


def _auth_cookie_secure(request: Request) -> bool:
    """Choose cookie transport security from server-observed request context.

    Loopback HTTP is the explicitly supported local-development exception.
    A client cannot disable ``Secure`` by sending a header or request field;
    non-loopback HTTP and every HTTPS request remain secure.
    """
    configured = os.getenv("AUTH_COOKIE_SECURE")
    scheme = request.url.scheme.casefold()
    host = (request.url.hostname or "").casefold().strip("[]")
    environment = os.getenv("APP_ENV", "production").strip().casefold()
    local_environment = environment in {"development", "dev", "local", "test"}
    loopback = host in {"127.0.0.1", "localhost", "::1", "testserver"}
    configured_value = None
    if configured is not None:
        configured_value = configured.strip().casefold()
        if configured_value not in {"0", "1", "true", "false", "yes", "no"}:
            raise ValueError("AUTH_COOKIE_SECURE must be a boolean value")

    if scheme == "https":
        return True
    if scheme == "http" and loopback and local_environment:
        return False

    # Configuration is server-side only. A false value is accepted solely for
    # the explicitly identified local environment; never weaken production or
    # a non-loopback deployment based on client-controlled input.
    if configured_value is not None:
        if configured_value in {"1", "true", "yes"}:
            return True
        if configured_value in {"0", "false", "no"} and local_environment and loopback:
            return False
    return True


def _json_value(value: Any) -> Any:
    """Convert supported database scalars to JSON-compatible values."""
    return normalize_json_value(value)


def _dataframe_records(df) -> list[dict[str, Any]]:
    if df is None or df.empty:
        return []

    records = df.to_dict(orient="records")

    return [
        {
            str(key): _json_value(value)
            for key, value in record.items()
        }
        for record in records
    ]


def _usage_payload(usage) -> dict[str, Any] | None:
    if usage is None:
        return None

    try:
        return asdict(usage)
    except TypeError:
        return {
            key: getattr(usage, key)
            for key in (
                "prompt_tokens",
                "completion_tokens",
                "total_tokens",
                "cached_tokens",
                "latency_s",
                "calls",
            )
            if hasattr(usage, key)
        }


@lru_cache(maxsize=1)
def get_orchestrator() -> Orchestrator:
    """Create the production orchestrator once per application process."""

    settings = load_settings()

    conn = connect(settings)

    rules = load_rules(conn)
    products, stores = load_catalog(conn)

    resolver = EntityResolver(products, stores)

    queries = Queries(
        run=query,
        conn=conn,
        schema="dbo.",
    )

    llm = GroqClient(
        api_key=settings.groq_api_key,
        model=settings.groq_model,
        reasoning_effort="low",
    )

    narrator = Narrator(llm)

    orchestrator = Orchestrator(
        llm=llm,
        queries=queries,
        resolver=resolver,
        rules=rules,
        session_memory=SessionMemory(),
        narrator=narrator,
        dispatch_fn=dispatch,
    )
    # Leadership-only is the production demo default; operators can disable
    # the reversible gate explicitly for a broader authorized environment.
    demo_only = os.getenv("LEADERSHIP_ONLY_DEMO", "1").strip().casefold() in {"1", "true", "yes", "on"}
    demo_account = os.getenv("LEADERSHIP_DEMO_ACCOUNT", "LD-01").strip() or "LD-01"
    orchestrator.auth_service = AuthService(
        conn,
        demo_only=demo_only,
        demo_account=demo_account,
    )
    return orchestrator


def create_app(orchestrator_provider=get_orchestrator, conversation_store: ConversationStore | None = None) -> FastAPI:
    app = FastAPI(
        title="Inventory AI Assistant",
        description="Conversational inventory analytics API",
        version="1.0.0",
    )
    store = conversation_store or ConversationStore()
    auth_required = orchestrator_provider is get_orchestrator

    def auth_service() -> AuthService | None:
        try:
            orchestrator = orchestrator_provider()
            service = getattr(orchestrator, "auth_service", None)
            if service is not None:
                return service
            conn = getattr(getattr(orchestrator, "queries", None), "conn", None)
            if conn is not None:
                service = AuthService(conn)
                setattr(orchestrator, "auth_service", service)
                return service
        except Exception:
            return None
        return None

    def current_scope(session_token: str | None):
        service = auth_service()
        if service is None:
            if auth_required:
                raise HTTPException(status_code=503, detail="Authentication service unavailable")
            return None
        user = service.user_for_token(session_token)
        if user is None:
            raise HTTPException(status_code=401, detail="Authentication required")
        return service.scope(user)

    @app.post("/auth/login")
    def login(request: LoginRequest, response: Response, http_request: Request):
        service = auth_service()
        if service is None:
            raise HTTPException(status_code=503, detail="Authentication service unavailable")
        try:
            user, token = service.login(request.username, request.password)
        except PermissionError as exc:
            raise HTTPException(status_code=401, detail=str(exc)) from exc
        response.set_cookie(
            key="inventory_session",
            value=token,
            httponly=True,
            secure=_auth_cookie_secure(http_request),
            samesite="lax",
            path="/",
        )
        scope = service.scope(user)
        return {
            "user_id": user.user_id,
            "username": user.username,
            "account_id": user.username,
            "display_name": user.display_name,
            "role": user.role_code,
            "regions": list(scope.allowed_regions),
            "brands": list(scope.allowed_brands),
            "full_access": user.role_code in {"PLANNER", "LEADERSHIP"},
            "role_focus": role_policy(scope.user.role_code.replace("_", " ").title()).get("focus"),
        }

    @app.post("/auth/logout")
    def logout(response: Response, session_token: str | None = Cookie(default=None, alias="inventory_session")):
        service = auth_service()
        if service is None and auth_required:
            raise HTTPException(status_code=503, detail="Authentication service unavailable")
        if service is not None:
            service.logout(session_token)
        response.delete_cookie("inventory_session", path="/")
        return {"status": "ok"}

    @app.get("/auth/me")
    def me(session_token: str | None = Cookie(default=None, alias="inventory_session")):
        scope = current_scope(session_token)
        role = scope.user.role_code
        return {
            "user_id": scope.user.user_id,
            "username": scope.user.username,
            "account_id": scope.user.username,
            "display_name": scope.user.display_name,
            "role": role,
            "regions": list(scope.allowed_regions),
            "brands": list(scope.allowed_brands),
            "full_access": role in {"PLANNER", "LEADERSHIP"},
            "role_focus": role_policy({"STORE_MANAGER": "Store Manager", "BRAND_MANAGER": "Brand Manager", "PLANNER": "Planner", "LEADERSHIP": "Leadership"}.get(role, "Planner")).get("focus"),
        }

    app.add_middleware(
        CORSMiddleware,
        allow_origins=[
            "http://localhost:5173",
            "http://127.0.0.1:5173",
        ],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.get("/health")
    def health() -> dict[str, str]:
        return {
            "status": "ok",
            "service": "inventory-ai-assistant",
        }

    @app.post("/chat", response_model=ChatResponse)
    def chat(request: ChatRequest, session_token: str | None = Cookie(default=None, alias="inventory_session")) -> ChatResponse:
        conversation_id = request.conversation_id or request.session_id or f"session-{uuid4().hex[:12]}"
        session_id = request.session_id or conversation_id
        scope = current_scope(session_token)
        if scope is None:
            context = UserContext(user_id=request.user_id, display_name=request.display_name, persona=request.persona)
        else:
            context = scope.context()
        store.ensure_conversation(context.user_id, context.display_name, context.persona, conversation_id)
        store.add_message(conversation_id, "user", request.question)
        store.set_title_if_new(conversation_id, request.question)

        orchestrator = orchestrator_provider()

        # Restore a pending clarification from the existing transcript store
        # when the process/session memory was recreated. This metadata is
        # server-owned and is never returned to the client.
        sessions = getattr(orchestrator, "sessions", None)
        if sessions is not None and hasattr(sessions, "pending") and not sessions.pending(session_id):
            pending_state = store.latest_pending(context.user_id, conversation_id)
            if pending_state and hasattr(sessions, "set_pending"):
                sessions.set_pending(session_id, pending_state)

        kwargs = {"question": request.question, "session_id": session_id}
        if "user_context" in inspect.signature(orchestrator.handle).parameters:
            kwargs["user_context"] = context
        response = orchestrator.handle(**kwargs)
        pending_state = None
        if sessions is not None and hasattr(sessions, "pending"):
            pending_state = sessions.pending(session_id)
        response_intent = getattr(response, "intent", None)
        intent_payload = (
            response_intent.model_dump()
            if hasattr(response_intent, "model_dump")
            else response_intent
        )

        display = getattr(response, "display", {})
        table = [] if display.get("summary_only", False) else _dataframe_records(response.data)
        initial_count = int(display.get("initial_display_count", len(table)))
        response_payload = {
            "response_type": getattr(response, "response_type", getattr(response_intent, "question_kind", "lookup")),
            "narrative": response.narrative,
            "display_columns": display.get("columns", list(table[0]) if table else []),
            "display_rows": table[:initial_count],
            "all_rows": table,
            "total_result_count": display.get("total_result_count", len(table)),
            "available_result_count": display.get("available_result_count", len(table)),
            "result_counts": display.get("result_counts", {}),
            "has_more": bool(display.get("has_more", False)),
            "summary_only": bool(display.get("summary_only", False)),
            "followups": getattr(response, "followups", []),
            "scope": getattr(response, "result_counts", {}),
            "clarification_kind": getattr(response, "clarification_kind", None),
            "clarification_prompt": getattr(response, "clarification_prompt", None),
            "clarification_options": getattr(response, "clarification_options", []),
            "pending_clarification_id": getattr(response, "pending_clarification_id", None),
            "resolved_context": getattr(response, "resolved_context", {}),
            "answer_facts": getattr(response, "answer_facts", None),
        }
        chat_response = ChatResponse(
            session_id=session_id,
            status=response.status,
            intent=intent_payload,
            limit=(
                getattr(response_intent, "limit", None)
                if response_intent is not None
                else None
            ),
            narrative=response.narrative,
            table=table,
            interpretation=response.interpretation,
            trust_panel=response.trust_panel,
            basis=response.basis,
            usage=_usage_payload(response.usage),
            error=response.error,
            narration_verified=response.narration_verified,
            narration_regenerated=response.narration_regenerated,
            narration_fallback=response.narration_fallback,
            conversation_id=conversation_id,
            user_context=getattr(response, "user_context", {"user_id": context.user_id, "display_name": context.display_name, "persona": context.persona}),
            response_type=getattr(response, "response_type", getattr(response_intent, "question_kind", "lookup")),
            display=getattr(response, "display", {}),
            followups=getattr(response, "followups", []),
            response=response_payload,
            clarification_kind=getattr(response, "clarification_kind", None),
            clarification_prompt=getattr(response, "clarification_prompt", None),
            clarification_options=getattr(response, "clarification_options", []),
            pending_clarification_id=getattr(response, "pending_clarification_id", None),
            resolved_context=getattr(response, "resolved_context", {}),
            answer_facts=getattr(response, "answer_facts", None),
        )
        normalized_response = normalize_json_value(chat_response.model_dump())
        if response.narrative or response.error:
            # Persist the presentation evidence alongside assistant text so a
            # restored conversation renders the same table and Trust Panel.
            # The pending clarification state remains server-owned metadata.
            metadata = dict(normalized_response)
            metadata["pending_state"] = pending_state
            store.add_message(
                conversation_id,
                "assistant",
                response.narrative or response.error,
                metadata,
            )
        return normalized_response

    @app.get("/conversations/{user_id}")
    def conversations(user_id: str, session_token: str | None = Cookie(default=None, alias="inventory_session")) -> dict[str, Any]:
        scope = current_scope(session_token)
        if scope is not None and str(scope.user.user_id) != user_id and scope.user.username != user_id:
            raise HTTPException(status_code=403, detail="Conversation access denied")
        return {"conversations": store.recents(user_id)}

    @app.get("/conversations/{user_id}/{conversation_id}")
    def conversation(user_id: str, conversation_id: str, session_token: str | None = Cookie(default=None, alias="inventory_session")) -> dict[str, Any]:
        scope = current_scope(session_token)
        if scope is not None and str(scope.user.user_id) != user_id and scope.user.username != user_id:
            raise HTTPException(status_code=403, detail="Conversation access denied")
        return {"conversation_id": conversation_id, "messages": store.messages(user_id, conversation_id)}

    @app.patch("/conversations/{user_id}/{conversation_id}")
    def rename_conversation(user_id: str, conversation_id: str, payload: dict[str, str], session_token: str | None = Cookie(default=None, alias="inventory_session")) -> dict[str, Any]:
        scope = current_scope(session_token)
        if scope is not None and str(scope.user.user_id) != user_id and scope.user.username != user_id:
            raise HTTPException(status_code=403, detail="Conversation access denied")
        title = str(payload.get("title", ""))
        if not store.rename(user_id, conversation_id, title):
            return {"status": "not_found"}
        return {"status": "ok", "conversation_id": conversation_id, "title": title.strip()}

    @app.delete("/conversations/{user_id}/{conversation_id}")
    def delete_conversation(user_id: str, conversation_id: str, session_token: str | None = Cookie(default=None, alias="inventory_session")) -> dict[str, Any]:
        scope = current_scope(session_token)
        if scope is not None and str(scope.user.user_id) != user_id and scope.user.username != user_id:
            raise HTTPException(status_code=403, detail="Conversation access denied")
        return {"status": "ok" if store.delete(user_id, conversation_id) else "not_found"}

    return app


app = create_app()
