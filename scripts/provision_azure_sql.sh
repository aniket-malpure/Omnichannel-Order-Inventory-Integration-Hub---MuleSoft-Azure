#!/usr/bin/env bash
# Provisions a serverless Azure SQL Database for the hub and applies the schema.
# Requires: Azure CLI (az login), sqlcmd (go-sqlcmd or mssql-tools18).
#
# Usage:
#   SQL_ADMIN_PASSWORD='...' APP_PASSWORD='...' ./scripts/provision_azure_sql.sh
set -euo pipefail

RESOURCE_GROUP="${RESOURCE_GROUP:-rg-omnihub}"
LOCATION="${LOCATION:-eastus2}"
SERVER="${SERVER:-omnihub-sql}"
DATABASE="${DATABASE:-omnihub}"
SQL_ADMIN="${SQL_ADMIN:-omnihubadmin}"
SQL_ADMIN_PASSWORD="${SQL_ADMIN_PASSWORD:?Set SQL_ADMIN_PASSWORD}"
APP_USER="${APP_USER:-omnihub_app}"
APP_PASSWORD="${APP_PASSWORD:?Set APP_PASSWORD}"

echo "Creating resource group ${RESOURCE_GROUP}..."
az group create --name "$RESOURCE_GROUP" --location "$LOCATION" --output none

echo "Creating logical server ${SERVER}..."
az sql server create \
  --name "$SERVER" --resource-group "$RESOURCE_GROUP" --location "$LOCATION" \
  --admin-user "$SQL_ADMIN" --admin-password "$SQL_ADMIN_PASSWORD" \
  --minimal-tls-version 1.2 --output none

echo "Creating serverless database ${DATABASE} (auto-pause after 60 min)..."
az sql db create \
  --name "$DATABASE" --server "$SERVER" --resource-group "$RESOURCE_GROUP" \
  --edition GeneralPurpose --compute-model Serverless --family Gen5 \
  --capacity 1 --min-capacity 0.5 --auto-pause-delay 60 \
  --backup-storage-redundancy Local --output none

# CloudHub 2.0 shared spaces egress from AWS; for a private space use its static IPs instead.
echo "Allowing Azure services and your current IP..."
az sql server firewall-rule create --resource-group "$RESOURCE_GROUP" --server "$SERVER" \
  --name AllowAzureServices --start-ip-address 0.0.0.0 --end-ip-address 0.0.0.0 --output none
MY_IP="$(curl -s https://api.ipify.org)"
az sql server firewall-rule create --resource-group "$RESOURCE_GROUP" --server "$SERVER" \
  --name DevMachine --start-ip-address "$MY_IP" --end-ip-address "$MY_IP" --output none

FQDN="${SERVER}.database.windows.net"
SQLCMD="${SQLCMD:-sqlcmd}"

echo "Applying schema..."
for f in sql/azure-sql/001_schema.sql sql/azure-sql/002_procedures.sql sql/azure-sql/003_views.sql; do
  echo "  $(basename "$f")"
  "$SQLCMD" -S "$FQDN" -d "$DATABASE" -U "$SQL_ADMIN" -P "$SQL_ADMIN_PASSWORD" -b -i "$f"
done

echo "Creating least-privilege app user ${APP_USER}..."
"$SQLCMD" -S "$FQDN" -d "$DATABASE" -U "$SQL_ADMIN" -P "$SQL_ADMIN_PASSWORD" -b -Q "
IF NOT EXISTS (SELECT 1 FROM sys.database_principals WHERE name = '${APP_USER}')
    CREATE USER [${APP_USER}] WITH PASSWORD = '${APP_PASSWORD}';
GRANT EXECUTE ON SCHEMA::dbo TO [${APP_USER}];
GRANT SELECT ON dbo.vw_sync_success_rate_daily TO [${APP_USER}];
GRANT SELECT ON dbo.vw_order_sync_quality_daily TO [${APP_USER}];
GRANT SELECT ON dbo.vw_open_sync_errors TO [${APP_USER}];
GRANT SELECT ON dbo.sync_error_log TO [${APP_USER}];"

echo
echo "Done. JDBC URL for config-cloud.yaml:"
echo "jdbc:sqlserver://${FQDN}:1433;databaseName=${DATABASE};encrypt=true;trustServerCertificate=false;hostNameInCertificate=*.database.windows.net;loginTimeout=30"
