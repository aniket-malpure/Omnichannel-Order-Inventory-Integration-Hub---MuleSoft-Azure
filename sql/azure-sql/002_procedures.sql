-- Stored procedures called by the Mule app (db:stored-procedure)

CREATE OR ALTER PROCEDURE dbo.usp_log_sync_audit
    @channel        NVARCHAR(20),
    @entity_type    NVARCHAR(20),
    @entity_key     NVARCHAR(100),
    @outcome        NVARCHAR(30),
    @target_ref     NVARCHAR(200) = NULL,
    @duration_ms    INT = NULL,
    @correlation_id NVARCHAR(64) = NULL
AS
BEGIN
    SET NOCOUNT ON;
    INSERT INTO dbo.sync_audit (correlation_id, channel, entity_type, entity_key, outcome, target_ref, duration_ms)
    VALUES (@correlation_id, @channel, @entity_type, @entity_key, @outcome, @target_ref, @duration_ms);
END
GO

/*
  Records a failed sync. When @retry_of_error_id is set (a replay failed again),
  the existing row is updated with exponential backoff (2^retry_count minutes)
  and moves to DEAD_LETTER once max_retries is reached.
*/
CREATE OR ALTER PROCEDURE dbo.usp_log_sync_error
    @channel           NVARCHAR(20),
    @entity_type       NVARCHAR(20),
    @entity_key        NVARCHAR(100),
    @error_type        NVARCHAR(100),
    @error_message     NVARCHAR(4000),
    @payload_json      NVARCHAR(MAX),
    @is_retryable      BIT,
    @correlation_id    NVARCHAR(64),
    @retry_of_error_id BIGINT = NULL
AS
BEGIN
    SET NOCOUNT ON;
    SET XACT_ABORT ON;

    BEGIN TRANSACTION;

    IF @retry_of_error_id IS NOT NULL
    BEGIN
        UPDATE dbo.sync_error_log
           SET error_type     = @error_type,
               error_message  = @error_message,
               correlation_id = @correlation_id,
               is_retryable   = @is_retryable,
               status         = CASE
                                    WHEN @is_retryable = 0 THEN 'NON_RETRYABLE'
                                    WHEN retry_count >= max_retries THEN 'DEAD_LETTER'
                                    ELSE 'PENDING_RETRY'
                                END,
               next_retry_at  = DATEADD(MINUTE, POWER(2, CASE WHEN retry_count > 10 THEN 10 ELSE retry_count END), SYSUTCDATETIME()),
               updated_at     = SYSUTCDATETIME()
         WHERE error_id = @retry_of_error_id;
    END
    ELSE
    BEGIN
        INSERT INTO dbo.sync_error_log
            (correlation_id, channel, entity_type, entity_key, error_type, error_message,
             payload_json, is_retryable, status, next_retry_at)
        VALUES
            (@correlation_id, @channel, @entity_type, @entity_key, @error_type, @error_message,
             @payload_json, @is_retryable,
             CASE WHEN @is_retryable = 1 THEN 'PENDING_RETRY' ELSE 'NON_RETRYABLE' END,
             CASE WHEN @is_retryable = 1 THEN DATEADD(MINUTE, 1, SYSUTCDATETIME()) ELSE NULL END);
    END

    INSERT INTO dbo.sync_audit (correlation_id, channel, entity_type, entity_key, outcome)
    VALUES (@correlation_id, @channel, @entity_type, @entity_key, 'FAILED');

    COMMIT TRANSACTION;
END
GO

/*
  Claims due rows for replay. UPDLOCK + READPAST lets several CloudHub replicas
  call this concurrently without claiming the same row twice.
*/
CREATE OR ALTER PROCEDURE dbo.usp_claim_retryable_errors
    @batch_size INT = 50
AS
BEGIN
    SET NOCOUNT ON;

    -- Rows stuck in RETRYING for 30+ minutes (worker crashed) go back to the queue
    UPDATE dbo.sync_error_log
       SET status = 'PENDING_RETRY', updated_at = SYSUTCDATETIME()
     WHERE status = 'RETRYING'
       AND updated_at < DATEADD(MINUTE, -30, SYSUTCDATETIME());

    ;WITH due AS (
        SELECT TOP (@batch_size) *
          FROM dbo.sync_error_log WITH (UPDLOCK, READPAST, ROWLOCK)
         WHERE status = 'PENDING_RETRY'
           AND next_retry_at <= SYSUTCDATETIME()
         ORDER BY next_retry_at
    )
    UPDATE due
       SET status      = 'RETRYING',
           retry_count = retry_count + 1,
           updated_at  = SYSUTCDATETIME()
    OUTPUT inserted.error_id,
           inserted.channel,
           inserted.entity_type,
           inserted.entity_key,
           inserted.payload_json,
           inserted.retry_count;
END
GO

CREATE OR ALTER PROCEDURE dbo.usp_resolve_sync_error
    @error_id BIGINT
AS
BEGIN
    SET NOCOUNT ON;
    UPDATE dbo.sync_error_log
       SET status = 'RESOLVED', updated_at = SYSUTCDATETIME()
     WHERE error_id = @error_id;
END
GO
