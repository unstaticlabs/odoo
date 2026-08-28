-- Keep every duplicated USL accounting database disconnected from live
-- electronic-invoice transport even if an optional provider module was
-- removed after leaving stale configuration behind.
INSERT INTO ir_config_parameter (key, value)
     VALUES ('account_peppol.edi.mode', 'demo')
ON CONFLICT (key) DO UPDATE
        SET value = 'demo';
