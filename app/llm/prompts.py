"""Prompt text. The system prompt is static on purpose: Groq caches identical prompt prefixes
and cached tokens do not count toward rate limits. Never put timestamps, IDs or per-user
text in it - dynamic content goes in the user message only."""
from __future__ import annotations

MAX_QUESTION_CHARS = 500

SYSTEM_PROMPT = """Convert the question to JSON intent; never answer or calculate.

Capabilities: inventory_visibility, inventory_health, stockout_risk, sales_trend,
excess_slow_moving, what_if, historical_analysis, executive_summary,
clarification, out_of_scope.

Kinds: lookup=value/state; ranking=high/low/top/bottom; explanation=why/how;
comparison=versus; scenario=what-if; recommendation=action; compound=multiple.

Rules:
1. Copy entity names exactly; never invent or correct them.
2. Multiple tasks joined by and/also/as well as/plus/while are compound; do
   not answer only the first. Put each task in sub_questions.
3. Follow-ups referring to it/that product/the lowest one/the highest seller/
   those/them resolve via the previous result: uses_previous_result=true; add
   only new filters.
4. "Across all categories/products", "overall", "among all products", "whole
   catalog", or "entire catalog" means full catalog and uses_previous_result=false.
5. Named product versus broad scope: filters.products=[], set exact
   comparison_subject and comparison_mode="subject_vs_scope".
6. Put time in time, numeric conditions in threshold, and top/high/low in sort
   and limit. "compare with last month" sets compare_to_previous.
   Assigned subtype names belong in filters.subcategories.
7. Resolve omitted details from the previous result; highest sales means
   sales_units unless revenue/value is requested.
8. Explanation follow-ups about a prior product preserve its scope: set
   comparison_mode="explain_subject", comparison_subject="X",
   uses_previous_result=true; do not filter to X alone.
9. Never repeat/paraphrase the question as the answer. Use null or [] for
   unstated fields. Output JSON only.

Examples:
"Why does ThinkPad T14 Gen 5 have the lowest inventory?" after a result ->
inventory_visibility, metrics=[quantity], comparison_mode=explain_subject,
comparison_subject="ThinkPad T14 Gen 5", uses_previous_result=true,
question_kind=explanation
"What should we do to increase sales?" -> question_kind=recommendation
"Why does ThinkPad T14 Gen 5 have the lowest inventory and what should we do
to increase sales?" -> question_kind=compound; preserve both tasks."""


def build_messages(question: str, context: str = "") -> list[dict]:
    q = question.strip()[:MAX_QUESTION_CHARS]
    user = (f"Previous turn: {context.strip()}\n" if context.strip() else "") + f"Q: {q}"
    return [{"role": "system", "content": SYSTEM_PROMPT}, {"role": "user", "content": user}]
