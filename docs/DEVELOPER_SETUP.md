# Developer setup

This guide describes a local development setup. It uses placeholders only. Never copy production credentials, internal hostnames, private IP addresses, or real data into this document or a committed environment file.

## Prerequisites

- Python 3.10 or newer with `venv` and `pip`.
- Node.js and npm versions supported by the Vite version declared in `frontend/package.json`.
- Microsoft ODBC Driver 18 for SQL Server and network access to a developer-approved SQL Server, if running the real backend.
- A database provisioned with the required Inventory AI schema and approved development data. The base operational DDL is not part of this repository.
- A Groq API key if using the configured Groq interpretation/narration integration. A key is not needed for unit tests.
- PowerShell on Windows, or an equivalent terminal on other platforms.

The frontend package manager is npm. Install frontend dependencies from `frontend/package-lock.json`; do not run npm commands from the repository root.

## Clone and configure

Clone from the organization-approved repository URL; do not place a personal token in the URL:

```powershell
git clone <approved-repository-url>
cd <repository-directory>
```

Create a private local environment file from the sanitized example:

```powershell
Copy-Item .env.example .env
notepad .env
```

Supply values through your approved local secret-management process. The example includes placeholders only. Keep `.env` untracked and never paste its values into issues, logs, screenshots, or documentation.

Configuration names used by `app/config.py` and `app/api.py`:

| Variable | Purpose |
|---|---|
| `DB_SERVER` | SQL Server host or approved instance name. Required. |
| `DB_NAME` | Database name; defaults to `InventoryAI` in code if omitted. Use the approved development database. |
| `DB_AUTH` | `sql` or `windows`; defaults to `sql`. |
| `DB_USER`, `DB_PASSWORD` | Required for SQL authentication. Do not set or publish actual values in examples. |
| `DB_DRIVER` | ODBC driver name; defaults to `ODBC Driver 18 for SQL Server`. |
| `DB_TRUST_SERVER_CERT` | Controls ODBC `TrustServerCertificate`; defaults to enabled in current code. Follow local security policy. |
| `GROQ_API_KEY` | Optional for tests, required for requests needing the external LLM integration. |
| `GROQ_MODEL` | Optional model identifier; set to an approved model supported by the account. |
| `APP_ENV` | Server environment label. Use `development` only for local development. |
| `AUTH_COOKIE_SECURE` | Server-side cookie setting; loopback HTTP development may use `false`. HTTPS deployments must use `true`. |
| `LEADERSHIP_ONLY_DEMO` | Demo-only login gate; enabled by default in the API. Do not use it as a substitute for role authorization. |
| `LEADERSHIP_DEMO_ACCOUNT` | Account allowed by the demo gate. Configure it to match an approved local test account; never put a real account identifier in a committed example. |

The environment example is not a fully working database or login setup. If SQL Server Windows authentication is approved, set `DB_AUTH=windows`; otherwise SQL authentication uses the configured user and password. Authentication also requires provisioned application users with valid password hashes and role/access rows. Use an approved database administrator procedure; never commit passwords or hashes.

## Install Python dependencies

From the project root:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -r requirements.txt
```

If PowerShell blocks activation, follow your workstation policy or invoke the venv interpreter directly:

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

## Install frontend dependencies

In a second terminal:

```powershell
cd frontend
npm ci
```

`VITE_API_URL` is the frontend API base URL. Set it in the terminal before starting Vite, or in an untracked local frontend environment file:

```powershell
$env:VITE_API_URL = "http://127.0.0.1:8000"
```

The API's current CORS allowlist includes local frontend origins on `localhost:5173` and `127.0.0.1:5173`. Use matching hostnames in the browser and API URL; `localhost` and `127.0.0.1` are distinct cookie origins.

## Run locally

Terminal 1, at the project root:

```powershell
.\.venv\Scripts\Activate.ps1
python -m uvicorn app.api:app --host 127.0.0.1 --port 8000
```

Terminal 2:

```powershell
cd frontend
$env:VITE_API_URL = "http://127.0.0.1:8000"
npm run dev -- --host 127.0.0.1
```

Open the frontend URL printed by Vite. Check `http://127.0.0.1:8000/health` for API process health. This endpoint does not prove that SQL Server, LLM access, or authentication is healthy.

Application startup expects SQL Server connectivity and loads rules/catalog data. It does not create or migrate the SQL Server schema. See [Database schema](DATABASE_SCHEMA.md) for prerequisites. Do not run production migrations or data-load scripts as part of local setup.

## Run validation

From the project root:

```powershell
python -m pytest -q
```

From `frontend`:

```powershell
npm run lint
npm run build
```

Tests use local fixtures/fakes where configured and do not require production SQL access. A live connection check is a separate operation and should only be run against an approved development database.
