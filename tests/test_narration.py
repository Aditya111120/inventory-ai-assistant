from __future__ import annotations

import pandas as pd

from app.llm.client import Usage
from app.narration import (
    Narrator,
    NumberVerifier,
    extract_numbers,
    template_narrative,
)


class FakeTextLLM:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def complete_text(self, messages, *, max_tokens=300):
        self.calls.append({"messages": messages, "max_tokens": max_tokens})
        response = self.responses.pop(0)
        return (
            response,
            Usage(
                prompt_tokens=20,
                completion_tokens=10,
                total_tokens=30,
            ),
        )


def sample_df():
    return pd.DataFrame(
        [
            {
                "product_id": 92,
                "product_name": "Lenovo Laptop LEN-LAP-4",
                "quantity_on_hand": 212,
                "units_sold_window": 196,
                "avg_daily_sales": 6.533333,
                "days_of_inventory": 32.44898,
                "stockout_risk": "Low",
                "basis": "Actual",
            }
        ]
    )


def test_extract_numbers():
    text = "The product has 32.45 days and 212 units."

    assert extract_numbers(text) == [32.45, 212.0]


def test_verifier_accepts_numbers_from_dataframe():
    verifier = NumberVerifier()

    ok, reason = verifier.verify(
        "The product has 32.45 days of inventory and 212 units.",
        sample_df(),
    )

    assert ok is True
    assert reason == ""


def test_verifier_accepts_reasonable_rounding():
    verifier = NumberVerifier()

    ok, reason = verifier.verify(
        "The product has 32.45 days of coverage.",
        sample_df(),
    )

    assert ok is True
    assert reason == ""


def test_verifier_rejects_ungrounded_number():
    verifier = NumberVerifier()

    ok, reason = verifier.verify(
        "The product has 99 days of inventory.",
        sample_df(),
    )

    assert ok is False
    assert "99" in reason


def test_verifier_accepts_extra_scenario_value():
    verifier = NumberVerifier()

    ok, reason = verifier.verify(
        "With demand increasing by 20%, coverage changes.",
        sample_df(),
        extra_values=[20],
    )

    assert ok is True
    assert reason == ""


def test_narrator_accepts_verified_response():
    llm = FakeTextLLM(
        [
            "Lenovo Laptop LEN-LAP-4 has 32.45 days of inventory coverage (Actual)."
        ]
    )

    narrator = Narrator(llm)

    result = narrator.generate(
        question="How many days of inventory do we have?",
        df=sample_df(),
        basis="Actual",
    )

    assert result.text.startswith("Lenovo Laptop")
    assert result.verified is True
    assert result.regenerated is False
    assert result.used_fallback is False
    assert len(llm.calls) == 1


def test_narrator_regenerates_when_number_is_wrong():
    llm = FakeTextLLM(
        [
            "Lenovo Laptop LEN-LAP-4 has 99 days of inventory.",
            "Lenovo Laptop LEN-LAP-4 has 32.45 days of inventory coverage.",
        ]
    )

    narrator = Narrator(llm)

    result = narrator.generate(
        question="How many days of inventory do we have?",
        df=sample_df(),
        basis="Actual",
    )

    assert result.verified is True
    assert result.regenerated is True
    assert result.used_fallback is False
    assert "32.45" in result.text
    assert len(llm.calls) == 2


def test_narrator_uses_template_when_second_response_is_invalid():
    llm = FakeTextLLM(
        [
            "The product has 99 days of inventory.",
            "The product has 88 days of inventory.",
        ]
    )

    narrator = Narrator(llm)

    result = narrator.generate(
        question="How many days of inventory do we have?",
        df=sample_df(),
        basis="Actual",
    )

    assert result.verified is True
    assert result.regenerated is True
    assert result.used_fallback is True
    assert "32.45" in result.text


def test_narrator_sends_compact_result_not_full_dataframe():
    df = sample_df()

    for i in range(100):
        df.loc[len(df)] = df.iloc[0].to_dict()

    llm = FakeTextLLM(
        [
            "Lenovo Laptop LEN-LAP-4 has 32.45 days of inventory coverage."
        ]
    )

    narrator = Narrator(llm, max_rows=5)

    narrator.generate(
        question="How many days of inventory do we have?",
        df=df,
        basis="Actual",
    )

    messages = llm.calls[0]["messages"]
    user_message = messages[1]["content"]

    assert "Deterministic result rows:" in user_message
    assert "Lenovo Laptop LEN-LAP-4" in user_message


def test_template_narrative_for_doi():
    text = template_narrative(
        "How many days of inventory do we have?",
        sample_df(),
        "Actual",
    )

    assert "32.45" in text
    assert "Low" in text


def test_template_narrative_for_empty_dataframe():
    text = template_narrative(
        "Show inventory",
        pd.DataFrame(),
        "Actual",
    )

    assert text == "No matching records were found."
