/*
    InventoryAI - Product subcategory schema migration
    Design-only package. Execute only after review and approval.
    No product, inventory, sales, supplier, PO, or rule data is changed.
*/
SET XACT_ABORT ON;
SET NOCOUNT ON;

BEGIN TRY
    BEGIN TRANSACTION;

    IF OBJECT_ID(N'dbo.Products', N'U') IS NULL
        THROW 51000, 'Required table dbo.Products does not exist.', 1;

    IF NOT EXISTS (
        SELECT 1 FROM sys.columns
        WHERE object_id = OBJECT_ID(N'dbo.Products')
          AND name = N'category'
          AND system_type_id = TYPE_ID(N'nvarchar')
          AND max_length = 100
          AND is_nullable = 0
    )
        THROW 51001, 'dbo.Products.category is not the expected NVARCHAR(50) NOT NULL column.', 1;

    IF OBJECT_ID(N'dbo.ProductSubcategories', N'U') IS NULL
    BEGIN
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
    END
    ELSE
    BEGIN
        IF (SELECT COUNT(*) FROM sys.columns WHERE object_id = OBJECT_ID(N'dbo.ProductSubcategories')) <> 3
            THROW 51002, 'Existing dbo.ProductSubcategories has unexpected columns.', 1;
        IF NOT EXISTS (SELECT 1 FROM sys.columns WHERE object_id = OBJECT_ID(N'dbo.ProductSubcategories') AND name = N'subcategory_id')
            THROW 51003, 'Existing dbo.ProductSubcategories is incompatible: subcategory_id is missing.', 1;
        IF NOT EXISTS (SELECT 1 FROM sys.columns WHERE object_id = OBJECT_ID(N'dbo.ProductSubcategories') AND name = N'category')
            THROW 51004, 'Existing dbo.ProductSubcategories is incompatible: category is missing.', 1;
        IF NOT EXISTS (SELECT 1 FROM sys.columns WHERE object_id = OBJECT_ID(N'dbo.ProductSubcategories') AND name = N'subcategory_name')
            THROW 51005, 'Existing dbo.ProductSubcategories is incompatible: subcategory_name is missing.', 1;
        IF EXISTS (SELECT 1 FROM sys.columns WHERE object_id = OBJECT_ID(N'dbo.ProductSubcategories') AND name = N'subcategory_id' AND (system_type_id <> TYPE_ID(N'smallint') OR is_nullable <> 0 OR is_identity <> 1))
            THROW 51006, 'Existing subcategory_id must be IDENTITY SMALLINT NOT NULL.', 1;
        IF EXISTS (SELECT 1 FROM sys.columns WHERE object_id = OBJECT_ID(N'dbo.ProductSubcategories') AND name = N'category' AND (system_type_id <> TYPE_ID(N'nvarchar') OR max_length <> 100 OR is_nullable <> 0))
            THROW 51007, 'Existing category must be NVARCHAR(50) NOT NULL.', 1;
        IF EXISTS (SELECT 1 FROM sys.columns WHERE object_id = OBJECT_ID(N'dbo.ProductSubcategories') AND name = N'subcategory_name' AND (system_type_id <> TYPE_ID(N'nvarchar') OR max_length <> 200 OR is_nullable <> 0))
            THROW 51008, 'Existing subcategory_name must be NVARCHAR(100) NOT NULL.', 1;
        IF NOT EXISTS (
            SELECT 1 FROM sys.indexes i JOIN sys.index_columns ic ON ic.object_id=i.object_id AND ic.index_id=i.index_id
            WHERE i.object_id=OBJECT_ID(N'dbo.ProductSubcategories') AND i.is_primary_key=1 AND ic.key_ordinal=1 AND COL_NAME(ic.object_id,ic.column_id)=N'subcategory_id'
        )
            THROW 51009, 'Existing ProductSubcategories primary key is not mapped to subcategory_id.', 1;
        IF NOT EXISTS (
            SELECT i.index_id FROM sys.indexes i
            JOIN sys.index_columns ic ON ic.object_id=i.object_id AND ic.index_id=i.index_id
            WHERE i.object_id=OBJECT_ID(N'dbo.ProductSubcategories') AND i.is_unique=1
            GROUP BY i.index_id HAVING COUNT(*)=2 AND SUM(CASE WHEN COL_NAME(ic.object_id,ic.column_id)=N'category' AND ic.key_ordinal=1 THEN 1 ELSE 0 END)=1 AND SUM(CASE WHEN COL_NAME(ic.object_id,ic.column_id)=N'subcategory_name' AND ic.key_ordinal=2 THEN 1 ELSE 0 END)=1
        )
            THROW 51010, 'Existing ProductSubcategories lacks the unique (category, subcategory_name) key.', 1;
        IF NOT EXISTS (
            SELECT i.index_id FROM sys.indexes i
            JOIN sys.index_columns ic ON ic.object_id=i.object_id AND ic.index_id=i.index_id
            WHERE i.object_id=OBJECT_ID(N'dbo.ProductSubcategories') AND i.is_unique=1
            GROUP BY i.index_id HAVING COUNT(*)=2 AND SUM(CASE WHEN COL_NAME(ic.object_id,ic.column_id)=N'subcategory_id' AND ic.key_ordinal=1 THEN 1 ELSE 0 END)=1 AND SUM(CASE WHEN COL_NAME(ic.object_id,ic.column_id)=N'category' AND ic.key_ordinal=2 THEN 1 ELSE 0 END)=1
        )
            THROW 51011, 'Existing ProductSubcategories lacks the unique (subcategory_id, category) key.', 1;
    END;

    IF EXISTS (
        SELECT 1 FROM sys.columns
        WHERE object_id = OBJECT_ID(N'dbo.Products') AND name = N'subcategory_id'
          AND (system_type_id <> TYPE_ID(N'smallint') OR is_nullable <> 1)
    )
        THROW 51012, 'Existing dbo.Products.subcategory_id is incompatible: expected nullable SMALLINT.', 1;

    IF NOT EXISTS (
        SELECT 1 FROM sys.columns
        WHERE object_id = OBJECT_ID(N'dbo.Products') AND name = N'subcategory_id'
    )
        EXEC sys.sp_executesql N'ALTER TABLE dbo.Products ADD subcategory_id SMALLINT NULL;';

    IF EXISTS (
        SELECT 1 FROM sys.foreign_keys
        WHERE referenced_object_id = OBJECT_ID(N'dbo.ProductSubcategories')
          AND NOT (parent_object_id = OBJECT_ID(N'dbo.Products') AND name = N'FK_Products_ProductSubcategories')
    )
        THROW 51013, 'Additional foreign-key dependencies on ProductSubcategories require manual review.', 1;

    IF EXISTS (
        SELECT 1 FROM sys.foreign_keys fk
        WHERE fk.parent_object_id = OBJECT_ID(N'dbo.Products')
          AND fk.name = N'FK_Products_ProductSubcategories'
          AND (SELECT COUNT(*) FROM sys.foreign_key_columns fkc WHERE fkc.constraint_object_id=fk.object_id) <> 2
    )
        THROW 51014, 'Existing ProductSubcategories foreign key has incompatible column mapping.', 1;

    IF EXISTS (
        SELECT 1 FROM sys.foreign_keys fk
        JOIN sys.foreign_key_columns fkc ON fkc.constraint_object_id=fk.object_id
        JOIN sys.columns pc ON pc.object_id=fkc.parent_object_id AND pc.column_id=fkc.parent_column_id
        JOIN sys.columns rc ON rc.object_id=fkc.referenced_object_id AND rc.column_id=fkc.referenced_column_id
        WHERE fk.parent_object_id = OBJECT_ID(N'dbo.Products')
          AND fk.name = N'FK_Products_ProductSubcategories'
          AND NOT ((fkc.constraint_column_id=1 AND pc.name=N'subcategory_id' AND rc.name=N'subcategory_id') OR (fkc.constraint_column_id=2 AND pc.name=N'category' AND rc.name=N'category'))
    )
        THROW 51015, 'Existing ProductSubcategories foreign key has incompatible column mapping.', 1;

    IF NOT EXISTS (
        SELECT 1
        FROM sys.foreign_keys
        WHERE parent_object_id = OBJECT_ID(N'dbo.Products')
          AND name = N'FK_Products_ProductSubcategories'
    )
    BEGIN
        /* Dynamic execution is required: the new column may have been added
           earlier in this transaction but was not present at batch compile. */
        EXEC sys.sp_executesql N'
            ALTER TABLE dbo.Products WITH CHECK
            ADD CONSTRAINT FK_Products_ProductSubcategories
                FOREIGN KEY (subcategory_id, category)
                REFERENCES dbo.ProductSubcategories (subcategory_id, category);
            ALTER TABLE dbo.Products CHECK CONSTRAINT FK_Products_ProductSubcategories;';
    END;

    IF NOT EXISTS (
        SELECT 1 FROM sys.indexes
        WHERE object_id = OBJECT_ID(N'dbo.Products')
          AND name = N'IX_Products_subcategory_id'
    )
        EXEC sys.sp_executesql N'
            CREATE INDEX IX_Products_subcategory_id
                ON dbo.Products(subcategory_id)
                WHERE subcategory_id IS NOT NULL;';

    COMMIT TRANSACTION;
    PRINT 'Product subcategory schema migration completed successfully.';
END TRY
BEGIN CATCH
    IF XACT_STATE() <> 0 ROLLBACK TRANSACTION;
    THROW;
END CATCH;
