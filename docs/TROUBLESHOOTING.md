# Troubleshooting

## `ModuleNotFoundError`, missing Python package, or wrong interpreter

- Activate the project `.venv` from the project root.
- Confirm the shell prompt reflects the environment, then reinstall only if needed with `pip install -r requirements.txt`.
- Run tests with `python -m pytest -q` from the project root.

## API does not start or `/health` is unavailable

- Start Uvicorn from the project root: `python -m uvicorn app.api:app --host 127.0.0.1 --port 8000`.
- Check that port 8000 is not already in use and that the expected virtual environment is active.
- `/health` reports process health only; it does not verify SQL Server or Groq availability.

## Frontend cannot reach FastAPI

- Start Vite from `frontend/`, not the repository root.
- Set `VITE_API_URL` to the same API host/port. For example, local development can use `http://127.0.0.1:8000`.
- Keep hostnames consistent. Browser cookie behavior differs between `localhost` and `127.0.0.1`.
- The current API CORS configuration permits local origins on port 5173 only. A different origin requires an explicitly reviewed server configuration change.
- Restart Vite after changing environment variables.

## SQL Server connection or ODBC errors

- Confirm the approved development server/database values are supplied in the private `.env`.
- Check ODBC Driver 18 installation, firewall/network access, SQL Server availability, and the selected SQL/Windows authentication mode.
- Confirm encryption and certificate settings satisfy workstation policy.
- Do not print the ODBC connection string, `.env`, password, or token in logs or support requests.
- Confirm the base schema and read permissions with the database owner. The repository does not provide complete core-table DDL.

## `/chat` returns a database or query error

- Check backend logs for the failing operation, removing credentials and customer/business row data before sharing logs.
- Confirm the table/column contract in `DATABASE_SCHEMA.md` against the approved development database.
- Confirm `BusinessRules` includes valid numeric rule values (or understand that code defaults apply to missing keys).
- Compare date periods with data availability. “Latest available” is constrained by source dates; it is not necessarily today's date.

## Login rejected or authentication unavailable

- Authentication requires the SQL authorization schema, an active user row, a valid password hash, and appropriate access-scope rows.
- Do not insert raw passwords or edit authentication settings to bypass a rejection.
- Ask the database owner to provision or verify the development account using the approved process.
- In local HTTP development, cookie security can be disabled only through server-side local-development configuration. HTTPS deployments must retain Secure cookies.

## LLM integration unavailable

- Confirm `GROQ_API_KEY` is set privately and the configured model is supported for the account.
- Check network access and service availability.
- Deterministic calculation tests do not need a live LLM key.
- Never include API keys or full environment dumps in a bug report.

## Conversation history seems stale or missing

- History is held in a local SQLite database separate from SQL Server. Confirm the process is using the intended working directory and history file.
- Do not delete the history database unless the owner explicitly confirms it is disposable; it may contain user questions and answer evidence.
- Pending clarification state may be restored from the most recent assistant message metadata for the same conversation.

## Frontend lint/build failures

From `frontend/`, run:

```powershell
npm ci
npm run lint
npm run build
```

Do not commit `node_modules/` or generated `dist/` output unless the project's release process specifically requires it.

