from odoo import SUPERUSER_ID, api


def migrate(cr, version):
    del version
    env = api.Environment(cr, SUPERUSER_ID, {})

    # Use bounded SQL updates for the existing cache and diagnostic rows. ORM
    # writes here would recompute link access and could call the permission sync
    # once per relationship during an otherwise local schema migration.
    cr.execute(
        """
        UPDATE usl_document
           SET intake_role = CASE
               WHEN source = 'paperless' THEN 'background'
               ELSE 'library'
           END
        """,
    )
    cr.execute(
        """
        UPDATE usl_document_link AS link
           SET archive_mode = CASE
                   WHEN document.accounting_evidence THEN 'mandatory'
                   ELSE 'automatic'
               END,
               policy_role = CASE
                   WHEN document.accounting_evidence THEN 'evidence'
                   ELSE 'library'
               END,
               document_role = CASE
                   WHEN document.accounting_evidence THEN 'evidence'
                   ELSE 'library'
               END,
               attachment_origin = 'migration',
               policy_reason = 'legacy_relationship_backfill_pending'
          FROM usl_document AS document
         WHERE document.id = link.document_id
        """,
    )
    cr.execute(
        """
        UPDATE usl_document_operation
           SET archive_mode = CASE
                   WHEN accounting_evidence THEN 'mandatory'
                   ELSE 'automatic'
               END,
               document_role = CASE
                   WHEN accounting_evidence THEN 'evidence'
                   ELSE 'library'
               END,
               attachment_origin = 'migration',
               policy_reason = 'legacy_operation_backfill_pending'
        """,
    )

    parameters = env["ir.config_parameter"].sudo()
    parameters.set_int("usl_documents.attachment_backfill_cursor", 0)
    parameters.set_str("usl_documents.attachment_backfill_state", "pending")
