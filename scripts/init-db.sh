#!/usr/bin/env bash
# Applies sql/azure-sql/*.sql to the local SQL Server container once it accepts connections.
set -euo pipefail

SQLCMD=/opt/mssql-tools18/bin/sqlcmd
HOST="${SQL_HOST:-localhost}"
PASS="${SQL_PASSWORD:?SQL_PASSWORD is required}"

echo "Waiting for SQL Server at ${HOST}..."
for i in $(seq 1 60); do
  if "$SQLCMD" -S "$HOST" -U sa -P "$PASS" -C -Q "SELECT 1" > /dev/null 2>&1; then
    break
  fi
  sleep 2
done

"$SQLCMD" -S "$HOST" -U sa -P "$PASS" -C -b -i /sql/000_create_database_local.sql
for f in /sql/001_schema.sql /sql/002_procedures.sql /sql/003_views.sql; do
  echo "Applying $(basename "$f")"
  "$SQLCMD" -S "$HOST" -U sa -P "$PASS" -C -b -d omnihub -i "$f"
done
echo "Database omnihub is ready."
