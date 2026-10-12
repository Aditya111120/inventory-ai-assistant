# Automated Product Master Migration Runner

Runner: `scripts/migration_runner.py`

The runner reads `DB_SERVER`, `DB_NAME`, `DB_AUTH`, `DB_DRIVER`, `DB_ENCRYPT`,
`DB_TRUST_SERVER_CERT`, and (for SQL authentication) `DB_USER` and
`DB_PASSWORD` from the process environment only. It does not import
`app.config`, load dotenv files, inspect `.env`, or print credentials.
`DB_ENCRYPT` accepts `yes` (secure default), `no`, or `optional`; use
`optional` only when the verified local SQL Server connection requires it.

## Dry run (default)

From `inventory_ai`:

```powershell
$env:DB_SERVER = 'server\instance'
$env:DB_NAME = 'InventoryAI'
$env:DB_AUTH = 'windows'
python scripts/migration_runner.py
```

Dry-run mode validates the target configuration and migration package paths,
prints a masked target description, and opens no database connection. It never
executes validation or migration SQL.

## Read-only preflight

For the approved local test database, configure the process environment and
run:

```powershell
$env:DB_SERVER = 'SWGL056\SQLEXPRESS01'
$env:DB_NAME = 'InventoryAI_Test'
$env:DB_AUTH = 'windows'
$env:DB_DRIVER = 'ODBC Driver 18 for SQL Server'
$env:DB_ENCRYPT = 'optional'
$env:DB_TRUST_SERVER_CERT = 'yes'
python scripts/migration_runner.py --preflight `
  --expected-server 'SWGL056\SQLEXPRESS01' `
  --expected-database 'InventoryAI_Test' `
  --report .\PRODUCT_MASTER_TEST_MIGRATION_PREFLIGHT_REPORT.json
```

Preflight validates the SQL file is read-only, verifies database/server
identity, runs the validation result sets, rejects integrity failures, and
writes a JSON report. It never invokes the schema, classification, or rollback
scripts.

Production uses a separate `--production-preflight` mode restricted to the
exact `InventoryAI` target. Production apply additionally requires
`--production-authorize`; this authorization cannot be used for
`InventoryAI_Test`, and test preflight cannot authorize production.

## Apply

Create or obtain a non-empty recovery-plan document that identifies the backup,
restore point, owner, and recovery test. Then run:

```powershell
python scripts/migration_runner.py --apply --recovery-plan .\approved-recovery-plan.md `
  --expected-server 'server\instance' --expected-database InventoryAI `
  --report .\migration-reports\product-subcategories.json
```

The runner performs pre-validation, executes the transactional schema script,
skips the empty classification loader, performs post-validation, compares
counts/fingerprints/aggregates/integrity result sets, and writes a JSON report.
It is safe to rerun after a successful schema migration because the schema
script is idempotent and drift-checked.

Both expected-target arguments are mandatory in apply mode. The runner refuses
to apply if either is omitted or differs from the process environment. It also
checks `DB_NAME()` and `@@SERVERNAME` returned by the pre-validation query
before submitting any schema SQL.

Migration scripts are submitted as single pyodbc batches. If a referenced SQL
file contains a standalone `GO` batch separator, the runner refuses execution
instead of attempting an unsafe partial split.

## Classification authorization

Classification loading requires a separate `--authorize-classifications` flag.
Even with that flag, the runner skips Script B unless it detects active
approved rows in the loader. The checked-in loader intentionally contains no
active assignments. Review and edit Script B only after business approval.

## Failure and recovery behavior

- Missing configuration, recovery plan, package files, incompatible schema, or
  SQL errors stop execution and return a non-zero exit code.
- Post-validation mismatches stop with exit code 2 and a report; rollback is
  never automatic. Investigate the report and use the separately reviewed
  rollback script only under the documented authorization safeguards.
- Credentials are never included in the report or console output.
- Run the runner first against a disposable SQL Server copy, verify restore,
  and compare the saved pre/post result sets before production approval.

## Static tests

```powershell
python -m pytest tests/test_migration_runner.py -q
python -m py_compile scripts/migration_runner.py
```

No live database connection or database-changing SQL is performed while
creating or statically testing this runner.
