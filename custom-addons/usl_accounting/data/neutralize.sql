-- Keep every duplicated USL accounting database disconnected from live
-- electronic-invoice transport even if an optional provider module was
-- removed after leaving stale configuration behind.
INSERT INTO ir_config_parameter (key, value)
     VALUES ('account_peppol.edi.mode', 'demo')
ON CONFLICT (key) DO UPDATE
        SET value = 'demo';

-- Preserve receipt evidence while ensuring restored databases cannot run or
-- revive an external linked-receipt download.
DO $$
BEGIN
    IF to_regclass('public.usl_mail_pdf_retrieval') IS NOT NULL THEN
        UPDATE usl_mail_pdf_retrieval
           SET state = 'needs_attention',
               generation = generation + 1,
               failure_code = 'database_neutralized',
               failure_message = 'Automatic linked-receipt download is disabled in this restored database.'
         WHERE state IN ('queued', 'running', 'retrying');
    END IF;
    IF to_regclass('public.queue_job') IS NOT NULL THEN
        UPDATE queue_job
           SET state = 'cancelled',
               date_cancelled = now()
         WHERE model_name = 'usl.mail.pdf.retrieval'
           AND method_name = '_job_fetch_receipt'
           AND state IN ('wait_dependencies', 'pending', 'enqueued');
    END IF;
END
$$;
