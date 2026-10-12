import json

import pytest

from app.llm.client import GroqClient, LLMUnavailable
from app.llm.intent import IntentError, parse_intent
from app.llm.prompts import SYSTEM_PROMPT, build_messages
from app.llm.schema import Intent, SubQuestion, strict_json_schema


# ---------------------------------------------------------------- fakes
class _Msg:  # mimics groq response objects
    def __init__(self, content):
        self.content = content


class _Choice:
    def __init__(self, content):
        self.message = _Msg(content)


class _Resp:
    def __init__(self, content):
        self.choices = [_Choice(content)]
        self.usage = type(
            "U",
            (),
            {
                "prompt_tokens": 100,
                "completion_tokens": 20,
                "total_tokens": 120,
                "prompt_tokens_details": type("D", (), {"cached_tokens": 60})(),
            },
        )()


class _HTTPError(Exception):
    def __init__(self, status, retry_after=None):
        self.status_code = status
        self.response = type(
            "R",
            (),
            {
                "headers": {"retry-after": str(retry_after)}
                if retry_after
                else {}
            },
        )()


class _FakeCompletions:
    def __init__(self, script):
        self.script, self.calls = list(script), []

    def create(self, **kw):
        self.calls.append(kw)
        item = self.script.pop(0)
        if isinstance(item, Exception):
            raise item
        return _Resp(item if isinstance(item, str) else json.dumps(item))


class _FakeGroq:
    def __init__(self, script):
        self.chat = type("C", (), {})()
        self.chat.completions = _FakeCompletions(script)


def make(script, **kw):
    sleeps = []
    c = GroqClient(
        model="openai/gpt-oss-120b",
        client=_FakeGroq(script),
        sleep=sleeps.append,
        **kw,
    )
    return c, sleeps


GOOD = {
    "capability": "inventory_health",
    "metrics": ["stock_status"],
    "filters": {
        "products": [],
        "brands": [],
        "categories": [],
        "stores": [],
        "cities": [],
        "regions": [],
    },
    "group_by": None,
    "time": {"kind": "none", "n": None, "compare_to_previous": False},
    "threshold": None,
    "sort": None,
    "limit": None,
    "scenario": None,
    "uses_previous_result": False,
    "clarification_question": None,
}


# ---------------------------------------------------------------- schema
def _walk(node):
    if isinstance(node, dict):
        yield node
        for v in node.values():
            yield from _walk(v)
    elif isinstance(node, list):
        for v in node:
            yield from _walk(v)


def test_strict_schema_is_self_contained_and_closed():
    s = strict_json_schema(Intent)
    assert "$defs" not in s and not any("$ref" in n for n in _walk(s))
    for n in _walk(s):
        if n.get("type") == "object" and "properties" in n:
            assert n["additionalProperties"] is False
            assert n["required"] == list(n["properties"].keys())


def test_good_intent_validates_and_prompt_is_compact():
    Intent.model_validate(GOOD)
    assert len(SYSTEM_PROMPT) < 2200


def test_compound_intent_supports_structured_sub_questions():
    intent = Intent.model_validate(
        {
            **GOOD,
            "question_kind": "compound",
            "sub_questions": [
                {
                    "capability": "inventory_visibility",
                    "metrics": ["quantity"],
                    "filters": {"products": ["ThinkPad T14 Gen 5"]},
                    "question_kind": "explanation",
                },
                {
                    "capability": "sales_trend",
                    "metrics": ["sales_units"],
                    "filters": {"products": []},
                    "group_by": "product",
                    "sort": {"by": "sales_units", "direction": "desc"},
                    "limit": 1,
                    "question_kind": "recommendation",
                },
            ],
        }
    )

    assert len(intent.sub_questions) == 2
    assert isinstance(intent.sub_questions[0], SubQuestion)


def test_build_messages_truncates_and_keeps_system_prompt_static():
    m1 = build_messages("x" * 5000)
    m2 = build_messages("hello", context="stockout_risk for laptops")
    assert m1[0]["content"] == m2[0]["content"] == SYSTEM_PROMPT
    assert len(m1[1]["content"]) <= 510
    assert m2[1]["content"].startswith("Previous turn: stockout_risk for laptops")


# ---------------------------------------------------------------- client
def test_success_returns_usage_and_sends_expected_request():
    c, _ = make([GOOD])
    r = c.complete_json(
        [{"role": "user", "content": "hi"}], "intent", {"type": "object"}
    )
    assert r.data["capability"] == "inventory_health" and r.strict_used
    assert r.usage.total_tokens == 120 and r.usage.cached_tokens == 60
    kw = c._client.chat.completions.calls[0]
    assert kw["temperature"] == 0 and kw["extra_body"] == {"reasoning_effort": "low"}
    assert kw["response_format"]["json_schema"]["strict"] is True


def test_400_falls_back_to_non_strict_once():
    c, _ = make([_HTTPError(400), GOOD])
    r = c.complete_json([], "intent", {})
    assert r.strict_used is False
    assert c._client.chat.completions.calls[1]["response_format"]["json_schema"]["strict"] is False


def test_429_waits_for_retry_after_then_succeeds():
    c, sleeps = make([_HTTPError(429, retry_after=3), GOOD])
    assert c.complete_json([], "intent", {}).data["capability"] == "inventory_health"
    assert sleeps == [3.0]


def test_429_with_long_reset_gives_friendly_error_without_waiting():
    c, sleeps = make([_HTTPError(429, retry_after=600)])
    with pytest.raises(LLMUnavailable, match="resets in about 600"):
        c.complete_json([], "intent", {})
    assert sleeps == []


def test_server_errors_back_off_then_give_up():
    c, sleeps = make([_HTTPError(503)] * 4, max_attempts=4)
    with pytest.raises(LLMUnavailable):
        c.complete_json([], "intent", {})
    assert sleeps == [2, 4, 8]


def test_auth_error_is_not_swallowed():
    c, _ = make([_HTTPError(401)])
    with pytest.raises(_HTTPError):
        c.complete_json([], "intent", {})


def test_empty_and_malformed_output_are_user_safe_errors():
    for bad in ("", "not json"):
        c, _ = make([bad])
        with pytest.raises(LLMUnavailable):
            c.complete_json([], "intent", {})


def test_model_required():
    with pytest.raises(ValueError):
        GroqClient(model="", client=object())


# ---------------------------------------------------------------- intent
def test_parse_intent_happy_path():
    c, _ = make([GOOD])
    p = parse_intent("How many SKUs are low, healthy and excess?", c)
    assert p.intent.capability == "inventory_health" and not p.repaired


def test_attention_request_is_normalized_to_health_not_lowest_inventory():
    # Even if an LLM incorrectly proposes a quantity ranking, deterministic
    # normalization preserves the business meaning of "need attention".
    wrong = dict(GOOD, capability="inventory_visibility", metrics=["quantity"], sort={"by": "quantity", "direction": "asc"}, limit=1, question_kind="ranking")
    c, _ = make([wrong])
    intent = parse_intent("What products need attention?", c).intent
    assert intent.capability == "inventory_health"
    assert intent.sort is None and intent.limit is None
    assert intent.metrics == ["stock_status", "stockout_risk"]


def test_comparison_wording_cannot_inherit_previous_result_scope():
    wrong = dict(GOOD, uses_previous_result=True, question_kind="lookup")
    c, _ = make([wrong])
    intent = parse_intent("Compare laptop inventory between Chennai and Delhi", c).intent
    assert intent.question_kind == "comparison"
    assert intent.uses_previous_result is False


def test_zero_stock_wording_is_an_exact_quantity_filter():
    c, _ = make([dict(GOOD, capability="inventory_visibility")])
    intent = parse_intent("Show products with zero stock in Chennai", c).intent
    assert intent.capability == "inventory_visibility"
    assert intent.threshold is not None
    assert (intent.threshold.field, intent.threshold.op, intent.threshold.value) == ("quantity", "=", 0)


def test_date_range_between_is_not_misclassified_as_comparison():
    c, _ = make([dict(GOOD, capability="sales_trend")])
    intent = parse_intent("Show sales between May 2026 and June 2026", c).intent
    assert intent.question_kind != "comparison"


def test_explicit_month_comparison_preserves_both_full_calendar_periods():
    c, _ = make([dict(GOOD, capability="sales_trend", question_kind="lookup")])
    intent = parse_intent(
        "Compare sales units by category in June 2026 versus May 2026", c
    ).intent
    assert intent.question_kind == "comparison"
    assert intent.time.kind == "month"
    assert (intent.time.month, intent.time.year) == (6, 2026)
    assert (intent.time.comparison_month, intent.time.comparison_year) == (5, 2026)


def test_parse_intent_repairs_once():
    bad = dict(GOOD, capability="made_up")
    c, _ = make([bad, GOOD])
    p = parse_intent("q", c)
    assert p.repaired and p.usage.calls == 2
    assert "capability" in c._client.chat.completions.calls[1]["messages"][-1]["content"]


def test_parse_intent_gives_up_after_failed_repair():
    bad = dict(GOOD, capability="made_up")
    c, _ = make([bad, bad])
    with pytest.raises(IntentError):
        parse_intent("q", c)


def test_clarification_gets_default_question_and_empty_input_rejected():
    c, _ = make([dict(GOOD, capability="clarification")])
    assert parse_intent("hmm", c).intent.clarification_question
    with pytest.raises(IntentError):
        parse_intent("   ", c)


def test_null_objects_and_lists_are_treated_as_not_stated_without_a_repair_call():
    sloppy = dict(
        GOOD,
        capability="out_of_scope",
        filters=None,
        time=None,
        metrics=None,
        uses_previous_result=None,
    )
    c, _ = make([sloppy])
    p = parse_intent("delete all Apple inventory", c)
    assert not p.repaired and p.usage.calls == 1
    assert (
        p.intent.filters.brands == []
        and p.intent.time.kind == "none"
        and p.intent.metrics == []
    )


def test_null_inside_filters_and_time_is_coerced():
    sloppy = dict(
        GOOD,
        filters={
            "products": None,
            "brands": ["Apple"],
            "categories": [],
            "stores": [],
            "cities": None,
            "regions": [],
        },
        time={"kind": None, "n": None, "compare_to_previous": None},
    )
    c, _ = make([sloppy])
    p = parse_intent("q", c)
    assert not p.repaired and p.intent.filters.brands == ["Apple"]
    assert p.intent.filters.cities == []


def test_invalid_enum_is_not_coerced_and_repair_reason_is_recorded():
    bad = dict(GOOD, capability="made_up")
    c, _ = make([bad, GOOD])
    p = parse_intent("q", c)
    assert p.repaired and "capability" in p.repair_reason


def test_strict_flag_is_reported():
    c, _ = make([_HTTPError(400), GOOD])
    assert parse_intent("q", c).strict_used is False
