# InventoryAI Production Schema Migration — Recovery Plan

**Status:** Operator checklist for authorized change; execution evidence pending  
**Scope:** Authorized schema-only Product Master subcategory migration  
**Prepared:** 2026-10-09

## 1. Production identity and scope

| Item | Value | Status |
|---|---|---|
| SQL Server | `SWGL056\\SQLEXPRESS01` | Supplied target; final identity check required immediately before execution |
| Production database | `InventoryAI` | Protected target; exact `DB_NAME()`/`@@SERVERNAME` check required |
| Test database | `InventoryAI_Test` | Test migration/idempotency reported passed |
| Migration scope | Add `dbo.ProductSubcategories`, nullable `dbo.Products.subcategory_id`, category-constrained FK, and filtered index | Schema only |
| Classification loading | Not included | Must remain skipped |
| BusinessRules | No changes | Must remain unchanged |

No product classifications, BusinessRules, product attributes, inventory,
sales, suppliers, snapshots, purchase orders, or quantities may be changed.

## 2. Backup evidence

- Backup path: `C:\\Program Files\\Microsoft SQL Server\\MSSQL17.SQLEXPRESS01\\MSSQL\\Backup\\InventoryAI.bak`
- Backup verification: `RESTORE VERIFYONLY` previously passed (supplied fact)
- Backup checksums: Not enabled
- Backup freshness confirmation before execution: **PENDING**
- Backup owner/custodian: **PENDING**
- Backup timestamp and SQL Server version: **PENDING confirmation for this change window**

The operator must confirm that the backup is fresh enough for the production
change and that the file is readable by the SQL Server service account before
any schema-changing command is submitted.

## 2A. Final operator checklist — complete before execution

### Fresh backup

- [ ] Confirm the approved production backup procedure and change window.
- [ ] Create a new timestamped **full** backup of `InventoryAI`.
- [ ] Do not overwrite the existing `InventoryAI.bak`.
- [ ] Record the new backup absolute path: **PENDING**.
- [ ] Record backup completion timestamp: **PENDING**.
- [ ] Run `RESTORE VERIFYONLY` using the new backup path.
- [ ] Record verification result and whether checksums were enabled: **PENDING**.

### Accountability and window

- [ ] Responsible operator: **PENDING**.
- [ ] Recovery owner: **PENDING**.
- [ ] DBA: **PENDING**.
- [ ] Change approval/request ID: **PENDING**.
- [ ] Approved execution start/end window: **PENDING**.
- [ ] Application/concurrency communication completed: **PENDING**.

### Restore readiness

- [ ] Run `RESTORE FILELISTONLY` against the fresh backup.
- [ ] Record logical data-file name and reviewed production `.mdf` destination: **PENDING**.
- [ ] Record logical log-file name and reviewed production `.ldf` destination: **PENDING**.
- [ ] Confirm recovery procedure and restore command are reviewed by the DBA.
- [ ] Confirm recovery targets only `InventoryAI`; never `InventoryAI_Test` or another database.

### Migration readiness

- [ ] Confirm production preflight identity, baseline counts, fingerprints,
  aggregates, schema, and FK checks are current for the execution window.
- [ ] Confirm `InventoryAI_Test` migration and idempotency validation evidence
  is attached to the change record.
- [ ] Confirm only `001_product_subcategories_schema.sql` will execute.
- [ ] Confirm `002_product_subcategories_load_approved.sql` will not execute.
- [ ] Confirm rollback will not execute automatically and has no authorization.
- [ ] Confirm no product classifications or BusinessRules changes are included.

Checklist completion status: **PENDING**. Do not proceed while any required
item lacks evidence or approval.

## 3. Required pre-execution checks

The change owner must record:

- Fresh backup confirmation and timestamp.
- Recovery owner, DBA, change ID, and approval identities.
- Production preflight report showing exact server/database identity, baseline
  counts, fingerprints, aggregates, schema, and zero integrity violations.
- Test migration and idempotency report for `InventoryAI_Test`.
- Application/change-window confirmation that the additive DDL is safe.
- Explicit confirmation that no classification load or rollback is authorized.

Unknown values remain **PENDING**; this document does not grant approval by
itself.

## 4. Recovery procedure for InventoryAI

Recovery is not automatic and requires separate incident/change authorization.

1. Stop the migration and preserve runner output and validation reports.
2. Confirm the incident owner authorizes recovery of `InventoryAI`.
3. Run `RESTORE VERIFYONLY` against the recorded backup path if freshness or
   readability is in doubt.
4. Run `RESTORE FILELISTONLY` and record logical data/log names.
5. Review explicit `MOVE` destinations for the production data and log files;
   do not invent paths or overwrite another database.
6. Obtain DBA and change-owner approval for the exact restore command, target,
   file mappings, recovery mode, and outage window.
7. Restore only `InventoryAI` using the approved command and backup. Do not
   restore over `InventoryAI_Test` or any other database.
8. Verify `@@SERVERNAME`, `DB_NAME()`, database state, table counts, product
   fingerprints, fact aggregates, and FK integrity after recovery.

The executable restore command and reviewed file mappings are **PENDING**;
they must be completed by the DBA for the actual host. No restore operation is
authorized by this document.

## 5. Migration validation requirements

Before migration, save the production preflight result. After the schema-only
migration, run the validation script again and compare:

- Product count, IDs/SKUs, and product/catalog fingerprints.
- Inventory, Sales, SupplierProducts, PurchaseOrders, and
  InventorySnapshotWeekly counts and aggregates.
- BusinessRules count and values.
- Existing FK integrity and zero orphan rows.
- New lookup table, nullable column, composite FK, index, and category
  consistency.
- Existing application projection compatibility.

Any mismatch is a failure. Stop for investigation; do not automatically
rollback.

## 6. Failure handling and authorization

- Stop on wrong server/database identity, stale/unreadable backup, failed
  preflight, SQL error, lock/timeout, unexpected schema drift, or validation
  mismatch.
- Do not retry against another database.
- Do not run the classification loader.
- Do not execute rollback automatically.
- Recovery owner and DBA must explicitly authorize any restore or rollback
  after reviewing evidence and impact.
- Preserve reports and SQL Server error details without recording credentials.

## 7. Ownership and approval record

- Recovery owner: **PENDING**
- DBA/operator: **PENDING**
- Change/request ID: **PENDING**
- Backup freshness approver: **PENDING**
- Production migration approval: User authorization supplied; formal change
  record **PENDING**
- Recovery approval: **PENDING**
- Rollback authorization: **Not granted**

## 8. Remaining blockers

- Fresh timestamped backup path, timestamp, and verification output: **PENDING**.
- Recovery owner, responsible operator, DBA, change ID, and execution window: **PENDING**.
- Reviewed logical-to-physical restore mappings and recovery command: **PENDING**.
- Current production preflight report and baseline attachment: **PENDING confirmation**.
- Formal change-record approval: **PENDING**.

## Review conclusion

This plan records the supplied production target, prior backup evidence, and
schema-only scope. It provides the final operator checklist but does not invent
fresh backup facts or approval identities. It does not authorize a restore or
rollback, and execution remains conditional on completing every checklist item.
No `.env` file was read, and no SQL Server operation was performed while
preparing it.
