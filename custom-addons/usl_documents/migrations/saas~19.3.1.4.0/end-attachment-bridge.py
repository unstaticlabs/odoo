from odoo import SUPERUSER_ID, api


def migrate(cr, version):
    """Rebuild linked-record access after every dependent model is loaded."""
    del version
    env = api.Environment(cr, SUPERUSER_ID, {})
    linked_documents = env["usl.document"].sudo().search(
        [
            ("access_scope", "=", "linked_record"),
            ("link_ids.active", "=", True),
            ("source", "in", ("odoo_attachment", "odoo_generated")),
        ],
    )
    if linked_documents:
        linked_documents._recompute_linked_record_access(sync_permissions=False)
