from odoo import SUPERUSER_ID, api


def migrate(cr, version):
    del version
    env = api.Environment(cr, SUPERUSER_ID, {})
    documents = env["usl.document"].sudo().search(
        [
            ("review_state", "=", "needs_attention"),
            ("confidentiality", "in", ("hr", "private")),
            ("last_error", "like", "different archive metadata (confidentiality)"),
            ("link_ids.active", "=", True),
        ],
    )
    documents.with_context(usl_documents_cache_write=True).write(
        {"last_error": False},
    )
    if documents:
        env["usl.document"].reconcile_linked_classification(limit=0)
