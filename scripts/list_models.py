"""Lists the Groq models your key can call, and checks GROQ_MODEL against them.

Run from the project root:  python -m scripts.list_models
"""
import json
import urllib.error
import urllib.request

from app.config import load_settings


def main() -> None:
    s = load_settings()
    if not s.groq_api_key:
        raise SystemExit("GROQ_API_KEY is empty in .env")

    req = urllib.request.Request(
        "https://api.groq.com/openai/v1/models",
        headers={
            "Authorization": f"Bearer {s.groq_api_key}",
            "User-Agent": "inventory-ai/0.1",
            "Accept": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            ids = sorted(m["id"] for m in json.load(r)["data"])
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")[:500]
        raise SystemExit(f"Groq returned HTTP {e.code}: {e.reason}\n{body}")
    except urllib.error.URLError as e:
        raise SystemExit(f"Could not reach Groq: {e.reason}")

    print("\n".join(ids))
    print(f"\nGROQ_MODEL = {s.groq_model!r}")
    print("Available to this key:", s.groq_model in ids)


if __name__ == "__main__":
    main()
