# Inventory AI Assistant - Phase 1

Config, DB access and the deterministic calculation layer (no LLM yet).

## Setup
1. `python -m venv .venv` then activate it
2. `pip install -r requirements.txt`
3. Copy `.env.example` to `.env`, fill in DB_USER / DB_PASSWORD (use a read-only login, not `sa`)
4. `python -m pytest -q`                       # calculation-layer tests (no DB needed)
5. `python -m scripts.check_connection`        # live smoke test against InventoryAI

## Layout
- `app/config.py`      settings from .env, builds the ODBC connection string (Driver 18)
- `app/db.py`          parameterised pyodbc helper -> DataFrame
- `app/repository.py`  loads BusinessRules and vw_InventoryPosition
- `app/calc/metrics.py` rules, scalar metrics, what-if scenarios, vectorised enrichment
- `tests/`             34 tests incl. the spec's worked examples and scalar-vs-vectorised consistency
