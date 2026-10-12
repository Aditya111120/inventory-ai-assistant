import pandas as pd
import pytest

from app.answer_builder import build_answer
from app.llm.intent import _normalise
from app.llm.schema import Filters, Intent, TimeWindow


@pytest.mark.parametrize("capability", ["sales_trend", "inventory_visibility", "inventory_health"])
@pytest.mark.parametrize("month,number", [("May", 5), ("June", 6), ("September", 9)])
def test_explicit_month_never_becomes_first_day(capability, month, number):
    intent = Intent(capability=capability, filters=Filters(products=["Any Product"]), time=TimeWindow(kind="exact_date", date="2026-05-01"))
    result = _normalise(intent, f"Show Any Product sales in {month} 2026 at Any Store")
    assert (result.time.kind, result.time.month, result.time.year) == ("month", number, 2026)
    assert result.time.date is None


def test_exact_day_is_not_forced_into_month():
    intent = Intent(capability="sales_trend", time=TimeWindow(kind="exact_date", date="2026-05-05"))
    result = _normalise(intent, "Show sales on May 5, 2026")
    assert result.time.kind == "exact_date"


def test_aggregate_sales_uses_requested_product_and_store():
    intent = Intent(capability="sales_trend", filters=Filters(products=["iPhone 16"], stores=["T Nagar"]), time=TimeWindow(kind="month", month=5, year=2026))
    frame = pd.DataFrame([{"units": 13, "revenue": 1452500}])
    answer = build_answer(question="Show iPhone 16 sales in May 2026 at T Nagar", intent=intent, df=frame)
    assert "iPhone 16" in answer and "T Nagar" in answer and "May 2026" in answer
    assert "selected scope" not in answer.casefold() and "Basis:" not in answer
