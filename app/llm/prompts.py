"""Prompt text. The system prompt is static on purpose: Groq caches identical prompt prefixes
and cached tokens do not count toward rate limits. Never put timestamps, IDs or per-user
text in it - dynamic content goes in the user message only."""
from __future__ import annotations

MAX_QUESTION_CHARS = 500

SYSTEM_PROMPT = """Convert questions to JSON intent only; never answer or calculate.
Capabilities: inventory_visibility, inventory_health, stockout_risk, sales_trend,
excess_slow_moving, what_if, historical_analysis, executive_summary, clarification,
out_of_scope. Kinds: lookup, ranking, explanation, comparison, scenario,
recommendation, compound.

Rules:
- Copy entity names exactly; preserve explicit filters and periods.
- Canonical terms: slow-moving/slow sellers -> excess_slow_moving +
  slow_moving_candidate; no/zero recorded sales/never sold -> no_recorded_sales;
  generic excess/overstock -> excess_slow_moving, null subtype (legacy rule);
  explicit max-level/excess-coverage -> matching subtype; stockout/run-out risk ->
  stockout_risk; sales/units sold -> sales_trend + sales_units; revenue/turnover ->
  sales_trend + sales_revenue; current stock/on-hand -> inventory_visibility +
  quantity.
- Catalog browse ("what brands/categories/products do we carry?") is a grouped,
  quantity-based inventory_visibility lookup, not a catalog-only list.
- Never guess a store performance metric: "which store performs best?" uses
  group_by=store, question_kind=comparison, metrics=[] for clarification.
- Use analysis_scope=organization for explicit all-store requests, store when one
  store is required, else unspecified. Grouped category/brand/city/region/store
  work is organization-level.
- Put dates in time, conditions in threshold, ranking in sort/limit, and assigned
  subcategory names in filters.subcategories. "Compare with last month" sets
  compare_to_previous. Highest sales means units unless value is requested.
- Compound questions include every task in sub_questions. Follow-ups use prior
  context and add only new filters; independent requests do not inherit it.
  Subject-vs-scope and explain-subject comparisons preserve their distinct modes.
- Use only a specifically requested excess subtype: excess_by_max_level,
  excess_coverage_candidate, slow_moving_candidate (1-2 units), or
  no_recorded_sales (zero). Null preserves legacy behavior. Use null/[] when
  unstated. Output JSON only."""


def build_messages(question: str, context: str = "") -> list[dict]:
    q = question.strip()[:MAX_QUESTION_CHARS]
    user = (f"Previous turn: {context.strip()}\n" if context.strip() else "") + f"Q: {q}"
    return [{"role": "system", "content": SYSTEM_PROMPT}, {"role": "user", "content": user}]
