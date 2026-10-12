"""Live end-to-end check for the Inventory AI Assistant.

Run from the project root:

    python -m scripts.check_chat

This requires:
- working .env configuration
- SQL Server connection
- valid Groq credentials/model

No secrets are printed.
"""

from __future__ import annotations

import sys
import uuid

from app.config import load_settings
from app.db import connect, query
from app.llm.client import GroqClient
from app.orchestrator import Orchestrator
from app.repository import load_catalog, load_rules
from app.semantic.queries import Queries
from app.semantic.resolver import EntityResolver


def build_orchestrator() -> Orchestrator:
    settings = load_settings()

    if not settings.groq_api_key:
        raise RuntimeError("GROQ_API_KEY is not configured")

    conn = connect(settings)

    rules = load_rules(conn)
    products, stores = load_catalog(conn)

    resolver = EntityResolver(products, stores)

    queries = Queries(
        run=query,
        conn=conn,
        schema="dbo.",
    )

    llm = GroqClient(
        api_key=settings.groq_api_key,
        model=settings.groq_model,
        reasoning_effort="low",
    )

    from app.session import SessionMemory

    return Orchestrator(
        llm=llm,
        queries=queries,
        resolver=resolver,
        rules=rules,
        session_memory=SessionMemory(),
    )


def print_response(question: str, response) -> None:
    print("\n" + "=" * 80)
    print(f"QUESTION: {question}")
    print("=" * 80)

    print(f"STATUS: {response.status}")

    if response.error:
        print(f"ERROR: {response.error}")

    if response.intent is not None:
        print("\nINTENT:")
        print(f"  capability = {response.intent.capability}")
        print(f"  metrics    = {response.intent.metrics}")
        print(f"  group_by   = {response.intent.group_by}")
        print(f"  filters    = {response.intent.filters}")
        print(f"  threshold  = {response.intent.threshold}")
        print(f"  sort       = {response.intent.sort}")
        print(f"  limit      = {response.intent.limit}")
        print(f"  scenario   = {response.intent.scenario}")
        print(
            f"  uses_previous_result = "
            f"{response.intent.uses_previous_result}"
        )

    if response.interpretation:
        print("\nINTERPRETATION:")
        for line in response.interpretation:
            print(f"  {line}")

    print(f"\nROW COUNT: {response.row_count}")
    print(f"BASIS: {response.basis}")

    if response.narrative:
        print(f"\nNARRATIVE:\n{response.narrative}")

    if response.notes:
        print("\nNOTES:")
        for note in response.notes:
            print(f"  {note}")

    if response.sql:
        print("\nSQL:")
        for i, sql in enumerate(response.sql, start=1):
            print(f"  [{i}] {sql}")

    if response.params:
        print("\nPARAMS:")
        for i, params in enumerate(response.params, start=1):
            print(f"  [{i}] {params}")

    if response.data is not None:
        print("\nDATA PREVIEW:")
        print(response.data.head(10).to_string(index=False))

    if response.usage:
        print("\nUSAGE:")
        print(f"  prompt_tokens     = {response.usage.prompt_tokens}")
        print(f"  completion_tokens = {response.usage.completion_tokens}")
        print(f"  total_tokens      = {response.usage.total_tokens}")
        print(f"  cached_tokens     = {response.usage.cached_tokens}")
        print(f"  latency_s         = {response.usage.latency_s:.3f}")
        print(f"  calls             = {response.usage.calls}")


def main() -> int:
    try:
        orchestrator = build_orchestrator()
    except Exception as exc:
        print(f"SETUP FAILED: {exc}")
        return 1

    session_id = f"check-{uuid.uuid4().hex[:8]}"

    questions = [
        "Which Lenovo laptops have the lowest inventory?",
        "Which one has the highest sales?",
        "How many days of inventory do we have?",
        "What if demand increases by 20%?",
    ]

    for question in questions:
        try:
            response = orchestrator.handle(
                question=question,
                session_id=session_id,
            )
            print_response(question, response)
        except Exception as exc:
            print(f"\nQUESTION FAILED: {question}")
            print(f"ERROR: {type(exc).__name__}: {exc}")
            return 1

    print("\n" + "=" * 80)
    print("LIVE CHAT CHECK COMPLETE")
    print("=" * 80)

    return 0


if __name__ == "__main__":
    sys.exit(main())
