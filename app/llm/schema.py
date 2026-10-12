"""The 'intent' the LLM must produce. The LLM only interprets; it never answers or computes.

Entity values (products, brands, stores, ...) are kept exactly as the user wrote them.
Resolving them to real IDs is done later by deterministic code, so the Trust Panel can show
"you said 'Delhi stores' -> stores 1, 2, 3".
"""
from __future__ import annotations

from typing import Any, Literal, Optional

from pydantic import BaseModel, Field

Capability = Literal[
    "inventory_visibility",
    "inventory_health",
    "stockout_risk",
    "sales_trend",
    "excess_slow_moving",
    "what_if",
    "historical_analysis",
    "executive_summary",
    "clarification",
    "out_of_scope",
]
Dimension = Literal["product", "brand", "category", "subcategory", "store", "city", "region"]
Metric = Literal[
    "quantity",
    "inventory_value",
    "stock_status",
    "days_of_inventory",
    "stockout_risk",
    "sales_units",
    "sales_revenue",
    "sales_growth_pct",
    "inventory_growth_pct",
    "excess_value",
]
ComparisonMode = Literal[
    "none",
    "subject_vs_scope",
    "explain_subject",
]
QuestionKind = Literal[
    "lookup",
    "ranking",
    "explanation",
    "comparison",
    "scenario",
    "recommendation",
    "compound",
    "clarification",
    "out_of_scope",
]


class Filters(BaseModel):
    products: list[str] = Field(default_factory=list)
    brands: list[str] = Field(default_factory=list)
    categories: list[str] = Field(default_factory=list)
    subcategories: list[str] = Field(default_factory=list)
    stores: list[str] = Field(default_factory=list)
    cities: list[str] = Field(default_factory=list)
    regions: list[str] = Field(default_factory=list)


class TimeWindow(BaseModel):
    kind: Literal[
        "none",
        "last_n_days",
        "last_n_months",
        "this_month",
        "last_month",
        "exact_date",
        "month",
        "date_range",
        "latest",
    ] = "none"
    n: Optional[int] = None
    date: Optional[str] = None
    start_date: Optional[str] = None
    end_date: Optional[str] = None
    year: Optional[int] = None
    month: Optional[int] = None
    compare_to_previous: bool = False
    comparison_month: Optional[int] = None
    comparison_year: Optional[int] = None
    comparison_start_date: Optional[str] = None
    comparison_end_date: Optional[str] = None


class Threshold(BaseModel):
    field: Literal["quantity", "days_of_inventory", "lead_time_days", "sales_units"]
    op: Literal["<", "<=", ">", ">=", "="]
    value: float


class Sort(BaseModel):
    by: Metric
    direction: Literal["asc", "desc"]


class Scenario(BaseModel):
    kind: Literal[
        "demand_change_pct",
        "lead_time_extra_days",
        "lead_time_pct",
        "additional_units_sold",
        "target_coverage_days",
        "daily_sales_to",
    ]
    value: float


class SubQuestion(BaseModel):
    capability: Capability
    metrics: list[Metric]
    filters: Filters
    group_by: Optional[Dimension] = None
    sort: Optional[Sort] = None
    limit: Optional[int] = None
    question_kind: QuestionKind = "lookup"


class Intent(BaseModel):
    capability: Capability
    metrics: list[Metric] = Field(default_factory=list)
    filters: Filters = Field(default_factory=Filters)
    group_by: Optional[Dimension] = None
    time: TimeWindow = Field(default_factory=TimeWindow)
    threshold: Optional[Threshold] = None
    sort: Optional[Sort] = None
    limit: Optional[int] = None
    scenario: Optional[Scenario] = None
    comparison_mode: ComparisonMode = "none"
    comparison_subject: Optional[str] = None
    question_kind: QuestionKind = "lookup"
    sub_questions: list[SubQuestion] = Field(default_factory=list)
    uses_previous_result: bool = False
    clarification_question: Optional[str] = None


def strict_json_schema(model: type[BaseModel]) -> dict[str, Any]:
    """Pydantic schema -> schema accepted by Groq's strict structured-output mode.

    Inlines $ref/$defs, forbids extra properties and marks every property required
    (optional values are expressed as nullable). Defaults/titles are dropped.
    """
    schema = model.model_json_schema()
    defs = schema.pop("$defs", {})

    def walk(node: Any) -> Any:
        if isinstance(node, list):
            return [walk(x) for x in node]
        if not isinstance(node, dict):
            return node
        if "$ref" in node:
            return walk(defs[node["$ref"].split("/")[-1]])
        out = {k: walk(v) for k, v in node.items() if k not in ("default", "title")}
        if out.get("type") == "object" and "properties" in out:
            out["additionalProperties"] = False
            out["required"] = list(out["properties"].keys())
        return out

    return walk(schema)
