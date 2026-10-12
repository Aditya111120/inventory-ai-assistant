# Inventory AI Assistant — Phase 1 Data Audit

**Audit date:** 2026-10-08  
**Scope:** Repository SQL scripts, current query/calculation code, and read-only
database availability check.  
**Restrictions followed:** The backend `.env` was not opened, read, inspected,
or executed. No database writes were run. Business rules and chatbot behavior
were not changed.

## Executive summary and verification boundary

The repository contains DDL and generation scripts under `../SQL Files/` for
nine SQL Server tables and two views. The scripts describe a synthetic retail
inventory dataset with 150 generated products, 20 stores, approximately 2,400
inventory positions, 120,000 generated sale lines, and 26 weekly snapshots per
generated inventory position. These are **script targets**, not verified live
row counts.

Live SQL Server access was unavailable during this audit: the MSSQL server
listing tool returned no registered servers. Accordingly:

- **No live SQL Server query succeeded in this audit.**
- Current database object existence, final live column types, live constraints,
  actual row counts, distributions, nulls, duplicates, and referential integrity
  remain **unverified**.
- Generated row counts and patterns below are derived from the checked-in SQL
  scripts only; do not treat them as evidence that those scripts ran
  successfully or that the live database still matches them.
- The report includes read-only SQL checks to run once a database connection
  has been made available. Query text is supplied for reproducibility; it was
  not executed against SQL Server here.

## 1. Database inventory

### 1.1 SQL Server tables defined by `../SQL Files/Creation.sql`

Data types below describe the DDL as written. `../SQL Files/Insertion &
Generation.sql` subsequently alters `Products.sku` and `Products.selling_price`
to `NOT NULL`, so the post-generation target differs from the initial DDL.
Constraints and live schema must be checked in the actual database.

| Table | Primary key | Foreign keys | Columns and DDL types | Intended grain / purpose | Live row count |
|---|---|---|---|---|---|
| `dbo.Products` | `product_id INT IDENTITY(1,1)` | None | `product_id INT`; `sku VARCHAR(20) NULL` (later altered `NOT NULL`, unique index); `product_name NVARCHAR(150) NOT NULL`; `brand NVARCHAR(50) NOT NULL`; `category NVARCHAR(50) NOT NULL`; `model NVARCHAR(50) NOT NULL`; `launch_date DATE NOT NULL`; `cost_price DECIMAL(12,2) NOT NULL`; `selling_price DECIMAL(12,2) NULL` (later altered `NOT NULL`); `is_active BIT NOT NULL DEFAULT 1` | One row per catalog product/model/SKU. Product dimension. | Unverified; see §1.4 for script target. |
| `dbo.Stores` | `store_id INT` | None | `store_id INT`; `store_name NVARCHAR(100)`; `city NVARCHAR(50)`; `region NVARCHAR(20)`; `store_format NVARCHAR(20)`; `opened_date DATE`; all `NOT NULL` | One row per store/location. Store dimension. | Unverified; see §1.4. |
| `dbo.Suppliers` | `supplier_id INT` | None | `supplier_id INT`; `supplier_name NVARCHAR(100)`; `supplier_type NVARCHAR(20)`; all `NOT NULL` | One row per supplier. Supplier dimension. | Unverified; see §1.4. |
| `dbo.SupplierProducts` | `(supplier_id, product_id)` | `supplier_id -> Suppliers.supplier_id`; `product_id -> Products.product_id` | `supplier_id INT NOT NULL`; `product_id INT NOT NULL`; `lead_time_days INT NOT NULL`; `is_primary BIT NOT NULL` | One supplier-product association, including lead time and a primary flag. Many-to-many bridge. | Unverified; see §1.4. |
| `dbo.Inventory` | `(product_id, store_id)` | `product_id -> Products.product_id`; `store_id -> Stores.store_id` | `product_id INT NOT NULL`; `store_id INT NOT NULL`; `quantity_on_hand INT NOT NULL`; `reorder_level INT NOT NULL`; `max_level INT NOT NULL`; `as_of_date DATE NOT NULL` | Current inventory position per product and store. | Unverified; see §1.4. |
| `dbo.InventorySnapshotWeekly` | `(snapshot_date, product_id, store_id)` | `product_id -> Products.product_id`; `store_id -> Stores.store_id` | `snapshot_date DATE NOT NULL`; `product_id INT NOT NULL`; `store_id INT NOT NULL`; `quantity_on_hand INT NOT NULL` | One historical product-store quantity per weekly snapshot date. | Unverified; see §1.4. |
| `dbo.Sales` | `sale_id BIGINT IDENTITY(1,1)` | `product_id -> Products.product_id`; `store_id -> Stores.store_id` | `sale_id BIGINT`; `invoice_no VARCHAR(30) NOT NULL`; `sale_date DATE NOT NULL`; `product_id INT NOT NULL`; `store_id INT NOT NULL`; `quantity INT NOT NULL`; `unit_price DECIMAL(12,2) NOT NULL`; `discount_pct DECIMAL(5,2) NOT NULL`; `revenue DECIMAL(14,2) NOT NULL`; `payment_mode NVARCHAR(20) NOT NULL`; `customer_segment NVARCHAR(20) NOT NULL` | One invoice line / product sale event per row; invoice number can occur on multiple rows. | Unverified; see §1.4. |
| `dbo.PurchaseOrders` | `po_id INT IDENTITY(1,1)` | `product_id -> Products.product_id`; `supplier_id -> Suppliers.supplier_id` | `po_id INT`; `product_id INT NOT NULL`; `supplier_id INT NOT NULL`; `quantity INT NOT NULL`; `order_date DATE NOT NULL`; `expected_delivery_date DATE NOT NULL`; `status NVARCHAR(20) NOT NULL` | One PO line per product/supplier/order record; no received date, actual quantity, or line-level receipt history is defined. | Unverified; see §1.4. |
| `dbo.BusinessRules` | `rule_key VARCHAR(60)` | None | `rule_key VARCHAR(60) NOT NULL`; `rule_value DECIMAL(10,2) NOT NULL`; `description NVARCHAR(200) NULL` | One row per named calculation threshold/configuration rule. | Unverified; six seed rows in §1.4. |

### 1.2 SQL Server views in `../SQL Files/Views Creation & Validation.sql`

| View | Exposed fields / joins | Grain / caveats | Live row count |
|---|---|---|---|
| `dbo.vw_SalesEnriched` | `Sales` fields including sale/invoice/date/quantity/price/discount/revenue/payment/customer segment joined to `Products` and `Stores`; exposes product and location labels. | Sale line grain if referenced dimension keys are unique. The joins are inner joins. | Unverified. |
| `dbo.vw_InventoryPosition` | `Inventory` joined to `Products`, `Stores`, and primary `SupplierProducts`; includes product/store attributes, quantity/reorder/max, cost, computed inventory value, fixed-window `units_30d`, lead time, and inventory `as_of_date`. | Inventory position grain only if there is at most one `is_primary=1` supplier row per product. Its sales window is fixed to 30 days relative to max sales date, not parameterized. | Unverified. |

### 1.3 Additional user/persona/chat-history structures

No users, personas, or chat-history tables are defined in the SQL Server DDL
scripts. Application code has:

- `app/context.py`: in-memory `UserContext` with a `Persona` literal
  (`Planner`, `Brand Manager`, `Store Manager`, `Leadership`) and optional
  authorized store/brand/region ID tuples. This is a Python dataclass, not a
  database entity, and current API wiring does not apply authorization scope.
- `app/history.py`: a separate SQLite `ConversationStore`, creating:
  - `users(user_id TEXT PK, name TEXT, persona TEXT)`
  - `conversations(conversation_id TEXT PK, user_id TEXT, title, created_at, updated_at)`
  - `messages(message_id INTEGER PK AUTOINCREMENT, conversation_id, role, content, metadata, created_at)`
  - indexes on `(user_id, updated_at)` and `(conversation_id, message_id)`.
  These SQLite tables have no declared foreign-key constraints in the creation
  SQL. `ConversationStore` is not wired into the FastAPI orchestrator factory
  in `app/api.py`. SQLite row counts were not checked.

### 1.4 Script-intended row counts — not live-verified

Static derivation from `../SQL Files/Insertion & Generation.sql`:

| Object | Script-intended count / coverage | Basis and limitation |
|---|---:|---|
| Suppliers | 8 | Eight explicit seed rows. |
| Stores | 20 | Twenty explicit seed rows. |
| Products | 150 | 25 brand-category combinations × 6 model suffixes. |
| SupplierProducts | 180 | 150 primary links + one secondary link for each of 30 product IDs divisible by five. |
| Inventory | 2,400 | 150 × 20 combinations, excluding pairs where `(product_id + store_id) % 5 = 0` (600 excluded). |
| InventorySnapshotWeekly | 62,400 | 26 dates × the intended 2,400 inventory positions. |
| Sales | 120,000 | `TOP (120000)` generated rows, subject to source object cardinality and successful full execution. |
| PurchaseOrders | 1,500 | Ten generated orders for each of 150 primary supplier-product links. |
| BusinessRules | 6 | Six explicit rule rows. |
| `vw_InventoryPosition` | About 2,400 | View joins inventory to a primary supplier link and dimensions; exact count depends on live data and uniqueness. |
| `vw_SalesEnriched` | About 120,000 | Exact count depends on live sales and dimension integrity. |

Product combinations by brand/category are statically implied by the `VALUES`
list: Apple 24, Samsung 24, LG 18, Sony 18, Dell 6, HP 6, Lenovo 12, Asus 6,
OnePlus 12, Xiaomi 12, boAt 6, Canon 6. Category targets: Audio 36, Laptops
30, Smartphones 24, Televisions 24, Tablets 18, Cameras 12, Appliances 6.
These are generated targets, not live profile results.

## 2. Entity relationship overview

```text
Suppliers 1 ──< SupplierProducts >── 1 Products
Products  1 ──< Inventory >──────── 1 Stores
Products  1 ──< InventorySnapshotWeekly >── 1 Stores
Products  1 ──< Sales >──────────── 1 Stores
Products  1 ──< PurchaseOrders >─── 1 Suppliers

Products + Stores + primary SupplierProducts -> vw_InventoryPosition
Sales + Products + Stores                  -> vw_SalesEnriched
```

There is no explicit relationship from snapshots to a corresponding current
inventory row, only to the product and store dimensions. This allows historical
positions for a product-store pair that is not in the current `Inventory`
table, provided its product and store exist.

## 3. Data profiling and data quality

### 3.1 Live profile status

All live profiling measures requested (actual brand/category counts, duplicates,
nulls, distributions, coverage, actual date ranges, missing periods, sales
concentration, PO status, and rule values) are **unverified**. The database
server list was empty and no connection was made. No query results should be
inferred from sample fixtures or generation scripts.

The seed script does specify generated-data design:

- Products are generated from fixed brand/category combinations and six model
  codes each; names are assembled as `brand + category label + model code`.
- SKUs are assigned from product identity and a unique SKU index is created.
- Sales are randomly generated with `NEWID()` through 120,000 records. Dates
  are drawn from a one-year range ending at 2026-09-30, with product-specific
  skew rules. Daily gaps, concentration, and per-product recency therefore
  require live profiling.
- Quantities on generated sales are positive (normally 1–3); discounts are
  selected from 0%, 5%, and 10%; revenue is derived from quantity, selling
  price, and discount.
- Inventory positions are absent for one fifth of product-store pairs by
  construction. Generated quantities can be zero; the generator does not
  intentionally generate negative values.
- Snapshot values for historical dates are randomly scaled from current
  inventory (factor 0.60 through 1.49, rounded to an integer). They are
  synthetic historical values, not transactionally reconstructed balances.
- Purchase-order statuses are generated as Open, In Transit, Delayed,
  Cancelled, or Received; status distribution is randomized.

These statements describe the generation logic, not the actual contents or
quality of the live database.

### 3.2 Read-only SQL profile pack

Run the following in the `InventoryAI` database after connecting with a
read-only login. These queries are provided for reproducibility and were not
run in this audit.

#### A. Actual table/view row counts and database metadata

```sql
-- Table/view object inventory and exact row counts for tables.
SELECT
    s.name AS schema_name,
    o.name AS object_name,
    o.type_desc,
    CASE WHEN o.type = 'U' THEN
        (SELECT SUM(p.rows)
         FROM sys.partitions AS p
         WHERE p.object_id = o.object_id AND p.index_id IN (0, 1))
    END AS table_row_count
FROM sys.objects AS o
JOIN sys.schemas AS s ON s.schema_id = o.schema_id
WHERE o.type IN ('U', 'V')
  AND s.name = N'dbo'
ORDER BY o.type_desc, o.name;

-- Columns, final nullability, identity/default and SQL types.
SELECT
    s.name AS schema_name,
    o.name AS object_name,
    c.column_id,
    c.name AS column_name,
    TYPE_NAME(c.user_type_id) AS data_type,
    c.max_length,
    c.precision,
    c.scale,
    c.is_nullable,
    c.is_identity,
    dc.definition AS default_definition
FROM sys.objects AS o
JOIN sys.schemas AS s ON s.schema_id = o.schema_id
JOIN sys.columns AS c ON c.object_id = o.object_id
LEFT JOIN sys.default_constraints AS dc
  ON dc.parent_object_id = c.object_id
 AND dc.parent_column_id = c.column_id
WHERE o.type IN ('U', 'V')
  AND s.name = N'dbo'
ORDER BY o.name, c.column_id;

-- Primary and unique constraints/indexes.
SELECT
    s.name AS schema_name,
    t.name AS table_name,
    i.name AS index_name,
    i.is_primary_key,
    i.is_unique,
    STRING_AGG(c.name, N', ') WITHIN GROUP (ORDER BY ic.key_ordinal) AS key_columns
FROM sys.tables AS t
JOIN sys.schemas AS s ON s.schema_id = t.schema_id
JOIN sys.indexes AS i ON i.object_id = t.object_id
JOIN sys.index_columns AS ic
  ON ic.object_id = i.object_id
 AND ic.index_id = i.index_id
 AND ic.key_ordinal > 0
JOIN sys.columns AS c
  ON c.object_id = ic.object_id
 AND c.column_id = ic.column_id
WHERE s.name = N'dbo'
  AND (i.is_primary_key = 1 OR i.is_unique = 1)
GROUP BY s.name, t.name, i.name, i.is_primary_key, i.is_unique
ORDER BY t.name, i.is_primary_key DESC, i.name;

-- Foreign keys and their column mappings.
SELECT
    fk.name AS foreign_key_name,
    OBJECT_SCHEMA_NAME(fk.parent_object_id) AS child_schema,
    OBJECT_NAME(fk.parent_object_id) AS child_table,
    pc.name AS child_column,
    OBJECT_SCHEMA_NAME(fk.referenced_object_id) AS parent_schema,
    OBJECT_NAME(fk.referenced_object_id) AS parent_table,
    rc.name AS parent_column,
    fk.is_disabled,
    fk.is_not_trusted
FROM sys.foreign_keys AS fk
JOIN sys.foreign_key_columns AS fkc
  ON fkc.constraint_object_id = fk.object_id
JOIN sys.columns AS pc
  ON pc.object_id = fkc.parent_object_id
 AND pc.column_id = fkc.parent_column_id
JOIN sys.columns AS rc
  ON rc.object_id = fkc.referenced_object_id
 AND rc.column_id = fkc.referenced_column_id
WHERE SCHEMA_NAME(OBJECT_SCHEMA_ID(OBJECT_NAME(fk.parent_object_id))) = N'dbo'
ORDER BY child_table, foreign_key_name;
```

#### B. Products and stores

```sql
SELECT brand, category, COUNT(*) AS product_count
FROM dbo.Products
GROUP BY brand, category
ORDER BY brand, category;

SELECT sku, COUNT(*) AS row_count
FROM dbo.Products
GROUP BY sku
HAVING COUNT(*) > 1 OR sku IS NULL;

SELECT product_name, brand, category, model, COUNT(*) AS row_count
FROM dbo.Products
GROUP BY product_name, brand, category, model
HAVING COUNT(*) > 1;

SELECT
    SUM(CASE WHEN sku IS NULL OR LTRIM(RTRIM(sku)) = '' THEN 1 ELSE 0 END) AS missing_sku,
    SUM(CASE WHEN product_name IS NULL OR LTRIM(RTRIM(product_name)) = '' THEN 1 ELSE 0 END) AS missing_name,
    SUM(CASE WHEN brand IS NULL OR LTRIM(RTRIM(brand)) = '' THEN 1 ELSE 0 END) AS missing_brand,
    SUM(CASE WHEN category IS NULL OR LTRIM(RTRIM(category)) = '' THEN 1 ELSE 0 END) AS missing_category,
    SUM(CASE WHEN model IS NULL OR LTRIM(RTRIM(model)) = '' THEN 1 ELSE 0 END) AS missing_model,
    SUM(CASE WHEN cost_price <= 0 THEN 1 ELSE 0 END) AS nonpositive_cost,
    SUM(CASE WHEN selling_price <= 0 THEN 1 ELSE 0 END) AS nonpositive_selling_price
FROM dbo.Products;

-- Reveal inconsistent capitalization/spacing and names not following the
-- generated "brand + category label + model" convention for manual review.
SELECT product_id, sku, product_name, brand, category, model
FROM dbo.Products
WHERE product_name <> LTRIM(RTRIM(product_name))
   OR brand <> LTRIM(RTRIM(brand))
   OR category <> LTRIM(RTRIM(category))
   OR model <> LTRIM(RTRIM(model))
   OR product_name NOT LIKE brand + N'%'
ORDER BY brand, category, product_name;

SELECT city, region, COUNT(*) AS store_count
FROM dbo.Stores
GROUP BY city, region
ORDER BY region, city;

SELECT store_name, COUNT(*) AS row_count
FROM dbo.Stores
GROUP BY store_name
HAVING COUNT(*) > 1;

SELECT
    SUM(CASE WHEN city IS NULL OR LTRIM(RTRIM(city)) = '' THEN 1 ELSE 0 END) AS missing_city,
    SUM(CASE WHEN region IS NULL OR LTRIM(RTRIM(region)) = '' THEN 1 ELSE 0 END) AS missing_region,
    SUM(CASE WHEN store_format IS NULL OR LTRIM(RTRIM(store_format)) = '' THEN 1 ELSE 0 END) AS missing_format,
    SUM(CASE WHEN opened_date IS NULL THEN 1 ELSE 0 END) AS missing_opened_date
FROM dbo.Stores;
```

#### C. Inventory and valuation

```sql
SELECT
    store_id,
    COUNT(DISTINCT product_id) AS products_at_store,
    COUNT(*) AS positions
FROM dbo.Inventory
GROUP BY store_id
ORDER BY store_id;

SELECT
    COUNT(*) AS positions,
    MIN(quantity_on_hand) AS min_qty,
    MAX(quantity_on_hand) AS max_qty,
    AVG(CAST(quantity_on_hand AS DECIMAL(18,4))) AS avg_qty,
    SUM(CASE WHEN quantity_on_hand = 0 THEN 1 ELSE 0 END) AS zero_stock,
    SUM(CASE WHEN quantity_on_hand < 0 THEN 1 ELSE 0 END) AS negative_stock
FROM dbo.Inventory;

SELECT
    MIN(reorder_level) AS min_reorder,
    MAX(reorder_level) AS max_reorder,
    AVG(CAST(reorder_level AS DECIMAL(18,4))) AS avg_reorder,
    MIN(max_level) AS min_max,
    MAX(max_level) AS max_max,
    AVG(CAST(max_level AS DECIMAL(18,4))) AS avg_max,
    SUM(CASE WHEN reorder_level < 0 THEN 1 ELSE 0 END) AS negative_reorder,
    SUM(CASE WHEN max_level < 0 THEN 1 ELSE 0 END) AS negative_max,
    SUM(CASE WHEN reorder_level > max_level THEN 1 ELSE 0 END) AS reorder_above_max
FROM dbo.Inventory;

-- Compare stored view valuation to quantity × product cost.
SELECT
    COUNT(*) AS checked_positions,
    SUM(CASE WHEN v.inventory_value <> CAST(i.quantity_on_hand * p.cost_price AS DECIMAL(16,2))
             THEN 1 ELSE 0 END) AS mismatches,
    MAX(ABS(v.inventory_value - CAST(i.quantity_on_hand * p.cost_price AS DECIMAL(16,2))))
        AS maximum_absolute_difference
FROM dbo.vw_InventoryPosition AS v
JOIN dbo.Inventory AS i
  ON i.product_id = v.product_id AND i.store_id = v.store_id
JOIN dbo.Products AS p ON p.product_id = i.product_id;

SELECT as_of_date, COUNT(*) AS positions
FROM dbo.Inventory
GROUP BY as_of_date
ORDER BY as_of_date;
```

#### D. Sales

```sql
SELECT
    COUNT(*) AS sale_lines,
    COUNT(DISTINCT invoice_no) AS invoices,
    COUNT(DISTINCT product_id) AS products,
    COUNT(DISTINCT store_id) AS stores,
    COUNT(DISTINCT sale_date) AS sale_days,
    MIN(sale_date) AS first_sale_date,
    MAX(sale_date) AS last_sale_date
FROM dbo.Sales;

SELECT
    SUM(CASE WHEN quantity <= 0 THEN 1 ELSE 0 END) AS nonpositive_quantities,
    SUM(CASE WHEN unit_price <= 0 THEN 1 ELSE 0 END) AS nonpositive_unit_prices,
    SUM(CASE WHEN discount_pct < 0 OR discount_pct > 100 THEN 1 ELSE 0 END) AS invalid_discounts,
    SUM(CASE WHEN revenue < 0 THEN 1 ELSE 0 END) AS negative_revenue,
    SUM(CASE WHEN sale_date IS NULL THEN 1 ELSE 0 END) AS missing_dates,
    SUM(CASE WHEN invoice_no IS NULL OR LTRIM(RTRIM(invoice_no)) = '' THEN 1 ELSE 0 END) AS missing_invoice
FROM dbo.Sales;

-- Revenue arithmetic check. Tolerance is one cent for decimal rounding.
SELECT COUNT(*) AS arithmetic_mismatches
FROM dbo.Sales
WHERE ABS(revenue - ROUND(quantity * unit_price * (1 - discount_pct / 100.0), 2)) > 0.01;

-- Product/store units and revenue, ordered by concentration.
SELECT TOP (50)
    product_id, store_id, SUM(quantity) AS units, SUM(revenue) AS revenue,
    COUNT(*) AS sale_lines, COUNT(DISTINCT sale_date) AS active_days
FROM dbo.Sales
GROUP BY product_id, store_id
ORDER BY SUM(quantity) DESC;

-- Line-level revenue distribution and tail percentiles.
SELECT
    COUNT(*) AS sale_lines,
    MIN(revenue) AS min_line_revenue,
    MAX(revenue) AS max_line_revenue,
    AVG(CAST(revenue AS DECIMAL(18,4))) AS avg_line_revenue
FROM dbo.Sales;

SELECT DISTINCT
    PERCENTILE_CONT(0.50) WITHIN GROUP (ORDER BY revenue) OVER () AS p50_line_revenue,
    PERCENTILE_CONT(0.95) WITHIN GROUP (ORDER BY revenue) OVER () AS p95_line_revenue
FROM dbo.Sales;

-- Actual recent sales coverage against max sale date; live query anchor.
DECLARE @asof DATE = (SELECT MAX(sale_date) FROM dbo.Sales);
SELECT
    p.product_id, p.product_name,
    COUNT(s.sale_id) AS lines_30d,
    COALESCE(SUM(s.quantity), 0) AS units_30d,
    COUNT(DISTINCT s.sale_date) AS active_days_30d
FROM dbo.Products AS p
LEFT JOIN dbo.Sales AS s
  ON s.product_id = p.product_id
 AND s.sale_date > DATEADD(DAY, -30, @asof)
 AND s.sale_date <= @asof
GROUP BY p.product_id, p.product_name
HAVING COALESCE(SUM(s.quantity), 0) = 0
ORDER BY p.product_name;

-- Missing dates in the observed daily range. No row means no sales that day,
-- not necessarily a missing data load; interpret against business calendar.
;WITH d AS (
    SELECT MIN(sale_date) AS sale_date FROM dbo.Sales
    UNION ALL
    SELECT DATEADD(DAY, 1, sale_date)
    FROM d
    WHERE sale_date < (SELECT MAX(sale_date) FROM dbo.Sales)
)
SELECT d.sale_date AS no_sale_date
FROM d
LEFT JOIN dbo.Sales AS s ON s.sale_date = d.sale_date
WHERE s.sale_date IS NULL
OPTION (MAXRECURSION 0);

-- Product sales concentration and top-10 share.
;WITH p AS (
    SELECT product_id, SUM(quantity) AS units
    FROM dbo.Sales
    GROUP BY product_id
), r AS (
    SELECT product_id, units,
           ROW_NUMBER() OVER (ORDER BY units DESC) AS rn,
           SUM(units) OVER () AS total_units
    FROM p
)
SELECT
    SUM(CASE WHEN rn <= 10 THEN units ELSE 0 END) AS top10_units,
    MAX(total_units) AS all_units,
    CAST(100.0 * SUM(CASE WHEN rn <= 10 THEN units ELSE 0 END)
         / NULLIF(MAX(total_units), 0) AS DECIMAL(8,2)) AS top10_share_pct
FROM r;
```

Read the percentiles alongside unit-price/discount validation; extreme line
revenue alone is not proof of an error. Concentration by product, store, and
customer segment is descriptive evidence, not a causal explanation.

#### E. Snapshots, supplier links, purchase orders, and rule values

```sql
SELECT MIN(snapshot_date) AS first_snapshot,
       MAX(snapshot_date) AS last_snapshot,
       COUNT(DISTINCT snapshot_date) AS distinct_dates,
       COUNT(*) AS rows
FROM dbo.InventorySnapshotWeekly;

SELECT snapshot_date,
       COUNT(*) AS rows,
       COUNT(DISTINCT product_id) AS products,
       COUNT(DISTINCT store_id) AS stores,
       COUNT(DISTINCT CONCAT(product_id, ':', store_id)) AS product_store_pairs
FROM dbo.InventorySnapshotWeekly
GROUP BY snapshot_date
ORDER BY snapshot_date;

-- Compare every observed snapshot date with the current inventory assortment.
-- Missing pairs can mean either a missing snapshot or an intentionally unranged item.
SELECT d.snapshot_date, COUNT(*) AS missing_inventory_pairs
FROM (SELECT DISTINCT snapshot_date FROM dbo.InventorySnapshotWeekly) AS d
CROSS JOIN dbo.Inventory AS i
LEFT JOIN dbo.InventorySnapshotWeekly AS s
  ON s.snapshot_date = d.snapshot_date
 AND s.product_id = i.product_id
 AND s.store_id = i.store_id
WHERE s.product_id IS NULL
GROUP BY d.snapshot_date
ORDER BY d.snapshot_date;

-- Expected weekly spacing and product-store coverage between snapshot dates.
;WITH d AS (
    SELECT snapshot_date,
           LAG(snapshot_date) OVER (ORDER BY snapshot_date) AS previous_date
    FROM (SELECT DISTINCT snapshot_date FROM dbo.InventorySnapshotWeekly) AS s
)
SELECT snapshot_date, previous_date, DATEDIFF(DAY, previous_date, snapshot_date) AS gap_days
FROM d
WHERE previous_date IS NOT NULL
  AND DATEDIFF(DAY, previous_date, snapshot_date) <> 7
ORDER BY snapshot_date;

-- Historical step changes per product/store. Review large moves as candidates,
-- not automatic errors: receipts, transfers, and corrections can be legitimate.
;WITH q AS (
    SELECT snapshot_date, product_id, store_id, quantity_on_hand,
           LAG(quantity_on_hand) OVER (
               PARTITION BY product_id, store_id ORDER BY snapshot_date
           ) AS previous_quantity
    FROM dbo.InventorySnapshotWeekly
)
SELECT snapshot_date, product_id, store_id, previous_quantity, quantity_on_hand,
       quantity_on_hand - previous_quantity AS change_units
FROM q
WHERE previous_quantity IS NOT NULL
  AND ABS(quantity_on_hand - previous_quantity) >= 100
ORDER BY ABS(quantity_on_hand - previous_quantity) DESC;

-- Supplier coverage, including products with no supplier association.
SELECT p.product_id, p.product_name
FROM dbo.Products AS p
LEFT JOIN dbo.SupplierProducts AS sp ON sp.product_id = p.product_id
WHERE sp.product_id IS NULL
ORDER BY p.product_id;

SELECT product_id, COUNT(*) AS primary_supplier_links,
       MIN(lead_time_days) AS min_lead_days,
       MAX(lead_time_days) AS max_lead_days
FROM dbo.SupplierProducts
WHERE is_primary = 1
GROUP BY product_id
HAVING COUNT(*) <> 1 OR MIN(lead_time_days) <= 0 OR MAX(lead_time_days) <= 0;

SELECT
    COUNT(*) AS supplier_product_rows,
    SUM(CASE WHEN lead_time_days <= 0 THEN 1 ELSE 0 END) AS nonpositive_lead_times,
    SUM(CASE WHEN is_primary = 1 THEN 1 ELSE 0 END) AS primary_rows
FROM dbo.SupplierProducts;

SELECT supplier_id, COUNT(*) AS linked_products,
       MIN(lead_time_days) AS min_lead_days,
       MAX(lead_time_days) AS max_lead_days
FROM dbo.SupplierProducts
GROUP BY supplier_id
ORDER BY supplier_id;

SELECT status, COUNT(*) AS po_count,
       MIN(order_date) AS earliest_order,
       MAX(order_date) AS latest_order,
       SUM(quantity) AS ordered_units
FROM dbo.PurchaseOrders
GROUP BY status
ORDER BY status;

SELECT
    SUM(CASE WHEN quantity <= 0 THEN 1 ELSE 0 END) AS nonpositive_po_qty,
    SUM(CASE WHEN expected_delivery_date < order_date THEN 1 ELSE 0 END) AS delivery_before_order,
    SUM(CASE WHEN status NOT IN ('Open','In Transit','Delayed','Cancelled','Received')
             THEN 1 ELSE 0 END) AS unrecognized_status
FROM dbo.PurchaseOrders;

SELECT rule_key, rule_value, description
FROM dbo.BusinessRules
ORDER BY rule_key;
```

#### F. Referential integrity, duplicates, date alignment, and view join inflation

Foreign keys declared and enabled by SQL Server prevent ordinary orphan rows,
but the following checks still detect disabled/untrusted constraints, missing
constraints, or inconsistencies introduced through bulk operations.

```sql
SELECT i.product_id, i.store_id
FROM dbo.Inventory AS i
LEFT JOIN dbo.Products AS p ON p.product_id = i.product_id
LEFT JOIN dbo.Stores AS s ON s.store_id = i.store_id
WHERE p.product_id IS NULL OR s.store_id IS NULL;

SELECT sa.sale_id
FROM dbo.Sales AS sa
LEFT JOIN dbo.Products AS p ON p.product_id = sa.product_id
LEFT JOIN dbo.Stores AS st ON st.store_id = sa.store_id
WHERE p.product_id IS NULL OR st.store_id IS NULL;

SELECT sn.snapshot_date, sn.product_id, sn.store_id
FROM dbo.InventorySnapshotWeekly AS sn
LEFT JOIN dbo.Products AS p ON p.product_id = sn.product_id
LEFT JOIN dbo.Stores AS st ON st.store_id = sn.store_id
WHERE p.product_id IS NULL OR st.store_id IS NULL;

SELECT sp.supplier_id, sp.product_id
FROM dbo.SupplierProducts AS sp
LEFT JOIN dbo.Suppliers AS su ON su.supplier_id = sp.supplier_id
LEFT JOIN dbo.Products AS p ON p.product_id = sp.product_id
WHERE su.supplier_id IS NULL OR p.product_id IS NULL;

SELECT po.po_id
FROM dbo.PurchaseOrders AS po
LEFT JOIN dbo.Products AS p ON p.product_id = po.product_id
LEFT JOIN dbo.Suppliers AS su ON su.supplier_id = po.supplier_id
WHERE p.product_id IS NULL OR su.supplier_id IS NULL;

SELECT sp.product_id, COUNT(*) AS primary_links
FROM dbo.SupplierProducts AS sp
WHERE sp.is_primary = 1
GROUP BY sp.product_id
HAVING COUNT(*) <> 1;

-- Detect inventory rows multiplied by primary-supplier joins in the view.
SELECT product_id, store_id, COUNT(*) AS view_rows
FROM dbo.vw_InventoryPosition
GROUP BY product_id, store_id
HAVING COUNT(*) > 1;

-- Compare current inventory date to most recent sales/snapshot dates.
SELECT
    (SELECT MAX(as_of_date) FROM dbo.Inventory) AS latest_inventory_date,
    (SELECT MAX(snapshot_date) FROM dbo.InventorySnapshotWeekly) AS latest_snapshot_date,
    (SELECT MAX(sale_date) FROM dbo.Sales) AS latest_sale_date;
```

### 3.3 Genuine exceptions versus likely data errors

Cannot be classified from code/scripts alone; live validation and business
definitions are required.

| Observation | Potential interpretation | How to distinguish |
|---|---|---|
| Zero inventory | Legitimate out-of-stock condition | Compare to stock status, replenishment and snapshot sequence. |
| Negative inventory | Could represent returns/backorders in some systems, but the current schema and rules do not document this | Inspect source process and transaction policy; likely invalid for this current-position model. |
| No recent sales | Could be new, seasonal, discontinued, or intentionally slow/no-demand | Compare launch date, `is_active`, historical sales, and assortment policy. |
| No inventory row for product-store | Seed script intentionally omits 20% of pairs; could also indicate assortment or missing load | Distinguish “not ranged” from “missing position” with an explicit assortment table/flag. |
| Extreme DOI | Could be truly stagnant inventory, low denominator, or bad sales window | Review units and active days, not DOI alone; DOI is undefined when sales are zero. |
| `reorder_level > max_level` | Usually inconsistent threshold configuration | Confirm rule semantics and replenish settings. |
| Multiple primary suppliers | May be allowed for split sourcing, but query expects one | Decide whether primary means exactly one per product or whether lead time must be selected/combined. |
| Same invoice number on many rows | Expected if invoice number identifies a basket | Check duplicates at invoice + product + store grain, not invoice alone. |
| No sale on a calendar day | May be store closure/no demand, not data loss | Compare operating calendar, store open dates and ingestion completeness. |
| Snapshot differs from current inventory | Expected for historical dates; generated script randomizes old snapshots | For live production data, require snapshot provenance and reconcile latest snapshot to current position. |

## 4. Metric granularity analysis

### 4.1 Recommended calculations

Let `Q` be quantity on hand, `U` be units sold over `W` days, and `L` be
supplier lead time.

| Requested grain | Inventory quantity/value/status | Average daily sales and DOI | Stockout risk | Important condition |
|---|---|---|---|---|
| Product + Store | Sum/position per product-store; value `Q × cost`; status compare Q with that position's reorder/max. | `ADS=U/W`; `DOI=Q/ADS` where ADS>0. | Compare that row's DOI with its lead time and safety buffer. | This is the safest operational grain; do not blend stores. |
| Product | Sum Q and U across the product’s stores, then DOI=`SUM(Q)/(SUM(U)/W)`; sum inventory value; supplier lead time needs an explicit sourcing rule. | This is a demand-weighted/poolable product coverage measure, not the simple average of store DOI. | Pooled stock and a mean lead time may mask a store shortage. | Only operationally meaningful if stock can be transferred across stores quickly enough. |
| City | Sum Q and U for city, then calculate DOI from those totals. | Ratio of totals is preferable to average of store DOIs. | Do not average risk labels; calculate at store/product first or declare a pooling assumption. | Cities with multiple stores should retain location dimensions for follow-up. |
| Region | Same as city, aggregating totals before calculating coverage. | Ratio of totals. | Region-wide pooling can conceal store-level risk; mean supplier lead times are a weak proxy. | Validate transferability across stores/regions. |
| Brand | Sum Q/U across brand and calculate ratio of totals for descriptive coverage. | Ratio of totals; never mean raw DOI. | Brand-level risk is not a count of at-risk product-store positions. Calculate operational risk before aggregation. | Brand metric is descriptive, not a replenishment decision. |
| Category | Sum Q/U across category and calculate ratio of totals for descriptive coverage. | Ratio of totals. | Weighted average lead time can obscure critical suppliers/products. | Keep a product-level drill-down. |
| Company-wide | `SUM(Q)` and `SUM(U)` before DOI; company inventory value is sum of line values. | `company DOI=SUM(Q)/(SUM(U)/W)`. | A company-wide count must be computed from product-store risk classifications, not from a single company DOI or averaged labels. | The current inventory query defaults to product totals and has no explicit company-level `group_by`. |

`DOI` is undefined when average sales is zero. Current vectorized calculation
produces `NaN`; risk becomes `"No demand"`, coverage `"No recent sales"`, and
excess-by-DOI treats positive stock with no sales as excess.

### 4.2 Current code behavior and company-vs-city stockout discrepancy

`Queries.aggregate_inventory()` groups inventory before
`enrich_inventory()` computes DOI and risk. With no `group_by`, it defaults to
product totals. With `group_by="store"` it aggregates per store unless there is
a product/brand/category filter, in which case it retains product-store rows.
The stockout capability then keeps only High and Medium results.

Therefore, company-wide product aggregation can combine inventory in all stores
and compare that pooled amount with pooled demand. Chennai-only results use only
Chennai positions. A product may look adequately covered company-wide because
other locations' inventory is counted, while its Chennai position is high-risk.
Consequently, a company-wide query returning fewer at-risk products than a
Chennai-only query is plausible under current pooling logic. It is not
necessarily a data error; it is a **grain/transferability semantic issue**.

Reproducible code path: `app/semantic/queries.py`:
`aggregate_inventory()` defaults no group to product; `app/calc/metrics.py`:
`enrich_inventory()` computes DOI and lead-time risk after aggregation;
`Queries.finalize()` filters stockout results to High/Medium.

Suggested read-only comparison at product-store grain (replace the illustrative
dates with a consistent live anchor):

```sql
DECLARE @asof DATE = (SELECT MAX(sale_date) FROM dbo.Sales);
WITH recent AS (
    SELECT product_id, store_id, SUM(quantity) AS units
    FROM dbo.Sales
    WHERE sale_date > DATEADD(DAY, -30, @asof)
      AND sale_date <= @asof
    GROUP BY product_id, store_id
), pos AS (
    SELECT i.product_id, p.product_name, i.store_id, st.city,
           i.quantity_on_hand, sp.lead_time_days,
           COALESCE(r.units, 0) AS units_30d,
           CAST(COALESCE(r.units, 0) / 30.0 AS DECIMAL(18,6)) AS ads
    FROM dbo.Inventory AS i
    JOIN dbo.Products AS p ON p.product_id = i.product_id
    JOIN dbo.Stores AS st ON st.store_id = i.store_id
    JOIN dbo.SupplierProducts AS sp
      ON sp.product_id = i.product_id AND sp.is_primary = 1
    LEFT JOIN recent AS r
      ON r.product_id = i.product_id AND r.store_id = i.store_id
), risk AS (
    SELECT *,
           CASE
             WHEN ads <= 0 THEN 'No demand'
             WHEN quantity_on_hand / ads < lead_time_days THEN 'High'
             WHEN quantity_on_hand / ads < lead_time_days + 7 THEN 'Medium'
             ELSE 'Low'
           END AS store_risk
    FROM pos
)
SELECT product_id, product_name,
       SUM(quantity_on_hand) AS company_qty,
       SUM(units_30d) AS company_units_30d,
       SUM(CASE WHEN store_risk IN ('High','Medium') THEN 1 ELSE 0 END) AS at_risk_stores,
       SUM(CASE WHEN city = N'Chennai' AND store_risk IN ('High','Medium')
                THEN 1 ELSE 0 END) AS chennai_at_risk_stores
FROM risk
GROUP BY product_id, product_name
ORDER BY chennai_at_risk_stores DESC, product_name;
```

This query was not executed here. It also assumes one valid primary supplier
row per product; check that constraint first.

### 4.3 Other aggregation risks

- Reorder and max levels are summed at aggregate grains; that is useful only if
  the levels are additive across the contributing positions.
- Lead time is averaged during inventory aggregation. A simple mean is not
  demand-weighted and can understate a long-lead supplier's risk.
- `stock_status` is calculated after summing quantity/reorder/max. It is not
  equivalent to counting Low/Healthy/Excess product-store positions.
- `is_slow_moving` is evaluated using summed units after aggregation. A
  product can be slow in one store and fast in another while the product total
  is not slow.
- Brand/category/city/region groupings calculate aggregate ratios, not counts
  of underlying at-risk entities.
- Inventory SQL does not filter `Inventory.as_of_date` to the latest common
  date; stale or mixed-date current rows can enter a calculation.

## 5. Existing business-rule inventory

The checked-in seed inserts six values in `BusinessRules`; the Python defaults
match those values. Live values have **not** been queried and remain
unverified.

| Rule and current seed/default | Formula / source fields | Grain | Interpretation, edge cases, data support, approval |
|---|---|---|---|
| `sales_window_days = 30` | `ADS = units_sold_window / window_days`; source: `Sales.quantity`, `sale_date`, product/store IDs. `window_for()` uses a start-exclusive, end-inclusive interval. | Query intent grain, ideally product-store before roll-up. | A fixed recent window smooths daily variation. Zero units yields DOI undefined. Requires complete dated sales and a meaningful as-of date. Window length is a business choice; approval needed to change. |
| `excess_days_of_inventory = 60` | DOI excess if `DOI > 60`; current code also regards positive stock with no recent sales as excess by DOI rule. | Position or declared aggregation grain. | Exactly 60 is not excess (`>`). No-sales behavior flags positive stock. Data supports the arithmetic but not the business threshold or whether it should drive default results. Approval needed. |
| `slow_moving_units_window = 3` | `units_sold_window < 3`; source: `Sales.quantity` over sales window. | Product-store preferable; current flag follows whatever aggregate grain was dispatched. | Exactly 3 is not slow. No sales qualifies. Threshold only makes sense together with sales window and assortment/new-item policy. Approval needed. |
| `stockout_critical_days = 7` | Coverage band Critical if DOI `<7`; source: quantity and average daily sales. | Position first. | Exactly 7 is not Critical. If no sales, current code uses “No recent sales,” not Critical. This coverage-band label is separate from stockout risk. Approval needed. |
| `stockout_warning_days = 14` | Coverage band Warning if DOI `<14` after excluding `<7`; otherwise OK. | Position first. | Effective labels: Critical `<7`, Warning `[7,14)`, OK `>=14`; no-sales is separate. Does not by itself drive the stockout capability filter. Approval needed. |
| `safety_buffer_days = 7` | Risk: High if DOI `< lead_time`; Medium if DOI `< lead_time + 7`; else Low. Source: DOI, primary supplier lead time. | Product-store ideally. | At boundaries, DOI equal to lead is Medium; equal to lead+7 is Low. No demand yields `"No demand"`. Aggregated mean lead time can make result misleading. Requires approved lead-time source and buffer policy. |

Data support is only schema/script-level: fields exist and the seed generates
them. No live completeness/range validation was possible. The `BusinessRules`
table should remain unchanged until business owners approve threshold semantics.

## 6. Capability-to-data mapping

| Capability | Required tables / fields | Supported now (code-level) | Unsupported / limitations / improvement |
|---|---|---|---|
| Inventory visibility | `Inventory`, `Products`, `Stores`, primary `SupplierProducts`; quantity, value/cost, product/location attributes, lead time | Current inventory lookup, product/store/city/region/brand/category groupings; deterministic SQL filters. | No assortment relation distinguishes not-ranged pairs from missing inventory rows. `as_of_date` not consistently constrained. Add explicit assortment and latest-snapshot/current-position policy. |
| Inventory health | Above + reorder/max, recent `Sales.quantity` | Low stock, stock status, excess/slow-moving/no-demand reasons via calculation layer. | Aggregation can hide position-level exceptions. Clarify whether health is product, store, or pooled level; expose exception counts and drilldown. |
| Stockout risk | Inventory + sales window + supplier-product lead time | DOI/lead-time High/Medium/Low; final stockout query includes High/Medium and excludes Low/No demand. | Risk based on a single primary supplier and can be computed after pooling. Need position-level classification, validated lead times, and perhaps open PO/in-transit coverage. |
| Sales analysis | `Sales`, `Products`, `Stores`; date, quantity, revenue, invoice number | Units/revenue/invoice counts, time windows, product/brand/category/store/city/region groups, previous-period unit growth. | Current query does not expose payment mode, customer segment, unit prices, discounts as dimensions. Define returns/cancellations/tax treatment and calendar completeness. |
| Sales and inventory trends | Sales periods; `InventorySnapshotWeekly` snapshot date/quantity | Sales period comparison exists. `Queries.inventory_trend()` can compare snapshots. | Snapshot trend method is not routed as a supported dispatch capability in `app/dispatch.py`. No event-level inventory movement/receipt history. Wire trend only after validating snapshot provenance and dates. |
| Excess inventory | Inventory, sales, cost; BusinessRules | Both max-level and DOI flags are computed. | `excess_slow_moving` currently defaults to filtering `excess_by_max_level`, despite the rule description emphasizing DOI >60; slow-moving alone is not the final filter. Make policy explicit and obtain approval. |
| Slow-moving products | Sales quantity, window, products/active state | `is_slow_moving` flag exists and inventory health can surface it. | `excess_slow_moving` filters excess rather than slow-moving; no dedicated slow-moving-only query/filter path is evident. New products/no-demand need separate treatment. |
| Brand comparisons | Product brand and sales/inventory facts | Descriptive aggregation by brand is possible. | Brand-level stockout risk is not equivalent to product/store risk; current sales query supports aggregation but no causal drivers. Show component products and timeframe. |
| Store comparisons | Store dimensions and inventory/sales facts | Descriptive store/city/region comparisons and store-scoped filtering exist. | Product + store output semantics require care; company pooling can mask local risk. Store open dates/operating calendar are not used in analysis. |
| What-if scenarios | Inventory quantity, sales window, lead time, Rules | Demand change, lead time extra days, lead-time percentage are handled in `enrich_inventory()`. | Additional schema scenario kinds (additional units, target coverage, daily sales target) are not implemented in current `_scenario_params` / enrichment path. Scenarios are deterministic sensitivity analyses, not forecasts. |
| Business explanations | Sales/inventory evidence and context | Descriptive answers such as which product sold more, plus deterministic inventory explanations, can be grounded in provided rows. | Causal “why” is not supported without promotion, availability, price, marketing, placement, competitor, traffic, and customer-level exposure data. Narration must distinguish observed association from cause. |

### Descriptive versus causal question example

“Why is iPhone selling more than Samsung in this store?” can be answered
**descriptively** if the question is interpreted as a comparison: aggregate
observed units or revenue for Apple/iPhone and Samsung in the named store and
time window, then report the difference. `Sales` stores quantity, unit price,
discount, revenue, payment mode, and customer segment, but current `sales()`
query aggregation returns units, revenue, and invoice count only.

The existing data cannot establish the cause of the difference. It has no
explicit promotion/campaign exposure, marketing spend, store traffic,
in-stock availability over time, product placement, competitor pricing, or
customer choice/consideration data. Even discount values on sale lines cannot
show exposure to customers who did not purchase. The appropriate answer must
say what sold more and state that causal drivers are not present in the supplied
data.

## 7. Confirmed defects and risks

“Confirmed” below means confirmed from inspected repository code/scripts, not
validated against the live database.

| ID | Finding / impact | Reproducible check |
|---|---|---|
| D1 | **Creation script database-context ordering risk:** `Creation.sql` creates `dbo.Suppliers` before `USE InventoryAI`, then switches database for the other tables. If the script connection starts in another database, Suppliers is created there and the subsequent FK to `InventoryAI.dbo.Suppliers` can fail or bind to an unexpected object. | Inspect first lines of `../SQL Files/Creation.sql`; verify in a target DB with `SELECT DB_NAME()` at each batch and `sys.tables` in both databases. Do not execute DDL during this audit. |
| D2 | **Primary supplier uniqueness is not enforced:** composite PK permits multiple primary supplier links for one product. Inventory position joins every `is_primary=1` row, which can duplicate inventory positions and inflate aggregated quantity/value/sales if bad data exists. | `SupplierProducts` duplicate-primary query and view-row-inflation query in §3.2 E/F. |
| D3 | **Current inventory grain is pre-aggregated before DOI/risk:** default no-group inventory aggregates to product across all stores, then calculates risk. This can conceal local stockout exposure and explains fewer company-wide at-risk products than a city-only query. | Code path in §4.2; run product-store comparison SQL there. |
| D4 | **Inventory as-of date is not filtered in current raw inventory query:** query selects `i.as_of_date`, but no condition enforces latest/common inventory date. Stale/mixed inventory dates could be combined if present. | Inspect `Queries.inventory_position()` SQL; run `Inventory.as_of_date` distribution in §3.2 C. |
| D5 | **Excess rule selection differs from seed description:** `BusinessRules` description says DOI above threshold is Excess/slow-moving candidate, but `Queries.finalize()` defaults `excess_rule="max_level"` and `excess_slow_moving` filters max-level excess. DOI excess is calculated as an alternative, not the default result filter. | Compare seed insert in `Insertion & Generation.sql` to `Queries.finalize()` default and `dispatch()` call. |
| D6 | **Slow-moving capability is incomplete:** `is_slow_moving` is calculated, but `excess_slow_moving` final filtering chooses an excess rule; no dedicated slow-moving-only filter is applied there. | Inspect `enrich_inventory()` and `Queries.finalize()` in `app/calc/metrics.py` and `app/semantic/queries.py`. |
| D7 | **Snapshot trend query is not dispatched:** `Queries.inventory_trend()` exists, but `dispatch()` routes only inventory capabilities and `sales_trend`; no current intent capability invokes snapshot trend. | Inspect `app/semantic/queries.py::inventory_trend` and `app/dispatch.py::dispatch`. |
| D8 | **One averaged lead time is used after aggregation:** `aggregate_inventory()` computes a simple mean lead time; risk is then computed against it. This is not a conservative or demand-weighted supplier policy. | Inspect inventory aggregation and `enrich_inventory()` risk formula. |
| D9 | **SQL Server user/persona/history data is not present in provided DDL:** user/persona context is Python code and conversation history uses separate SQLite DDL. FastAPI factory does not wire `ConversationStore`. | Inspect `app/context.py`, `app/history.py`, and `app/api.py::get_orchestrator`. |
| D10 | **Live schema and data claims cannot be certified from repository artifacts:** no SQL Server connection was registered; scripts may be unapplied, partially applied, or stale. | Live server listing returned an empty server list; no database query was run. |

Potential edge case to test live: `Queries.as_of()` reads `MAX(Sales.sale_date)` and
uses the first returned cell. An empty Sales table may yield NULL and fail when
converted to a date. Whether this occurs is unverified.

## 8. Proposed data improvements

These are proposals only; no changes were made.

1. Add a product-store assortment/ranging table or equivalent explicit flag so
   absence of an inventory row is distinguishable from “not carried here.”
2. Enforce exactly one primary supplier per active product, or replace the
   single-primary assumption with an explicit supplier-selection/weighted
   sourcing policy.
3. Add an inventory snapshot ingestion timestamp/source and validate that
   current inventory is a single, complete as-of population.
4. Keep inventory calculations at product-store grain for operational risk;
   use pooled aggregations only for clearly labeled descriptive coverage.
5. Add inventory movement / purchase-order receipt events if users need
   replenishment lead-time performance or historical causal explanations.
6. Add promotion/campaign exposure, price history, availability, store traffic,
   and placement if causal sales drivers are a required analytical capability.
7. Add explicit cancellation/return/refund facts and revenue policy if Sales
   includes non-positive quantity or adjustments in the real system.
8. Add sales completeness/load-status and store operating calendar data to
   distinguish true zero-demand days from missing periods.
9. Add an agreed time zone/business-date convention if timestamps or
   cross-region trading-day boundaries become relevant.
10. Keep chat history separate from SQL Server business facts; if persisted,
    add explicit foreign keys, retention/deletion policy, access control, and
    sensitive-data policy before production use.

## 9. Proposed business-rule improvements requiring approval

No thresholds were changed.

- Decide whether “excess” means above `max_level`, DOI >60, or either; align the
  BusinessRules description, capability name, code default, and UI explanation.
- Decide if slow-moving is based on total units, active selling days, or
  velocity normalized by the sales window; clarify treatment of newly launched,
  inactive, and zero-demand items.
- Decide whether 7/14 day bands are intended as coverage labels or stockout
  risk classifications; current code has distinct label/risk algorithms.
- Approve how to treat stock at other stores when evaluating a location’s
  stockout risk (no pooling, transfer-aware pooling, or replenishment network).
- Define supplier lead-time selection when multiple suppliers exist and whether
  the safety buffer is fixed by product, supplier, location, or service level.
- Define the sales window anchoring rule (latest sale globally versus
  as-of date/current business date) and whether windows should be calendar or
  trading days.
- Define how to classify no-sales positive stock: excess, no-demand, new item,
  or separate exception.

## 10. Questions needing business clarification

1. Is the live dataset still the synthetic dataset generated by
   `Insertion & Generation.sql`, or has it been replaced/augmented with
   production data?
2. Is an Inventory row required for every product-store pair, or does missing
   position mean “not ranged”?
3. Can stock be transferred between stores quickly enough that pooled
   product/company DOI should influence stockout risk?
4. Should there be exactly one primary supplier per product?
5. Should “Excess” follow max level, DOI >60, or both?
6. Should zero sales over 30 days mean slow-moving, no demand, excess, or a
   separate state? How should new/discontinued products be handled?
7. What is the authoritative inventory as-of date and how fresh must data be?
8. Does revenue include tax, discounts, returns, cancelled transactions, and
   other adjustments? Is unit price historical?
9. Are purchase-order statuses authoritative, and are actual receipt dates and
   received quantities available elsewhere?
10. Is causal analysis (“why did one brand sell more?”) a required product
    capability, or are descriptive comparisons sufficient?
11. Should personas/authorization be persisted and enforced before the UI
    exposes store/brand data?

## 11. Recommended implementation sequence

1. Obtain a read-only SQL Server connection and verify database name/server
   without reading the backend `.env`; run §3.2 A to capture actual DDL,
   constraints, object counts, and versions.
2. Run all §3.2 profile groups and preserve aggregate-only results; compare
   live results with script-intended targets in §1.4.
3. Resolve integrity/data-quality findings with data owners; do not “repair”
   generated or business data until causes are confirmed.
4. Approve definitions for excess, slow-moving, no-demand, and stockout pooling.
5. Establish canonical grain for operational metrics: calculate DOI/risk at
   product-store first; aggregate labels/counts only after classification.
6. Validate snapshot provenance and current inventory freshness; then expose
   inventory trend through a supported dispatch capability.
7. Align capability implementation to approved data coverage, starting with
   inventory visibility, product-store health/risk, and descriptive sales
   comparisons.
8. Add scenario assumptions and causal-driver data only after business owners
   approve intended questions and required data sources.
9. Add regression tests for all accepted grains, threshold boundaries,
   multiple-primary-supplier behavior, no-sales cases, and company-vs-location
   risk semantics before changing the chatbot response engine.

## 12. Audit command / evidence log

- Inspected `../SQL Files/Creation.sql`.
- Inspected `../SQL Files/Insertion & Generation.sql`.
- Inspected `../SQL Files/Views Creation & Validation.sql`.
- Inspected `app/repository.py`, `app/semantic/queries.py`,
  `app/semantic/resolver.py`, `app/dispatch.py`, `app/calc/metrics.py`,
  `app/context.py`, `app/history.py`, `app/api.py`, and intent/response
  definitions.
- Read `.env.example` for documented configuration only.
- Did **not** inspect or execute the backend `.env`.
- MSSQL server listing returned no registered servers. **No live SQL was
  executed; live-data findings are unverified.**
- No business data, BusinessRules, SQL calculations, or chatbot responses were
  modified.
