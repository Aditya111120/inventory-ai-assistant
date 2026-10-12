"""Narration and number-verification layer.

The LLM receives only a compact summary of the deterministic result.
It does not receive the full dataframe and it is not allowed to calculate
new business numbers.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from difflib import SequenceMatcher
from typing import Any, Iterable, Optional

import pandas as pd

from app.llm.client import LLMUnavailable, Usage


NUMBER_RE = re.compile(
    r"(?<![A-Za-z0-9-])[-+]?(?:\d+(?:,\d{3})*(?:\.\d+)?|\.\d+)(?:%)?(?![A-Za-z0-9-])"
)


@dataclass
class NarrationResult:
    text: str
    usage: Usage
    verified: bool
    regenerated: bool
    used_fallback: bool
    verification_reason: str = ""


def _normalise_number(value: float) -> float:
    """Normalise a numeric value for tolerant comparison."""
    return round(float(value), 4)


def _number_candidates(value: Any) -> set[float]:
    """Return numeric representations accepted by the verifier."""
    if value is None or isinstance(value, bool):
        return set()

    try:
        number = float(value)
        if not math.isfinite(number):
            return set()
        return {
            _normalise_number(number),
            _normalise_number(round(number, 1)),
            _normalise_number(round(number, 2)),
        }
    except (TypeError, ValueError):
        return set()


def extract_numbers(text: str) -> list[float]:
    """Extract numeric tokens from generated text."""
    values = []
    for match in NUMBER_RE.findall(text):
        cleaned = match.replace(",", "").rstrip("%")
        try:
            values.append(float(cleaned))
        except ValueError:
            continue
    return values


def _normalise_text(text: str) -> str:
    return re.sub(
        r"[^a-z0-9\s]",
        " ",
        text.casefold(),
    ).strip()


def _question_similarity(question: str, answer: str) -> float:
    q = _normalise_text(question)
    a = _normalise_text(answer)

    if not q or not a:
        return 0.0

    return SequenceMatcher(None, q, a).ratio()


def _answer_has_result_evidence(
    answer: str,
    df: pd.DataFrame,
) -> bool:
    """Require the answer to mention at least one useful result field."""

    normalized = _normalise_text(answer)

    candidate_columns = (
        "product_name",
        "brand",
        "category",
        "store_name",
        "stock_status",
        "stockout_risk",
    )

    for column in candidate_columns:
        if column not in df.columns:
            continue

        for value in df[column].dropna().astype(str).head(10):
            if len(value.strip()) >= 3:
                if _normalise_text(value) in normalized:
                    return True

    return bool(extract_numbers(answer))


class NumberVerifier:
    """Verify that generated numeric claims are grounded in known values."""

    def allowed_numbers(
        self,
        df: pd.DataFrame,
        extra_values: Iterable[Any] = (),
    ) -> set[float]:
        allowed: set[float] = set()
        for value in extra_values:
            allowed.update(_number_candidates(value))
        for column in df.columns:
            series = df[column]
            if not pd.api.types.is_numeric_dtype(series):
                continue
            for value in series.dropna().tolist():
                allowed.update(_number_candidates(value))
        return allowed

    def verify(
        self,
        text: str,
        df: pd.DataFrame,
        extra_values: Iterable[Any] = (),
    ) -> tuple[bool, str]:
        """Return whether every numeric claim can be grounded."""
        allowed = self.allowed_numbers(df, extra_values=extra_values)
        for number in extract_numbers(text):
            if not self._matches(number, allowed):
                return False, f"Ungrounded number: {number:g}"
        return True, ""

    def verify_narration(
        self,
        question: str,
        text: str,
        df: pd.DataFrame,
        extra_values: Iterable[Any] = (),
    ) -> tuple[bool, str]:
        """Verify numbers and basic answer quality."""

        number_ok, number_reason = self.verify(
            text,
            df,
            extra_values=extra_values,
        )

        if not number_ok:
            return False, number_reason

        similarity = _question_similarity(
            question,
            text,
        )

        if similarity >= 0.82:
            return (
                False,
                "Narration is too similar to the user question.",
            )

        if not _answer_has_result_evidence(
            text,
            df,
        ):
            return (
                False,
                "Narration does not reference the calculated result.",
            )

        return True, ""

    @staticmethod
    def _matches(number: float, allowed: set[float]) -> bool:
        candidate = _normalise_number(number)
        if candidate in allowed:
            return True
        return any(
            math.isclose(candidate, allowed_number, rel_tol=0, abs_tol=0.02)
            for allowed_number in allowed
        )


def _value(row: pd.Series, column: str, default: Any = None) -> Any:
    if column not in row.index:
        return default
    value = row[column]
    if pd.isna(value):
        return default
    return value


def template_narrative(question: str, df: pd.DataFrame, basis: str) -> str:
    """Deterministic fallback when LLM narration cannot be trusted."""
    if df.empty:
        return "No matching records were found."

    row = df.iloc[0]
    product_name = _value(row, "product_name", "The top matching product")
    basis_text = "Simulated" if basis == "Simulated" else "Actual"

    if "days_of_inventory" in df.columns:
        doi = _value(row, "days_of_inventory")
        if doi is not None:
            risk = _value(row, "stockout_risk")
            sentence = (
                f"{product_name} has {float(doi):.2f} days of inventory "
                f"coverage ({basis_text})."
            )
            if risk is not None:
                sentence += f" Stockout risk is {risk}."
            return sentence

    if "units" in df.columns:
        units = _value(row, "units")
        if units is not None:
            return f"{product_name} recorded {float(units):g} sales units ({basis_text})."

    if "quantity_on_hand" in df.columns:
        quantity = _value(row, "quantity_on_hand")
        if quantity is not None:
            return (
                f"{product_name} currently has {float(quantity):g} units "
                f"in inventory ({basis_text})."
            )

    return f"Found {len(df)} matching result{'' if len(df) == 1 else 's'} ({basis_text})."


class Narrator:
    """Generate a concise natural-language answer from deterministic data."""

    def __init__(
        self,
        llm,
        *,
        verifier: Optional[NumberVerifier] = None,
        max_rows: int = 20,
    ) -> None:
        self.llm = llm
        self.verifier = verifier or NumberVerifier()
        self.max_rows = max_rows

    @staticmethod
    def _compact_value(value: Any) -> Any:
        if pd.isna(value):
            return None
        if isinstance(value, float):
            return round(value, 4)
        return value

    def _compact_result(self, df: pd.DataFrame) -> list[dict]:
        """Send only a small result subset to the LLM."""
        columns = [
            "product_id", "product_name", "brand", "category", "subcategory",
            "store_name", "city", "quantity_on_hand", "reorder_level", "max_level",
            "inventory_value", "units_sold_window", "avg_daily_sales", "units",
            "revenue", "growth_pct", "days_of_inventory", "lead_time_days",
            "basis",
            "inventory_status", "inventory_data_quality", "demand_status",
            "stockout_risk_v1",
        ]
        available = [c for c in columns if c in df.columns] or list(df.columns[:15])
        subset = df.head(self.max_rows)[available]
        return [
            {key: self._compact_value(value) for key, value in row.items()}
            for row in subset.to_dict(orient="records")
        ]

    def _messages(self, question: str, df: pd.DataFrame, basis: str) -> list[dict]:
        compact = self._compact_result(df)
        system = """You narrate deterministic inventory analytics results.

Your job is to answer the user's question using the supplied result.

Rules:
1. Answer the question directly.
2. Never repeat or paraphrase the user's question as the answer.
3. Never ask the user the same question back.
4. Never calculate or estimate a number.
5. Use only numbers explicitly present in the supplied result.
6. Mention the relevant product, category, store, metric, or result.
7. Do not introduce facts that are not in the result.
8. Keep the answer to 1-3 concise sentences.
9. Clearly state whether the result is Actual or Simulated when relevant.
10. Do not mention SQL, prompts, models, or internal implementation.
11. Do not invent missing values.
12. When both legacy and V1 labels are supplied, use only inventory_status,
    demand_status, stockout_risk_v1, and the V1 quality fields. Do not mention
    legacy stock_status or stockout_risk labels.
13. For recommendations, state only what the supplied evidence supports and
    identify what additional information is needed; do not invent actions or claims.
"""
        user = (
            f"User question: {question}\n"
            f"Basis: {basis}\n"
            f"Deterministic result rows:\n{compact}\n"
            "Write the final answer now."
        )
        return [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ]

    def generate(
        self,
        *,
        question: str,
        df: pd.DataFrame,
        basis: str,
        extra_values: Iterable[Any] = (),
    ) -> NarrationResult:
        """Generate, verify, optionally regenerate, then fallback."""
        if df.empty:
            return NarrationResult(
                text="No matching records were found.",
                usage=Usage(),
                verified=True,
                regenerated=False,
                used_fallback=True,
            )

        total_usage = Usage(
            prompt_tokens=0, completion_tokens=0, total_tokens=0,
            cached_tokens=0, latency_s=0.0, calls=0,
        )

        try:
            text, usage = self.llm.complete_text(
                self._messages(question, df, basis), max_tokens=300
            )
            total_usage = total_usage + usage
            ok, reason = self.verifier.verify_narration(
                question,
                text,
                df,
                extra_values=extra_values,
            )
            if ok:
                return NarrationResult(text, total_usage, True, False, False)

            repair_messages = self._messages(question, df, basis)
            repair_messages.extend(
                [
                    {"role": "assistant", "content": text},
                    {
                        "role": "user",
                        "content": (
                            f"Your previous answer contained an ungrounded numeric "
                            f"claim ({reason}). Rewrite the answer using only "
                            f"numbers present in the supplied result."
                        ),
                    },
                ]
            )
            repaired, repair_usage = self.llm.complete_text(
                repair_messages, max_tokens=300
            )
            total_usage = total_usage + repair_usage
            ok2, reason2 = self.verifier.verify_narration(
                question,
                repaired,
                df,
                extra_values=extra_values,
            )
            if ok2:
                return NarrationResult(repaired, total_usage, True, True, False)

            return NarrationResult(
                template_narrative(question, df, basis),
                total_usage,
                True,
                True,
                True,
                reason2,
            )
        except LLMUnavailable:
            return NarrationResult(
                template_narrative(question, df, basis),
                total_usage,
                True,
                False,
                True,
                "LLM unavailable",
            )
