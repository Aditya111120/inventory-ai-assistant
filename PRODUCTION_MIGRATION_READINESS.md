# InventoryAI Production Migration Readiness

This is an instruction and checklist document. No production connection or
migration is authorized by this file.

## Production preflight

Use process-environment configuration only; never load backend `.env`:

```powershell
$env:DB_SERVER = 'SWGL056\SQLEXPRESS01'
$env:DB_NAME = 'InventoryAI'
$env:DB_AUTH = 'windows'
$env:DB_DRIVER = 'ODBC Driver 18 for SQL Server'
$env:DB_ENCRYPT = 'yes'
$env:DB_TRUST_SERVER_CERT = 'yes'

python scripts/migration_runner.py --production-preflight `
  --expected-server 'SWGL056\SQLEXPRESS01' `
  --expected-database 'InventoryAI' `
  --report .\production-preflight-report.json
```

The command is read-only. It verifies server/database identity, runs the
existing validation SQL, captures counts, fingerprints, aggregates, schema,
and FK integrity, and writes a production-specific report. It must pass with
zero integrity violations before any apply command is considered.

## Required approvals and recovery evidence

- Fresh production baseline captured immediately before migration.
- Fresh verified backup and tested recovery procedure for `InventoryAI`.
- Non-empty approved recovery-plan file identifying owner, change ID, backup,
  restore test, and escalation contact.
- DBA/change-owner approval for the exact server and database.
- Confirmation that the application change window and concurrency policy allow
  the additive DDL.
- No classification approval: Script B remains skipped.

## Production migration command

Only after the production preflight and recovery approvals pass:

```powershell
python scripts/migration_runner.py --apply --production-authorize `
  --expected-server 'SWGL056\SQLEXPRESS01' `
  --expected-database 'InventoryAI' `
  --recovery-plan .\approved-production-recovery-plan.md `
  --report .\production-migration-report.json
```

`--production-authorize` is separate from test authorization. It is rejected
for `InventoryAI_Test`; test preflight cannot authorize production. The runner
does not automatically run rollback or classification loading.

## Post-migration checklist

- Product IDs/SKUs and catalog fingerprint unchanged.
- Products, Inventory, Sales, SupplierProducts, PurchaseOrders,
  InventorySnapshotWeekly, and BusinessRules counts/aggregates unchanged.
- `ProductSubcategories`, nullable `Products.subcategory_id`, composite FK,
  and filtered index exist and match the reviewed schema.
- FK orphan and category-consistency checks return zero rows.
- Existing application projection remains valid.
- Rerun the production preflight/validation and retain both reports.
- If any mismatch occurs, stop and investigate; do not auto-rollback.

## Remaining blockers

Production execution remains blocked until the fresh baseline, recovery plan,
approval identities, backup evidence, and successful production preflight are
recorded. The migration SQL is unchanged by this production guard.
