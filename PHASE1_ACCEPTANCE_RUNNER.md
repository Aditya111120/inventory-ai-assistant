# Phase 1 acceptance runner

`python scripts/phase1_acceptance_runner.py` runs the real FastAPI endpoints
for SM-01 through SM-04, BM-01, BM-02, PL-01, and LD-01. Each account gets a
separate HTTP cookie jar and conversation ID. The runner obtains role scopes
from SQL Server using SELECT-only queries, checks server/database identity, and
validates returned rows against those scopes. It also exercises Continue
Exploring, logout/revocation, and the HP → Chennai → Anna Nagar → Latest
Inventory path when the catalog contains that store.

The runner never reads `.env`, prints credentials, or executes SQL writes. It
does not start FastAPI; the operator must point `--api-url` at the already
running application. It writes a JSON report and a same-named Markdown report.
No runner-owned SQLite history file is created.

## Secure local invocation

Supply credentials through the process environment (never command-line
arguments or source files):

```powershell
$env:PHASE1_PASSWORD_SM_01 = $env:TEST_SM_01_PASSWORD
# ...set PHASE1_PASSWORD_SM_02 through PHASE1_PASSWORD_LD_01...
python scripts/phase1_acceptance_runner.py `
  --api-url http://127.0.0.1:8000 `
  --expected-server 'SWGL056\SQLEXPRESS01' `
  --expected-database InventoryAI `
  --report reports/phase1_acceptance_report.json
```

The protected production database requires the separate process opt-in
`PHASE1_ALLOW_PRODUCTION=yes`; omitting it is a deliberate safety block. For
the disposable database use `--expected-database InventoryAI_Test` and do not
set that opt-in. SQL authentication, where required, uses
`PHASE1_DB_AUTH=sql`, `PHASE1_DB_USER`, and `PHASE1_DB_PASSWORD`; SQL
authentication follows the documented `.env.example` default. Set
`PHASE1_DB_AUTH=windows` explicitly when Windows Authentication is intended.
The runner never reads `.env`. `PHASE1_DB_ENCRYPT` is configurable and
defaults to `optional` for this local environment, with
`PHASE1_DB_TRUST_SERVER_CERT=yes` as documented by the existing setup.

## Result interpretation

`PASS` means every executed account/case passed. `FAIL` means the infrastructure
was available but an assertion failed. `BLOCKED` means credentials, SQL Server,
API, identity, or another prerequisite was unavailable; blocked cases are not
treated as passed. Review both report files for the exact account, question,
expected behavior, actual behavior, and root cause.

The runner intentionally performs broad checks for all eight accounts and the
HP targeted flow. Additional data-driven question matrices can be enabled in a
future change after the local catalog and test credentials are confirmed; this
runner does not claim live validation when prerequisites are absent.

## Visible Edge browser layer

The approved Playwright dependency is installed in `frontend` and the existing
Edge executable is used directly; no Playwright-managed browser was downloaded.
Run the visible browser layer from the repository root:

```powershell
node frontend/scripts/phase1_browser_acceptance.mjs
```

It opens Edge with `headless: false`, verifies the login screen at the local
React URL, saves `reports/phase1_browser_smoke.png`, and then runs isolated
account login/example/logout checks when all eight `PHASE1_PASSWORD_*`
variables are present. Missing credentials produce `BLOCKED`, never a pass.
The browser script does not replace the API/SQL runner; both layers must pass
before Phase 1 can be classified as PASS.
