/* Read-only validation; safe before or after migration. */
SET NOCOUNT ON;
SELECT DB_NAME() AS database_name, @@SERVERNAME AS server_name, @@VERSION AS sql_server_version;

SELECT 'Products' AS table_name, COUNT_BIG(*) AS row_count FROM dbo.Products
UNION ALL SELECT 'Inventory', COUNT_BIG(*) FROM dbo.Inventory
UNION ALL SELECT 'Sales', COUNT_BIG(*) FROM dbo.Sales
UNION ALL SELECT 'SupplierProducts', COUNT_BIG(*) FROM dbo.SupplierProducts
UNION ALL SELECT 'PurchaseOrders', COUNT_BIG(*) FROM dbo.PurchaseOrders
UNION ALL SELECT 'InventorySnapshotWeekly', COUNT_BIG(*) FROM dbo.InventorySnapshotWeekly;

/* Stable SHA-256 fingerprints; compare exact values before and after. */
DECLARE @ProductIdentityXml XML, @ProductCatalogXml XML;
SELECT @ProductIdentityXml = (SELECT CONCAT(CONVERT(varchar(20), p.product_id), '|', COALESCE(p.sku, ''), ';')
    FROM dbo.Products AS p ORDER BY p.product_id FOR XML PATH(''), TYPE);
SELECT @ProductCatalogXml = (SELECT CONCAT(CONVERT(varchar(20), p.product_id), '|', COALESCE(p.sku, ''), '|',
    p.product_name, '|', p.brand, '|', p.category, '|', p.model, ';')
    FROM dbo.Products AS p ORDER BY p.product_id FOR XML PATH(''), TYPE);
SELECT HASHBYTES('SHA2_256', @ProductIdentityXml.value('.', 'nvarchar(max)')) AS product_id_sku_sha256,
       HASHBYTES('SHA2_256', @ProductCatalogXml.value('.', 'nvarchar(max)')) AS product_catalog_sha256;

/* Fact-table row counts and aggregate fingerprints. */
SELECT 'Inventory' AS table_name, COUNT_BIG(*) AS row_count,
       COALESCE(SUM(CONVERT(bigint, quantity_on_hand)), 0) AS quantity_sum,
       COALESCE(SUM(CONVERT(bigint, reorder_level)), 0) AS reorder_sum,
       COALESCE(SUM(CONVERT(bigint, max_level)), 0) AS max_level_sum,
       MIN(as_of_date) AS first_date, MAX(as_of_date) AS last_date FROM dbo.Inventory
UNION ALL SELECT 'InventorySnapshotWeekly', COUNT_BIG(*), COALESCE(SUM(CONVERT(bigint, quantity_on_hand)), 0), NULL, NULL,
       MIN(snapshot_date), MAX(snapshot_date) FROM dbo.InventorySnapshotWeekly
UNION ALL SELECT 'Sales', COUNT_BIG(*), COALESCE(SUM(CONVERT(bigint, quantity)), 0), NULL, NULL,
       MIN(sale_date), MAX(sale_date) FROM dbo.Sales
UNION ALL SELECT 'SupplierProducts', COUNT_BIG(*), COALESCE(SUM(CONVERT(bigint, lead_time_days)), 0), NULL, NULL, NULL, NULL
       FROM dbo.SupplierProducts
UNION ALL SELECT 'PurchaseOrders', COUNT_BIG(*), COALESCE(SUM(CONVERT(bigint, quantity)), 0), NULL, NULL,
       MIN(order_date), MAX(order_date) FROM dbo.PurchaseOrders;
SELECT 'Sales' AS table_name, COALESCE(SUM(revenue), 0) AS revenue_sum FROM dbo.Sales;

SELECT c.name AS column_name, TYPE_NAME(c.user_type_id) AS data_type, c.max_length, c.is_nullable, c.is_identity
FROM sys.columns AS c WHERE c.object_id = OBJECT_ID(N'dbo.Products') ORDER BY c.column_id;
SELECT i.name AS index_name, i.type_desc, i.is_unique, i.is_primary_key, ic.key_ordinal,
       COL_NAME(ic.object_id, ic.column_id) AS column_name
FROM sys.indexes AS i LEFT JOIN sys.index_columns AS ic ON ic.object_id = i.object_id AND ic.index_id = i.index_id
WHERE i.object_id = OBJECT_ID(N'dbo.Products') ORDER BY i.name, ic.key_ordinal;
SELECT fk.name AS foreign_key_name, OBJECT_NAME(fk.parent_object_id) AS child_table,
       OBJECT_NAME(fk.referenced_object_id) AS parent_table, fk.is_disabled, fk.is_not_trusted,
       COL_NAME(fkc.parent_object_id, fkc.parent_column_id) AS child_column,
       COL_NAME(fkc.referenced_object_id, fkc.referenced_column_id) AS parent_column,
       fkc.constraint_column_id AS column_ordinal
FROM sys.foreign_keys AS fk JOIN sys.foreign_key_columns AS fkc ON fkc.constraint_object_id = fk.object_id
WHERE fk.parent_object_id = OBJECT_ID(N'dbo.Products') OR fk.referenced_object_id = OBJECT_ID(N'dbo.Products')
ORDER BY fk.name, fkc.constraint_column_id;

/* Existing fact-table FK integrity: every orphan_rows value should be zero. */
SELECT 'Inventory' AS table_name, COUNT_BIG(*) AS orphan_rows FROM dbo.Inventory AS x LEFT JOIN dbo.Products AS p ON p.product_id=x.product_id WHERE p.product_id IS NULL
UNION ALL SELECT 'InventorySnapshotWeekly', COUNT_BIG(*) FROM dbo.InventorySnapshotWeekly AS x LEFT JOIN dbo.Products AS p ON p.product_id=x.product_id WHERE p.product_id IS NULL
UNION ALL SELECT 'Sales', COUNT_BIG(*) FROM dbo.Sales AS x LEFT JOIN dbo.Products AS p ON p.product_id=x.product_id WHERE p.product_id IS NULL
UNION ALL SELECT 'SupplierProducts', COUNT_BIG(*) FROM dbo.SupplierProducts AS x LEFT JOIN dbo.Products AS p ON p.product_id=x.product_id WHERE p.product_id IS NULL
UNION ALL SELECT 'PurchaseOrders', COUNT_BIG(*) FROM dbo.PurchaseOrders AS x LEFT JOIN dbo.Products AS p ON p.product_id=x.product_id WHERE p.product_id IS NULL;

/* Supplier/store FK integrity checks. */
SELECT 'SupplierProducts.supplier_id' AS relationship, COUNT_BIG(*) AS orphan_rows
FROM dbo.SupplierProducts AS x LEFT JOIN dbo.Suppliers AS s ON s.supplier_id=x.supplier_id WHERE s.supplier_id IS NULL
UNION ALL SELECT 'PurchaseOrders.supplier_id', COUNT_BIG(*)
FROM dbo.PurchaseOrders AS x LEFT JOIN dbo.Suppliers AS s ON s.supplier_id=x.supplier_id WHERE s.supplier_id IS NULL
UNION ALL SELECT 'Inventory.store_id', COUNT_BIG(*)
FROM dbo.Inventory AS x LEFT JOIN dbo.Stores AS s ON s.store_id=x.store_id WHERE s.store_id IS NULL
UNION ALL SELECT 'InventorySnapshotWeekly.store_id', COUNT_BIG(*)
FROM dbo.InventorySnapshotWeekly AS x LEFT JOIN dbo.Stores AS s ON s.store_id=x.store_id WHERE s.store_id IS NULL
UNION ALL SELECT 'Sales.store_id', COUNT_BIG(*)
FROM dbo.Sales AS x LEFT JOIN dbo.Stores AS s ON s.store_id=x.store_id WHERE s.store_id IS NULL;

SELECT COUNT_BIG(*) AS duplicate_product_ids FROM (SELECT product_id FROM dbo.Products GROUP BY product_id HAVING COUNT(*) > 1) AS d;
SELECT COUNT_BIG(*) AS duplicate_skus FROM (SELECT sku FROM dbo.Products GROUP BY sku HAVING COUNT(*) > 1) AS d;
SELECT CASE WHEN OBJECT_ID(N'dbo.ProductSubcategories', N'U') IS NULL THEN 'not installed' ELSE 'installed' END AS migration_state;

IF OBJECT_ID(N'dbo.ProductSubcategories', N'U') IS NOT NULL
BEGIN
    SELECT c.name AS column_name, TYPE_NAME(c.user_type_id) AS data_type, c.max_length, c.is_nullable, c.is_identity
    FROM sys.columns AS c WHERE c.object_id=OBJECT_ID(N'dbo.ProductSubcategories') ORDER BY c.column_id;
    SELECT i.name AS index_name, i.is_unique, i.is_primary_key, ic.key_ordinal, COL_NAME(ic.object_id, ic.column_id) AS column_name
    FROM sys.indexes AS i LEFT JOIN sys.index_columns AS ic ON ic.object_id=i.object_id AND ic.index_id=i.index_id
    WHERE i.object_id=OBJECT_ID(N'dbo.ProductSubcategories') ORDER BY i.name, ic.key_ordinal;
    SELECT fk.name AS foreign_key_name, OBJECT_NAME(fk.parent_object_id) AS child_table, OBJECT_NAME(fk.referenced_object_id) AS parent_table,
           COL_NAME(fkc.parent_object_id, fkc.parent_column_id) AS child_column, COL_NAME(fkc.referenced_object_id, fkc.referenced_column_id) AS parent_column,
           fkc.constraint_column_id AS column_ordinal
    FROM sys.foreign_keys AS fk JOIN sys.foreign_key_columns AS fkc ON fkc.constraint_object_id=fk.object_id
    WHERE fk.referenced_object_id=OBJECT_ID(N'dbo.ProductSubcategories') ORDER BY fk.name, fkc.constraint_column_id;
END;

/*
   Post-migration checks are dynamic because SQL Server compiles column names
   in a static batch even when an enclosing IF is false. This keeps the
   pre-migration schema (which has no Products.subcategory_id) valid while
   retaining strict checks whenever both objects exist.
*/
IF COL_LENGTH(N'dbo.Products', N'subcategory_id') IS NOT NULL
   AND OBJECT_ID(N'dbo.ProductSubcategories', N'U') IS NOT NULL
BEGIN
    DECLARE @subcategory_validation_sql nvarchar(max) = N'
        SELECT p.product_id, p.sku, p.category, p.subcategory_id
        FROM dbo.Products AS p
        LEFT JOIN dbo.ProductSubcategories AS s
          ON s.subcategory_id=p.subcategory_id AND s.category=p.category
        WHERE p.subcategory_id IS NOT NULL AND s.subcategory_id IS NULL;

        SELECT p.product_id, p.sku, p.subcategory_id, s.category AS subcategory_parent
        FROM dbo.Products AS p
        JOIN dbo.ProductSubcategories AS s ON s.subcategory_id=p.subcategory_id
        WHERE p.category<>s.category;

        SELECT COUNT_BIG(*) AS assigned_products
        FROM dbo.Products WHERE subcategory_id IS NOT NULL;';
    EXEC sys.sp_executesql @subcategory_validation_sql;
END
ELSE
    SELECT CAST(0 AS bigint) AS assigned_products;

/* Existing application projection remains valid. */
SELECT TOP (0) product_id, sku, product_name, brand, category, model FROM dbo.Products WHERE is_active=1;
