from odoo import SUPERUSER_ID, api


def migrate(cr, version):
    del version
    env = api.Environment(cr, SUPERUSER_ID, {})
    workspace_menu = env.ref(
        "usl_documents.menu_usl_documents_workspace",
        raise_if_not_found=False,
    )
    if workspace_menu:
        workspace_menu.unlink()
