"""Live smoke test of the intent step against Groq.  Run:  python -m scripts.check_intent"""
import time

from app.config import load_settings
from app.llm.client import GroqClient
from app.llm.intent import parse_intent


QUESTIONS = [
    ("Which products are at risk of stockout in Delhi stores?", ""),
    ("Show me all products with inventory below 50 units", ""),
    ("Compare Samsung and LG TV sales for the last three months", ""),
    ("What happens if supplier lead time increases by 5 days?", "stockout_risk for laptops"),
    ("Which one has the highest sales?", "stockout_risk for laptops"),
    ("Please delete all Apple inventory", ""),
]


def main() -> None:
    s = load_settings()
    llm = GroqClient(api_key=s.groq_api_key, model=s.groq_model)
    print(f"Model: {s.groq_model}\n")
    total = 0
    for q, ctx in QUESTIONS:
        p = parse_intent(q, llm, ctx)
        i = p.intent
        total += p.usage.total_tokens
        print(f"Q: {q}" + (f"   [context: {ctx}]" if ctx else ""))
        print(
            f"   capability={i.capability} group_by={i.group_by} "
            f"follow_up={i.uses_previous_result}"
        )
        print(
            f"   filters={i.filters.model_dump(exclude_defaults=True)} "
            f"threshold={i.threshold} time={i.time.kind}/{i.time.n} "
            f"scenario={i.scenario}"
        )
        print(
            f"   tokens={p.usage.total_tokens} (cached {p.usage.cached_tokens}) "
            f"latency={p.usage.latency_s:.1f}s repaired={p.repaired} strict={p.strict_used}"
        )
        if p.repaired:
            print(f"   repair reason: {p.repair_reason}")
        print()
        time.sleep(8)  # stay under the free tier's 8K tokens/minute
    print(f"Total tokens: {total}")


if __name__ == "__main__":
    main()
