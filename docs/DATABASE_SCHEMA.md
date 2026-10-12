# Database schema and data contract

## Verification status

The application source establishes which SQL Server objects and columns its current query paths expect. The repository does **not** include a canonical create script for the complete base operational schema, and no live database was inspected for this documentation task. Treat the tables below as the code-supported minimum contract, not as a verified inventory of every deployed column, datatype, key, index, row count, or constraint.

Before connecting an environment, the database owner must provide a compatible schema and confirm actual key relationships and data types. Application startup does not create the base schema.

## Code-supported business objects

All operational query templates currently use the `dbo` schema by default; the query class also allows an empty schema for SQLite test fixtures.

| Object | Required columns observed in code | Purpose and expected grain |
|---|---|---|
| `Products` | `product_id`, `sku`, `product_name`, `brand`, `category`, `model`, `cost_price`, `is_active`; `subcategory_id` when subcategory support is installed | Product dimension; one row per product. Catalog loading filters to active products. |
| `Stores` | `store_id`, `store_name`, `city`, `region` | Store/location dimension; one row per store. |
| `Inventory` | `product_id`, `store_id`, `quantity_on_hand`, `reorder_level`, `max_level`, `as_of_date` | Current operational stock position; expected product-store grain. Actual uniqueness and effective-date behavior need DBA confirmation. |
| `Sales` | `sale_date`, `product_id`, `store_id`, `quantity`, `revenue`, `invoice_no` | Sales fact rows; source grain is a sales record/line, aggregated by selected product/store/date window. Exact key and invoice semantics need confirmation. |
| `SupplierProducts` | `product_id`, `lead_time_days`, `is_primary` | Product-supplier association used for primary supplier lead-time selection. The supplier key/table definition and uniqueness rules require DBA confirmation. |
| `InventorySnapshotWeekly` | `product_id`, `store_id`, `snapshot_date`, `quantity_on_hand` | Historical inventory snapshot, expected product-store-snapshot-date grain. |
| `BusinessRules` | `rule_key`, `rule_value` | Global V1 thresholds loaded into the calculation `Rules` object. The code does not read scoped overrides. |
| `ProductSubcategories` (optional additive feature) | `subcategory_id`, `category`, `subcategory_name` | Category-scoped subcategory lookup. See migration below. |
| `vw_InventoryPosition` (legacy repository helper) | Shape is consumed via `SELECT *`; expected fields are documented in `app/calc/metrics.py` | Legacy helper for inventory positions. Production request dispatch currently uses query templates; the view's complete definition is not in this repository. |

### Joins and keys assumed by current queries

Query templates join:

- `Inventory.product_id` to `Products.product_id`.
- `Inventory.store_id` to `Stores.store_id`.
- `Sales.product_id` to `Products.product_id` and `Sales.store_id` to `Stores.store_id`.
- Snapshot product/store IDs to their dimension IDs.
- Supplier associations by `product_id`.
- Optional `Products.subcategory_id` plus `Products.category` to `ProductSubcategories.subcategory_id` plus `category`.

These are application join assumptions. Verify the deployed foreign keys before production use; do not infer that every assumption is enforced by a database constraint.

### Authorization objects

The SQL authentication service expects:

| Object | Columns directly used |
|---|---|
| `AppRoles` | `role_id`, `role_code`, `role_name` (role name used by the migration; authentication joins role code). |
| `AppUsers` | `user_id`, `username`, `display_name`, `password_hash`, `is_active`, `role_id`. |
| `UserRegionAccess` | `user_id`, `region`. |
| `UserBrandAccess` | `user_id`, `brand`. |
| `AppSessions` | `session_id`, `token_hash`, `user_id`, `expires_at`, `revoked_at`. |

The authorization migration/provisioning SQL is not included in the tracked repository. It is not a universal base-schema setup script. Obtain an approved version from the database owner and review/execute it only under the organization's change process against an approved non-production target first.

### Local conversation history

`app/history.py` creates a separate local SQLite database with:

- `users(user_id, name, persona)`
- `conversations(conversation_id, user_id, title, created_at, updated_at)`
- `messages(message_id, conversation_id, role, content, metadata, created_at)`

SQLite conversation data may contain user-entered questions and answer evidence. It is local application state, not disposable source material; keep it out of commits and public artifacts.

## Subcategory migration

Subcategory-enabled databases require the lookup and product link described above. The complete subcategory migration DDL and product mappings are not included in the tracked repository. Obtain a reviewed migration from the database owner; verify its preconditions against the target schema and do not treat local, untracked scripts as canonical release artifacts.

This feature is additive but not required to infer all base-schema requirements. Do not run any migration or load script automatically while setting up a developer environment. A DBA must inspect the exact target schema, review mappings, back up as required, and approve an execution plan.

## Objects not established by code

- No `PurchaseOrders` query or supported purchase-order capability is present in the inspected runtime query path.
- Full base table DDL, complete views, exact primary/foreign keys, indexes, nullability, precision/scale, constraints, and data retention rules are not included.
- No live catalog, production server, or row counts are reported here.

## Database onboarding checklist

1. Obtain approved development connection details from the database owner through secret management.
2. Confirm the database contains the code-supported operational and auth objects.
3. Compare actual column names/types and keys with this contract.
4. Verify query permissions are least-privilege; application analytical reads should not require DDL or business-data writes.
5. Provision authentication users/access rows through an approved process.
6. Confirm the installed ODBC Driver 18 and network/TLS policy.
7. Test against a non-production environment only.
