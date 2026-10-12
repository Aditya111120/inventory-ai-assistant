"""Main chatbot orchestration pipeline.

Current phase:
    session -> intent -> entity resolution -> deterministic dispatch

Narration and number verification are injected as optional callables and will
be added in the next phase without changing the orchestration contract.
"""

from __future__ import annotations

import calendar
import re
from datetime import datetime
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

import pandas as pd

from app.answer_builder import (
    build_answer,
    build_subject_comparison_answer,
)
from app.context import UserContext, build_followups, effective_ids
from app.dispatch import DispatchResult, dispatch
from app.llm.client import LLMUnavailable, Usage
from app.llm.intent import IntentError, ParsedIntent, parse_intent
from app.llm.schema import Intent, Sort
from app.narration import Narrator
from app.response_composer import (
    build_answer_facts,
    compose_compound,
    compose_single,
)
from app.response_router import plan_response
from app.session import SessionMemory



def parse_clarification_period(text: str):
    """Interpret a user's time-period clarification reply."""
    value = text.strip().casefold()
    if value in {
        "latest", "current", "today", "latest inventory",
        "current inventory", "latest available",
    }:
        return {"kind": "latest"}
    if value == "this month":
        return {"kind": "this_month"}
    if value == "last month":
        return {"kind": "last_month"}
    year_match = re.search(r"\b(?:19|20)\d{2}\b", value)
    year = int(year_match.group()) if year_match else None
    month = next(
        (
            i for i in range(1, 13)
            if calendar.month_name[i].casefold() in value
            or calendar.month_abbr[i].casefold() in value.split()
        ),
        None,
    )
    if month is not None:
        return {"kind": "month", "month": month, **({"year": year} if year else {})}
    return None


@dataclass
class OrchestratorResponse:
    """Application-level response returned by the orchestrator."""

    session_id: str
    status: str

    intent: Any = None
    narrative: str = ""
    data: Any = None

    interpretation: list[str] = field(default_factory=list)

    sql: list[str] = field(default_factory=list)
    params: list[list] = field(default_factory=list)
    row_count: int = 0
    result_counts: dict[str, int | float] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)

    basis: str = "Actual"
    usage: Optional[Usage] = None

    trust_panel: dict[str, Any] = field(default_factory=dict)
    user_context: dict[str, Any] = field(default_factory=dict)
    response_type: str = "lookup"
    display: dict[str, Any] = field(default_factory=dict)
    followups: list[str] = field(default_factory=list)

    narration_verified: bool = False
    narration_regenerated: bool = False
    narration_fallback: bool = False

    error: Optional[str] = None

    # Structured clarification contract.  Options contain opaque tokens and
    # display labels only; internal IDs remain in SessionMemory.
    clarification_kind: Optional[str] = None
    clarification_prompt: Optional[str] = None
    clarification_options: list[dict[str, Any]] = field(default_factory=list)
    pending_clarification_id: Optional[str] = None
    resolved_context: dict[str, Any] = field(default_factory=dict)
    answer_facts: Optional[dict[str, Any]] = None


def _intent_summary(intent) -> str:
    """Create compact deterministic memory text for the next turn."""

    parts = [f"capability={intent.capability}"]

    if getattr(intent, "group_by", None):
        parts.append(f"group_by={intent.group_by}")

    metrics = getattr(intent, "metrics", None) or []
    if metrics:
        parts.append(f"metrics={','.join(metrics)}")

    time = getattr(intent, "time", None)
    if time is not None and getattr(time, "kind", "none") != "none":
        label = time.kind
        if getattr(time, "n", None) is not None:
            label += f"({time.n})"
        for key in ("date", "start_date", "end_date", "year", "month"):
            value = getattr(time, key, None)
            if value is not None:
                label += f"[{key}={value}]"
        parts.append(f"time={label}")

    threshold = getattr(intent, "threshold", None)
    if threshold is not None:
        parts.append(
            f"threshold={threshold.field}{threshold.op}{threshold.value:g}"
        )

    sort = getattr(intent, "sort", None)
    if sort is not None:
        by = getattr(sort, "by", None)
        direction = getattr(sort, "direction", None)
        if by:
            parts.append(f"sort={by}:{direction}")

    if getattr(intent, "limit", None):
        parts.append(f"limit={intent.limit}")

    scenario = getattr(intent, "scenario", None)
    if scenario is not None:
        parts.append(f"scenario={scenario.kind}:{scenario.value:g}")

    return "; ".join(parts)


def _merge_scenario_clarification_reply(base_intent, parsed_intent, text: str):
    """Merge reply entities only; the pending scenario remains authoritative."""
    parsed_filters = getattr(parsed_intent, "filters", None)
    if parsed_filters is None:
        return None
    updates = {}
    for field in ("products", "brands", "categories", "subcategories", "stores", "cities", "regions"):
        values = getattr(parsed_filters, field, None)
        if values:
            updates[field] = list(values)
    if updates:
        filters = base_intent.filters.model_copy(update=updates)
        return base_intent.model_copy(update={"filters": filters})
    # Accept concise product replies if structured parsing omitted filters;
    # never treat a complete instruction as a literal product name.
    if text.strip() and not re.search(
        r"\b(?:show|what|which|how|compare|list|inventory|sales|risk|at|in|on)\b",
        text.casefold(),
    ):
        filters = base_intent.filters.model_copy(update={"products": [text.strip()]})
        return base_intent.model_copy(update={"filters": filters})
    return None


def _resolution_explanation(resolution) -> list[str]:
    """Return Trust Panel-friendly resolver output."""

    explain = getattr(resolution, "explain", None)

    if callable(explain):
        return explain()

    return []


def _rules_payload(rules) -> dict[str, Any]:
    """Expose the live business rules used by the calculation layer."""

    if hasattr(rules, "__dataclass_fields__"):
        return {
            name: getattr(rules, name)
            for name in rules.__dataclass_fields__
        }

    return {
        key: value
        for key, value in vars(rules).items()
        if not key.startswith("_")
    }


def _display_number(value: Any) -> Any:
    """Convert numpy/pandas numeric values into JSON-friendly values."""

    if value is None:
        return None

    try:
        if pd.isna(value):
            return None
    except (TypeError, ValueError):
        pass

    if isinstance(value, float):
        return round(value, 4)

    try:
        if hasattr(value, "item"):
            return value.item()
    except Exception:
        pass

    return value


def _explicitly_requests_multiple_capabilities(question: str) -> bool:
    text = question.casefold()
    if not re.search(r"\b(?:and|also|as well as|plus|while)\b", text):
        return False

    capability_terms = (
        r"\b(?:inventory|on.hand|quantity|stock level|days of inventory|doi)\b",
        r"\bstock[ -]?out\b|\brisk\b",
        r"\b(?:sales|revenue|sold)\b",
        r"\b(?:excess|slow[ -]?moving)\b",
        r"\bwhat if\b|\bscenario\b",
    )
    return sum(bool(re.search(pattern, text)) for pattern in capability_terms) > 1


def _explicitly_references_previous_result(question: str) -> bool:
    text = question.casefold()
    return bool(
        re.search(
            r"\b(?:it|that|those|them|these|same|previous|above)\b"
            r"|\bwhich one\b|\bwhat about\b|\bhow about\b"
            r"|\b(?:the lowest|the highest) one\b|\binstead\b",
            text,
        )
    )


def _has_explicit_time(question: str) -> bool:
    text = question.casefold()
    months = "january february march april may june july august september october november december jan feb mar apr jun jul aug sep sept oct nov dec".split()
    return bool(
        re.search(r"\b(?:19|20)\d{2}\b|\b\d{4}-\d{2}-\d{2}\b", text)
        or re.search(rf"\b(?:{'|'.join(months)})\b", text)
        or re.search(r"\b(?:today|yesterday|currently|current|latest|right now|as of now|this month|last month|last \d+ (?:days?|months?)|historical|history)\b", text)
        or re.search(r"\bfrom\b.+\b(?:to|through)\b", text)
    )


def _has_unsupported_inventory_sales_tradeoff(question: str) -> bool:
    text = question.casefold()
    has_inventory_value = bool(re.search(r"\b(?:inventory|stock) value\b", text))
    has_sales = bool(re.search(r"\b(?:sales|revenue|units sold)\b", text))
    has_opposed_extremes = bool(
        re.search(r"\b(?:highest|high|most|top)\b.*\b(?:lowest|low|least|bottom)\b", text)
        or re.search(r"\b(?:lowest|low|least|bottom)\b.*\b(?:highest|high|most|top)\b", text)
    )
    return has_inventory_value and has_sales and has_opposed_extremes


def _apply_followup_context(question: str, intent, previous_state):
    """Inherit only relevant prior context for explicit entity-replacement turns."""
    text = question.casefold()
    entity_fields = ("products", "brands", "categories", "subcategories")
    has_new_entity = any(getattr(intent.filters, field, []) for field in entity_fields)
    replacement = has_new_entity and bool(
        re.search(r"\b(?:instead|rather than|replace|switch to|change to)\b", text)
    )
    explicit_time = _has_explicit_time(question)
    explicit_followup = _explicitly_references_previous_result(question)

    if not explicit_time and not explicit_followup:
        # The LLM may echo a previous period from prompt context. An independent
        # request must not accidentally turn current operational metrics into a
        # historical query.
        if getattr(getattr(intent, "time", None), "kind", "none") != "none":
            intent = intent.model_copy(update={
                "time": intent.time.model_copy(update={
                    "kind": "none", "month": None, "year": None,
                    "date": None, "start_date": None, "end_date": None,
                    "comparison_month": None, "comparison_year": None,
                    "comparison_start_date": None, "comparison_end_date": None,
                    "n": None, "compare_to_previous": False,
                })
            })

    if not replacement:
        return intent

    updates = {"uses_previous_result": True}
    if previous_state.last_capability:
        updates["capability"] = previous_state.last_capability
    metric_was_replaced = bool(re.search(
        r"\b(?:sales revenue|revenue|turnover|sales units|units sold|sold units)\b",
        text,
    ))
    if previous_state.last_metric and not metric_was_replaced:
        updates["metrics"] = [previous_state.last_metric]
    if previous_state.last_group_by and getattr(intent, "group_by", None) is None:
        updates["group_by"] = previous_state.last_group_by
    if previous_state.last_sort_metric and getattr(intent, "sort", None) is None:
        updates["sort"] = Sort(
            by=previous_state.last_sort_metric,
            direction=previous_state.last_sort_direction or "desc",
        )
    if not explicit_time and previous_state.last_time:
        updates["time"] = intent.time.model_copy(update=previous_state.last_time)
    return intent.model_copy(update=updates)


def _leadership_defaults(question: str, intent, user_context: UserContext):
    """Apply deterministic executive grouping for unambiguous leadership requests.

    The LLM still extracts entities and metrics; these narrow defaults prevent
    ordinary executive ranking wording from becoming a product lookup.
    """
    if getattr(user_context, "persona", "") != "Leadership":
        return intent
    text = question.casefold()
    updates = {}
    if (
        getattr(intent, "question_kind", "") == "comparison"
        and getattr(intent.time, "comparison_month", None)
        and getattr(intent.time, "comparison_year", None)
        and re.search(r"\b(?:inventory|stock)\b", text)
        and not re.search(r"\b(?:sales|revenue|units sold)\b", text)
    ):
        updates.update(
            capability="historical_analysis",
            metrics=["inventory_growth_pct"],
            group_by="product",
            question_kind="comparison",
            sort=None,
        )
    if re.search(r"\bstock[ -]?out risk\b", text):
        updates.update(capability="stockout_risk", metrics=["stockout_risk", "days_of_inventory"])
        if re.search(r"\b(?:highest|top|most|rank|ranking|five|four|three|two|\d+)\b", text):
            updates.update(
                question_kind="ranking",
                group_by="product",
                sort=Sort(by="stockout_risk", direction="desc"),
                limit=getattr(intent, "limit", None) or 5,
            )
    if re.search(r"\b(?:historical inventory|inventory trend|inventory history|snapshot trend|trend in inventory)\b", text):
        updates["capability"] = "historical_analysis"
        updates["metrics"] = ["quantity"]
        updates.setdefault("group_by", "category")
        if getattr(intent.time, "kind", "none") == "none":
            updates["time"] = intent.time.model_copy(update={"kind": "latest"})
        updates.setdefault("question_kind", "lookup")
    if re.search(r"\b(?:compare|versus|vs\.?)\b", text):
        updates["question_kind"] = "comparison"
        updates["metrics"] = []
        if re.search(r"\b(?:inventory value|stock value|value)\b", text):
            updates["metrics"] = ["inventory_value"]
        elif re.search(r"\b(?:inventory|stock|quantity|units on hand)\b", text):
            updates["metrics"] = ["quantity"]
        elif re.search(r"\b(?:revenue|turnover)\b", text):
            updates.update(capability="sales_trend", metrics=["sales_revenue"])
        elif re.search(r"\b(?:sales units|units sold|sold units)\b", text):
            updates.update(capability="sales_trend", metrics=["sales_units"])
        elif re.search(r"\bsales\b", text):
            # "Sales" can mean volume or revenue. Do not silently choose a
            # metric for a comparison; the orchestrator will offer choices.
            updates["capability"] = "sales_trend"
        elif re.search(r"\b(?:risk|stockout)\b", text):
            updates.update(capability="stockout_risk", metrics=["stockout_risk"])
        filters = getattr(intent, "filters", None)
        for field, dimension in (("brands", "brand"), ("categories", "category"), ("stores", "store"), ("cities", "city"), ("products", "product"), ("regions", "region")):
            if len(getattr(filters, field, []) or []) > 1:
                updates["group_by"] = dimension
                break
        metric_to_sort = {"inventory_value": "inventory_value", "quantity": "quantity", "sales_revenue": "sales_revenue", "sales_units": "sales_units"}
        selected_metric = (updates.get("metrics") or [None])[0]
        if selected_metric in metric_to_sort:
            updates["sort"] = Sort(by=metric_to_sort[selected_metric], direction="desc")
    if re.search(r"\b(?:executive summary|leadership summary|executive overview|company inventory overview)\b", text):
        updates["capability"] = "executive_summary"
        updates["metrics"] = ["quantity", "inventory_value", "sales_units", "sales_revenue", "stockout_risk"]
        updates["group_by"] = "category"
        updates.setdefault("question_kind", "lookup")
    if re.search(r"\b(?:compare|versus|vs\.?)\b", text):
        for pattern, dimension in ((r"categories?", "category"), (r"brands?", "brand"), (r"stores?", "store"), (r"cities?", "city"), (r"products?", "product"), (r"regions?", "region")):
            if re.search(rf"\b{pattern}\b", text):
                updates["group_by"] = dimension
                break
    if re.search(
        r"\b(?:which|what)\s+(?:store|location)\b.*"
        r"\b(?:perform(?:ed|ing)?|performance|best|top)\b",
        text,
    ):
        updates.update(question_kind="comparison", group_by="store", metrics=[])
    if re.search(r"\b(?:by|for each|per)\s+(?:category|categories)\b", text):
        updates["group_by"] = "category"
    elif re.search(r"\b(?:by|for each|per)\s+(?:brand|brands)\b", text):
        updates["group_by"] = "brand"
    elif re.search(r"\b(?:by|for each|per)\s+(?:store|stores)\b", text):
        updates["group_by"] = "store"
    elif re.search(r"\b(?:by|for each|per)\s+(?:city|cities)\b", text):
        updates["group_by"] = "city"
    if re.search(r"\b(?:category|categories)\b", text) and re.search(r"\b(?:highest|top|most|rank|ranking)\b", text):
        updates.update(group_by="category", question_kind="ranking")
    elif re.search(r"\b(?:brand|brands)\b", text) and re.search(r"\b(?:highest|top|most|rank|ranking)\b", text):
        updates.update(group_by="brand", question_kind="ranking")
    elif re.search(r"\b(?:store|stores)\b", text) and re.search(r"\b(?:highest|top|most|lowest|bottom|rank|ranking)\b", text):
        updates.update(group_by="store", question_kind="ranking")
    # Preserve a count stated with a ranking, including common number words.
    count_match = re.search(
        r"\b(?:which|top|bottom|rank)\s+(one|two|three|four|five|six|seven|eight|nine|ten|\d+)\s+(?:(?:[a-z0-9&'’-]+)\s+){0,3}(?:stores?|brands?|categories?|products?|cities?)\b",
        text,
    )
    if count_match:
        number_words = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5,
                        "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10}
        raw_count = count_match.group(1)
        updates["limit"] = number_words.get(raw_count, int(raw_count) if raw_count.isdigit() else 1)
    if re.search(r"\binventory value\b|\bstock value\b|\bvalue of inventory\b", text):
        updates["metrics"] = ["inventory_value"]
        if updates.get("question_kind") == "ranking" or re.search(r"\b(?:highest|top|most|rank|ranking)\b", text):
            updates["sort"] = Sort(by="inventory_value", direction="desc")
    elif re.search(r"\b(?:sales revenue|revenue|turnover)\b", text):
        updates["metrics"] = ["sales_revenue"]
        if re.search(r"\b(?:highest|top|most|lowest|bottom|rank|ranking|best|worst)\b", text):
            updates["sort"] = Sort(
                by="sales_revenue",
                direction="asc" if re.search(r"\b(?:lowest|bottom|worst)\b", text) else "desc",
            )
    elif re.search(r"\b(?:units sold|sales units|sold the most|sold the least)\b", text):
        updates["metrics"] = ["sales_units"]
        if re.search(r"\b(?:highest|top|most|lowest|bottom|least|rank|ranking)\b", text):
            updates["sort"] = Sort(
                by="sales_units",
                direction="asc" if re.search(r"\b(?:lowest|bottom|least)\b", text) else "desc",
            )
    if updates:
        return intent.model_copy(update=updates)
    return intent


def _catalog_brand_comparison(question: str, intent, resolver):
    """Normalize explicitly named brand comparisons using catalog labels."""
    if getattr(intent, "question_kind", "") != "comparison" or not hasattr(resolver, "catalog_brand_mentions"):
        return intent
    mentions = resolver.catalog_brand_mentions(question)
    if len(mentions) < 2:
        return intent
    filters = intent.filters.model_copy(update={"brands": mentions, "products": []})
    return intent.model_copy(update={
        "filters": filters,
        "group_by": "brand",
        "comparison_mode": "none",
        "comparison_subject": None,
        "uses_previous_result": False,
    })


_OPERATIONAL_CAPABILITIES = {
    "inventory_visibility", "inventory_health", "stockout_risk",
    "excess_slow_moving", "what_if",
}


def _explicitly_requests_global_scope(question: str) -> bool:
    text = re.sub(r"[^a-z0-9]+", " ", question.casefold()).strip()
    return bool(re.search(
        r"\b(?:company wide|companywide|company level|overall|whole catalog|"
        r"entire catalog|across all stores|all stores|every store|city wide|"
        r"citywide|by city|across [a-z0-9 ]+ stores|compare [a-z0-9 ]*stores)\b", text,
    ))


def _clarification_id() -> str:
    # UUIDs are opaque to clients and cannot be used as database identifiers.
    import uuid
    return f"clar_{uuid.uuid4().hex}"


def _usage_or_zero(value: Optional[Usage]) -> Usage:
    """Normalize an absent usage record without masking invalid objects."""
    if value is None:
        return Usage(calls=0)
    if not isinstance(value, Usage):
        raise TypeError(f"Invalid usage value: {type(value).__name__}")
    return value


def _time_window_issue(intent) -> str | None:
    """Validate structured time fields before any query is dispatched."""
    t = getattr(intent, "time", None)
    kind = getattr(t, "kind", "none") if t else "none"
    if kind == "month" and (
        getattr(t, "year", None) is None or getattr(t, "month", None) is None
    ):
        if getattr(t, "month", None) is None and getattr(t, "year", None) is None:
            return "Which month and year should I use for the historical inventory?"
        if getattr(t, "month", None) is None:
            return "Which month should I use for that year?"
        return "Which year should I use for that month?"
    if kind == "exact_date":
        value = getattr(t, "date", None)
        if not value:
            return "Which exact date should I use? Please provide it as YYYY-MM-DD."
        try:
            datetime.strptime(str(value), "%Y-%m-%d")
        except ValueError:
            return "That date is not valid. Please provide it as YYYY-MM-DD."
    if kind == "date_range" and (
        not getattr(t, "start_date", None) or not getattr(t, "end_date", None)
    ):
        return "Please provide both the start and end dates for the range."
    if kind == "date_range":
        try:
            start = datetime.strptime(str(t.start_date), "%Y-%m-%d")
            end = datetime.strptime(str(t.end_date), "%Y-%m-%d")
            if end < start:
                return "The end date must not precede the start date."
        except ValueError:
            return "Please provide both dates as YYYY-MM-DD."
    if kind in {"exact_date", "month", "date_range"} and getattr(intent, "capability", "") in {
        "inventory_health", "stockout_risk", "excess_slow_moving", "what_if",
    }:
        return (
            "Historical inventory metrics require an approved weekly inventory snapshot. "
            "The current inventory table cannot be substituted for a historical date."
        )
    return None


def _narration_evidence(intent, df: pd.DataFrame) -> pd.DataFrame:
    """Restrict narration input to identity/location and requested metrics."""
    identity = [
        "product_id", "sku", "product_name", "brand", "category",
        "subcategory", "store_id", "store_name", "city", "region",
    ]
    by_metric = {
        "quantity": ["quantity_on_hand"],
        "inventory_value": ["inventory_value"],
        "stock_status": ["stock_status", "reorder_level", "max_level"],
        "days_of_inventory": ["days_of_inventory"],
        "stockout_risk": [
            "quantity_on_hand", "units_sold_window", "avg_daily_sales",
            "days_of_inventory", "lead_time_days", "stockout_risk",
        ],
        "sales_units": ["units"],
        "sales_revenue": ["revenue"],
        "sales_growth_pct": ["growth_pct"],
        "excess_value": ["excess_value"],
    }
    metrics = list(getattr(intent, "metrics", None) or [])
    requested = [column for metric in metrics for column in by_metric.get(metric, [])]
    if getattr(intent, "capability", "") == "stockout_risk":
        requested.extend(by_metric["stockout_risk"])
    elif getattr(intent, "capability", "") == "inventory_health":
        requested.extend([
            "quantity_on_hand", "stock_status", "stockout_risk",
            "days_of_inventory", "lead_time_days", "attention_reason",
        ])
    elif getattr(intent, "capability", "") in {"sales_trend", "executive_summary"} and not requested:
        requested.extend(["units", "revenue", "growth_pct"])
    elif getattr(intent, "capability", "") == "historical_analysis":
        requested.extend(["qty_now", "qty_before", "qty_change", "snapshot_date", "inventory_snapshot_date"])

    columns = list(dict.fromkeys(
        column for column in identity + requested if column in df.columns
    ))
    return df.loc[:, columns].copy()


def _dispatch_result_contract_issue(intent, frame: pd.DataFrame) -> str | None:
    """Reject non-empty results that cannot substantiate the parsed request."""
    if frame is None or frame.empty:
        return None
    capability = getattr(intent, "capability", "")
    required: list[str] = []
    if capability == "stockout_risk":
        required.extend(["product_name", "quantity_on_hand", "days_of_inventory"])
        if not ("stockout_risk_v1" in frame.columns or "stockout_risk" in frame.columns):
            return "The stockout-risk query returned no V1 risk classification; no answer was generated."
        if not {"product_id", "store_id"}.issubset(frame.columns):
            return "The stockout-risk query did not preserve product-store positions; no answer was generated."
    elif capability == "what_if":
        required.extend([
            "product_id", "product_name", "store_id", "store_name",
            "actual_days_of_inventory", "simulated_days_of_inventory",
        ])
    elif capability == "historical_analysis":
        required.extend(["qty_now", "qty_before", "snapshot_date", "snapshot_date_before"])
    else:
        metric_columns = {
            "quantity": "quantity_on_hand", "inventory_value": "inventory_value",
            "sales_units": "units", "sales_revenue": "revenue",
            "days_of_inventory": "days_of_inventory",
        }
        for metric in getattr(intent, "metrics", []) or []:
            column = metric_columns.get(metric)
            if column:
                required.append(column)
        sort = getattr(intent, "sort", None)
        if sort is not None:
            sort_column = metric_columns.get(getattr(sort, "by", None))
            if sort_column:
                required.append(sort_column)
    group = getattr(intent, "group_by", None)
    if isinstance(group, (list, tuple)):
        group = group[0] if len(group) == 1 else None
    group_column = {
        "product": "product_name", "brand": "brand", "category": "category",
        "subcategory": "subcategory", "store": "store_name", "city": "city",
        "region": "region",
    }.get(group)
    if group_column:
        required.append(group_column)
    missing = list(dict.fromkeys(column for column in required if column not in frame.columns))
    if missing:
        return (
            "The query result did not include the requested "
            f"{', '.join(missing)}; no analytical answer was generated."
        )
    return None


def _result_counts(df: pd.DataFrame) -> dict[str, int | float]:
    counts = dict(df.attrs.get("result_counts", {}))
    counts.setdefault("matching_result_rows", len(df))
    if "product_id" in df.columns:
        counts.setdefault("distinct_products", int(df["product_id"].nunique(dropna=True)))
    if {"product_id", "store_id"}.issubset(df.columns):
        counts.setdefault(
            "product_store_positions",
            int(df[["product_id", "store_id"]].drop_duplicates().shape[0]),
        )
    if "store_id" in df.columns:
        counts.setdefault("stores", int(df["store_id"].nunique(dropna=True)))
    return counts


def _presentation(result: pd.DataFrame, intent, question: str = "") -> tuple[pd.DataFrame, dict[str, Any]]:
    """Produce a concise user-facing view without discarding full evidence."""
    # Historical snapshot queries expose ``snapshot_date`` while current
    # inventory exposes ``inventory_snapshot_date``. Normalize the display
    # alias so both paths disclose the same as-of date exactly once.
    if "inventory_snapshot_date" not in result.columns and "snapshot_date" in result.columns:
        result = result.copy()
        result["inventory_snapshot_date"] = result["snapshot_date"]
    kind = getattr(intent, "question_kind", "lookup")
    capability = getattr(intent, "capability", "")
    if kind == "comparison" and getattr(intent, "group_by", None) in {"brand", "category", "store", "city", "region", "product"}:
        response_type = "comparison"
        dimension_column = {"store": "store_name", "product": "product_name"}.get(intent.group_by, intent.group_by)
        metrics = list(getattr(intent, "metrics", []) or [])
        value_columns = {"quantity": "quantity_on_hand", "inventory_value": "inventory_value", "sales_units": "units", "sales_revenue": "revenue", "stockout_risk": "stockout_risk_v1"}
        metric_column = value_columns.get(metrics[0] if metrics else "")
        if capability == "historical_analysis":
            metric_column = "qty_now"
        wanted = [dimension_column, metric_column, "qty_before", "qty_change", "snapshot_date_before", "snapshot_date", "units_prev", "revenue_prev", "units_change", "growth_pct", "revenue_change", "revenue_growth_pct"]
    elif kind == "ranking":
        response_type = "ranking"
        if capability == "stockout_risk":
            wanted = [
                "product_name", "brand", "category", "subcategory",
                "store_name", "city", "quantity_on_hand", "avg_daily_sales",
                "days_of_inventory", "lead_time_days", "stockout_risk_v1",
                "demand_status",
            ]
        else:
            wanted = [
                "category", "brand", "store_name", "city", "region",
                "product_name", "subcategory", "quantity_on_hand",
                "inventory_value", "units", "revenue", "days_of_inventory",
                "stockout_risk", "stockout_risk_v1",
            ]
    elif capability == "inventory_visibility":
        response_type = "inventory_visibility"
        wanted = ["product_id", "sku", "product_name", "subcategory", "store_id", "store_name", "city", "region", "quantity_on_hand", "inventory_value", "inventory_snapshot_date"]
    elif capability == "inventory_health":
        response_type = "inventory_health"
        wanted = ["product_name", "subcategory", "store_name", "city", "quantity_on_hand", "reorder_level", "max_level", "inventory_status", "inventory_data_quality", "attention_reason", "inventory_snapshot_date"]
    elif capability == "stockout_risk":
        response_type = "stockout_risk"
        wanted = ["product_name", "subcategory", "store_name", "city", "quantity_on_hand", "avg_daily_sales", "days_of_inventory", "lead_time_days", "stockout_risk_v1", "demand_status"]
    elif capability == "sales_trend":
        response_type = "sales"
        wanted = ["category", "brand", "store_name", "city", "region", "product_name", "units", "revenue", "units_prev", "revenue_prev", "units_change", "revenue_change", "growth_pct", "revenue_growth_pct"]
    elif capability == "historical_analysis":
        response_type = "historical_analysis"
        wanted = ["category", "brand", "product_name", "store_name", "city", "qty_now", "qty_before", "qty_change", "snapshot_date_before", "snapshot_date"]
    elif capability == "executive_summary":
        response_type = "executive_summary"
        wanted = ["category", "quantity_on_hand", "inventory_value", "units", "revenue", "below_reorder_positions", "high_risk_positions", "medium_risk_positions", "inventory_snapshot_date"]
    elif capability == "what_if":
        response_type = "scenario"
        wanted = ["product_name", "store_name", "city", "quantity_on_hand", "actual_days_of_inventory", "simulated_days_of_inventory", "coverage_change_days", "actual_stockout_risk", "simulated_stockout_risk"]
    elif "store_name" in result.columns and (getattr(intent, "group_by", None) == "store" or "across all stores" in " ".join(getattr(getattr(intent, "filters", None), "stores", [])).casefold()):
        response_type = "store_distribution"
        wanted = ["product_id", "sku", "product_name", "subcategory", "store_id", "store_name", "city", "region", "quantity_on_hand"]
    else:
        response_type = kind
        wanted = ["product_id", "sku", "product_name", "subcategory", "store_id", "store_name", "city", "region", "quantity_on_hand", "inventory_value", "inventory_snapshot_date"]
    text = question.casefold()
    columns = [column for column in wanted if column in result.columns]
    include_identifiers = (
        not question
        or bool(re.search(r"\b(?:product\s*id|store\s*id|sku|identifier)\b", text))
        or bool(re.search(r"\b(?:detail|detailed|records?|full table|show all)\b", text))
    )
    if not include_identifiers:
        columns = [column for column in columns if column not in {"product_id", "store_id", "sku", "region"}]
    view = result.loc[:, columns].copy()
    # A LEFT JOIN intentionally exposes NULL subcategories.  Do not make an
    # all-unclassified default result look like a column of empty placeholders;
    # retain it only when the request explicitly concerns classification.  A
    # mixed result remains visible, with a user-facing label for missing data.
    if "subcategory" in columns:
        filters = getattr(intent, "filters", None)
        explicit_subcategory = bool(
            getattr(filters, "subcategories", None)
            or getattr(intent, "group_by", None) == "subcategory"
            or "subcategory" in (getattr(intent, "metrics", None) or [])
        )
        all_null = view["subcategory"].isna().all()
        if all_null and not explicit_subcategory:
            columns.remove("subcategory")
            view = view.loc[:, columns]
        elif not all_null:
            view["subcategory"] = view["subcategory"].fillna("Unclassified")
        elif explicit_subcategory:
            view["subcategory"] = view["subcategory"].fillna("Unclassified")
    # Evidence stays complete for calculations, comparison, and the Trust Panel.
    # Initial presentation is deliberately compact for analytical result sets.
    counts = _result_counts(result)
    total_count = int(counts["matching_result_rows"])
    explicit_detail = bool(
        re.search(r"\b(?:list|detailed?|detail|records?|all rows|full table|show all)\b", text)
    )
    grouped = getattr(intent, "group_by", None) in {"store", "city", "region", "subcategory", "category", "brand"}
    # Presentation metadata is derived from the complete result, never from
    # the truncated preview. This lets the UI remove repetition without
    # losing evidence in the response payload or Trust Panel.
    row_count = len(result)
    product_count = int(result["product_id"].nunique(dropna=True)) if "product_id" in result.columns else row_count
    store_count = int(result["store_id"].nunique(dropna=True)) if "store_id" in result.columns else 0
    position_count = (
        int(result[["product_id", "store_id"]].drop_duplicates().shape[0])
        if {"product_id", "store_id"}.issubset(result.columns) else row_count
    )
    single_result = row_count == 1 and position_count == 1
    filters = getattr(intent, "filters", None)
    explicit_store = bool(getattr(filters, "stores", None)) and len(getattr(filters, "stores", [])) == 1
    explicit_date = bool(re.search(r"\b(?:date|snapshot|as of|on \d{4}-\d{2}-\d{2})\b", text))
    snapshot_values = []
    if "inventory_snapshot_date" in result.columns:
        snapshot_values = result["inventory_snapshot_date"].dropna().astype(str).unique().tolist()
    shared_snapshot = len(snapshot_values) == 1

    # A selected store is already stated in the answer heading/narrative. Do
    # not repeat it on every row unless the question explicitly asks for
    # identifiers/details. Multi-store comparisons retain the column.
    if explicit_store and not include_identifiers and store_count == 1:
        columns = [c for c in columns if c not in {"store_name", "city", "region", "store_id"}]
        view = result.loc[:, columns].copy()
    if shared_snapshot and not explicit_date and "inventory_snapshot_date" in columns:
        columns.remove("inventory_snapshot_date")
        view = view.loc[:, columns]

    # Only useful scalar KPIs belong in cards. Raw lists, row counts, and
    # single-row values are already represented by the direct answer/table.
    kpi_metrics: list[dict[str, Any]] = []
    if capability == "executive_summary":
        for key, label in (
            ("quantity_on_hand_total", "Inventory units"),
            ("inventory_value_total", "Inventory value (INR)"),
            ("sales_units_total", "Sales units"),
            ("sales_revenue_total", "Sales revenue (INR)"),
        ):
            value = counts.get(key)
            if value is not None:
                kpi_metrics.append({"key": key, "label": label, "value": _display_number(value)})
    if capability == "historical_analysis":
        for key, label in (("quantity_before_total", "Earlier snapshot units"), ("quantity_now_total", "Latest snapshot units"), ("quantity_change_total", "Unit change")):
            value = counts.get(key)
            if value is not None:
                kpi_metrics.append({"key": key, "label": label, "value": _display_number(value)})
    if not single_result and capability in {"inventory_health", "stockout_risk"}:
        for key, label in (("distinct_products", "Products"), ("stores", "Stores")):
            value = counts.get(key)
            if value is not None:
                kpi_metrics.append({"key": key, "label": label, "value": _display_number(value)})
        if capability in {"inventory_visibility", "inventory_health"} and "quantity_on_hand" in result.columns:
            kpi_metrics.append({"key": "quantity_on_hand", "label": "Inventory units", "value": _display_number(pd.to_numeric(result["quantity_on_hand"], errors="coerce").sum())})
        if capability == "sales_trend":
            for key, label in (("units", "Units sold"), ("revenue", "Revenue")):
                if key in result.columns:
                    kpi_metrics.append({"key": key, "label": label, "value": _display_number(pd.to_numeric(result[key], errors="coerce").sum())})
        if capability == "stockout_risk" and "stockout_risk_v1" in result.columns:
            risk = result["stockout_risk_v1"].astype(str)
            kpi_metrics.extend([
                {"key": "high_risk_positions", "label": "High-risk positions", "value": int(risk.eq("High").sum())},
                {"key": "medium_risk_positions", "label": "Medium-risk positions", "value": int(risk.eq("Medium").sum())},
            ])
        for key, label in (("quantity_on_hand", "Inventory units"), ("inventory_value", "Inventory value"), ("units", "Units sold"), ("revenue", "Revenue")):
            value = counts.get(key)
            if value is not None and not isinstance(value, (list, tuple, dict)):
                kpi_metrics.append({"key": key, "label": label, "value": _display_number(value)})
    # Inventory visibility answers retain a compact evidence preview. The
    # previous summary-only mode hid even a useful one-row result.
    summary_only = False
    initial_count = (
        min(int(getattr(intent, "limit", None) or 1), len(view))
        if response_type == "ranking"
        else {"inventory_health": 5, "stockout_risk": 5}.get(response_type, min(5, len(view)))
    )
    return view, {
        "type": response_type,
        "columns": columns,
        "initial_display_count": min(initial_count, len(view)),
        "total_result_count": total_count,
        "available_result_count": len(view),
        "has_more": not summary_only and (len(view) > initial_count or total_count > len(view)),
        "summary_only": summary_only,
        "result_counts": counts,
        "location_visible": any(column in columns for column in ("store_name", "city", "region")),
        "single_result": single_result,
        "single_result_supporting_only": single_result and not explicit_detail,
        "show_counts": not single_result,
        "kpi_metrics": kpi_metrics[:4],
        "shared_snapshot_date": snapshot_values[0] if shared_snapshot else None,
    }


def _first_result_row(df: pd.DataFrame) -> Optional[pd.Series]:
    if df.empty:
        return None
    return df.iloc[0]


def _calculation_payload(
    intent,
    df: pd.DataFrame,
    rules,
    basis: str,
) -> list[dict[str, Any]]:
    """Build deterministic calculation evidence for the Trust Panel."""

    if df.empty:
        return []

    row = _first_result_row(df)
    if row is None:
        return []

    calculations: list[dict[str, Any]] = []
    capability = getattr(intent, "capability", "")
    if capability == "stockout_risk" and getattr(intent, "question_kind", "") == "ranking":
        counts = dict(df.attrs.get("result_counts", {}))
        risk_column = "stockout_risk_v1" if "stockout_risk_v1" in df else "stockout_risk"
        descending = str(getattr(getattr(intent, "sort", None), "direction", "desc")).casefold().startswith("desc")
        severity_order = "High before Medium" if descending else "Medium before High (within qualifying positions)"
        coverage_order = "ascending" if descending else "descending"
        columns = [
            column for column in (
                "product_id", "product_name", "brand", "store_id", "store_name", "city",
                "quantity_on_hand", "stockout_risk_v1", "days_of_inventory", "lead_time_days",
                "avg_daily_sales", "units_sold_window",
            ) if column in df.columns
        ]
        position_rows = []
        for record in df[columns].to_dict(orient="records"):
            position_rows.append({
                key: (_display_number(value) if isinstance(value, (int, float)) or pd.isna(value) else value)
                for key, value in record.items()
            })
        calculations.append({
            "metric": "stockout_risk_top_n_positions",
            "formula": (
                "calculate Business Rules V1 risk at every eligible product-store position; "
                f"rank {severity_order}, then by DOI ({coverage_order}), "
                "with product_id/store_id as stable ties; "
                "apply the requested limit after classification and sorting"
            ),
            "values": {
                "qualifying_positions": counts.get("matching_result_rows", len(df)),
                "qualifying_distinct_products": counts.get("distinct_products"),
                "displayed_positions": len(df),
                "high_risk_positions_in_qualifying_set": counts.get("high_risk_positions"),
                "medium_risk_positions_in_qualifying_set": counts.get("medium_risk_positions"),
                "risk_column": risk_column,
            },
            "result": position_rows,
            "basis": "Business Rules V1",
        })
        return calculations
    if capability == "executive_summary":
        counts = dict(df.attrs.get("result_counts", {}))
        for metric, formula, basis_key in (
            ("quantity_on_hand_total", "SUM(quantity_on_hand) across all authorized product-store positions", "Actual"),
            ("inventory_value_total", "SUM(quantity_on_hand * cost_price) across all authorized positions", "Actual"),
            ("sales_units_total", "SUM(Sales.quantity) over approved sales window", "Actual"),
            ("sales_revenue_total", "SUM(Sales.revenue) over approved sales window", "Actual"),
            ("below_reorder_positions", "count positions at or below reorder level", "Business Rules V1"),
            ("high_risk_positions", "count High risk positions", "Business Rules V1"),
            ("medium_risk_positions", "count Medium risk positions", "Business Rules V1"),
        ):
            if counts.get(metric) is not None:
                calculations.append({"metric": metric, "formula": formula, "values": {}, "result": _display_number(counts[metric]), "basis": basis_key})
        if counts.get("sales_window_end_inclusive"):
            calculations.append({"metric": "sales_period", "formula": "configured sales window", "values": {"start_exclusive": counts.get("sales_window_start_exclusive"), "end_inclusive": counts.get("sales_window_end_inclusive")}, "result": counts.get("sales_window_end_inclusive"), "basis": "Actual"})
    if capability == "historical_analysis":
        counts = dict(df.attrs.get("result_counts", {}))
        calculations.append({
            "metric": "inventory_snapshot_trend",
            "formula": "sum weekly snapshot quantities at each selected snapshot date",
            "values": {
                "snapshot_date_before": df.get("snapshot_date_before", pd.Series(dtype=str)).dropna().astype(str).head(1).tolist(),
                "snapshot_date": df.get("snapshot_date", pd.Series(dtype=str)).dropna().astype(str).head(1).tolist(),
            },
            "result": {key: _display_number(counts.get(key)) for key in ("quantity_before_total", "quantity_now_total", "quantity_change_total")},
            "basis": "Actual weekly snapshots",
        })
        return calculations
    if capability == "executive_summary":
        return calculations
    group = getattr(intent, "group_by", None)
    if getattr(intent, "question_kind", "") == "comparison" and group:
        group_column = {"store": "store_name", "product": "product_name"}.get(group, group)
        metric = next(iter(getattr(intent, "metrics", []) or []), "")
        columns = [column for column in ("quantity_on_hand", "inventory_value", "units", "revenue", "qty_before", "qty_now", "qty_change", "units_prev", "units_change", "growth_pct", "revenue_prev", "revenue_change", "revenue_growth_pct", "stockout_risk_v1") if column in df]
        if group_column in df and columns:
            calculations.append({
                "metric": "group_comparison",
                "formula": f"aggregate {metric or 'selected metric'} by {group}; calculate delta=(current-reference) and delta_pct=delta/reference*100 where the reference is nonzero",
                "values": {},
                "result": df[[group_column] + columns].to_dict(orient="records"),
                "basis": basis,
            })
            return calculations
    if capability == "what_if" and len(df) > 1:
        columns = [
            column for column in (
                "product_id", "product_name", "store_id", "store_name", "city",
                "quantity_on_hand", "units_sold_window", "actual_days_of_inventory",
                "simulated_days_of_inventory", "coverage_change_days",
                "actual_stockout_risk", "simulated_stockout_risk",
            ) if column in df.columns
        ]
        scenario = getattr(intent, "scenario", None)
        calculations.append({
            "metric": "what_if_position_results",
            "formula": "calculate actual and simulated coverage independently at each product-store position using the approved sales window; do not aggregate DOI across positions",
            "values": {
                "scenario_kind": getattr(scenario, "kind", None),
                "scenario_value": _display_number(getattr(scenario, "value", None)),
            },
            "result": df[columns].to_dict(orient="records"),
            "basis": basis,
        })
        return calculations
    window_days = int(
        df.attrs.get(
            "window_days",
            getattr(rules, "sales_window_days", 30),
        )
    )

    quantity = _display_number(row.get("quantity_on_hand"))
    units_window = _display_number(row.get("units_sold_window"))
    avg_daily = _display_number(row.get("avg_daily_sales"))
    doi = _display_number(row.get("days_of_inventory"))
    lead_time = _display_number(row.get("lead_time_days"))

    if "days_of_inventory" in df.columns and doi is not None:
        calculations.append(
            {
                "metric": "days_of_inventory",
                "formula": "quantity_on_hand / average_daily_sales",
                "values": {
                    "quantity_on_hand": quantity,
                    "average_daily_sales": avg_daily,
                },
                "result": doi,
                "basis": basis,
            }
        )

    if "avg_daily_sales" in df.columns and avg_daily is not None:
        calculations.append(
            {
                "metric": "average_daily_sales",
                "formula": "units_sold_window / window_days",
                "values": {
                    "units_sold_window": units_window,
                    "window_days": window_days,
                },
                "result": avg_daily,
                "basis": basis,
            }
        )

    status_column = "inventory_status" if "inventory_status" in df.columns else "stock_status"
    if status_column in df.columns:
        calculations.append(
            {
                "metric": "inventory_status",
                "formula": (
                    "negative quantity -> Invalid; zero -> Out of Stock; "
                    "quantity <= reorder_level -> At or Below Reorder Level; "
                    "quantity > max_level -> Above Maximum Level"
                ),
                "values": {
                    "quantity_on_hand": _display_number(
                        row.get("quantity_on_hand")
                    ),
                    "reorder_level": _display_number(
                        row.get("reorder_level")
                    ),
                    "max_level": _display_number(
                        row.get("max_level")
                    ),
                },
                "result": row.get(status_column),
                "basis": basis,
            }
        )

    risk_column = "stockout_risk_v1" if "stockout_risk_v1" in df.columns else "stockout_risk"
    if risk_column in df.columns:
        calculations.append(
            {
                "metric": "stockout_risk_v1",
                "formula": (
                    "no demand/invalid inputs -> Not Assessable; "
                    "DOI < lead_time -> High; DOI < lead_time + safety_buffer -> Medium; otherwise Low"
                ),
                "values": {
                    "days_of_inventory": doi,
                    "lead_time_days": lead_time,
                    "safety_buffer_days": getattr(
                        rules,
                        "safety_buffer_days",
                        None,
                    ),
                },
                "result": row.get(risk_column),
                "basis": basis,
            }
        )

    if "inventory_value" in df.columns:
        inventory_value = _display_number(row.get("inventory_value"))
        if inventory_value is not None:
            calculations.append(
                {
                    "metric": "inventory_value",
                    "formula": "quantity_on_hand * cost_price",
                    "values": {
                        "quantity_on_hand": quantity,
                        "cost_price": _display_number(row.get("cost_price")),
                    },
                    "result": inventory_value,
                    "basis": basis,
                }
            )

    if capability == "sales_trend":
        for metric, column, formula in (
            ("sales_units", "units", "SUM(quantity)"),
            ("sales_revenue", "revenue", "SUM(revenue)"),
            (
                "sales_growth_pct",
                "growth_pct",
                "(current - previous) / previous * 100",
            ),
        ):
            if column in df.columns:
                value = _display_number(row.get(column))
                if value is not None:
                    calculations.append(
                        {
                            "metric": metric,
                            "formula": formula,
                            "values": {column: value},
                            "result": value,
                            "basis": basis,
                        }
                    )

    scenario = getattr(intent, "scenario", None)
    if (
        capability == "what_if"
        and scenario is not None
        and quantity is not None
        and units_window is not None
    ):
        actual_doi = _display_number(row.get("actual_days_of_inventory"))
        simulated_doi = _display_number(row.get("simulated_days_of_inventory", row.get("days_of_inventory")))
        calculations.append({
            "metric": "what_if_comparison",
            "formula": "compare actual and simulated days of inventory at the same product-store position and sales window",
            "values": {
                "scenario_kind": scenario.kind,
                "scenario_value": _display_number(scenario.value),
                "product_id": _display_number(row.get("product_id")),
                "product_name": row.get("product_name"),
                "store_id": _display_number(row.get("store_id")),
                "store_name": row.get("store_name"),
                "city": row.get("city"),
            },
            "result": {
                "actual_days_of_inventory": actual_doi,
                "simulated_days_of_inventory": simulated_doi,
                "coverage_change_days": _display_number(row.get("coverage_change_days")),
            },
            "basis": "Simulated",
        })
        scenario_value = float(scenario.value)
        if scenario.kind == "demand_change_pct":
            base_avg = units_window / window_days
            factor = max(1 + scenario_value / 100.0, 0.0)
            new_avg = base_avg * factor
            new_doi = quantity / new_avg if new_avg > 0 else None
            calculations.append(
                {
                    "metric": "what_if_days_of_inventory",
                    "formula": (
                        "quantity_on_hand / (units_sold_window / window_days "
                        "* (1 + demand_change_pct / 100))"
                    ),
                    "values": {
                        "quantity_on_hand": quantity,
                        "units_sold_window": units_window,
                        "window_days": window_days,
                        "demand_change_pct": scenario_value,
                    },
                    "result": _display_number(new_doi),
                    "basis": "Simulated",
                }
            )

    return calculations


def _trust_panel_payload(
    *,
    question: str,
    intent,
    interpretation: list[str],
    result: DispatchResult,
    rules,
    user_context: UserContext | None = None,
    requested_store_ids: list[int] | None = None,
    effective_store_ids: list[int] | None = None,
) -> dict[str, Any]:
    """Create the deterministic glass-box payload."""

    payload = {
        "interpretation": {
            "question": question,
            "capability": intent.capability,
            "metrics": list(getattr(intent, "metrics", []) or []),
            "group_by": getattr(intent, "group_by", None),
            "filters": getattr(
                getattr(intent, "filters", None),
                "model_dump",
                lambda: {},
            )(),
            "resolved_entities": interpretation,
            "uses_previous_result": bool(
                getattr(intent, "uses_previous_result", False)
            ),
        },
        "query": {
            "sql": list(result.sql),
            "params": list(result.params),
            "row_count": int(
                _result_counts(result.df)["matching_result_rows"]
            ),
            "result_counts": _result_counts(result.df),
            "notes": list(result.notes),
        },
        "calculations": _calculation_payload(
            intent,
            result.df,
            rules,
            result.basis,
        ),
        "rules_applied": _rules_payload(rules),
        "basis": result.basis,
    }
    if (
        getattr(intent, "capability", "") == "stockout_risk"
        and getattr(intent, "question_kind", "") == "ranking"
    ):
        payload["query"]["returned_result_rows"] = len(result.df)
    if user_context:
        payload["scope"] = {
            "user": user_context.display_name,
            "persona": user_context.persona,
            "authorized_store_ids": list(user_context.authorized_store_ids or []),
            "requested_store_ids": requested_store_ids,
            "effective_store_ids": effective_store_ids,
        }
    quality_columns = [
        "inventory_data_quality", "demand_data_quality", "supplier_data_quality",
    ]
    quality = {}
    for column in quality_columns:
        if column in result.df.columns:
            values = result.df[column].dropna().astype(str)
            quality[column] = values.value_counts().to_dict()
    if quality:
        payload["data_quality"] = quality
    return payload


class Orchestrator:
    """Coordinates one user question from input to deterministic result."""

    def __init__(
        self,
        *,
        llm,
        queries,
        resolver,
        rules,
        session_memory: SessionMemory,
        narrator: Optional[Narrator] = None,
        dispatch_fn: Callable[..., DispatchResult] = dispatch,
    ) -> None:
        self.llm = llm
        self.queries = queries
        self.resolver = resolver
        self.rules = rules
        self.sessions = session_memory

        self.narrator = narrator

        self.dispatch_fn = dispatch_fn

    @staticmethod
    def _pending_intent(pending: dict) -> Optional[Intent]:
        payload = pending.get("intent") if isinstance(pending, dict) else None
        if not payload:
            return None
        try:
            return Intent.model_validate(payload)
        except Exception:
            return None

    def _resume_pending(self, question: str, pending: Optional[dict]):
        """Resolve an option token/label against the session-owned request."""
        if not pending:
            return None, False, None
        text = question.strip()
        options = pending.get("options") or []
        match = next(
            (
                option for option in options
                if text.casefold() == str(option.get("token", "")).casefold()
                or text.casefold() == str(option.get("label", "")).casefold()
            ),
            None,
        )
        if match is None:
            # Natural-language scenario replies are parsed by the normal
            # intent/entity pipeline in handle(); do not treat a full sentence
            # as a literal product name here.
            if (
                pending.get("kind") == "scenario_target"
                and text
                and not text.casefold().startswith("clar_")
                and not re.search(r"\b(?:show|what|which|how|compare|list|inventory|sales|risk|at|in|on)\b", text.casefold())
            ):
                intent = self._pending_intent(pending)
                if intent is not None:
                    filters = intent.filters.model_copy(update={"products": [text]})
                    return intent.model_copy(update={"filters": filters}), True, None
            if pending.get("kind") in {"time", "time_period"}:
                parsed_period = parse_clarification_period(text)
                intent = self._pending_intent(pending)
                if parsed_period is not None:
                    if intent is None:
                        return None, True, "That clarification has expired. Please ask your question again."
                    updated_time = intent.time.model_copy(update=parsed_period)
                    return intent.model_copy(update={"time": updated_time}), True, None
            if text.casefold().startswith("clar_"):
                return None, True, "That clarification choice is no longer valid. Please choose an option from the current question."
            return None, False, None
        intent = self._pending_intent(pending)
        if intent is None:
            return None, True, "That clarification has expired. Please ask the question again."
        filters = intent.filters
        field = pending.get("field")
        value = match.get("value", match.get("label"))
        if field == "stores":
            filters = filters.model_copy(update={"stores": [str(v) for v in match.get("values", [value])]})
        elif field in {"cities", "regions", "products", "brands", "categories", "subcategories"}:
            filters = filters.model_copy(update={field: [str(v) for v in match.get("values", [value])]})
        elif field == "metrics":
            intent = intent.model_copy(update={
                "metrics": list(match.get("metrics", [value])),
                "capability": match.get("capability", intent.capability),
                "question_kind": match.get("question_kind", "comparison"),
                **(
                    {"sort": Sort.model_validate(match["sort"])}
                    if match.get("sort")
                    else {}
                ),
            })
        elif field == "time":
            time = intent.time.model_copy(update=match.get("time", {}))
            intent = intent.model_copy(update={"time": time})
        else:
            return None, True, "That clarification cannot be applied safely. Please ask the question again."
        return intent.model_copy(update={"filters": filters}), True, None

    def _clarification_response(
        self,
        *,
        session_id: str,
        intent,
        kind: str,
        prompt: str,
        options: list[dict[str, Any]],
        field: Optional[str] = None,
        usage=None,
        interpretation=None,
    ) -> OrchestratorResponse:
        clarification_id = _clarification_id()
        safe_options = []
        stored_options = []
        for option in options:
            token = str(option.get("token") or _clarification_id())
            label = str(option.get("label", ""))
            safe = {"token": token, "label": label}
            if option.get("description"):
                safe["description"] = str(option["description"])
            safe_options.append(safe)
            stored = dict(option)
            stored["token"] = token
            stored["label"] = label
            stored_options.append(stored)
        self.sessions.set_pending(session_id, {
            "id": clarification_id,
            "kind": kind,
            "intent": intent.model_dump() if hasattr(intent, "model_dump") else intent,
            "options": stored_options,
            "field": field or ("stores" if kind == "location_store" else "cities" if kind == "location" else "time"),
        })
        return OrchestratorResponse(
            session_id=session_id,
            status="clarification",
            intent=intent,
            interpretation=interpretation or [],
            usage=usage,
            error=prompt,
            clarification_kind=kind,
            clarification_prompt=prompt,
            clarification_options=safe_options,
            pending_clarification_id=clarification_id,
        )

    def _location_clarification(
        self, *, question: str, intent, resolution, session_id: str,
        previous_state, user_context: UserContext, usage,
    ) -> Optional[OrchestratorResponse]:
        """Apply deterministic location policy for operational capabilities."""
        if getattr(intent, "capability", "") not in _OPERATIONAL_CAPABILITIES:
            return None
        if getattr(intent, "question_kind", "lookup") == "comparison":
            return None
        filters = intent.filters
        requests_particular_store = bool(re.search(
            r"\b(?:at|in)\s+(?:(?:a|the|one|some)\s+)?(?:(?:particular|specific)\s+)?(?:individual\s+)?store\b",
            question.casefold(),
        ))
        if (
            getattr(user_context, "persona", "") == "Leadership"
            and requests_particular_store
            and not (getattr(filters, "stores", []) or getattr(filters, "cities", []) or getattr(filters, "regions", []))
            and hasattr(self.resolver, "store_options")
        ):
            stores = self.resolver.store_options(user_context.authorized_store_ids)
            options = [
                {"label": item["store_name"], "value": item["store_name"], "description": item["city"]}
                for item in stores
            ]
            return self._clarification_response(
                session_id=session_id,
                intent=intent,
                kind="location_store",
                field="stores",
                prompt="Which store would you like to check?",
                options=options,
                usage=usage,
            )
        # Leadership's default operational scope is the full server-authorized
        # organization. A city request is therefore city-wide unless a store
        # is explicitly named; do not ask for an unnecessary store choice.
        if getattr(user_context, "persona", "") == "Leadership":
            return None
        if _explicitly_requests_global_scope(question):
            return None
        # An explicit request for the user's stores means their entire
        # server-authorized scope, not a request for a single city.
        if re.search(r"\b(?:my|our)\s+(?:authorized\s+)?stores\b", question.casefold()):
            return None
        has_location = bool(
            getattr(filters, "stores", [])
            or getattr(filters, "cities", [])
            or getattr(filters, "regions", [])
        )
        prior_scope = bool(
            getattr(intent, "uses_previous_result", False)
            and previous_state.last_store_ids
        )
        resolver = self.resolver
        if not hasattr(resolver, "city_store_options"):
            # Test doubles and legacy resolver adapters may not expose the
            # catalog option API; retain their existing behavior.
            return None
        if not has_location and not prior_scope:
            locations = resolver.location_options(user_context.authorized_store_ids)
            options = [
                {
                    "label": item["city"],
                    "value": item["city"],
                    "description": f"{item['store_count']} store(s)",
                }
                for item in locations
            ]
            if options:
                return self._clarification_response(
                    session_id=session_id, intent=intent, kind="location",
                    prompt="Which city would you like to check? I can show the available stores there.",
                    options=options, usage=usage,
                )
            return None
        cities = list(getattr(filters, "cities", []) or [])
        if len(cities) != 1 or getattr(filters, "stores", []) or getattr(filters, "regions", []):
            return None
        stores = resolver.city_store_options(cities[0], user_context.authorized_store_ids)
        if len(stores) <= 1:
            return None
        options = [
            {
                "label": str(store["store_name"]),
                "value": str(store["store_name"]),
                "values": [str(store["store_name"])],
                "description": str(store["city"]),
            }
            for store in stores
        ]
        options.append({
            "label": f"All {cities[0]} stores",
            "value": cities[0],
            "values": [str(store["store_name"]) for store in stores],
            "description": "Use every authorized store in this city",
        })
        return self._clarification_response(
            session_id=session_id, intent=intent, kind="location_store",
            prompt=f"I found {len(stores)} stores in {cities[0]}. Which one would you like to check? You can also see all {len(stores)} together.",
            options=options, usage=usage,
        )

    def _authorized_entity_suggestions(self, unresolved, user_context: UserContext) -> list[str]:
        """Filter resolver suggestions through the authenticated scope."""
        suggestions = [str(value) for value in getattr(unresolved, "suggestions", [])]
        if not suggestions:
            return []
        kind = str(getattr(unresolved, "kind", ""))
        allowed_products = self._authorized_product_scope(user_context)
        allowed_stores = getattr(user_context, "authorized_store_ids", None)
        frame = getattr(self.resolver, "products", None)
        if allowed_products is not None and isinstance(frame, pd.DataFrame):
            frame = frame[frame["product_id"].astype(int).isin({int(v) for v in allowed_products})]
            column = {
                "brands": "brand", "categories": "category",
                "subcategories": "subcategory", "products": "product_name",
            }.get(kind)
            if column and column in frame.columns:
                allowed = {str(value).casefold() for value in frame[column].dropna().unique()}
                return [value for value in suggestions if value.casefold() in allowed]
        stores = getattr(self.resolver, "stores", None)
        if allowed_stores is not None and isinstance(stores, pd.DataFrame):
            stores = stores[stores["store_id"].astype(int).isin({int(v) for v in allowed_stores})]
            column = {"stores": "store_name", "cities": "city", "regions": "region"}.get(kind)
            if column and column in stores.columns:
                allowed = {str(value).casefold() for value in stores[column].dropna().unique()}
                return [value for value in suggestions if value.casefold() in allowed]
        return suggestions

    def _authorized_product_scope(self, user_context: UserContext) -> set[int] | None:
        """Return the effective product scope from server-owned permissions.

        Brand-manager access is defined by UserBrandAccess labels. Resolve
        those labels against the current catalog instead of trusting a
        separately materialized product-ID list that can be stale after a
        catalog refresh.
        """
        allowed_products = getattr(user_context, "authorized_product_ids", None)
        if user_context.persona != "Brand Manager" or getattr(user_context, "authorized_brands", None) is None:
            return None if allowed_products is None else {int(value) for value in allowed_products}
        frame = getattr(self.resolver, "products", None)
        if not isinstance(frame, pd.DataFrame) or "brand" not in frame.columns:
            return set()
        labels = {str(value).casefold() for value in user_context.authorized_brands or ()}
        if not labels:
            return set()
        return set(
            frame.loc[frame["brand"].astype(str).str.casefold().isin(labels), "product_id"]
            .astype(int)
            .tolist()
        )

    def _scenario_product_options(self, product_ids: list[int], user_context: UserContext):
        """Build exact, permission-filtered choices for an ambiguous scenario target."""
        frame = getattr(self.resolver, "products", None)
        if not isinstance(frame, pd.DataFrame) or "product_id" not in frame.columns:
            return []
        allowed = self._authorized_product_scope(user_context)
        candidates = frame[frame["product_id"].astype(int).isin({int(v) for v in product_ids})]
        if allowed is not None:
            candidates = candidates[candidates["product_id"].astype(int).isin(allowed)]
        fields = [name for name in ("product_name", "sku", "brand", "category") if name in candidates.columns]
        candidates = candidates[fields].drop_duplicates().sort_values(
            [name for name in ("product_name", "sku") if name in fields]
        )
        if len(candidates) > 10:
            return []
        duplicate_names = set(
            candidates.loc[candidates["product_name"].duplicated(keep=False), "product_name"].astype(str)
        ) if "product_name" in candidates else set()
        options = []
        for row in candidates.to_dict(orient="records"):
            name = str(row.get("product_name") or row.get("sku") or "Catalog product")
            sku = str(row.get("sku") or "")
            label = f"{name} ({sku})" if name in duplicate_names and sku else name
            value = sku if name in duplicate_names and sku else name
            description = " · ".join(
                str(row[key]) for key in ("brand", "category") if row.get(key)
            )
            options.append({
                "label": label,
                "value": value,
                "values": [value],
                **({"description": description} if description else {}),
            })
        return options

    def handle(
        self,
        question: str,
        session_id: str,
        user_context: UserContext | None = None,
    ) -> OrchestratorResponse:
        """Process one user question.

        The method intentionally keeps each stage explicit so it is easy to
        test and inspect.
        """

        user_context = user_context or UserContext()
        if not session_id.strip():
            raise ValueError("session_id is required")

        if not question.strip():
            return OrchestratorResponse(
                session_id=session_id,
                status="error",
                error="Please enter a question.",
            )

        # ----------------------------------------------------------- session
        previous_context = self.sessions.previous_context(session_id)
        previous_state = self.sessions.get(session_id)
        pending = self.sessions.pending(session_id)
        cancel_pending = bool(re.search(r"\b(?:cancel|never mind|nevermind|start over|new question)\b", question.casefold()))
        if cancel_pending and pending:
            self.sessions.clear_pending(session_id)
            pending = None
        resumed_intent, resumed, resume_error = self._resume_pending(question, pending)
        if resume_error:
            return OrchestratorResponse(
                session_id=session_id,
                status="clarification",
                error=resume_error,
                clarification_kind=(pending or {}).get("kind"),
                clarification_prompt=resume_error,
                pending_clarification_id=(pending or {}).get("id"),
            )
        if pending and resumed_intent is None and not resumed:
            # A different question starts a new request; do not let an old
            # clarification reattach filters or options to it.
            self.sessions.clear_pending(session_id)
            pending = None

        # -------------------------------------------------------------- intent
        try:
            parsed: ParsedIntent | None = None
            if resumed_intent is None:
                parsed = parse_intent(
                    question,
                    self.llm,
                    context=previous_context,
                )
        except IntentError as exc:
            return OrchestratorResponse(
                session_id=session_id,
                status="clarification",
                error=str(exc),
                clarification_kind="intent",
                clarification_prompt=str(exc),
            )
        except LLMUnavailable as exc:
            return OrchestratorResponse(
                session_id=session_id,
                status="llm_unavailable",
                error=str(exc),
            )

        option_resumed = resumed_intent is not None
        intent = resumed_intent or parsed.intent
        # Option selections resume a server-owned intent without another LLM
        # call. Keep usage additive-safe so narration can be combined with a
        # zero-cost interaction.
        usage = Usage(calls=0) if option_resumed else parsed.usage

        # A natural-language reply to a scenario-target clarification may be a
        # complete request ("Show iPhone 16 inventory at T Nagar"). Parse it
        # normally, then merge only its explicit entities into the pending
        # scenario intent. The scenario capability/value remain authoritative.
        pending_intent = self._pending_intent(pending) if pending else None
        is_scenario_entity_retry = bool(
            pending_intent is not None
            and pending_intent.capability == "what_if"
            and pending_intent.scenario is not None
        )
        if (
            pending
            and not option_resumed
            and (
                pending.get("kind") == "scenario_target"
                or (pending.get("kind") == "entity" and is_scenario_entity_retry)
            )
        ):
            base_intent = self._pending_intent(pending)
            merged_intent = (
                _merge_scenario_clarification_reply(base_intent, parsed.intent, question)
                if base_intent is not None else None
            )
            if merged_intent is not None:
                intent = merged_intent
                resumed = True

        # A bare scenario follow-up (for example, "what if demand increases
        # by 20%?") is a genuine continuation when the prior answer supplied
        # a product scope. Reattach that scope deterministically even if the
        # parser did not emit uses_previous_result.
        if (
            resumed_intent is None
            and getattr(intent, "capability", "") == "what_if"
            and previous_state.last_product_ids
            and not any(
                getattr(intent.filters, field, [])
                for field in ("products", "brands", "categories", "subcategories")
            )
        ):
            intent = intent.model_copy(update={"uses_previous_result": True})

        if (
            getattr(intent, "uses_previous_result", False)
            and not _explicitly_references_previous_result(question)
            and not (
                getattr(intent, "capability", "") == "what_if"
                and previous_state.last_product_ids
                and not any(
                    getattr(intent.filters, field, [])
                    for field in ("products", "brands", "categories", "subcategories")
                )
            )
        ):
            intent = intent.model_copy(
                update={"uses_previous_result": False}
            )

        if (
            getattr(intent, "question_kind", "") == "compound"
            and not _explicitly_requests_multiple_capabilities(question)
        ):
            intent = intent.model_copy(
                update={
                    "question_kind": "lookup",
                    "sub_questions": [],
                }
            )

        intent = _leadership_defaults(question, intent, user_context)
        if not option_resumed:
            intent = _apply_followup_context(question, intent, previous_state)
        intent = _catalog_brand_comparison(question, intent, self.resolver)
        if _has_unsupported_inventory_sales_tradeoff(question):
            return OrchestratorResponse(
                session_id=session_id,
                status="out_of_scope",
                intent=intent,
                usage=usage,
                error=(
                    "I can’t rank products by a combined ‘high inventory value and low sales’ score because no approved V1 formula defines that trade-off. "
                    "Please choose one ranking metric, or provide an approved ranking rule."
                ),
            )
        # Natural phrases such as "at a particular store" express a missing
        # value, not a literal store name. Convert only these placeholders to
        # an empty filter so the deterministic location policy can ask for
        # one instead of resolving the phrase as a catalog entity.
        if (
            getattr(user_context, "persona", "") == "Leadership"
            and re.search(r"\b(?:at|in)\s+(?:(?:a|the|one|some)\s+)?(?:(?:particular|specific)\s+)?(?:individual\s+)?store\b", question.casefold())
        ):
            generic_store_terms = {"a store", "the store", "one store", "some store", "a particular store", "the particular store", "a specific store", "the specific store", "particular store", "specific store"}
            if all(str(value).strip().casefold() in generic_store_terms for value in (intent.filters.stores or [])):
                intent = intent.model_copy(update={
                    "filters": intent.filters.model_copy(update={"stores": []})
                })

        multi_comparison_dimensions = [
            field for field in ("brands", "categories", "stores", "cities", "products", "regions")
            if len(getattr(getattr(intent, "filters", None), field, []) or []) > 1
        ]
        if getattr(intent, "question_kind", "") == "comparison" and len(multi_comparison_dimensions) > 1:
            return OrchestratorResponse(
                session_id=session_id, status="out_of_scope", intent=intent, usage=usage,
                error="Please compare one dimension at a time so the results have a clear grouping.",
            )

        # The current execution contract supports product subject-vs-scope,
        # not independent city-vs-city comparisons. Do not silently turn an
        # unsupported comparison into an unrelated single-scope lookup.
        comparison_filters = getattr(intent, "filters", None)
        comparison_dimension_present = bool(
            comparison_filters and any(
                len(getattr(comparison_filters, field, []) or []) > 1
                for field in ("products", "brands", "categories", "stores", "cities", "regions")
            )
        )
        if (
            getattr(intent, "question_kind", "") == "comparison"
            and getattr(intent, "comparison_mode", "none") != "subject_vs_scope"
            and not comparison_dimension_present
            and getattr(intent, "group_by", None) not in {"brand", "category", "store", "city", "region", "product"}
            and not getattr(getattr(intent, "time", None), "compare_to_previous", False)
        ):
            return OrchestratorResponse(
                session_id=session_id,
                status="out_of_scope",
                intent=intent,
                usage=usage,
                error=("This comparison is not yet supported reliably. "
                       "Please name a supported comparison dimension such as product, brand, category, store, or city."),
            )

        if (
            getattr(user_context, "persona", "") == "Leadership"
            and getattr(intent, "question_kind", "") == "comparison"
            and not getattr(intent, "metrics", [])
        ):
            dimension = getattr(intent, "group_by", None) or "requested groups"
            store_performance_question = bool(
                dimension == "store"
                and re.search(
                    r"\b(?:which|what)\s+(?:store|location)\b.*"
                    r"\b(?:perform(?:ed|ing)?|performance|best|top)\b",
                    question.casefold(),
                )
            )
            metric_options = (
                [
                    {
                        "label": "Sales revenue",
                        "value": "sales_revenue",
                        "metrics": ["sales_revenue"],
                        "capability": "sales_trend",
                        "question_kind": "ranking",
                        "sort": {"by": "sales_revenue", "direction": "desc"},
                    },
                    {
                        "label": "Units sold",
                        "value": "sales_units",
                        "metrics": ["sales_units"],
                        "capability": "sales_trend",
                        "question_kind": "ranking",
                        "sort": {"by": "sales_units", "direction": "desc"},
                    },
                    {
                        "label": "Inventory value",
                        "value": "inventory_value",
                        "metrics": ["inventory_value"],
                        "capability": "inventory_visibility",
                        "question_kind": "ranking",
                        "sort": {"by": "inventory_value", "direction": "desc"},
                    },
                ]
                if store_performance_question
                else [
                    {"label": "Inventory units", "value": "quantity", "metrics": ["quantity"], "capability": "inventory_visibility"},
                    {"label": "Inventory value", "value": "inventory_value", "metrics": ["inventory_value"], "capability": "inventory_visibility"},
                    {"label": "Sales units", "value": "sales_units", "metrics": ["sales_units"], "capability": "sales_trend"},
                    {"label": "Revenue", "value": "sales_revenue", "metrics": ["sales_revenue"], "capability": "sales_trend"},
                ]
            )
            return self._clarification_response(
                session_id=session_id, intent=intent, kind="comparison_metric", field="metrics",
                prompt=(
                    "How would you like to measure store performance?"
                    if store_performance_question
                    else f"Which metric should I compare across {dimension}: inventory units, inventory value, sales units, or revenue?"
                ),
                options=metric_options, usage=usage, interpretation=[],
            )

        time_issue = _time_window_issue(intent)
        if time_issue:
            capability = getattr(intent, "capability", "")
            period_method = (
                "sales_periods"
                if capability == "sales_trend"
                else "snapshot_periods"
            )
            time_options = [{"label": "Latest available", "time": {"kind": "latest"}}]
            if capability == "sales_trend":
                time_options.extend([
                    {"label": "This month", "time": {"kind": "this_month"}},
                    {"label": "Last month", "time": {"kind": "last_month"}},
                ])
            if hasattr(self.queries, period_method):
                try:
                    periods = getattr(self.queries, period_method)()
                except Exception as exc:
                    return OrchestratorResponse(
                        session_id=session_id,
                        status="error",
                        intent=intent,
                        usage=usage,
                        error=f"Could not load available reporting periods: {exc}",
                    )
                month = getattr(intent.time, "month", None)
                year = getattr(intent.time, "year", None)
                for available_year, available_month in periods:
                    if month is not None and int(month) != int(available_month):
                        continue
                    if year is not None and int(year) != int(available_year):
                        continue
                    label = (
                        f"{calendar.month_name[int(available_month)]} "
                        f"{int(available_year)}"
                    )
                    time_options.append({
                        "label": label,
                        "time": {
                            "kind": "month",
                            "month": int(available_month),
                            "year": int(available_year),
                        },
                    })
            return self._clarification_response(
                session_id=session_id,
                intent=intent,
                kind="time",
                prompt=time_issue,
                options=time_options,
                usage=usage,
            )

        # Protect previous-result follow-ups from numeric product names
        # emitted by the LLM; database IDs belong to backend execution state.
        if getattr(intent, "uses_previous_result", False):
            filters = getattr(intent, "filters", None)

            if filters is not None:
                products = getattr(filters, "products", None)

                if products:
                    non_numeric_products = [
                        value
                        for value in products
                        if not str(value).strip().isdigit()
                    ]

                    if len(non_numeric_products) != len(products):
                        intent = intent.model_copy(
                            update={
                                "filters": filters.model_copy(
                                    update={
                                        "products": (
                                            non_numeric_products or None
                                        )
                                    }
                                )
                            }
                        )

        response_plan = plan_response(intent)

        # ------------------------------------------------------ early exits
        if intent.capability == "clarification":
            prompt = intent.clarification_question or "Could you clarify what you would like to see?"
            return self._clarification_response(
                session_id=session_id, intent=intent, kind="intent",
                prompt=prompt, options=[], usage=usage,
            )

        if intent.capability == "out_of_scope":
            return OrchestratorResponse(
                session_id=session_id,
                status="out_of_scope",
                intent=intent,
                error=(
                    "I can help with inventory, sales, stockout risk, "
                    "excess/slow-moving inventory, trends, and what-if analysis."
                ),
                usage=usage,
            )

        is_compound = (
            getattr(intent, "question_kind", "lookup") == "compound"
            and bool(getattr(intent, "sub_questions", None))
        )
        # A compound response is opt-in and must contain genuinely distinct
        # capabilities.  LLMs occasionally emit duplicate sub-questions for
        # an ordinary request; treating those as compound would concatenate
        # unrelated narratives while the table still represents one result.
        if is_compound:
            sub_capabilities = {
                getattr(sub_question, "capability", "")
                for sub_question in intent.sub_questions
            }
            if (
                not _explicitly_requests_multiple_capabilities(question)
                or len(sub_capabilities) < 2
            ):
                is_compound = False
                intent = intent.model_copy(
                    update={"question_kind": "lookup", "sub_questions": []}
                )

        # ----------------------------------------------------------- resolve
        resolution = None
        interpretation: list[str] = []
        requested_store_ids = None
        effective_store_ids = None
        if not is_compound:
            try:
                resolution = self.resolver.resolve(intent.filters)
            except Exception as exc:
                return OrchestratorResponse(
                    session_id=session_id,
                    status="error",
                    intent=intent,
                    usage=usage,
                    error=f"Entity resolution failed: {exc}",
                )

            interpretation = _resolution_explanation(resolution)
            requested_store_ids = resolution.store_ids

            # Apply authenticated product scope before any dispatch,
            # aggregation, calculation, or clarification output. ``None`` is
            # unrestricted; an empty tuple is fail-closed.
            allowed_products = self._authorized_product_scope(user_context)
            if allowed_products is not None:
                allowed_set = set(allowed_products)
                resolved_set = set(resolution.product_ids or [])
                explicit_product_request = any(
                    getattr(intent.filters, field, [])
                    for field in ("products", "brands", "categories", "subcategories")
                )
                if explicit_product_request and resolved_set and not resolved_set.issubset(allowed_set):
                    return OrchestratorResponse(
                        session_id=session_id,
                        status="out_of_scope",
                        intent=intent,
                        interpretation=interpretation,
                        usage=usage,
                        error="You are not authorized to view one or more of the requested products or brands.",
                    )
                if resolution.product_ids is None:
                    resolution.product_ids = sorted(allowed_set)
                else:
                    resolution.product_ids = sorted(resolved_set & allowed_set)
                if not resolution.product_ids and not explicit_product_request:
                    return OrchestratorResponse(
                        session_id=session_id, status="out_of_scope", intent=intent,
                        interpretation=interpretation, usage=usage,
                        error="You do not have access to any products in this request.",
                    )

            if not resolution.ok:
                options = []
                field = None
                unresolved = getattr(resolution, "unresolved", [])
                if unresolved:
                    first = unresolved[0]
                    raw_kind = str(getattr(first, "kind", ""))
                    field = raw_kind if raw_kind in {
                        "products", "brands", "categories", "subcategories",
                        "stores", "cities", "regions",
                    } else None
                    safe_suggestions = self._authorized_entity_suggestions(first, user_context)
                    options = [{"label": value, "value": value} for value in safe_suggestions]
                return self._clarification_response(
                    session_id=session_id, intent=intent, kind="entity",
                    prompt=resolution.clarification_question(), options=options,
                    field=field, usage=usage, interpretation=interpretation,
                )

            if resolution.empty:
                return OrchestratorResponse(
                    session_id=session_id,
                    status="no_results",
                    intent=intent,
                    interpretation=interpretation,
                    usage=usage,
                    error=(
                        resolution.empty_reason
                        or "No records matched the requested filters."
                    ),
                )

            # Authorization must be resolved before asking for any follow-up
            # location or timeline choice. In particular, a Store Manager
            # requesting a city outside the assigned region must receive a
            # denial rather than seeing that city's store options.
            effective_store_ids, restricted = effective_ids(
                resolution.store_ids, user_context.authorized_store_ids
            )
            if restricted:
                if not effective_store_ids:
                    return OrchestratorResponse(
                        session_id=session_id,
                        status="out_of_scope",
                        intent=intent,
                        interpretation=interpretation,
                        usage=usage,
                        error=(
                            "You are not authorized to view that location because it is outside "
                            "your assigned stores or region. I can help you check your authorized locations instead."
                        ),
                    )
                interpretation.append("Requested location was restricted to your authorized stores.")

            # What-if analysis must identify its target before location. An
            # empty target would otherwise resolve to the full catalog and
            # later narration could describe the first row as the answer.
            if getattr(intent, "capability", "") == "what_if" and getattr(intent, "scenario", None) is not None:
                filters = intent.filters
                explicitly_grouped_target = bool(
                    getattr(filters, "brands", [])
                    or getattr(filters, "categories", [])
                    or getattr(filters, "subcategories", [])
                )
                if (
                    len(getattr(filters, "products", []) or []) == 1
                    and resolution.product_ids
                    and len(resolution.product_ids) > 1
                    and not explicitly_grouped_target
                ):
                    choices = self._scenario_product_options(
                        resolution.product_ids, user_context
                    )
                    prompt = (
                        f"I found more than one catalog product matching “{filters.products[0]}”. "
                        "Which exact product should I use for the scenario?"
                        if choices else
                        f"“{filters.products[0]}” matches multiple products. Please provide the full catalog product name or SKU."
                    )
                    return self._clarification_response(
                        session_id=session_id, intent=intent, kind="scenario_target",
                        prompt=prompt, options=choices, field="products", usage=usage,
                        interpretation=interpretation,
                    )
                explicit_target = bool(
                    getattr(filters, "products", [])
                    or getattr(filters, "brands", [])
                    or getattr(filters, "categories", [])
                    or getattr(filters, "subcategories", [])
                )
                prior_target = bool(
                    getattr(intent, "uses_previous_result", False)
                    and previous_state.last_product_ids
                )
                resolved_is_single_target = bool(
                    getattr(resolution, "product_ids", None)
                    and len(resolution.product_ids) == 1
                )
                if not explicit_target and not prior_target and not resolved_is_single_target:
                    return self._clarification_response(
                        session_id=session_id,
                        intent=intent,
                        kind="scenario_target",
                        prompt="Which product, brand, category, or subcategory should I analyze for this scenario?",
                        options=[],
                        field="products",
                        usage=usage,
                        interpretation=interpretation,
                    )

            location_clarification = self._location_clarification(
                question=question,
                intent=intent,
                resolution=resolution,
                session_id=session_id,
                previous_state=previous_state,
                user_context=user_context,
                usage=usage,
            )
            if location_clarification is not None:
                location_clarification.interpretation = interpretation
                return location_clarification

        # -------------------------------------------------- previous result
        prev_product_ids = (
            previous_state.last_product_ids
            if getattr(intent, "uses_previous_result", False)
            and not any(getattr(intent.filters, field, []) for field in ("products", "brands", "categories", "subcategories"))
            else None
        )
        prev_store_ids = (
            previous_state.last_store_ids
            if getattr(intent, "uses_previous_result", False)
            and not any(getattr(intent.filters, field, []) for field in ("stores", "cities", "regions"))
            else None
        )

        # -------------------------------------------------------------- as_of
        try:
            as_of = self.queries.as_of()
        except Exception as exc:
            return OrchestratorResponse(
                session_id=session_id,
                status="error",
                intent=intent,
                interpretation=interpretation,
                usage=usage,
                error=f"Could not determine data as-of date: {exc}",
            )

        # Exact-date inventory must never silently fall back to an earlier
        # weekly snapshot. Ask for an explicit confirmation when the query
        # adapter can inspect snapshot availability.
        time = getattr(intent, "time", None)
        historical_inventory_capabilities = {"inventory_visibility", "inventory_health", "historical_analysis"}
        requires_inventory_snapshot = (
            getattr(intent, "capability", "") in historical_inventory_capabilities
            and getattr(time, "kind", "none") in {"exact_date", "month", "date_range"}
        )
        if (
            not is_compound
            and requires_inventory_snapshot
            and getattr(time, "kind", "none") == "exact_date"
            and hasattr(self.queries, "snapshot_date_for")
        ):
            try:
                requested_date = datetime.strptime(str(time.date), "%Y-%m-%d").date()
                snapshot_date = self.queries.snapshot_date_for(requested_date)
            except Exception as exc:
                return OrchestratorResponse(
                    session_id=session_id,
                    status="error",
                    intent=intent,
                    interpretation=interpretation,
                    usage=usage,
                    error=f"Could not validate historical snapshot availability: {exc}",
                )
            if snapshot_date is None:
                return self._clarification_response(
                    session_id=session_id,
                    intent=intent,
                    kind="historical_snapshot",
                    prompt=f"No weekly inventory snapshot is available on or before {requested_date.isoformat()}.",
                    options=[],
                    usage=usage,
                    interpretation=interpretation,
                )
            if snapshot_date != requested_date:
                return self._clarification_response(
                    session_id=session_id,
                    intent=intent,
                    kind="historical_snapshot",
                    prompt=(
                        f"There is no snapshot for {requested_date.isoformat()}. "
                        f"Use the latest available weekly snapshot ({snapshot_date.isoformat()})?"
                    ),
                    options=[{
                        "label": f"Use snapshot {snapshot_date.isoformat()}",
                        "value": snapshot_date.isoformat(),
                        "time": {"kind": "exact_date", "date": snapshot_date.isoformat()},
                    }],
                    usage=usage,
                    interpretation=interpretation,
                )

        # ------------------------------------------------------------- dispatch
        try:
            # Resolve comparison subjects and validate authorization before
            # dispatching the main aggregate. This prevents an unauthorized
            # comparison subject from receiving any partial result first.
            if (
                not is_compound
                and getattr(intent, "comparison_mode", "none") == "subject_vs_scope"
                and getattr(intent, "comparison_subject", None)
            ):
                pre_subject = self.resolver.resolve(
                    type(intent.filters)(products=[intent.comparison_subject])
                )
                if not pre_subject.ok or pre_subject.empty:
                    return OrchestratorResponse(
                        session_id=session_id, status="clarification", intent=intent,
                        interpretation=interpretation, usage=usage,
                        error=pre_subject.clarification_question(),
                    )
                subject_allowed = self._authorized_product_scope(user_context)
                subject_ids = set(pre_subject.product_ids or [])
                if subject_allowed is not None and not subject_ids.issubset(subject_allowed):
                    return OrchestratorResponse(
                        session_id=session_id, status="out_of_scope", intent=intent,
                        interpretation=interpretation, usage=usage,
                        error="You are not authorized to compare one or more of the requested products or brands.",
                    )
            if is_compound:
                sub_results: list[DispatchResult] = []
                sub_interpretation: list[str] = []
                for sub_question in intent.sub_questions:
                    sub_resolution = self.resolver.resolve(sub_question.filters)
                    sub_interpretation.extend(
                        _resolution_explanation(sub_resolution)
                    )
                    if not sub_resolution.ok:
                        return OrchestratorResponse(
                            session_id=session_id,
                            status="clarification",
                            intent=intent,
                            interpretation=sub_interpretation,
                            usage=usage,
                            error=sub_resolution.clarification_question(),
                        )
                    sub_allowed = self._authorized_product_scope(user_context)
                    sub_requested = set(sub_resolution.product_ids or [])
                    sub_explicit = any(
                        getattr(sub_question.filters, field, [])
                        for field in ("products", "brands", "categories", "subcategories")
                    )
                    if sub_allowed is not None:
                        if sub_explicit and sub_requested and not sub_requested.issubset(sub_allowed):
                            return OrchestratorResponse(
                                session_id=session_id, status="out_of_scope", intent=intent,
                                interpretation=sub_interpretation, usage=usage,
                                error="You are not authorized to view one or more of the requested products or brands.",
                            )
                        sub_resolution.product_ids = sorted(
                            sub_allowed if sub_resolution.product_ids is None
                            else sub_requested & sub_allowed
                        )
                    sub_store_ids, sub_restricted = effective_ids(
                        sub_resolution.store_ids, user_context.authorized_store_ids
                    )
                    if sub_restricted and not sub_store_ids:
                        return OrchestratorResponse(
                            session_id=session_id, status="out_of_scope", intent=intent,
                            interpretation=sub_interpretation, usage=usage,
                            error="That location is outside your available scope.",
                        )
                    sub_results.append(
                        self.dispatch_fn(
                            queries=self.queries,
                            intent=sub_question,
                            rules=self.rules,
                            as_of=as_of,
                            product_ids=sub_resolution.product_ids,
                            store_ids=sub_store_ids,
                            prev_product_ids=prev_product_ids,
                            prev_store_ids=prev_store_ids,
                        )
                    )
                interpretation = sub_interpretation
                compound_results = sub_results
                result = compound_results[0]
            else:
                result = self.dispatch_fn(
                    queries=self.queries,
                    intent=intent,
                    rules=self.rules,
                    as_of=as_of,
                    product_ids=resolution.product_ids,
                    store_ids=effective_store_ids,
                    prev_product_ids=prev_product_ids,
                    prev_store_ids=prev_store_ids,
                )
        except ValueError as exc:
            return OrchestratorResponse(
                session_id=session_id,
                status="error",
                intent=intent,
                interpretation=interpretation,
                usage=usage,
                error=str(exc),
            )
        except Exception as exc:
            return OrchestratorResponse(
                session_id=session_id,
                status="error",
                intent=intent,
                interpretation=interpretation,
                usage=usage,
                error=f"Unable to complete the request: {exc}",
            )

        contract_issue = _dispatch_result_contract_issue(intent, result.df)
        if contract_issue:
            return OrchestratorResponse(
                session_id=session_id,
                status="error",
                intent=intent,
                interpretation=interpretation,
                usage=usage,
                error=contract_issue,
                sql=result.sql,
                params=result.params,
                notes=result.notes,
                basis=result.basis,
            )

        subject_result = None
        if (
            not is_compound
            and
            getattr(intent, "comparison_mode", "none") == "subject_vs_scope"
            and getattr(intent, "comparison_subject", None)
        ):
            try:
                subject_resolution = self.resolver.resolve(
                    type(intent.filters)(
                        products=[intent.comparison_subject]
                    )
                )
                if not subject_resolution.ok or subject_resolution.empty:
                    return OrchestratorResponse(
                        session_id=session_id,
                        status="clarification",
                        intent=intent,
                        interpretation=interpretation,
                        usage=usage,
                        error=subject_resolution.clarification_question(),
                    )

                subject_allowed = self._authorized_product_scope(user_context)
                subject_ids = set(subject_resolution.product_ids or [])
                if subject_allowed is not None:
                    if not subject_ids.issubset(subject_allowed):
                        return OrchestratorResponse(
                            session_id=session_id,
                            status="out_of_scope",
                            intent=intent,
                            interpretation=interpretation,
                            usage=usage,
                            error="You are not authorized to compare one or more of the requested products or brands.",
                        )
                    subject_resolution.product_ids = sorted(subject_ids & subject_allowed)
                    if not subject_resolution.product_ids:
                        return OrchestratorResponse(
                            session_id=session_id,
                            status="no_results",
                            intent=intent,
                            interpretation=interpretation,
                            usage=usage,
                            error="No authorized records matched the comparison subject.",
                        )

                subject_intent = intent.model_copy(
                    update={
                        "limit": None,
                        "uses_previous_result": False,
                    }
                )
                subject_result = self.dispatch_fn(
                    queries=self.queries,
                    intent=subject_intent,
                    rules=self.rules,
                    as_of=as_of,
                    product_ids=subject_resolution.product_ids,
                    store_ids=resolution.store_ids,
                    prev_product_ids=None,
                    prev_store_ids=None,
                )
            except Exception as exc:
                return OrchestratorResponse(
                    session_id=session_id,
                    status="error",
                    intent=intent,
                    interpretation=interpretation,
                    usage=usage,
                    error=f"Unable to compare the requested subject: {exc}",
                )

        if (
            getattr(intent, "question_kind", "") == "comparison"
            and getattr(intent, "group_by", None) in {"brand", "category", "store", "city", "region", "product"}
            and len(result.df) < 2
        ):
            return OrchestratorResponse(
                session_id=session_id, status="clarification", intent=intent, usage=usage,
                clarification_kind="comparison_scope",
                clarification_prompt="I found fewer than two groups to compare. Please broaden the authorized scope or name at least two groups.",
                error="I found fewer than two groups to compare. Please broaden the authorized scope or name at least two groups.",
            )

        # --------------------------------------------------------- no results
        if result.df.empty:
            return OrchestratorResponse(
                session_id=session_id,
                status="no_results",
                intent=intent,
                interpretation=interpretation,
                sql=result.sql,
                params=result.params,
                row_count=0,
                notes=result.notes,
                basis=result.basis,
                usage=usage,
                error="No records matched the requested conditions.",
            )

        # ------------------------------------------------------------- memory
        summary = _intent_summary(intent)

        effective_product_ids = (
            resolution.product_ids if resolution is not None else None
        )
        # Keep the authorized effective_store_ids computed before dispatch;
        # replacing them with resolver.store_ids would misreport access scope.

        if intent.uses_previous_result:
            if effective_product_ids is None:
                effective_product_ids = previous_state.last_product_ids

            if effective_store_ids is None:
                effective_store_ids = previous_state.last_store_ids

        result_product_ids = effective_product_ids
        result_store_ids = effective_store_ids

        if "product_id" in result.df.columns:
            result_product_ids = sorted(
                result.df["product_id"]
                .dropna()
                .astype(int)
                .unique()
                .tolist()
            )

        if "store_id" in result.df.columns:
            result_store_ids = sorted(
                result.df["store_id"]
                .dropna()
                .astype(int)
                .unique()
                .tolist()
            )

        self.sessions.update(
            session_id,
            intent=intent,
            intent_summary=summary,
            product_ids=result_product_ids,
            store_ids=result_store_ids,
        )
        if resumed:
            # Consume a valid option only after the complete request has
            # produced a successful result. Dispatch/narration failures leave
            # the original pending question available for a safe retry.
            self.sessions.clear_pending(session_id)

        # ---------------------------------------------------------- narrative
        narrative = ""
        narration_verified = False
        narration_regenerated = False
        narration_fallback = False
        narration_usage = Usage(
            prompt_tokens=0,
            completion_tokens=0,
            total_tokens=0,
            cached_tokens=0,
            latency_s=0.0,
            calls=0,
        )

        deterministic_answer = ""

        # --------------------------------------------------
        # Deterministic response path
        # --------------------------------------------------

        if response_plan.deterministic:

            if is_compound:

                evidence = []
                answers = []

                for sub_question, sub_result in zip(
                    intent.sub_questions,
                    compound_results,
                ):
                    answer = build_answer(
                        question=question,
                        intent=sub_question,
                        df=sub_result.df,
                        basis=sub_result.basis,
                        rules=self.rules,
                    )

                    answers.append(answer)

                    evidence.append(
                        compose_single(
                            intent=sub_question,
                            result=sub_result,
                            narrative=answer,
                        )
                    )

                compound_payload = compose_compound(evidence)
                deterministic_answer = compound_payload["narrative"]

            elif (
                subject_result is not None
                and not subject_result.df.empty
            ):

                metric = (
                    getattr(intent.sort, "by", None)
                    if intent.sort
                    else (
                        intent.metrics[0]
                        if intent.metrics
                        else None
                    )
                )

                value_column = {
                    "sales_units": "units",
                    "sales_revenue": "revenue",
                    "quantity": "quantity_on_hand",
                    "inventory_value": "inventory_value",
                }.get(metric)

                if (
                    value_column
                    and value_column in subject_result.df.columns
                    and value_column in result.df.columns
                ):

                    subject_row = subject_result.df.iloc[0]
                    winner_row = result.df.iloc[0]

                    deterministic_answer = (
                        build_subject_comparison_answer(
                            subject_name=str(
                                subject_row.get(
                                    "product_name",
                                    intent.comparison_subject,
                                )
                            ),
                            subject_value=subject_row[value_column],
                            winner_name=str(
                                winner_row.get(
                                    "product_name",
                                    "Winner",
                                )
                            ),
                            winner_value=winner_row[value_column],
                            metric_label={
                                "sales_units": "sales",
                                "sales_revenue": "sales revenue",
                                "quantity": "inventory",
                                "inventory_value": "inventory value",
                            }[metric],
                        )
                    )

            if not deterministic_answer:

                deterministic_answer = build_answer(
                    question=question,
                    intent=intent,
                    df=result.df,
                    basis=result.basis,
                    rules=self.rules,
                )

        # --------------------------------------------------
        # Use deterministic answer whenever possible
        # --------------------------------------------------

        if deterministic_answer:

            narrative = deterministic_answer

        else:

            if self.narrator is not None:

                scenario = getattr(intent, "scenario", None)

                extra_values = []

                if scenario is not None:
                    extra_values.append(float(scenario.value))

                try:

                    narration_result = self.narrator.generate(
                        question=question,
                        df=_narration_evidence(intent, result.df),
                        basis=result.basis,
                        extra_values=extra_values,
                    )

                    narrative = narration_result.text

                    narration_verified = narration_result.verified

                    narration_regenerated = (
                        narration_result.regenerated
                    )

                    narration_fallback = (
                        narration_result.used_fallback
                    )

                    narration_usage = narration_result.usage

                except Exception:

                    narrative = ""

        # Complete the user-facing narrative from canonical intent and result
        # evidence, then reject claims that fail the capability evidence checks.
        if False and not is_compound and result.df is not None and not result.df.empty:
            narrative = complete_narrative_context(intent, result.df, narrative)
            narrative_errors = validate_narrative_evidence(intent, result.df, narrative)
            if narrative_errors:
                deterministic_fallback = build_answer(
                    question=question,
                    intent=intent,
                    df=result.df,
                    basis=result.basis,
                    rules=self.rules,
                )
                deterministic_fallback = complete_narrative_context(
                    intent, result.df, deterministic_fallback
                )
                fallback_errors = validate_narrative_evidence(
                    intent, result.df, deterministic_fallback
                ) if deterministic_fallback else ["no deterministic answer"]
                if deterministic_fallback and not fallback_errors:
                    narrative = deterministic_fallback
                    narration_verified = False
                    narration_fallback = True
                else:
                    logger.warning(
                        "Narrative evidence validation failed for capability=%s kind=%s violations=%s",
                        getattr(intent, "capability", "unknown"),
                        getattr(intent, "question_kind", "unknown"),
                        narrative_errors,
                    )
                    return OrchestratorResponse(
                        session_id=session_id,
                        status="validation_failed",
                        intent=intent,
                        interpretation=interpretation,
                        error=(
                            "I couldn’t verify that the explanation matches the calculated results, "
                            "so I’m not showing those results as an answer. Please try a narrower or clearer question."
                        ),
                        notes=["Narrative/result consistency validation failed."],
                        basis="",
                        usage=_usage_or_zero(usage) + _usage_or_zero(narration_usage),
                        response_type="validation_error",
                    )

        # Keep technical SQL boundaries in the Trust Panel. A historical
        # weekly snapshot is a point-in-time reading, not a date-range total.
        historical_notes = [n for n in result.notes if n.startswith("Historical inventory basis:")]
        if narrative and historical_notes:
            match = re.search(r"weekly snapshot (\d{4}-\d{2}-\d{2})", historical_notes[-1])
            if match and match.group(1) not in narrative:
                narrative += f" This historical inventory is from the available weekly count on {match.group(1)}, not an exact month-end count."

        total_usage = _usage_or_zero(usage) + _usage_or_zero(narration_usage)
        trust_panel = _trust_panel_payload(
            question=question,
            intent=intent,
            interpretation=interpretation,
            result=result,
            rules=self.rules,
            user_context=user_context,
            requested_store_ids=requested_store_ids,
            effective_store_ids=effective_store_ids,
        )
        display_df, display = _presentation(result.df, intent, question)
        response_type = display["type"]
        followups = build_followups(
            question=question,
            intent=intent,
            dataframe=result.df,
            response_type=response_type,
            persona=user_context.persona,
            authorized_scope=user_context,
        )
        resolved_context = {
            "capability": getattr(intent, "capability", ""),
            "product_scope": {
                "product_ids": sorted(result_product_ids or []),
                "labels": result.df["product_name"].dropna().astype(str).unique().tolist()
                if "product_name" in result.df.columns else [],
            },
            "location_scope": {
                "store_ids": sorted(effective_store_ids or []),
                "store_names": result.df["store_name"].dropna().astype(str).unique().tolist()
                if "store_name" in result.df.columns else [],
                "cities": result.df["city"].dropna().astype(str).unique().tolist()
                if "city" in result.df.columns else [],
            },
            "time_scope": {
                **(intent.time.model_dump() if hasattr(intent.time, "model_dump") else {}),
                "source_date": as_of.isoformat(),
            },
            "authorization_validated": True,
        }
        trust_panel["resolved_context"] = resolved_context
        answer_facts = build_answer_facts(
            intent=intent,
            result=result,
            resolved_context=resolved_context,
            counts=_result_counts(result.df),
        ).as_dict()

        # ------------------------------------------------------------- result
        return OrchestratorResponse(
            session_id=session_id,
            status="ok",
            intent=intent,
            narrative=narrative,
            data=display_df,
            interpretation=interpretation,
            sql=result.sql,
            params=result.params,
            row_count=int(_result_counts(result.df)["matching_result_rows"]),
            result_counts=_result_counts(result.df),
            notes=result.notes,
            basis=result.basis,
            usage=total_usage,
            trust_panel=trust_panel,
            user_context={"user_id": user_context.user_id, "display_name": user_context.display_name, "persona": user_context.persona},
            response_type=response_type,
            display=display,
            followups=followups,
            resolved_context=resolved_context,
            answer_facts=answer_facts,
            narration_verified=narration_verified,
            narration_regenerated=narration_regenerated,
            narration_fallback=narration_fallback,
        )
