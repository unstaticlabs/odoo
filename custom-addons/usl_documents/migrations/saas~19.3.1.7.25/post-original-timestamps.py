from odoo import SUPERUSER_ID, api


def migrate(cr, version):
    del version
    env = api.Environment(cr, SUPERUSER_ID, {})
    env.cr.execute(
        """
        UPDATE usl_document
           SET original_created_at = COALESCE(submitted_at, paperless_created)
         WHERE original_created_at IS NULL
        """,
    )
    env.cr.execute(
        """
        UPDATE usl_document_version
           SET original_created_at = COALESCE(submitted_at, created_at)
         WHERE original_created_at IS NULL
        """,
    )
