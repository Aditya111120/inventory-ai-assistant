"""Question -> validated Intent. One repair attempt on invalid output, then a clear error."""
from __future__ import annotations

import json
import calendar
import re
from dataclasses import dataclass

from pydantic import ValidationError

from app.llm.client import Usage
from app.llm.prompts import build_messages
from app.llm.schema import Intent, Scenario, TimeWindow, strict_json_schema


class IntentError(RuntimeError):
    pass


@dataclass
class ParsedIntent:
    intent: Intent
    usage: Usage
    repaired: bool
    repair_reason: str = ""
    strict_used: bool = True


_SCHEMA = strict_json_schema(Intent)

# Fields that are never null in our schema. If the model sends null for one of them it means
# "not stated", so we drop the key and let the default apply. Invalid enum values are NOT
# coerced; those still go through the repair step.
_NOT_NULLABLE_TOP = (
    "metrics",
    "filters",
    "analysis_scope",
    "time",
    "comparison_mode",
    "question_kind",
    "sub_questions",
    "uses_previous_result",
)
_NOT_NULLABLE_FILTERS = ("products", "brands", "categories", "subcategories", "stores", "cities", "regions")
_NOT_NULLABLE_TIME = ("kind", "compare_to_previous")


def _drop_nulls(d: dict, keys) -> None:
    for k in keys:
        if k in d and d[k] is None:
            del d[k]


def _coerce(data: dict) -> dict:
    d = json.loads(json.dumps(data))
    _drop_nulls(d, _NOT_NULLABLE_TOP)
    if isinstance(d.get("filters"), dict):
        _drop_nulls(d["filters"], _NOT_NULLABLE_FILTERS)
    if isinstance(d.get("time"), dict):
        _drop_nulls(d["time"], _NOT_NULLABLE_TIME)
    return d


def _problems(err: ValidationError) -> str:
    return "; ".join(
        f"{'.'.join(map(str, e['loc']))}: {e['msg']}" for e in err.errors()[:5]
    )


def parse_intent(question: str, llm, context: str = "") -> ParsedIntent:
    if not question.strip():
        raise IntentError("Empty question.")
    messages = build_messages(question, context)
    first = llm.complete_json(messages, "intent", _SCHEMA)
    try:
        intent = Intent.model_validate(_coerce(first.data))
        return ParsedIntent(_normalise(intent, question), first.usage, False, "", first.strict_used)
    except ValidationError as err:
        reason = _problems(err)
        repair = messages + [
            {"role": "assistant", "content": json.dumps(first.data)},
            {"role": "user", "content": f"Invalid: {reason}. Return corrected JSON only."},
        ]
        second = llm.complete_json(repair, "intent", _SCHEMA)
        try:
            intent = Intent.model_validate(_coerce(second.data))
            return ParsedIntent(
                _normalise(intent, question),
                first.usage + second.usage,
                True,
                reason,
                second.strict_used,
            )
        except ValidationError as err2:
            raise IntentError(
                "Could not understand the question. Please rephrase it."
            ) from err2


def _normalise(intent: Intent, question: str = "") -> Intent:
    """Apply semantic rules the model must not be allowed to override."""
    text = question.casefold()
    can_normalize_analytical_request = intent.capability not in {
        "clarification", "out_of_scope",
    }
    explicit_organization_scope = bool(re.search(
        r"\b(?:company[- ]wide|organization[- ]wide|across all authorized stores|"
        r"across all stores|all stores|entire organization|whole organization)\b",
        text,
    ))
    if explicit_organization_scope and not any(
        getattr(intent.filters, field, [])
        for field in ("stores", "cities", "regions")
    ):
        intent.analysis_scope = "organization"

    # Normalize clear operational language to the existing validated intent
    # contract. Filters and time are left untouched so entity resolution and
    # the follow-up/clarification flows remain authoritative.
    no_sales_request = bool(re.search(
        r"\b(?:no|zero|without)\s+(?:recorded\s+)?(?:sales|sales units)\b"
        r"|\bno[- ]sales\b|\bnever sold\b|\bnot sold\b",
        text,
    ))
    slow_moving_request = bool(re.search(
        r"\bslow[- ]moving\b|\bslow movers?\b|\bslow sellers?\b",
        text,
    ))
    excess_request = bool(re.search(
        r"\b(?:excess|excessive)\s+(?:inventory|stock|coverage)\b"
        r"|\boverstock(?:ed|ing)?\b|\btoo much stock\b",
        text,
    ))
    excess_coverage_request = bool(re.search(
        r"\b(?:excess|excessive)\s+coverage\b|\bexcess coverage candidate\b",
        text,
    ))
    excess_level_request = bool(re.search(
        r"\b(?:above|over|exceeding)\s+(?:the\s+)?(?:maximum|max)\s+(?:stock\s+)?level\b"
        r"|\b(?:above|over)\s+max(?:imum)?\b",
        text,
    ))
    stockout_request = bool(re.search(
        r"\bstock[- ]?out\b|\brun(?:ning)?\s+out\s+of\s+stock\b"
        r"|\bdepletion\s+risk\b",
        text,
    ))
    sales_request = bool(re.search(
        r"\b(?:sales?|sold|revenue|turnover)\b",
        text,
    ))
    explicit_revenue = bool(re.search(
        r"\b(?:revenue|turnover|sales value)\b",
        text,
    ))
    inventory_request = bool(re.search(
        r"\b(?:inventory|stock|on[- ]hand|on hand|quantity)\b",
        text,
    ))
    scenario_change = re.search(
        r"\b(?:demand|sales)\s+(increases?|increased|rises?|raised|"
        r"decreases?|decreased|falls?|drops?)\s+(?:by\s+)?"
        r"(\d+(?:\.\d+)?)\s*%",
        text,
    )
    explicit_what_if = bool(
        scenario_change
        and re.search(r"\b(?:what[- ]if|if|simulate|simulation|scenario)\b", text)
    )
    historical_request = bool(
        inventory_request
        and re.search(
            r"\b(?:historical|history|snapshots?|over time|trend over time)\b",
            text,
        )
    )
    period_compare = bool(
        re.search(r"\b(?:current|this)\s+month\b", text)
        and re.search(r"\b(?:previous|prior|last)\s+month\b", text)
        and re.search(r"\b(?:compare|comparison|versus|vs\.?)\b", text)
    )

    if can_normalize_analytical_request and explicit_what_if:
        amount = float(scenario_change.group(2))
        direction = scenario_change.group(1)
        if direction.startswith(("decreas", "fall", "drop")):
            amount = -amount
        intent.capability = "what_if"
        intent.question_kind = "scenario"
        intent.scenario = Scenario(kind="demand_change_pct", value=amount)
        intent.metrics = ["days_of_inventory"]
        if re.search(r"\b(?:for|by|per)\s+(?:each\s+)?products?\b", text):
            intent.group_by = "product"
    elif can_normalize_analytical_request and historical_request:
        intent.capability = "historical_analysis"
        intent.question_kind = "comparison"
        intent.metrics = ["quantity"]
        if intent.time.kind == "none":
            intent.time = TimeWindow(kind="latest")
    elif can_normalize_analytical_request and (
        no_sales_request or slow_moving_request or excess_request or excess_level_request
    ):
        if no_sales_request:
            intent.inventory_classification = "no_recorded_sales"
        elif slow_moving_request:
            intent.inventory_classification = "slow_moving_candidate"
        elif excess_level_request:
            intent.inventory_classification = "excess_by_max_level"
        elif excess_coverage_request:
            intent.inventory_classification = "excess_coverage_candidate"
        else:
            # Generic excess wording keeps the established null-subtype
            # behavior; it does not select or redefine an excess rule.
            intent.inventory_classification = None
        intent.capability = "excess_slow_moving"
        intent.question_kind = "ranking" if re.search(
            r"\b(?:top|bottom|highest|lowest|rank|ranking|most|least)\b", text,
        ) else "lookup"
        intent.metrics = ["sales_units"] if intent.inventory_classification in {
            "slow_moving_candidate", "no_recorded_sales",
        } else ["quantity"]
        if (
            intent.inventory_classification in {
                "slow_moving_candidate", "no_recorded_sales",
            }
            and intent.analysis_scope != "organization"
            and not (intent.group_by in {"brand", "category", "city", "region", "store"})
        ):
            intent.analysis_scope = "store"
    elif can_normalize_analytical_request and stockout_request:
        intent.capability = "stockout_risk"
        intent.metrics = ["stockout_risk"]
        if re.search(r"\b(?:top|bottom|highest|lowest|rank|ranking|most|least)\b", text):
            intent.question_kind = "ranking"
    elif can_normalize_analytical_request and sales_request and not inventory_request:
        intent.capability = "sales_trend"
        if explicit_revenue:
            intent.metrics = ["sales_revenue"]
        elif re.search(r"\b(?:growth|change|increase|decrease|trend)\b", text):
            intent.metrics = ["sales_growth_pct"]
        else:
            intent.metrics = ["sales_units"]
    elif can_normalize_analytical_request and inventory_request and not sales_request and not no_sales_request:
        explicit_visibility_request = bool(re.search(
            r"\b(?:current|currently|on[- ]hand|quantity on hand|inventory level)\b"
            r"|\bhow much\b|\b(?:compare|comparison|versus|vs\.?)\b",
            text,
        ))
        explicit_health_request = bool(re.search(
            r"\b(?:health|attention|exception|below reorder|at or below reorder|"
            r"low stock|low inventory|healthy stock)\b",
            text,
        ))
        if (
            intent.capability not in {"stockout_risk", "what_if"}
            and not explicit_health_request
        ):
            intent.capability = "inventory_visibility"
            if explicit_visibility_request or not intent.metrics:
                intent.metrics = ["quantity"]

    # Preserve multiple explicitly requested measures so the result can be
    # dispatched at one entity grain and rendered as a scatter plot.
    compare_measures = bool(re.search(
        r"\b(?:compare|comparison|relationship|correlation|versus|vs\.?|against)\b",
        text,
    ))
    explicit_measure_patterns = (
        ("quantity", r"\b(?:quantity(?:\s+on\s+hand)?|on[- ]hand\s+quantity|inventory\s+units|stock\s+units)\b"),
        ("inventory_value", r"\b(?:inventory\s+value|stock\s+value|inventory\s+worth)\b"),
        ("sales_units", r"\b(?:sales\s+units|units\s+(?:sold|sales))\b"),
        ("sales_revenue", r"\b(?:sales\s+revenue|revenue|turnover)\b"),
        ("days_of_inventory", r"\b(?:days\s+of\s+inventory|inventory\s+coverage\s+days)\b"),
    )
    explicit_measures = [
        (match.start(), metric)
        for metric, pattern in explicit_measure_patterns
        if (match := re.search(pattern, text))
    ]
    explicit_measures = [
        metric for _, metric in sorted(explicit_measures)
    ]
    if (
        can_normalize_analytical_request
        and compare_measures
        and len(explicit_measures) >= 2
        and len(set(explicit_measures)) >= 2
    ):
        inventory_measures = {"quantity", "inventory_value", "days_of_inventory"}
        sales_measures = {"sales_units", "sales_revenue"}
        selected = list(dict.fromkeys(explicit_measures))
        if set(selected).issubset(inventory_measures):
            intent.capability = "inventory_visibility"
            intent.metrics = selected
        elif set(selected).issubset(sales_measures):
            intent.capability = "sales_trend"
            intent.metrics = selected

    if (
        can_normalize_analytical_request
        and re.search(r"\b(?:inventory|stock)\s+health\b|\bstatus\s+breakdown\b", text)
        and not stockout_request
    ):
        intent.capability = "inventory_health"
        intent.metrics = ["stock_status", "stockout_risk"]

    # A catalog browse is an inventory-backed grouped lookup, not a new query
    # capability. Keep it distinct from metric comparisons so the whole list
    # remains available to the existing result table.
    catalog_discovery = bool(re.search(
        r"\b(?:what|which|show|list|browse)\b.{0,50}"
        r"\b(?:products?|brands?|categories|subcategories|items?)\b"
        r".{0,40}\b(?:carry|stock|catalog|available|offer|have)\b"
        r"|\b(?:available|carried|in stock)\s+"
        r"(?:products?|brands?|categories|subcategories|items?)\b"
        r"|\b(?:catalog|product range|assortment)\b",
        text,
    ))
    if (
        can_normalize_analytical_request
        and catalog_discovery and not sales_request and not stockout_request
    ):
        dimension = None
        for pattern, candidate in (
            (r"\bsubcategories?\b", "subcategory"),
            (r"\bcategories\b|\bcategory\b", "category"),
            (r"\bbrands?\b", "brand"),
            (r"\bproducts?\b|\bitems?\b", "product"),
        ):
            if re.search(pattern, text):
                dimension = candidate
                break
        if dimension:
            intent.capability = "inventory_visibility"
            intent.metrics = ["quantity"]
            intent.group_by = dimension
            intent.question_kind = "lookup"
            intent.sort = None
            intent.limit = None

    # Preserve two explicitly named calendar months as a period comparison;
    # do not collapse the pair to one month or infer an adjacent period.
    month_years = []
    for month_number in range(1, 13):
        for label in (calendar.month_name[month_number], calendar.month_abbr[month_number]):
            month_years.extend(
                (match.start(), month_number, int(match.group(1)))
                for match in re.finditer(rf"\b{label}\s+(19\d{{2}}|20\d{{2}})\b", text, flags=re.I)
            )
    month_years.sort(key=lambda item: item[0])
    month_years = list(dict.fromkeys(month_years))
    is_explicit_period_comparison = bool(
        re.search(r"\b(?:compare|versus|vs\.?)\b", text)
        and not re.search(r"\bbetween\b", text)
        and len(month_years) == 2
    )
    if is_explicit_period_comparison:
        _, first_month, first_year = month_years[0]
        _, second_month, second_year = month_years[1]
        intent.time = intent.time.model_copy(update={
            "kind": "month", "month": first_month, "year": first_year,
            "comparison_month": second_month, "comparison_year": second_year,
            "compare_to_previous": False,
        })
    elif period_compare and intent.capability == "sales_trend":
        intent.time = intent.time.model_copy(update={
            "kind": "this_month",
            "compare_to_previous": True,
            "month": None,
            "year": None,
            "comparison_month": None,
            "comparison_year": None,
        })
    elif re.search(r"\bbetween\b", text) and len(month_years) == 2:
        _, first_month, first_year = month_years[0]
        _, second_month, second_year = month_years[1]
        intent.time = intent.time.model_copy(update={
            "kind": "month", "month": second_month, "year": second_year,
            "comparison_month": first_month, "comparison_year": first_year,
            "compare_to_previous": False,
        })
    # Interpret a natural-language month range with a shared year (for
    # example, "change from August to September 2026") as two complete
    # calendar months. The later month is the current period and the earlier
    # month is the comparison period.
    month_labels = {}
    for month_number in range(1, 13):
        for label in (calendar.month_name[month_number], calendar.month_abbr[month_number]):
            if label:
                month_labels[label.casefold()] = month_number
    month_pattern = "|".join(
        re.escape(label) for label in sorted(month_labels, key=len, reverse=True)
    )
    month_mentions = [
        (match.start(), match.end(), month_labels[match.group(0).casefold()])
        for match in re.finditer(rf"\b(?:{month_pattern})\b", text, flags=re.I)
    ]
    years = [(match.start(), int(match.group(0))) for match in re.finditer(r"\b(?:19|20)\d{2}\b", text)]
    from_to_period = re.search(r"\bfrom\b.+\b(?:to|through)\b", text)
    between_period = re.search(r"\bbetween\b.+\band\b", text)
    if len(month_mentions) == 2 and len(years) == 1 and (from_to_period or between_period):
        first_month = month_mentions[0][2]
        second_month = month_mentions[1][2]
        year = years[0][1]
        intent.time = intent.time.model_copy(update={
            "kind": "month", "month": second_month, "year": year,
            "comparison_month": first_month, "comparison_year": year,
            "compare_to_previous": False,
            "date": None, "start_date": None, "end_date": None, "n": None,
        })
        intent.question_kind = "comparison"
        intent.uses_previous_result = False
    # Comparison wording is a semantic guarantee, not an LLM preference.  In
    # particular, prevent a previous-result explanation from turning a fresh
    # "between X and Y" question into a lookup against stale context.
    comparison_word = re.search(
        r"\b(?:compare|comparison|compared\s+(?:with|to)|versus|vs\.?|side[- ]by[- ]side)\b",
        text,
    )
    between_entities = re.search(r"\bbetween\b", text) and not re.search(
        r"\b(?:19|20)\d{2}\b|\b(?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\b|\b\d{4}-\d{2}-\d{2}\b",
        text,
    )
    if comparison_word or between_entities:
        if intent.capability != "what_if":
            intent.question_kind = "comparison"
        intent.uses_previous_result = False
        if intent.group_by is None:
            grouped_filters = [
                (field, dimension)
                for field, dimension in (
                    ("products", "product"),
                    ("brands", "brand"),
                    ("categories", "category"),
                    ("subcategories", "subcategory"),
                    ("stores", "store"),
                    ("cities", "city"),
                    ("regions", "region"),
                )
                if len(getattr(intent.filters, field, []) or []) > 1
            ]
            if len(grouped_filters) == 1:
                intent.group_by = grouped_filters[0][1]

    if intent.group_by is None:
        for pattern, dimension in (
            (r"\b(?:by|for each|for|per)\s+(?:the\s+)?(?:product|products|item|items)\b", "product"),
            (r"\b(?:by|for each|for|per)\s+(?:the\s+)?brands?\b", "brand"),
            (r"\b(?:by|for each|for|per)\s+(?:the\s+)?categories\b|\bby\s+category\b", "category"),
            (r"\b(?:by|for each|for|per)\s+(?:the\s+)?subcategories\b|\bby\s+subcategory\b", "subcategory"),
            (r"\b(?:by|for each|for|per)\s+(?:the\s+)?stores?\b", "store"),
            (r"\b(?:by|for each|for|per)\s+(?:the\s+)?cities\b|\bby\s+city\b", "city"),
            (r"\b(?:by|for each|for|per)\s+(?:the\s+)?regions?\b", "region"),
        ):
            if re.search(pattern, text):
                intent.group_by = dimension
                break

    # Preserve ambiguity for the existing metric clarification flow instead of
    # selecting an arbitrary definition of "best" or "performance".
    ambiguous_store_performance = bool(
        re.search(r"\b(?:which|what)\b.{0,25}\b(?:stores?|locations?)\b", text)
        and re.search(r"\b(?:perform(?:ed|ing)?|performance|best|top)\b", text)
        and not re.search(r"\b(?:revenue|turnover|sales|units|inventory value)\b", text)
    )
    if ambiguous_store_performance:
        intent.capability = "sales_trend"
        intent.question_kind = "comparison"
        intent.group_by = "store"
        intent.metrics = []
        intent.sort = None
        intent.limit = None

    # A zero-stock request is an equality predicate on the operational
    # product-store grain.  Keep this deterministic so an omitted/incorrect
    # threshold from the model cannot broaden the result to all inventory.
    run_out_risk = bool(re.search(r"\b(?:run|running)\s+out\s+of\s+stock\b", text))
    if not run_out_risk and re.search(
        r"\b(?:zero stock|out of stock|no stock|zero inventory|without stock)\b",
        text,
    ):
        from app.llm.schema import Threshold

        intent.capability = "inventory_visibility"
        intent.question_kind = "lookup"
        intent.threshold = Threshold(field="quantity", op="=", value=0)
        intent.sort = None
        intent.limit = None
    # Explicit month/year wording always means the full calendar month. Do
    # not allow an LLM to collapse "May 2026" into May 1st.
    if not is_explicit_period_comparison and not re.search(r"\b(?:from|between)\b.+\b(?:to|and)\b", text):
        for month_number in range(1, 13):
            names = (calendar.month_name[month_number], calendar.month_abbr[month_number])
            match = re.search(rf"\b(?:{names[0]}|{names[1]})\s+(20\d{{2}}|19\d{{2}})\b", text, flags=re.I)
            if not match:
                continue
            suffix = text[match.end():]
            prefix = text[max(0, match.start() - 5):match.start()]
            day_after = re.match(r"\s*\d{1,2}(?:st|nd|rd|th)?\b", suffix)
            day_before = re.search(r"\b\d{1,2}\s*$", prefix)
            if not (day_after or day_before):
                intent.time.kind = "month"
                intent.time.month = month_number
                intent.time.year = int(match.group(1))
                intent.time.date = None
                intent.time.start_date = None
                intent.time.end_date = None
                intent.time.n = None
            break
    # "Need attention" is an exception request, never a lowest-inventory ranking.
    if any(phrase in text for phrase in ("need attention", "needs attention", "require attention", "requires attention")):
        intent.capability = "inventory_health"
        intent.question_kind = "lookup"
        intent.metrics = ["stock_status", "stockout_risk"]
        intent.sort = None
        intent.limit = None
    # An explicit global population always supersedes conversational narrowing.
    if any(token in text for token in (
        "overall", "across all categories", "across all products", "across all stores",
        "whole catalog", "entire catalog", "all categories",
    )):
        intent.uses_previous_result = False
    # IDs are execution-only.  A bare numeric product is never a catalog name.
    products = intent.filters.products
    if products:
        intent.filters.products = [p for p in products if not str(p).strip().isdigit()]
    if intent.capability == "clarification" and not intent.clarification_question:
        intent.clarification_question = (
            "Could you tell me more about what you'd like to see?"
        )
    if intent.limit is not None and intent.limit <= 0:
        intent.limit = None
    if intent.visualization_goal == "automatic":
        if re.search(r"\b(?:relationship|correlation|correlate)\b", text):
            intent.visualization_goal = "relationship"
        elif re.search(r"\b(?:trend|over time|chronological|month(?:ly)? trend)\b", text):
            intent.visualization_goal = "trend"
        elif re.search(r"\b(?:distribution|proportion|percentage|percent|composition|breakdown)\b", text):
            intent.visualization_goal = "distribution"
        elif re.search(r"\b(?:detailed records|full table|all records|list all)\b", text):
            intent.visualization_goal = "detailed_records"
        elif intent.question_kind == "ranking":
            intent.visualization_goal = "ranking"
        elif intent.question_kind == "comparison":
            intent.visualization_goal = "comparison"
        elif re.search(r"\b(?:total|overall count|how many)\b", text):
            intent.visualization_goal = "kpi"
    return intent
