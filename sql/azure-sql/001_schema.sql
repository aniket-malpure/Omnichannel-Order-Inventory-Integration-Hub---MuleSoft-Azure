-- Omnichannel Integration Hub: audit trail + error log / replay queue
-- Target: Azure SQL Database (also runs on SQL Server 2022 for local dev)

IF OBJECT_ID(N'dbo.sync_audit', N'U') IS NULL
BEGIN
    CREATE TABLE dbo.sync_audit (
        audit_id        BIGINT IDENTITY(1,1) NOT NULL CONSTRAINT pk_sync_audit PRIMARY KEY,
        correlation_id  NVARCHAR(64)   NULL,
        channel         NVARCHAR(20)   NOT NULL,
        entity_type     NVARCHAR(20)   NOT NULL,
        entity_key      NVARCHAR(100)  NOT NULL,
        outcome         NVARCHAR(30)   NOT NULL
            CONSTRAINT ck_sync_audit_outcome CHECK (outcome IN ('SYNCED', 'SKIPPED_DUPLICATE', 'PARTIAL', 'FAILED')),
        target_ref      NVARCHAR(200)  NULL,
        duration_ms     INT            NULL,
        created_at      DATETIME2(3)   NOT NULL CONSTRAINT df_sync_audit_created DEFAULT SYSUTCDATETIME()
    );

    CREATE INDEX ix_sync_audit_created ON dbo.sync_audit (created_at) INCLUDE (channel, entity_type, outcome);
    CREATE INDEX ix_sync_audit_entity  ON dbo.sync_audit (entity_type, entity_key) INCLUDE (outcome, created_at);
END
GO

IF OBJECT_ID(N'dbo.sync_error_log', N'U') IS NULL
BEGIN
    CREATE TABLE dbo.sync_error_log (
        error_id        BIGINT IDENTITY(1,1) NOT NULL CONSTRAINT pk_sync_error_log PRIMARY KEY,
        correlation_id  NVARCHAR(64)   NULL,
        channel         NVARCHAR(20)   NOT NULL,
        entity_type     NVARCHAR(20)   NOT NULL,
        entity_key      NVARCHAR(100)  NOT NULL,
        error_type      NVARCHAR(100)  NOT NULL,
        error_message   NVARCHAR(4000) NULL,
        payload_json    NVARCHAR(MAX)  NULL
            CONSTRAINT ck_sync_error_payload_json CHECK (payload_json IS NULL OR ISJSON(payload_json) = 1),
        is_retryable    BIT            NOT NULL,
        status          NVARCHAR(20)   NOT NULL
            CONSTRAINT ck_sync_error_status CHECK (status IN ('PENDING_RETRY', 'RETRYING', 'RESOLVED', 'DEAD_LETTER', 'NON_RETRYABLE')),
        retry_count     INT            NOT NULL CONSTRAINT df_sync_error_retry_count DEFAULT 0,
        max_retries     INT            NOT NULL CONSTRAINT df_sync_error_max_retries DEFAULT 5,
        next_retry_at   DATETIME2(3)   NULL,
        created_at      DATETIME2(3)   NOT NULL CONSTRAINT df_sync_error_created DEFAULT SYSUTCDATETIME(),
        updated_at      DATETIME2(3)   NOT NULL CONSTRAINT df_sync_error_updated DEFAULT SYSUTCDATETIME()
    );

    -- The replay scheduler only ever scans due PENDING_RETRY rows
    CREATE INDEX ix_sync_error_due ON dbo.sync_error_log (next_retry_at)
        WHERE status = 'PENDING_RETRY';
    CREATE INDEX ix_sync_error_status ON dbo.sync_error_log (status, updated_at DESC);
    CREATE INDEX ix_sync_error_entity ON dbo.sync_error_log (entity_type, entity_key);
END
GO
