from odoo import SUPERUSER_ID, api


ROLLED_BACK_VIEW_XMLIDS = (
    ("usl_documents", "account_payment_form_documents"),
    ("usl_documents_accounting", "view_account_asset_documents"),
)


def migrate(cr, version):
    """Remove views left by the separately developed attachment bridge.

    The canonical development database briefly ran that feature before its
    changes were intentionally isolated in the Documents migration implementation.
    Without this cleanup, any later account.payment view validation fails
    because the rolled-back view calls a method not present on this branch.
    """
    del version
    env = api.Environment(cr, SUPERUSER_ID, {})
    model_data_model = env["ir.model.data"].sudo()
    for module, name in ROLLED_BACK_VIEW_XMLIDS:
        model_data = model_data_model.search(
            [
                ("module", "=", module),
                ("name", "=", name),
                ("model", "=", "ir.ui.view"),
            ],
            limit=1,
        )
        if not model_data:
            continue
        env["ir.ui.view"].browse(model_data.res_id).exists().unlink()
        model_data.exists().unlink()
