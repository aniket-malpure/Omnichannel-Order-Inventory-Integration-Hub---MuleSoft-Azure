-- Reporting views (also consumed by GET /api/v1/metrics/sync)

/*
  Eventual success rate: an entity counts as succeeded if ANY attempt synced
  (first try or a later replay). Inventory runs report PARTIAL when some SKUs
  failed; those are counted as not succeeded.
*/
CREATE OR ALTER VIEW dbo.vw_sync_success_rate_daily
AS
WITH per_entity AS (
    SELECT
        CAST(MIN(created_at) AS DATE) AS sync_date,
        channel,
        entity_type,
        entity_key,
        MAX(CASE WHEN outcome IN ('SYNCED', 'SKIPPED_DUPLICATE') THEN 1 ELSE 0 END) AS eventually_ok,
        COUNT(*) AS attempts
    FROM dbo.sync_audit
    GROUP BY channel, entity_type, entity_key
)
SELECT
    sync_date,
    channel,
    entity_type,
    COUNT(*)            AS entities,
    SUM(attempts)       AS attempts,
    SUM(eventually_ok)  AS succeeded,
    CAST(100.0 * SUM(eventually_ok) / NULLIF(COUNT(*), 0) AS DECIMAL(5, 2)) AS success_rate_pct
FROM per_entity
GROUP BY sync_date, channel, entity_type;
GO

-- First-pass vs eventual success, useful for spotting flaky downstream systems
CREATE OR ALTER VIEW dbo.vw_order_sync_quality_daily
AS
WITH attempts AS (
    SELECT
        entity_key,
        channel,
        outcome,
        created_at,
        ROW_NUMBER() OVER (PARTITION BY entity_key ORDER BY created_at) AS attempt_no
    FROM dbo.sync_audit
    WHERE entity_type = 'ORDER'
),
per_order AS (
    SELECT
        CAST(MIN(created_at) AS DATE) AS sync_date,
        channel,
        entity_key,
        MAX(CASE WHEN attempt_no = 1 AND outcome IN ('SYNCED', 'SKIPPED_DUPLICATE') THEN 1 ELSE 0 END) AS first_pass_ok,
        MAX(CASE WHEN outcome IN ('SYNCED', 'SKIPPED_DUPLICATE') THEN 1 ELSE 0 END) AS eventually_ok
    FROM attempts
    GROUP BY channel, entity_key
)
SELECT
    sync_date,
    channel,
    COUNT(*) AS orders,
    CAST(100.0 * SUM(first_pass_ok) / NULLIF(COUNT(*), 0) AS DECIMAL(5, 2)) AS first_pass_rate_pct,
    CAST(100.0 * SUM(eventually_ok) / NULLIF(COUNT(*), 0) AS DECIMAL(5, 2)) AS eventual_success_rate_pct
FROM per_order
GROUP BY sync_date, channel;
GO

-- Open issues by type, for a quick triage dashboard
CREATE OR ALTER VIEW dbo.vw_open_sync_errors
AS
SELECT
    channel,
    entity_type,
    error_type,
    status,
    COUNT(*)        AS error_count,
    MIN(created_at) AS oldest_error_at,
    MAX(updated_at) AS last_seen_at
FROM dbo.sync_error_log
WHERE status IN ('PENDING_RETRY', 'RETRYING', 'DEAD_LETTER', 'NON_RETRYABLE')
GROUP BY channel, entity_type, error_type, status;
GO
