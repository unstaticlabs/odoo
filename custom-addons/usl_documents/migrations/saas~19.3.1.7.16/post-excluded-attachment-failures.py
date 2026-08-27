from odoo import SUPERUSER_ID, api


def migrate(cr, version):
    del version
    env = api.Environment(cr, SUPERUSER_ID, {})
    failed_attachments = env["usl.document.operation"].sudo().search(
        [
            ("source_attachment_id", "!=", False),
            ("state", "in", ("failed", "duplicate")),
            ("acknowledged", "=", False),
        ],
    ).mapped("source_attachment_id")
    for attachment in failed_attachments:
        attachment._usl_documents_archive_eligibility(refresh=True)
