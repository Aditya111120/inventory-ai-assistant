/*
    InventoryAI - approved product subcategory assignments
    This script intentionally contains ZERO active assignments.
    Add only business-approved rows to @Approved before execution.
    Confidence in the review CSV is not approval.
*/
SET XACT_ABORT ON;
SET NOCOUNT ON;

DECLARE @Approved TABLE (
    product_id INT NOT NULL,
    sku VARCHAR(20) NOT NULL,
    category NVARCHAR(50) NOT NULL,
    subcategory_name NVARCHAR(100) NOT NULL,
    PRIMARY KEY (product_id),
    UNIQUE (sku)
);

/* Example only - remain commented until explicitly approved:
INSERT @Approved(product_id, sku, category, subcategory_name)
VALUES (10, 'SKU-0010', N'Audio', N'Speaker');
*/

IF NOT EXISTS (SELECT 1 FROM @Approved)
BEGIN
    PRINT 'No approved assignments supplied. No data was changed.';
    RETURN;
END;

BEGIN TRY
    BEGIN TRANSACTION;

    IF OBJECT_ID(N'dbo.ProductSubcategories', N'U') IS NULL
        THROW 51100, 'Run 001_product_subcategories_schema.sql first.', 1;

    IF EXISTS (
        SELECT 1
        FROM @Approved a
        LEFT JOIN dbo.Products p ON p.product_id = a.product_id
        WHERE p.product_id IS NULL
           OR p.sku <> a.sku
           OR p.category <> a.category
    )
        THROW 51101, 'Approved assignment does not match the existing product ID, SKU, or category.', 1;

    /* Never overwrite or silently accept assignments outside this approved
       manifest. A rerun is allowed only when the existing assignment matches
       the same product identity, category, and approved subcategory. */
    IF EXISTS (
        SELECT 1
        FROM dbo.Products AS p
        JOIN dbo.ProductSubcategories AS existing_s
          ON existing_s.subcategory_id = p.subcategory_id
        WHERE p.subcategory_id IS NOT NULL
          AND NOT EXISTS (
              SELECT 1
              FROM @Approved AS a
              WHERE a.product_id = p.product_id
                AND a.sku = p.sku
                AND a.category = p.category
                AND a.subcategory_name = existing_s.subcategory_name
          )
    )
        THROW 51103, 'Unexpected existing subcategory assignments detected; approved manifest does not authorize overwrite.', 1;

    INSERT dbo.ProductSubcategories(category, subcategory_name)
    SELECT DISTINCT a.category, a.subcategory_name
    FROM @Approved a
    WHERE NOT EXISTS (
        SELECT 1 FROM dbo.ProductSubcategories s
        WHERE s.category = a.category AND s.subcategory_name = a.subcategory_name
    );

    UPDATE p
    SET p.subcategory_id = s.subcategory_id
    FROM dbo.Products p
    JOIN @Approved a ON a.product_id = p.product_id AND a.sku = p.sku AND a.category = p.category
    JOIN dbo.ProductSubcategories s ON s.category = a.category AND s.subcategory_name = a.subcategory_name;

    IF EXISTS (
        SELECT 1
        FROM dbo.Products p
        JOIN @Approved a ON a.product_id = p.product_id
        JOIN dbo.ProductSubcategories s ON s.subcategory_id = p.subcategory_id
        WHERE s.category <> p.category
    )
        THROW 51102, 'Post-load category constraint validation failed.', 1;

    COMMIT TRANSACTION;
    PRINT 'Approved product subcategory assignments loaded successfully.';
END TRY
BEGIN CATCH
    IF XACT_STATE() <> 0 ROLLBACK TRANSACTION;
    THROW;
END CATCH;
