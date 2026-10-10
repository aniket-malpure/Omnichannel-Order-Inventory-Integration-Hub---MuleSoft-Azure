-- Local only (SQL Server 2022 container). On Azure, the database is created by
-- scripts/provision_azure_sql.sh, so skip this file there.
IF DB_ID(N'omnihub') IS NULL
BEGIN
    CREATE DATABASE omnihub;
END
GO
