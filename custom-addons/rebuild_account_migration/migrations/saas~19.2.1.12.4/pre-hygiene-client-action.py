from odoo import SUPERUSER_ID, api


def migrate(cr, version):
    del version
    env = api.Environment(cr, SUPERUSER_ID, {})
    model_data = env["ir.model.data"].search(
        [
            ("module", "=", "rebuild_account_migration"),
            ("name", "=", "action_open_current_company_hygiene"),
            ("model", "=", "ir.actions.server"),
        ],
        limit=1,
    )
    if not model_data:
        return
    server_action = env["ir.actions.server"].browse(model_data.res_id).exists()
    server_action.unlink()
    model_data.unlink()
