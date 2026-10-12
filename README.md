# Inventory AI Assistant

Inventory AI Assistant is a React and FastAPI application for asking natural-language questions about inventory, sales, stock availability, and operational risk. The backend resolves requests against a SQL Server catalog, runs parameterized analytical queries, and calculates metrics deterministically. An LLM may interpret questions and provide optional narration; it is not the source of calculated results.

## Start here

1. Read [Developer setup](docs/DEVELOPER_SETUP.md) for prerequisites, safe environment configuration, database access requirements, and local run commands.
2. Read [Architecture](docs/ARCHITECTURE.md) for the request flow and component responsibilities.
3. Read [Database schema](docs/DATABASE_SCHEMA.md) and [Business rules](docs/BUSINESS_RULES.md) for the code-supported data contract and calculations.
4. Use [Troubleshooting](docs/TROUBLESHOOTING.md) if local setup or a request fails.

### Quick start on Windows

Start the API in one PowerShell terminal, from the project root, after configuring `.env` and activating the virtual environment:

```powershell
.\.venv\Scripts\Activate.ps1
python -m uvicorn app.api:app --host 127.0.0.1 --port 8000
```

Start the frontend in a second terminal:

```powershell
cd frontend
$env:VITE_API_URL = "http://127.0.0.1:8000"
npm run dev -- --host 127.0.0.1
```

Open the local URL printed by Vite. The API still requires a reachable, correctly provisioned SQL Server database and a valid server-side configuration. The example settings contain placeholders, not working credentials.

## Repository map

- `app/api.py` — FastAPI endpoints and response serialization.
- `app/orchestrator.py` — request interpretation, clarification, authorization-aware scope, and response flow.
- `app/semantic/` — catalog resolution and parameterized SQL query templates.
- `app/calc/metrics.py` — deterministic metrics and Business Rules V1.
- `app/auth.py` — SQL-backed login, session revocation, and role scope.
- `app/history.py` — local SQLite conversation history.
- `frontend/src/` — React application and styling.
- `scripts/` — diagnostics, initialization, and acceptance utilities.
- `tests/` — backend regression tests.

## Validation

```powershell
python -m pytest -q
```

```powershell
cd frontend
npm run lint
npm run build
```

## Safety and scope

Do not commit `.env`, credentials, database exports, conversation databases, or unreviewed product/data reports. See [Publication security review](docs/PUBLICATION_SECURITY_REVIEW.md) before publishing repository contents. SQL migrations are not run automatically by application startup and must be reviewed and approved by the database owner.

The complete SQL Server DDL for the base operational schema and access provisioning is not included in this repository. See [Database schema](docs/DATABASE_SCHEMA.md) for the verified code contract and required external prerequisites.
