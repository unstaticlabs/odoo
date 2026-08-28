-- A restored Odoo database must never retain a route or credential to the
-- production Paperless archive. Native Odoo neutralization disables cron, but
-- these values would still make manual document actions externally effective.
DELETE FROM ir_config_parameter
 WHERE key LIKE 'usl_documents.paperless\_%' ESCAPE '\'
    OR key LIKE 'usl_documents.sync\_%' ESCAPE '\';
