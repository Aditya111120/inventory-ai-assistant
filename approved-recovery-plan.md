# InventoryAI Product Master Migration — Recovery Plan

**Status:** Draft for review only — not approved automatically  
**Prepared:** 2026-10-09  
**Scope:** Disposable integration test for the Product Master subcategory schema migration

## 1. Approved scope and protected database

| Item | Value | Verification status |
|---|---|---|
| SQL Server instance | `SWGL056\\SQLEXPRESS01` | Supplied; connectivity verification failed in this environment |
| Approved test database | `InventoryAI_Test` | Supplied; online status could not be verified from this environment |
| Protected database | `InventoryAI` | Must not be connected to for this test or overwritten |
| Approved operation | Restore/recover only `InventoryAI_Test` if separately authorized; run schema-only migration test there | Pending formal recovery approval |

No database restore, overwrite, migration, classification load, or rollback is authorized by this document alone.

### Test database physical files (operator-provided)

- `InventoryAI_Test.mdf`
- `InventoryAI_Test_log.ldf`

Absolute file paths and SQL Server file metadata remain **PENDING**. Separate
physical files do not by themselves establish application isolation.

## 2. Backup source

- Backup file name: `InventoryAI.bak`
- Verified absolute path: `C:\\Program Files\\Microsoft SQL Server\\MSSQL17.SQLEXPRESS01\\MSSQL\\Backup\\InventoryAI.bak`
- Backup completed: `2026-10-09 10:10:57`
- Backup verification: `RESTORE VERIFYONLY` passed (operator-provided verification)
- Backup checksums: Not enabled
- Backup owner/custodian: **PENDING**
- SQL Server version at backup: **PENDING**

The backup facts above are recorded from the operator's verification. This review does not rerun `RESTORE VERIFYONLY` and does not restore or modify any database.

## 3. Backup verification procedure

After the absolute path and operator approval are recorded, an authorized SQL Server operator should perform these read-only checks against the backup file:

```sql
RESTORE VERIFYONLY
FROM DISK = N'<VERIFIED_ABSOLUTE_PATH_TO_InventoryAI.bak>'
WITH CHECKSUM;
```

If verification succeeds and the SQL Server version supports it, record the output and media-set identity in the change record. Do not run `RESTORE DATABASE` as part of verification. The command has not been executed by this review.

## 4. Recovery prerequisites and permissions

Before any restore is considered, the recovery owner must confirm:

- A verified absolute backup path and sufficient disk space for data and log files.
- A disposable target policy that prohibits overwrite of `InventoryAI`.
- SQL Server permission to run `RESTORE VERIFYONLY` and, if separately approved, `RESTORE DATABASE`.
- Permission to query database state, metadata, foreign keys, and row counts.
- An approved maintenance window and application-disconnection plan for `InventoryAI_Test`.
- A tested destination file mapping that does not point to the original `InventoryAI` data/log files.
- A named recovery owner and escalation contact.

Recovery owner, permissions, disk-capacity evidence, and application-disconnection evidence remain **PENDING** unless explicitly completed in the change record.

## 5. Restore procedure (only after explicit recovery authorization)

Do not execute these steps during migration preparation. The recovery owner must first approve the exact target and file paths.

1. Confirm the connected server is exactly `SWGL056\\SQLEXPRESS01`.
2. Confirm the source backup is the verified `InventoryAI.bak` at the recorded absolute path.
3. Confirm the destination database name is exactly `InventoryAI_Test`.
4. Confirm `InventoryAI` is not the target and will not be dropped, replaced, or put in `SINGLE_USER` mode.
5. If `InventoryAI_Test` already exists, obtain separate written authorization for the approved test-database recovery action; do not overwrite it implicitly.
6. Inspect logical file names with `RESTORE FILELISTONLY` and prepare reviewed `MOVE` destinations for the test database.
7. Execute a separately approved `RESTORE DATABASE ... WITH MOVE ..., RECOVERY` operation.
8. Record restore output, time, operator, database state, and resulting file locations.

No restore command is included here with executable paths because the required absolute backup and destination paths are still pending.

## 6. Post-restore identity and integrity verification

Run read-only checks after an authorized restore and before migration:

```sql
SELECT @@SERVERNAME AS server_name,
       DB_NAME() AS current_database,
       DATABASEPROPERTYEX(N'InventoryAI_Test', N'Status') AS database_status;

SELECT name, state_desc, recovery_model_desc
FROM sys.databases
WHERE name IN (N'InventoryAI_Test', N'InventoryAI');

USE InventoryAI_Test;
SELECT name
FROM sys.tables
WHERE schema_id = SCHEMA_ID(N'dbo')
  AND name IN (N'Products', N'Inventory', N'Sales', N'Stores',
               N'SupplierProducts', N'PurchaseOrders', N'BusinessRules',
               N'InventorySnapshotWeekly')
ORDER BY name;
```

Record exact table counts and foreign-key integrity using
`PRODUCT_MASTER_MIGRATION_VALIDATION.sql`. Expected counts, fingerprints,
and integrity results are **PENDING** until the read-only baseline is captured.

The verification must demonstrate that the session is using
`InventoryAI_Test`, not `InventoryAI`, and that the required tables exist.

## 7. Failure handling and escalation

- Stop immediately on a missing/invalid backup, failed checksum verification,
  wrong server/database identity, missing table, insufficient permission, file
  collision, or unexpected integrity failure.
- Do not retry with a different database name or overwrite `InventoryAI`.
- Do not automatically drop, restore, or roll back any database.
- Preserve SQL Server error output and the change record, without recording
  credentials or secrets.
- Escalate to the recovery owner and database administrator for target, backup,
  permission, or file-path decisions.

## 8. Explicit protection of the original database

`InventoryAI` is protected. It must never be overwritten, restored over,
dropped, renamed, placed in single-user mode, or used as the migration-test
target. Any command that does not explicitly target `InventoryAI_Test` must be
rejected before execution.

## 9. Ownership and approval

- Recovery owner: **PENDING — name and contact required**
- Database administrator: **PENDING**
- Change/request identifier: **PENDING**
- Backup verification approver: **PENDING**
- Recovery approval status: **PENDING — draft only**
- Rollback authorization: **Not granted**

### Recovery decision procedure

1. Recovery owner records the verified absolute backup path and verification output.
2. DBA confirms server, destination database, file mappings, permissions, and disk capacity.
3. Change approver explicitly authorizes restore of `InventoryAI_Test` only, if needed.
4. Operator records the approval identifier and executes only the reviewed restore command.
5. Operator performs identity/table/integrity checks and records pass/fail results.
6. Only after a passing recovery check may the separate migration-runner authorization be used.

## Review conclusion

This document is a recovery-plan template for review. It does not establish
approval, does not verify the backup, and does not authorize a restore or
migration. No `.env` file was read, and no SQL Server operation was performed
while preparing it.
