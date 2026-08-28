from odoo import SUPERUSER_ID, api


def migrate(cr, version):
    del version
    env = api.Environment(cr, SUPERUSER_ID, {})
    native_filter = env.ref(
        "usl_documents.ir_filter_quick_needs_review",
        raise_if_not_found=False,
    )
    if native_filter:
        native_filter.write(
            {
                "name": "Ready for review",
                "domain": repr([("review_state", "=", "classified")]),
            },
        )
    shortcut = env.ref(
        "usl_documents.quick_filter_needs_review",
        raise_if_not_found=False,
    )
    if shortcut:
        shortcut.write(
            {
                "name": "Ready for review",
                "icon": "fa-check-square-o",
            },
        )
    attention_view = env.ref(
        "usl_documents.smart_view_needs_review",
        raise_if_not_found=False,
    )
    if attention_view:
        attention_view.name = "Needs attention"
