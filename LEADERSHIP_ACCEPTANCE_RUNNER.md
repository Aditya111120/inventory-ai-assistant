# LD-01 acceptance runner

`scripts/leadership_acceptance_runner.py` runs a dedicated authenticated matrix for LD-01 only. It validates SQL Server identity and `BusinessRules`, authenticates through the existing API, executes API conversations, and uses SELECT-only SQL references. Seven excluded-account login checks are reported in a separate section when credentials are available. No credential values are printed or written to the report.

The matrix contains 18 cases: inventory totals and value; category and brand rankings; store revenue; sales totals by units, category, brand, and city; V1 stockout risk; zero stock; weekly historical inventory; category comparison; a full-calendar-month sales comparison using the two latest available sales months; what-if; executive summary; metric clarification; and context isolation. Periods and the what-if product/store are selected from live SQL data rather than hardcoded. If fewer than two sales months or no suitable product-store row exists, the relevant case is marked BLOCKED.

## Local PowerShell

Start the existing FastAPI app first. Set only process-scoped credentials:

```powershell
$securePassword = Read-Host 'LD-01 password' -AsSecureString
$env:PHASE1_PASSWORD_LD_01 = [System.Net.NetworkCredential]::new('', $securePassword).Password
$env:PHASE1_DB_AUTH = 'sql'
$env:PHASE1_DB_USER = Read-Host 'SQL Server read-only user'
$secureSqlPassword = Read-Host 'SQL Server password' -AsSecureString
$env:PHASE1_DB_PASSWORD = [System.Net.NetworkCredential]::new('', $secureSqlPassword).Password
$env:PHASE1_DB_ENCRYPT = 'optional'
$env:PHASE1_DB_TRUST_SERVER_CERT = 'yes'

python scripts/leadership_acceptance_runner.py `
  --api-url http://127.0.0.1:8000 `
  --expected-server 'SWGL056\SQLEXPRESS01' `
  --expected-database InventoryAI_Test `
  --report reports/ld01_acceptance_report.json

Remove-Item Env:PHASE1_PASSWORD_LD_01, Env:PHASE1_DB_USER, Env:PHASE1_DB_PASSWORD -ErrorAction SilentlyContinue
```

For the protected `InventoryAI` database, the runner additionally requires the explicit process opt-in `$env:PHASE1_ALLOW_PRODUCTION = 'yes'`. Set it only for a specifically approved read-only acceptance window. The runner rejects a mismatched server/database identity before any API matrix cases run. SQL authentication is read-only by convention and the runner issues only SELECT statements; use a SQL principal that has SELECT-only access.

To perform the excluded-account negative login checks, supply `PHASE1_PASSWORD_SM_01` through `PHASE1_PASSWORD_PL_01` as process variables as well. Each is tested separately. Missing credentials remain BLOCKED in the separate report section and never count as a pass.

Reports contain one PASS/FAIL/BLOCKED record per case and root-cause text. Infrastructure or credential failures block the matrix rather than producing a pass. Do not share reports containing internal catalog metrics outside the authorized team.
