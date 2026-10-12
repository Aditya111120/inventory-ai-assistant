# InventoryAI Phase 1 Automated Acceptance — Closure Report

**Classification: BLOCKED**

This is an evidence-based closure report. Unit tests and build results do not
substitute for the required eight-account live acceptance matrix.

## Environment

| Check | Result |
|---|---|
| FastAPI `http://127.0.0.1:8000/health` | Reachable, HTTP 200 |
| React `http://127.0.0.1:5173` | Reachable, HTTP 200 |
| Browser automation | Visible Edge smoke test passed; eight-account browser matrix blocked by missing credentials |
| SQL Server `SWGL056\\SQLEXPRESS01` | BLOCKED — ODBC Driver 18 Windows-auth connection failed with 08001/security-package error |
| Target database identity | Not verified because SQL connection failed |
| Eight test credentials | BLOCKED — no `PHASE1_PASSWORD_*` variables were configured |

The existing runner was executed against the authorized target with
`PHASE1_ALLOW_PRODUCTION=yes`. It correctly returned `BLOCKED` and produced
`reports/phase1_acceptance_report.json` and `.md`; zero live account cases were
marked passed.

Visible browser verification was executed with Playwright using the installed
Edge executable. Edge opened the React login screen at
`http://127.0.0.1:5173`, and `reports/phase1_browser_smoke.png` was captured.
The account browser matrix did not start because all eight process-environment
passwords were absent.

## Code and automated checks

- Backend regression suite: **325 passed, 2 warnings**.
- Frontend lint: completed with the existing React `set-state-in-effect`
  warning at `src/App.jsx:659`.
- Frontend production build: **passed**.
- Cookie regression tests: local HTTP, HTTPS, non-loopback protection, login,
  `/auth/me`, and logout continuity all pass.

## Acceptance matrix status

The following mandatory live checks remain **BLOCKED**, not passed:

- SM-01 through SM-04 region authorization and denied-location checks.
- BM-01/BM-02 assigned-brand, mixed-brand, and unauthorized-brand checks.
- Planner and Leadership full-scope checks.
- HP → Chennai → Anna Nagar → Latest Inventory filter-preservation flow.
- Independent SQL aggregate, identity, table, Trust Panel, and row checks.
- Continue Exploring click-through validation for all accounts.
- Cross-user clarification-token and browser-session checks.
- Role-aware browser welcome-screen and logout checks.

## Smallest unblock action

Provide the eight test passwords through the documented process environment
(`PHASE1_PASSWORD_SM_01` … `PHASE1_PASSWORD_LD_01`, or the credentials JSON)
and make the approved SQL Server target reachable with the configured
read-only connection (`Encrypt=optional;TrustServerCertificate=yes` for this
local setup). A browser-capable automation surface is also required for the
browser portion; otherwise those checks remain blocked.

After those prerequisites are available, rerun:

```powershell
$env:PHASE1_ALLOW_PRODUCTION = 'yes'
python scripts/phase1_acceptance_runner.py `
  --api-url http://127.0.0.1:8000 `
  --expected-server 'SWGL056\SQLEXPRESS01' `
  --expected-database InventoryAI `
  --report reports/phase1_acceptance_report.json
```

No `.env` file was read. No SQL Server data, assignments, schema, credentials,
or Business Rules were modified.
