# Product Master Subcategory Migration Guide

**Status:** Prepared for review only. The migration has not been executed.

## Scope

This package adds optional product subcategory support. It does not change product IDs, SKUs, names, brands, categories, inventory quantities, sales, suppliers, purchase orders, snapshots, BusinessRules, or application code.

The checked-in schema defines `dbo.Products.product_id INT IDENTITY(1,1) PRIMARY KEY`, `category NVARCHAR(50) NOT NULL`, and a nullable `sku VARCHAR(20)` later made `NOT NULL` with unique index `UX_Products_sku` by the seed script. Existing fact tables reference `Products(product_id)`.

## Files and execution order

1. `PRODUCT_MASTER_MIGRATION_VALIDATION.sql` — read-only baseline checks. Save the result sets before migration.
2. `001_product_subcategories_schema.sql` — transactional, idempotent schema migration. Creates `ProductSubcategories`, adds nullable `Products.subcategory_id`, adds the category-constrained FK, and adds a filtered index.
3. `002_product_subcategories_load_approved.sql` — transactional assignment template. It contains no active rows and therefore performs no data change until explicitly approved rows are added.
4. Run `PRODUCT_MASTER_MIGRATION_VALIDATION.sql` again and compare counts, SHA-256 product fingerprints, fact-table aggregates, columns, indexes, foreign keys, identifiers, and empty violation result sets. The validator is guarded and runs successfully in either pre- or post-migration state.

## Classification loading rules

The CSV confidence and review status are not approval. Add only explicitly approved rows to `@Approved` in Script B, retaining the exact product ID, SKU, and existing category. Unclassified products remain `NULL`. The loader validates identity/category before updating and relies on the composite FK to prevent a cross-category reference.

## Prerequisites

- Confirm the target database and SQL Server version.
- Obtain a backup/change window and deployment owner approval.
- Run the read-only validation script and retain its outputs.
- Test both migration and rollback on a disposable database copy.
- Review and approve the subcategory mapping separately.

## Rollback

`001_product_subcategories_rollback.sql` refuses to run when assignments exist unless `@AllowAssignmentRemoval` is explicitly set to `1`. It also refuses to drop a populated lookup table, or a lookup table with additional foreign-key, SQL-object, or index dependencies, unless the corresponding explicit authorization flags are enabled. All guards run before any assignment update; a failure rolls back the transaction. It never deletes dependent inventory/sales/supplier/PO/snapshot data. Do not enable any removal flag without an explicit rollback decision, backup, and dependency review.

## Expected results

- Product and fact-table row counts remain unchanged.
- Existing product IDs and SKUs remain unchanged.
- Existing application projection (`product_id, sku, product_name, brand, category, model`) remains valid.
- Before any approved load, all `subcategory_id` values are `NULL`.
- Post-migration validation returns no category/FK violation rows.

## Risks and limitations

- SQL Server DDL compatibility and existing drift must be checked on the target; the schema script fails rather than silently adapting an incompatible table.
- The schema script validates the existing lookup's exact columns, SQL types/nullability/identity, key columns, and expected composite FK mapping; incompatible drift fails safely.
- The validation script emits result sets for external baseline comparison; it does not persist a baseline table. SHA-256 fingerprints and aggregate totals must be captured and compared by the operator.
- `HASHBYTES('SHA2_256', ...)` requires a supported SQL Server version and the same deterministic row ordering; confirm compatibility in the disposable test copy.
- Script B is intentionally empty and cannot classify products automatically.
- A rollback after approved assignments is destructive to those assignments, which is why it is blocked by default. Populated lookup rows and additional dependencies are also protected by default.

## Execution status

No migration script in this package has been executed against SQL Server. No database-changing SQL was run while preparing this package.
