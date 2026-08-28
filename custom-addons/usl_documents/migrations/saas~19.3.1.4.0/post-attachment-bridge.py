from odoo import SUPERUSER_ID, Command, api


def migrate(cr, version):
    del version
    env = api.Environment(cr, SUPERUSER_ID, {})
    linked_documents = env["usl.document"].sudo().search(
        [
            ("link_ids.active", "=", True),
            ("source", "in", ("odoo_attachment", "odoo_generated")),
        ],
    )
    if linked_documents:
        linked_documents.with_context(
            usl_documents_policy_write=True,
            usl_documents_cache_write=True,
            skip_permission_invalidation=True,
        ).write(
            {
                "access_scope": "linked_record",
                "permitted_user_ids": [Command.clear()],
                "permission_sync_state": "pending",
                "permission_sync_error": False,
            },
        )
    parameters = env["ir.config_parameter"].sudo()
    parameters.set_int("usl_documents.attachment_backfill_cursor", 0)
    parameters.set_str("usl_documents.attachment_backfill_state", "pending")
