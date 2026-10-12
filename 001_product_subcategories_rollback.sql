/*
    InventoryAI - product subcategory rollback
    Safe default: refuses to remove approved assignments.
    Set @AllowAssignmentRemoval = 1 only after an explicit rollback decision.
*/
SET XACT_ABORT ON;
SET NOCOUNT ON;

DECLARE @AllowAssignmentRemoval BIT = 0;
DECLARE @AllowLookupRemoval BIT = 0;
DECLARE @AllowDependencyRemoval BIT = 0;

BEGIN TRY
    BEGIN TRANSACTION;

    IF OBJECT_ID(N'dbo.Products', N'U') IS NULL
        THROW 51200, 'Required table dbo.Products does not exist.', 1;

    IF NOT EXISTS (SELECT 1 FROM sys.columns WHERE object_id = OBJECT_ID(N'dbo.Products') AND name = N'subcategory_id')
    BEGIN
        PRINT 'Rollback not required: subcategory_id is absent.';
        COMMIT TRANSACTION;
        RETURN;
    END;

    DECLARE @Assignments BIGINT = (SELECT COUNT_BIG(*) FROM dbo.Products WHERE subcategory_id IS NOT NULL);
    IF @Assignments > 0 AND @AllowAssignmentRemoval = 0
        THROW 51201, 'Rollback refused: approved subcategory assignments exist. Set @AllowAssignmentRemoval=1 only with explicit approval.', 1;

    IF OBJECT_ID(N'dbo.ProductSubcategories', N'U') IS NOT NULL
    BEGIN
        IF EXISTS (SELECT 1 FROM dbo.ProductSubcategories) AND @AllowLookupRemoval = 0
            THROW 51202, 'Rollback refused: ProductSubcategories contains rows. Set @AllowLookupRemoval=1 only with explicit approval.', 1;
        IF EXISTS (
            SELECT 1 FROM sys.foreign_keys
            WHERE referenced_object_id = OBJECT_ID(N'dbo.ProductSubcategories')
              AND NOT (parent_object_id = OBJECT_ID(N'dbo.Products') AND name = N'FK_Products_ProductSubcategories')
        ) AND @AllowDependencyRemoval = 0
            THROW 51203, 'Rollback refused: additional foreign-key dependencies exist.', 1;
        IF EXISTS (
            SELECT 1 FROM sys.sql_expression_dependencies
            WHERE referenced_id = OBJECT_ID(N'dbo.ProductSubcategories')
              AND referencing_id <> OBJECT_ID(N'dbo.Products')
        ) AND @AllowDependencyRemoval = 0
            THROW 51204, 'Rollback refused: additional SQL object dependencies exist.', 1;
        IF (SELECT COUNT(*) FROM sys.indexes WHERE object_id=OBJECT_ID(N'dbo.ProductSubcategories') AND index_id > 0) > 3 AND @AllowDependencyRemoval = 0
            THROW 51205, 'Rollback refused: additional indexes exist on ProductSubcategories.', 1;
    END;

    /* Perform the explicitly authorized assignment removal only after every
       lookup/dependency guard has passed. */
    IF @Assignments > 0
        UPDATE dbo.Products SET subcategory_id = NULL;

    IF EXISTS (SELECT 1 FROM sys.foreign_keys WHERE parent_object_id = OBJECT_ID(N'dbo.Products') AND name = N'FK_Products_ProductSubcategories')
        ALTER TABLE dbo.Products DROP CONSTRAINT FK_Products_ProductSubcategories;

    IF EXISTS (SELECT 1 FROM sys.indexes WHERE object_id = OBJECT_ID(N'dbo.Products') AND name = N'IX_Products_subcategory_id')
        DROP INDEX IX_Products_subcategory_id ON dbo.Products;

    ALTER TABLE dbo.Products DROP COLUMN subcategory_id;

    IF OBJECT_ID(N'dbo.ProductSubcategories', N'U') IS NOT NULL
        DROP TABLE dbo.ProductSubcategories;

    COMMIT TRANSACTION;
    PRINT 'Product subcategory schema rollback completed. Existing product and fact identifiers were not changed.';
END TRY
BEGIN CATCH
    IF XACT_STATE() <> 0 ROLLBACK TRANSACTION;
    THROW;
END CATCH;
