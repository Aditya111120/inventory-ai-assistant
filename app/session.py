"""Per-session conversational memory.

Stores only deterministic context needed for follow-up questions.
No secrets and no raw conversation transcript are stored.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional


@dataclass
class SessionState:
    """State retained for one chatbot session."""

    last_intent_summary: str = ""
    last_product_ids: Optional[list[int]] = None
    last_store_ids: Optional[list[int]] = None
    last_capability: str = ""
    last_question_kind: str = ""
    last_metric: str = ""
    last_group_by: str = ""
    last_sort_metric: str = ""
    last_sort_direction: str = ""
    last_subject: str = ""
    last_comparison_mode: str = ""
    last_scope: str = ""
    last_time: Optional[dict] = None
    last_intent: Optional[dict] = None
    last_basis: str = ""
    # Structured, server-owned clarification state.  Option IDs in this
    # object are opaque tokens; database IDs are never sent to the LLM/UI.
    pending_clarification: Optional[dict] = None


@dataclass
class SessionMemory:
    """In-memory session store.

    The API layer will provide one SessionMemory instance for the running app.
    """

    _sessions: dict[str, SessionState] = field(default_factory=dict)

    def get(self, session_id: str) -> SessionState:
        """Return existing state or create an empty state."""

        if not session_id:
            raise ValueError("session_id is required")

        return self._sessions.setdefault(session_id, SessionState())

    def update(
        self,
        session_id: str,
        *,
        intent=None,
        intent_summary: str,
        product_ids: Optional[list[int]],
        store_ids: Optional[list[int]],
        basis: str = "",
    ) -> SessionState:
        """Store the latest deterministic context for a session."""

        state = self.get(session_id)

        state.last_intent_summary = intent_summary
        state.last_product_ids = (
            None if product_ids is None else list(product_ids)
        )
        state.last_store_ids = (
            None if store_ids is None else list(store_ids)
        )

        if intent is not None:
            state.last_intent = (
                intent.model_dump()
                if hasattr(intent, "model_dump")
                else dict(intent)
            )
            state.last_capability = getattr(intent, "capability", "")
            state.last_question_kind = getattr(intent, "question_kind", "")
            metrics = getattr(intent, "metrics", None) or []
            state.last_metric = metrics[0] if metrics else ""
            state.last_group_by = getattr(intent, "group_by", None) or ""
            sort = getattr(intent, "sort", None)
            if sort:
                state.last_sort_metric = getattr(sort, "by", "")
                state.last_sort_direction = getattr(sort, "direction", "")
            else:
                state.last_sort_metric = ""
                state.last_sort_direction = ""
            state.last_subject = (
                getattr(intent, "comparison_subject", None) or ""
            )
            state.last_comparison_mode = (
                getattr(intent, "comparison_mode", None) or ""
            )
            state.last_scope = (
                "previous"
                if getattr(intent, "uses_previous_result", False)
                else "global"
            )
            time = getattr(intent, "time", None)
            state.last_time = (
                time.model_dump() if hasattr(time, "model_dump") else None
            )
            state.last_basis = basis

        return state

    def set_pending(self, session_id: str, pending: dict) -> SessionState:
        """Store a clarification request for exactly one session."""
        state = self.get(session_id)
        state.pending_clarification = dict(pending)
        return state

    def pending(self, session_id: str) -> Optional[dict]:
        return self.get(session_id).pending_clarification

    def clear_pending(self, session_id: str) -> None:
        self.get(session_id).pending_clarification = None

    def previous_context(self, session_id: str) -> str:
        """
        Build semantic context for the next intent parse.

        Previous database IDs are intentionally NOT exposed to the LLM.
        They are internal execution state and are applied deterministically
        by the orchestrator/query layer.
        """

        state = self.get(session_id)

        parts = []

        if state.last_intent_summary:
            parts.append(state.last_intent_summary)

        if state.last_capability:
            parts.append(f"capability={state.last_capability}")

        if state.last_question_kind:
            parts.append(f"question_kind={state.last_question_kind}")

        if state.last_metric:
            parts.append(f"metric={state.last_metric}")

        if state.last_group_by:
            parts.append(f"group_by={state.last_group_by}")

        if state.last_sort_metric:
            parts.append(
                f"sort={state.last_sort_metric}:{state.last_sort_direction}"
            )

        if state.last_subject:
            parts.append(f"subject={state.last_subject}")

        if state.last_comparison_mode:
            parts.append(f"comparison_mode={state.last_comparison_mode}")

        if state.last_scope:
            parts.append(f"scope={state.last_scope}")

        if state.last_intent:
            parts.append(
                f"classification={state.last_intent.get('inventory_classification') or 'none'}"
            )
            filters = state.last_intent.get("filters") or {}
            for field in (
                "products", "brands", "categories", "subcategories",
                "stores", "cities", "regions",
            ):
                values = filters.get(field) or []
                if values:
                    parts.append(f"{field}={','.join(map(str, values))}")
            if state.last_basis:
                parts.append(f"basis={state.last_basis}")

        if state.last_time and state.last_time.get("kind", "none") != "none":
            parts.append("time=" + ";".join(
                f"{key}={value}" for key, value in state.last_time.items()
                if value not in (None, "", False)
            ))

        # Database IDs remain backend state and are never sent to the LLM.
        return "; ".join(parts)

    def clear(self, session_id: str) -> None:
        """Remove one session."""

        self._sessions.pop(session_id, None)

    def clear_all(self) -> None:
        """Remove all sessions."""

        self._sessions.clear()
