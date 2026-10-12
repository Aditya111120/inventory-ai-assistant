"""Does Groq report prompt-cache hits?   python -m scripts.check_cache"""
import time

from app.config import load_settings
from app.llm.client import GroqClient
from app.llm.intent import _SCHEMA
from app.llm.prompts import build_messages


s = load_settings()
llm = GroqClient(api_key=s.groq_api_key, model=s.groq_model)
fmt = {
    "type": "json_schema",
    "json_schema": {"name": "intent", "schema": _SCHEMA, "strict": True},
}

for i in range(3):
    resp = llm._client.chat.completions.create(
        model=s.groq_model,
        messages=build_messages("Show me inventory by store"),
        temperature=0,
        max_tokens=800,
        response_format=fmt,
        extra_body={"reasoning_effort": "low"},
    )
    print(f"call {i + 1}:", resp.usage.model_dump())
    time.sleep(5)
