/* Phase 1 authorization schema and demo principals. Review and execute in InventoryAI only. */
SET XACT_ABORT ON;
BEGIN TRANSACTION;
BEGIN TRY
    IF OBJECT_ID(N'dbo.AppRoles', N'U') IS NULL
    CREATE TABLE dbo.AppRoles (
        role_id INT IDENTITY(1,1) NOT NULL CONSTRAINT PK_AppRoles PRIMARY KEY,
        role_code VARCHAR(30) NOT NULL CONSTRAINT UQ_AppRoles_Code UNIQUE,
        role_name NVARCHAR(80) NOT NULL
    );
    IF OBJECT_ID(N'dbo.AppUsers', N'U') IS NULL
    CREATE TABLE dbo.AppUsers (
        user_id INT IDENTITY(1,1) NOT NULL CONSTRAINT PK_AppUsers PRIMARY KEY,
        username VARCHAR(30) NOT NULL CONSTRAINT UQ_AppUsers_Username UNIQUE,
        display_name NVARCHAR(100) NOT NULL,
        role_id INT NOT NULL CONSTRAINT FK_AppUsers_Role REFERENCES dbo.AppRoles(role_id),
        password_hash NVARCHAR(512) NULL,
        is_active BIT NOT NULL CONSTRAINT DF_AppUsers_Active DEFAULT 1,
        created_at DATETIME2(0) NOT NULL CONSTRAINT DF_AppUsers_Created DEFAULT SYSUTCDATETIME()
    );
    IF OBJECT_ID(N'dbo.UserRegionAccess', N'U') IS NULL
    CREATE TABLE dbo.UserRegionAccess (
        user_id INT NOT NULL CONSTRAINT FK_UserRegionAccess_User REFERENCES dbo.AppUsers(user_id),
        region NVARCHAR(50) NOT NULL,
        CONSTRAINT PK_UserRegionAccess PRIMARY KEY(user_id, region)
    );
    IF OBJECT_ID(N'dbo.UserBrandAccess', N'U') IS NULL
    CREATE TABLE dbo.UserBrandAccess (
        user_id INT NOT NULL CONSTRAINT FK_UserBrandAccess_User REFERENCES dbo.AppUsers(user_id),
        brand NVARCHAR(50) NOT NULL,
        CONSTRAINT PK_UserBrandAccess PRIMARY KEY(user_id, brand)
    );
    IF OBJECT_ID(N'dbo.AppSessions', N'U') IS NULL
    CREATE TABLE dbo.AppSessions (
        session_id BIGINT IDENTITY(1,1) NOT NULL CONSTRAINT PK_AppSessions PRIMARY KEY,
        token_hash VARBINARY(32) NOT NULL CONSTRAINT UQ_AppSessions_Token UNIQUE,
        user_id INT NOT NULL CONSTRAINT FK_AppSessions_User REFERENCES dbo.AppUsers(user_id),
        expires_at DATETIME2(0) NOT NULL,
        revoked_at DATETIME2(0) NULL,
        created_at DATETIME2(0) NOT NULL CONSTRAINT DF_AppSessions_Created DEFAULT SYSUTCDATETIME()
    );

    INSERT dbo.AppRoles(role_code,role_name)
    SELECT v.role_code,v.role_name FROM (VALUES
      ('STORE_MANAGER','Store Manager'),('BRAND_MANAGER','Brand Manager'),
      ('PLANNER','Planner'),('LEADERSHIP','Leadership')) v(role_code,role_name)
    WHERE NOT EXISTS (SELECT 1 FROM dbo.AppRoles r WHERE r.role_code=v.role_code);

    IF EXISTS (SELECT 1 FROM (VALUES ('SM-01'),('SM-02'),('SM-03'),('SM-04'),('BM-01'),('BM-02'),('PL-01'),('LD-01')) v(username)
               WHERE EXISTS (SELECT 1 FROM dbo.AppUsers u WHERE u.username=v.username AND u.password_hash IS NOT NULL))
        THROW 51001, 'Existing demo account has a password hash; migration refuses to overwrite it.', 1;

    INSERT dbo.AppUsers(username,display_name,role_id)
    SELECT v.username,v.display_name,r.role_id FROM (VALUES
      ('SM-01','North Store Manager','STORE_MANAGER'),('SM-02','South Store Manager','STORE_MANAGER'),
      ('SM-03','East Store Manager','STORE_MANAGER'),('SM-04','West Store Manager','STORE_MANAGER'),
      ('BM-01','Technology Brand Manager','BRAND_MANAGER'),('BM-02','Consumer Brand Manager','BRAND_MANAGER'),
      ('PL-01','Planner','PLANNER'),('LD-01','Leadership','LEADERSHIP')) v(username,display_name,role_code)
    JOIN dbo.AppRoles r ON r.role_code=v.role_code
    WHERE NOT EXISTS (SELECT 1 FROM dbo.AppUsers u WHERE u.username=v.username);

    IF EXISTS (SELECT 1 FROM (VALUES ('North'),('South'),('East'),('West')) v(region)
               WHERE NOT EXISTS (SELECT 1 FROM dbo.Stores s WHERE s.region=v.region))
        THROW 51002, 'One or more required store regions do not exist.', 1;
    IF EXISTS (SELECT 1 FROM (VALUES ('Apple'),('Samsung'),('Sony'),('LG'),('Lenovo'),('Dell'),('HP'),('Asus'),('Canon'),('OnePlus'),('Xiaomi'),('boAt')) v(brand)
               WHERE NOT EXISTS (SELECT 1 FROM dbo.Products p WHERE p.brand=v.brand))
        THROW 51003, 'One or more required brands do not exist.', 1;

    INSERT dbo.UserRegionAccess(user_id,region)
    SELECT u.user_id,v.region FROM (VALUES ('SM-01','North'),('SM-02','South'),('SM-03','East'),('SM-04','West')) v(username,region)
    JOIN dbo.AppUsers u ON u.username=v.username WHERE NOT EXISTS (SELECT 1 FROM dbo.UserRegionAccess a WHERE a.user_id=u.user_id AND a.region=v.region);
    INSERT dbo.UserBrandAccess(user_id,brand)
    SELECT u.user_id,v.brand FROM (VALUES
      ('BM-01','Apple'),('BM-01','Samsung'),('BM-01','Sony'),('BM-01','LG'),('BM-01','Lenovo'),('BM-01','Dell'),
      ('BM-02','HP'),('BM-02','Asus'),('BM-02','Canon'),('BM-02','OnePlus'),('BM-02','Xiaomi'),('BM-02','boAt')) v(username,brand)
    JOIN dbo.AppUsers u ON u.username=v.username WHERE NOT EXISTS (SELECT 1 FROM dbo.UserBrandAccess a WHERE a.user_id=u.user_id AND a.brand=v.brand);
    COMMIT;
END TRY
BEGIN CATCH
    IF XACT_STATE() <> 0 ROLLBACK;
    THROW;
END CATCH;
