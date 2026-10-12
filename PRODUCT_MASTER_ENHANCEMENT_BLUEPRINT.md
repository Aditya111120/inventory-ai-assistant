# InventoryAI Product Master Enhancement Blueprint

**Prepared:** 2026-10-08  
**Status:** Proposal for review only — no database changes made  
**Restrictions:** The backend `.env` was not opened, read, or executed. The
`.env.example` was used only to understand the documented connection settings.
No live database connection was available, and no database query or write was
run.

## 1. Executive summary and evidence boundary

The checked-in SQL seed script generates 150 product rows from 25
brand/category combinations, with six synthetic model-code suffixes per
combination. These are generated labels, not meaningful manufacturer model
numbers or verified real-world product specifications. The identity values
assigned to those products are not safely inferable from the script: the
`INSERT ... SELECT` that creates products has no deterministic `ORDER BY`.

The database server listing returned no registered servers. Consequently, the
following live facts could not be inspected:

- Whether the target database currently contains 150 products or matches the
  checked-in generation script.
- The actual `product_id`, SKU, product name, model, launch date, or active flag
  for any live product.
- Existing live constraints, foreign keys, product-specific variants, or data
  quality.

This blueprint maps all **150 script-defined generated labels** below, grouped
as 25 groups of six. It does not claim that these are the current live product
records. Before approving assignments or migration, retrieve and review the
actual catalog using the read-only checks in §7.

## 2. Existing catalog and implications

`dbo.Products` as defined by `SQL Files/Creation.sql` contains:

| Column | Current role | What it establishes / does not establish |
|---|---|---|
| `product_id INT IDENTITY` | Product key | Referenced by inventory, sales, snapshots, supplier links, and POs; preserve every value. |
| `sku VARCHAR(20)` | Product identifier | Made `NOT NULL` and unique by the seed script; preserve existing values. |
| `product_name NVARCHAR(150)` | Display label | Seed names contain brand, generic item type, and synthetic model code. |
| `brand NVARCHAR(50)` | Brand grouping | Seed contains the brands shown in §3. |
| `category NVARCHAR(50)` | Broad product grouping | Seed uses broad values such as `Audio` and `Appliances`; not enough to distinguish many useful subtypes. |
| `model NVARCHAR(50)` | Model-like value | Seed-generated codes are category/brand abbreviations plus suffix 1–6, not verified manufacturer model numbers. |
| `launch_date DATE` | Date field | Seed script assigns a randomized date relative to 2026-09-30; it is not evidence of a real launch date unless independently sourced. |
| `cost_price`, `selling_price` | Financial attributes | Existing analytics use cost to calculate inventory value and sales price/revenue; not sufficient evidence for retail lifecycle or physical specifications. |
| `is_active BIT NOT NULL DEFAULT 1` | Binary flag | Seed insert omits it, so the default applies. Its operational meaning (available for sale, orderable, or not discontinued) is not defined by the schema. |

Current application product resolution selects only
`product_id, sku, product_name, brand, category, model` for rows with
`is_active = 1`. The two checked-in views expose the current core product
labels/brand/category/model but do not expose a subcategory or product family.
Optional new columns can therefore be added without changing current query
results; any analytics that use them would be a separately reviewed change.

The generation script derives:

- 25 brand/category tuples × six suffixes = 150 generated products.
- A `model_code` of `UPPER(LEFT(brand,3)) + '-' +
  UPPER(LEFT(category,3)) + '-' + suffix`.
- `product_name` from brand + generic type label + model code.
- `launch_date` from a random day offset in a 1,300-day range ending at the
  script's fixed `@asof` date of 2026-09-30.
- Prices from category/brand formulas rather than source catalog records.
- `is_active` via the table default, since it is not supplied by the insert.

Those properties describe the checked-in generator. They do not validate live
records.

## 3. Proposed mapping of the 150 script-defined labels

Each row below explicitly enumerates six generated model-code labels; together,
the rows cover 25 × 6 = 150 generated product labels. The proposed subcategory
is deliberately no more specific than the seed's category and generated name
support. For audio and appliances, even the subtype is unknown. These proposed
labels are **not approved live assignments**.

| Existing brand | Existing category | Generated labels (six products) | Conservative proposed subcategory | Family / variant evidence |
|---|---|---|---|---|
| Apple | Smartphones | `APP-SMA-1`, `APP-SMA-2`, `APP-SMA-3`, `APP-SMA-4`, `APP-SMA-5`, `APP-SMA-6` | General smartphone; no verified subtype/specification | Same seed template only; actual family and variant relationships unknown. |
| Apple | Laptops | `APP-LAP-1`, `APP-LAP-2`, `APP-LAP-3`, `APP-LAP-4`, `APP-LAP-5`, `APP-LAP-6` | General laptop; no verified subtype/specification | Same seed template only; actual family and variant relationships unknown. |
| Apple | Tablets | `APP-TAB-1`, `APP-TAB-2`, `APP-TAB-3`, `APP-TAB-4`, `APP-TAB-5`, `APP-TAB-6` | General tablet; no verified subtype/specification | Same seed template only; actual family and variant relationships unknown. |
| Apple | Audio | `APP-AUD-1`, `APP-AUD-2`, `APP-AUD-3`, `APP-AUD-4`, `APP-AUD-5`, `APP-AUD-6` | Audio product — subtype unclassified | Same seed template only; cannot tell headphones, earbuds, speakers, or another type. |
| Samsung | Smartphones | `SAM-SMA-1`, `SAM-SMA-2`, `SAM-SMA-3`, `SAM-SMA-4`, `SAM-SMA-5`, `SAM-SMA-6` | General smartphone; no verified subtype/specification | Same seed template only; actual family and variant relationships unknown. |
| Samsung | Televisions | `SAM-TEL-1`, `SAM-TEL-2`, `SAM-TEL-3`, `SAM-TEL-4`, `SAM-TEL-5`, `SAM-TEL-6` | General television; display technology/size unknown | Same seed template only; actual family and variant relationships unknown. |
| Samsung | Tablets | `SAM-TAB-1`, `SAM-TAB-2`, `SAM-TAB-3`, `SAM-TAB-4`, `SAM-TAB-5`, `SAM-TAB-6` | General tablet; no verified subtype/specification | Same seed template only; actual family and variant relationships unknown. |
| Samsung | Audio | `SAM-AUD-1`, `SAM-AUD-2`, `SAM-AUD-3`, `SAM-AUD-4`, `SAM-AUD-5`, `SAM-AUD-6` | Audio product — subtype unclassified | Same seed template only; cannot tell headphones, earbuds, speakers, or another type. |
| LG | Televisions | `LG-TEL-1`, `LG-TEL-2`, `LG-TEL-3`, `LG-TEL-4`, `LG-TEL-5`, `LG-TEL-6` | General television; display technology/size unknown | Same seed template only; actual family and variant relationships unknown. |
| LG | Audio | `LG-AUD-1`, `LG-AUD-2`, `LG-AUD-3`, `LG-AUD-4`, `LG-AUD-5`, `LG-AUD-6` | Audio product — subtype unclassified | Same seed template only; cannot tell audio product type. |
| LG | Appliances | `LG-APP-1`, `LG-APP-2`, `LG-APP-3`, `LG-APP-4`, `LG-APP-5`, `LG-APP-6` | Appliance — subtype unclassified | Same seed template only; cannot tell refrigerator, washer, or another appliance. |
| Sony | Televisions | `SON-TEL-1`, `SON-TEL-2`, `SON-TEL-3`, `SON-TEL-4`, `SON-TEL-5`, `SON-TEL-6` | General television; display technology/size unknown | Same seed template only; actual family and variant relationships unknown. |
| Sony | Audio | `SON-AUD-1`, `SON-AUD-2`, `SON-AUD-3`, `SON-AUD-4`, `SON-AUD-5`, `SON-AUD-6` | Audio product — subtype unclassified | Same seed template only; cannot tell audio product type. |
| Sony | Cameras | `SON-CAM-1`, `SON-CAM-2`, `SON-CAM-3`, `SON-CAM-4`, `SON-CAM-5`, `SON-CAM-6` | Camera — subtype unclassified | Same seed template only; camera type/lens/bundle unknown. |
| Dell | Laptops | `DEL-LAP-1`, `DEL-LAP-2`, `DEL-LAP-3`, `DEL-LAP-4`, `DEL-LAP-5`, `DEL-LAP-6` | General laptop; no verified subtype/specification | Same seed template only; actual family and variant relationships unknown. |
| HP | Laptops | `HP-LAP-1`, `HP-LAP-2`, `HP-LAP-3`, `HP-LAP-4`, `HP-LAP-5`, `HP-LAP-6` | General laptop; no verified subtype/specification | Same seed template only; actual family and variant relationships unknown. |
| Lenovo | Laptops | `LEN-LAP-1`, `LEN-LAP-2`, `LEN-LAP-3`, `LEN-LAP-4`, `LEN-LAP-5`, `LEN-LAP-6` | General laptop; no verified subtype/specification | Same seed template only; actual family and variant relationships unknown. |
| Lenovo | Tablets | `LEN-TAB-1`, `LEN-TAB-2`, `LEN-TAB-3`, `LEN-TAB-4`, `LEN-TAB-5`, `LEN-TAB-6` | General tablet; no verified subtype/specification | Same seed template only; actual family and variant relationships unknown. |
| Asus | Laptops | `ASU-LAP-1`, `ASU-LAP-2`, `ASU-LAP-3`, `ASU-LAP-4`, `ASU-LAP-5`, `ASU-LAP-6` | General laptop; no verified subtype/specification | Same seed template only; actual family and variant relationships unknown. |
| OnePlus | Smartphones | `ONE-SMA-1`, `ONE-SMA-2`, `ONE-SMA-3`, `ONE-SMA-4`, `ONE-SMA-5`, `ONE-SMA-6` | General smartphone; no verified subtype/specification | Same seed template only; actual family and variant relationships unknown. |
| OnePlus | Audio | `ONE-AUD-1`, `ONE-AUD-2`, `ONE-AUD-3`, `ONE-AUD-4`, `ONE-AUD-5`, `ONE-AUD-6` | Audio product — subtype unclassified | Same seed template only; cannot tell audio product type. |
| Xiaomi | Smartphones | `XIA-SMA-1`, `XIA-SMA-2`, `XIA-SMA-3`, `XIA-SMA-4`, `XIA-SMA-5`, `XIA-SMA-6` | General smartphone; no verified subtype/specification | Same seed template only; actual family and variant relationships unknown. |
| Xiaomi | Televisions | `XIA-TEL-1`, `XIA-TEL-2`, `XIA-TEL-3`, `XIA-TEL-4`, `XIA-TEL-5`, `XIA-TEL-6` | General television; display technology/size unknown | Same seed template only; actual family and variant relationships unknown. |
| boAt | Audio | `BOA-AUD-1`, `BOA-AUD-2`, `BOA-AUD-3`, `BOA-AUD-4`, `BOA-AUD-5`, `BOA-AUD-6` | Audio product — subtype unclassified | Same seed template only; cannot tell headphones, earbuds, speakers, or another type. |
| Canon | Cameras | `CAN-CAM-1`, `CAN-CAM-2`, `CAN-CAM-3`, `CAN-CAM-4`, `CAN-CAM-5`, `CAN-CAM-6` | Camera — subtype unclassified | Same seed template only; camera type/lens/bundle unknown. |

### 3.1 Family and related-SKU assessment

- The generator yields six rows sharing each brand/category abbreviation prefix.
  This is a **synthetic grouping**, not evidence that the products are members
  of a real manufacturer family.
- Suffixes `1`–`6` do not encode RAM, storage, screen size, color, generation,
  bundle, or any other known variant dimension.
- The current source cannot identify duplicate real-world products, parent
  models, compatible accessories, replacement products, or variant SKUs.
- Do not create family assignments or change model/SKU values from these codes.
  Retrieve and match an authoritative catalog first.

## 4. Product classification and lifecycle conclusions

### 4.1 Categories and subcategories

The existing category is useful for broad inventory analytics, but not enough
for future rules that need defensible distinctions such as headphone vs.
speaker, refrigerator vs. washer, or camera body vs. lens kit. The seed's
generated product name only repeats a generic item type. Therefore:

- A top-level subcategory can be safely proposed from the existing broad type
  (for example, general smartphone or general laptop).
- The generic classes under `Audio`, `Appliances`, and `Cameras` must remain
  explicitly unclassified until the business provides authoritative data.
- Do not map high-level brand/category/code groups to imagined product lines.
- Keep unclassified values `NULL`; do not use a fabricated category such as
  “Other” to conceal missing knowledge.

### 4.2 Launch date and active flag

`launch_date` is non-null in the DDL, but the generation script creates it using
a random offset from a hard-coded date. It is not proven to be the actual
commercial launch date. `is_active` is a required Boolean with default `1`,
and the seed insert omits it; that only makes all generated rows active by
default, not proof that the flag means “currently orderable” or “not
discontinued.”

Conclusion: neither field currently supports reliable lifecycle classification
without live validation and business definitions. Do not infer lifecycle
statuses from age, sales recency, `is_active`, or synthetic launch dates.
Clarify whether a product can be active but not sellable, discontinued but
still held in inventory, or temporarily unavailable. Only then consider adding
authoritative fields such as lifecycle status or effective dates.

## 5. Attributes needed and proposed schema

### 5.1 Minimal product-master additions

For current inventory arithmetic, no new product attribute is required:
quantity, price/cost, product key, and current dimensions already support the
existing calculations. For meaningful category-aware analysis, the only
generally justified addition is a controlled, nullable subcategory reference.

For family-aware analytics, a nullable family reference is useful only after
authoritative family membership is supplied. It must not be populated from
synthetic model codes.

| Proposed attribute | Need | Initial population |
|---|---|---|
| `subcategory_id` | Distinguishes validated product classes below the broad current category; enables consistent grouping and future scoped rules. | Nullable; assign only confirmed mappings. |
| `product_family_id` | Enables within-family rollups/comparisons where real related-SKU membership is confirmed. | Nullable; leave unset until product owner/catalog source confirms relationships. |
| Lifecycle status/dates | Potentially needed for launch/discontinuation reporting, but not justified until definitions and authoritative dates are available. | Do not add in this migration. |
| Technical specifications (storage, display size, color, etc.) | Not needed for current stock/sales/DOI calculations; no trustworthy values in current generator. | Do not add until a defined analytics use case and source exist. |
| Unit-of-measure / pack size | Needed only if catalog validation finds quantities are not comparable in a common unit. | Do not add based on current integer columns alone. |

### 5.2 Backward-compatible SQL Server proposal

The following is design-only SQL. It has **not** been executed. The existing
product and fact rows remain unchanged; new reference columns are nullable.
The composite subcategory foreign key ensures a subcategory belongs to the
product's existing top-level `category`. Family membership similarly guards
brand/category alignment. Validate names, collations, exact DDL compatibility,
permissions, and SQL Server version before deployment.

```sql
CREATE TABLE dbo.ProductSubcategories (
    subcategory_id   SMALLINT IDENTITY(1,1) NOT NULL
        CONSTRAINT PK_ProductSubcategories PRIMARY KEY,
    category         NVARCHAR(50) NOT NULL,
    subcategory_name NVARCHAR(100) NOT NULL,
    CONSTRAINT UQ_ProductSubcategories_CategoryName
        UNIQUE (category, subcategory_name),
    CONSTRAINT UQ_ProductSubcategories_IdCategory
        UNIQUE (subcategory_id, category)
);
GO

ALTER TABLE dbo.Products
ADD subcategory_id SMALLINT NULL;
GO

ALTER TABLE dbo.Products
ADD CONSTRAINT FK_Products_ProductSubcategories
    FOREIGN KEY (subcategory_id, category)
    REFERENCES dbo.ProductSubcategories (subcategory_id, category);
GO

CREATE INDEX IX_Products_subcategory_id
    ON dbo.Products(subcategory_id)
    WHERE subcategory_id IS NOT NULL;
GO

CREATE TABLE dbo.ProductFamilies (
    product_family_id INT IDENTITY(1,1) NOT NULL
        CONSTRAINT PK_ProductFamilies PRIMARY KEY,
    brand             NVARCHAR(50) NOT NULL,
    category          NVARCHAR(50) NOT NULL,
    family_name       NVARCHAR(100) NOT NULL,
    CONSTRAINT UQ_ProductFamilies_BrandCategoryName
        UNIQUE (brand, category, family_name),
    CONSTRAINT UQ_ProductFamilies_IdBrandCategory
        UNIQUE (product_family_id, brand, category)
);
GO

ALTER TABLE dbo.Products
ADD product_family_id INT NULL;
GO

ALTER TABLE dbo.Products
ADD CONSTRAINT FK_Products_ProductFamilies
    FOREIGN KEY (product_family_id, brand, category)
    REFERENCES dbo.ProductFamilies (product_family_id, brand, category);
GO

CREATE INDEX IX_Products_product_family_id
    ON dbo.Products(product_family_id)
    WHERE product_family_id IS NOT NULL;
GO
```

No rows are proposed for either lookup table in this blueprint. In particular,
the family table should remain empty until approved family assignments exist.
Subcategory lookup rows and product assignments should likewise be loaded only
from a reviewed mapping to actual product IDs/SKUs.

### 5.3 Future business-rule scoping

Existing `BusinessRules` is one global value per `rule_key`; it cannot express
different approved values by category, subcategory, or product. Do not add
threshold columns to every product. If scoped rules are later approved, use a
separate rule-scope/override design, with explicit scope type/key, validity
period, uniqueness, and documented precedence (global → category →
subcategory → family/product, or another approved ordering). This is a
separate change requiring calculation-layer and conflict-resolution design;
it is not part of the product-master migration proposed here.

## 6. Migration plan (for a later approved change)

1. **Obtain read-only live extracts.** Record product IDs/SKUs and all current
   product fields, metadata, constraints, and FK row counts. Confirm the live
   database target and backup/change process without reading `.env`.
2. **Reconcile the catalog.** Compare live records against authoritative
   product-source data. Identify duplicates, ambiguous names, synthetic model
   codes, invalid or stale launch dates, and actual active-flag semantics.
   Preserve an exception list; do not infer missing facts.
3. **Approve taxonomy and family definitions.** Confirm controlled subcategory
   names, category ownership, family criteria, and how unknown/unclassified
   products appear in reports. Require business-owner sign-off on each
   product-ID/SKU mapping.
4. **Stage and review mapping.** Prepare a mapping keyed by stable
   `product_id` and `sku`, with source, reviewer, and confidence/provenance.
   Reject duplicate/contradictory assignments and verify every proposed row
   joins to exactly one live product.
5. **Deploy additive schema only.** Create lookup tables and nullable FK
   columns/indexes in a transaction/change window, after checking the actual
   schema and deployment permissions. No existing IDs, SKUs, fact rows, or
   foreign keys are to be rewritten.
6. **Load only approved mappings.** Insert validated lookup values and update
   only nullable classification references for reviewed products. Keep
   unresolved products null. Do not update launch dates, `is_active`, names,
   SKUs, or model values as part of classification.
7. **Validate and observe.** Run the checklist in §7, compare pre/post counts,
   test application queries/views, then separately authorize any use of new
   attributes in analytics or rules.

## 7. Read-only inspection and validation checklist

All queries below are proposed checks for a future authorized connection.
They were not executed. Restrict returned product data to approved users; no
credentials are included here.

### 7.1 Establish actual schema and catalog

```sql
SELECT
    c.column_id,
    c.name AS column_name,
    TYPE_NAME(c.user_type_id) AS data_type,
    c.max_length,
    c.precision,
    c.scale,
    c.is_nullable,
    c.is_identity
FROM sys.columns AS c
WHERE c.object_id = OBJECT_ID(N'dbo.Products')
ORDER BY c.column_id;

SELECT
    p.product_id, p.sku, p.product_name, p.brand, p.category, p.model,
    p.launch_date, p.is_active, p.cost_price, p.selling_price
FROM dbo.Products AS p
ORDER BY p.product_id;

SELECT
    COUNT(*) AS product_count,
    COUNT(DISTINCT product_id) AS distinct_product_ids,
    COUNT(DISTINCT sku) AS distinct_skus,
    MIN(product_id) AS min_product_id,
    MAX(product_id) AS max_product_id
FROM dbo.Products;
```

### 7.2 Duplicate and ambiguous labels

```sql
SELECT sku, COUNT(*) AS row_count
FROM dbo.Products
GROUP BY sku
HAVING COUNT(*) > 1;

SELECT brand, category, model, COUNT(*) AS row_count,
       COUNT(DISTINCT product_name) AS distinct_names
FROM dbo.Products
GROUP BY brand, category, model
HAVING COUNT(*) > 1;

SELECT product_name, COUNT(*) AS row_count
FROM dbo.Products
GROUP BY product_name
HAVING COUNT(*) > 1;

SELECT brand, category, COUNT(*) AS products
FROM dbo.Products
GROUP BY brand, category
ORDER BY brand, category;
```

### 7.3 Launch-date and active-flag profile

```sql
SELECT
    is_active,
    COUNT(*) AS products,
    MIN(launch_date) AS first_launch_date,
    MAX(launch_date) AS last_launch_date,
    SUM(CASE WHEN launch_date > CAST(GETDATE() AS DATE) THEN 1 ELSE 0 END)
        AS future_launch_dates
FROM dbo.Products
GROUP BY is_active
ORDER BY is_active;

SELECT product_id, sku, product_name, launch_date, is_active
FROM dbo.Products
WHERE launch_date > CAST(GETDATE() AS DATE)
ORDER BY launch_date, product_id;
```

These checks describe distributions only. They cannot establish authenticity
or the business meaning of `is_active`.

### 7.4 Verify preservation of dependent records

Capture these counts before and after any later migration and require exact
equality. These queries do not mutate data.

```sql
SELECT 'Products' AS table_name, COUNT_BIG(*) AS row_count
FROM dbo.Products
UNION ALL
SELECT 'Inventory', COUNT_BIG(*) FROM dbo.Inventory
UNION ALL
SELECT 'Sales', COUNT_BIG(*) FROM dbo.Sales
UNION ALL
SELECT 'InventorySnapshotWeekly', COUNT_BIG(*) FROM dbo.InventorySnapshotWeekly
UNION ALL
SELECT 'SupplierProducts', COUNT_BIG(*) FROM dbo.SupplierProducts
UNION ALL
SELECT 'PurchaseOrders', COUNT_BIG(*) FROM dbo.PurchaseOrders;

SELECT
    fk.name AS foreign_key_name,
    OBJECT_SCHEMA_NAME(fk.parent_object_id) AS child_schema,
    OBJECT_NAME(fk.parent_object_id) AS child_table,
    COL_NAME(fkc.parent_object_id, fkc.parent_column_id) AS child_column,
    OBJECT_SCHEMA_NAME(fk.referenced_object_id) AS parent_schema,
    OBJECT_NAME(fk.referenced_object_id) AS parent_table,
    COL_NAME(fkc.referenced_object_id, fkc.referenced_column_id) AS parent_column,
    fk.is_disabled,
    fk.is_not_trusted
FROM sys.foreign_keys AS fk
JOIN sys.foreign_key_columns AS fkc
  ON fkc.constraint_object_id = fk.object_id
WHERE fk.referenced_object_id = OBJECT_ID(N'dbo.Products')
ORDER BY child_table, foreign_key_name, fkc.constraint_column_id;
```

### 7.5 Validate proposed schema after an approved migration

```sql
-- No assignments may point to nonexistent/wrong-category subcategories.
SELECT p.product_id, p.sku, p.category, p.subcategory_id
FROM dbo.Products AS p
LEFT JOIN dbo.ProductSubcategories AS s
  ON s.subcategory_id = p.subcategory_id
 AND s.category = p.category
WHERE p.subcategory_id IS NOT NULL
  AND s.subcategory_id IS NULL;

-- No family assignment may cross brand/category.
SELECT p.product_id, p.sku, p.brand, p.category, p.product_family_id
FROM dbo.Products AS p
LEFT JOIN dbo.ProductFamilies AS f
  ON f.product_family_id = p.product_family_id
 AND f.brand = p.brand
 AND f.category = p.category
WHERE p.product_family_id IS NOT NULL
  AND f.product_family_id IS NULL;

SELECT COUNT(*) AS products_without_subcategory
FROM dbo.Products
WHERE subcategory_id IS NULL;

SELECT COUNT(*) AS products_without_family
FROM dbo.Products
WHERE product_family_id IS NULL;
```

Expected behavior: the first two checks return no rows. Null counts are
informational; they must not be forced to zero where source evidence is
missing.

## 8. Validation and compatibility checklist

- [ ] Verify actual server/database identity and obtain a read-only catalog
  extract; do not inspect backend `.env`.
- [ ] Confirm the live product count and stable unique `product_id`/SKU.
- [ ] Confirm seed-generated labels against actual products; do not assume
  product IDs correspond to listed model-code suffixes.
- [ ] Obtain authoritative product source and provenance for each proposed
  subcategory/family.
- [ ] Keep unresolved or ambiguous classifications null.
- [ ] Confirm no existing product, inventory, sales, snapshot, supplier-link,
  or PO records are updated or deleted.
- [ ] Confirm existing primary keys and foreign keys remain unchanged.
- [ ] Confirm new lookup constraints enforce valid subcategory/category and
  family/brand/category combinations.
- [ ] Confirm current repository selects and views still return the same
  columns and values; expose new columns only in a separately reviewed
  analytics change.
- [ ] Confirm row counts and fact-table aggregates match pre-migration
  baselines.
- [ ] Test rollback in a non-production copy before any production deployment.
- [ ] Obtain business approval for lifecycle definitions and any future
  category/product rule override precedence.

## 9. Decisions needed before implementation

1. Which authoritative catalog source should determine real product names,
   model numbers, variants, and subcategories?
2. What is the agreed controlled taxonomy below the current broad categories?
   In particular, how should `Audio`, `Appliances`, and `Cameras` be divided?
3. What evidence defines a product family, and who approves family membership?
4. What does `is_active` mean operationally, and how should discontinued,
   temporarily unavailable, and sellable-but-not-stocked products be handled?
5. Are current `launch_date` values synthetic in the live database, and should
   they be replaced only from an authoritative source?
6. Which future business rules need category/subcategory/family/product scope,
   and what is the approved precedence when scopes overlap?
7. Are all inventory and sales quantities in comparable eaches, or is a
   unit-of-measure/pack-size model actually needed?

## 10. Recommendation

Do not assign the generated six-code groups as real families or infer
specifications/lifecycle states. First obtain the actual product extract and an
authoritative catalog mapping. Subject to that review, the smallest useful
additive change is a nullable, category-constrained subcategory reference.
Add the nullable family reference only if validated family-level reporting is
an approved requirement. Defer lifecycle, specification, unit-of-measure, and
scoped-rule changes until their data sources and business semantics are
confirmed.

**End of blueprint — awaiting review; no database objects or data were changed.**

## Final review supersession note — 2026-10-08

The live-catalog review in [PRODUCT_SUBCATEGORY_REVIEW_SUMMARY.md](PRODUCT_SUBCATEGORY_REVIEW_SUMMARY.md) and the revised mapping in [PRODUCT_MASTER_SUBCATEGORY_MAPPING.csv](PRODUCT_MASTER_SUBCATEGORY_MAPPING.csv) supersede the earlier script-derived mapping in §§1 and 3. The live CSV is authoritative for this review. The current proposal intentionally leaves 110 products unclassified and retains only conservative name-supported subcategories. The earlier optional `product_family_id` design is deferred; the first migration proposal is limited to `ProductSubcategories`, nullable `subcategory_id`, category-constrained foreign key, and index. No database changes have been made.
## Revision 2026-10-08 — authoritative live catalog extract

This revision supersedes any product-name assumptions derived from the original generation script for the current demo catalog. The authoritative review source is `C:\Users\sai.telaprolu\Downloads\inventory_product_catalog.csv`; it was read only and no database or `.env` file was accessed.

### 1. Extract validation

| Check | Result |
|---|---:|
| Header set | `product_id`, `sku`, `product_name`, `brand`, `category` |
| Records | 150 |
| Blank required fields | 0 |
| Unique product IDs | 150 / 150 |
| Unique SKUs | 150 / 150 |
| Unique product names | 150 / 150 |
| Brands | 12 |
| Broad categories | 7 |

Category counts are Audio 36, Laptops 30, Smartphones 24, Televisions 24, Tablets 18, Cameras 12, and Appliances 6. The ID range is 1–150 and the SKU range is `SKU-0001`–`SKU-0150`. Existing IDs, SKUs, names, brands, categories, relationships, and fact records must remain unchanged.

The complete proposed row-level mapping is in [PRODUCT_MASTER_SUBCATEGORY_MAPPING.csv](PRODUCT_MASTER_SUBCATEGORY_MAPPING.csv). It is keyed by the stable ID and SKU so it can be reconciled directly to the extract before any approved migration.

### 2. Mapping policy

The mapping is a proposal for review, not a claim that the CSV contains authoritative specifications. It uses only explicit product-name evidence:

- Smartphones, laptops, tablets, televisions, and cameras default to `* — named model`; no RAM, storage, display size, lens, generation, or lifecycle facts are inferred.
- Explicit names support conservative classes such as Air conditioner, Refrigerator, Washer, Dryer, Soundbar, Headphones, Earbuds, Portable speaker, and Foldable smartphone.
- Audio model families such as `WH-*`, `WF-*`, `Buds`, `Airdopes`, and `XBOOM` still require taxonomy-owner confirmation where the name does not explicitly state the subtype.
- Every row is marked `Proposed - review required`; variant rows containing `Variant 2` require an authoritative source to establish whether they are distinct sellable variants, duplicate labels, or synthetic rows.
- No product family, lifecycle, launch-date, technical specification, or replacement relationship is proposed.

### 3. Ambiguities requiring clarification

1. `Variant 2` names (IDs 96 and 101–150 where present) do not prove a real-world variant dimension. Confirm whether these are distinct catalog items and what differentiates them.
2. `Xiaomi XIA-TEL-*` names (IDs 23, 48, 73, 98, 123, 148) expose a generated-looking code rather than a verified commercial model name. Keep the name unchanged and classify only as a named television pending source validation.
3. Audio names such as `WH-1000XM5`, `WF-1000XM5`, `Airdopes`, `Rockerz`, `Stone`, `Nirvana`, `XBOOM`, and `ULT` suggest families but do not establish the approved subcategory taxonomy or bundle/pack meaning.
4. Camera names identify product families/models but do not establish body, lens kit, bundle, or sensor specifications.
5. Named television and laptop products do not establish screen size, panel technology, CPU, memory, storage, or generation beyond the literal name.
6. No evidence in this extract establishes lifecycle status, launch dates, active/sellable meaning, family membership, or unit of measure.

### 4. Minimal backward-compatible schema proposal

Keep the existing `dbo.Products` columns and all relationships unchanged. If approved, add only nullable classification references:

- `subcategory_id SMALLINT NULL` referencing a controlled `ProductSubcategories` lookup constrained to the existing top-level `category`.
- Defer `product_family_id` until family membership is confirmed from an authoritative source; if approved later, add it nullable and constrain it to the existing `brand` + `category`.
- Do not add or populate lifecycle, technical-specification, launch-date, active-state, or unit-of-measure fields in this change.
- Do not populate lookup rows or update products until each mapping has business-owner approval and provenance.

This is a design-only proposal. No DDL, data updates, or database migrations were executed.

### 5. Review and approval sequence

1. Review the 150-row mapping and resolve the ambiguity list.
2. Confirm the controlled subcategory vocabulary and owner approval process.
3. Reconcile every mapping row against the live extract by both `product_id` and SKU.
4. Approve additive nullable schema only, with pre/post row-count and foreign-key checks.
5. Load only approved subcategory assignments; leave unresolved products `NULL`.

