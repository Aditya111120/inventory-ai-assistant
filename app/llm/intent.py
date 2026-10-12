"""Question -> validated Intent. One repair attempt on invalid output, then a clear error."""
from __future__ import annotations

import json
import calendar
import re
from dataclasses import dataclass

from pydantic import ValidationError

from app.llm.client import Usage
from app.llm.prompts import build_messages
from app.llm.schema import Intent, strict_json_schema


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
    comparison_word = re.search(r"\b(?:compare|comparison|versus|vs\.?)\b", text)
    between_entities = re.search(r"\bbetween\b", text) and not re.search(
        r"\b(?:19|20)\d{2}\b|\b(?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\b|\b\d{4}-\d{2}-\d{2}\b",
        text,
    )
    if comparison_word or between_entities:
        intent.question_kind = "comparison"
        intent.uses_previous_result = False

    # A zero-stock request is an equality predicate on the operational
    # product-store grain.  Keep this deterministic so an omitted/incorrect
    # threshold from the model cannot broaden the result to all inventory.
    if re.search(
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
    return intent
