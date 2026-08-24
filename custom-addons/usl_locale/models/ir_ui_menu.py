"""Keep secondary framework capabilities out of the Distribution launcher."""

from odoo import models

# The menus remain active so intentional workflow links continue to work.
DEEMPHASIZED_ROOT_MENU_XMLIDS = (
    "mail.menu_root_discuss",
    "project_todo.menu_todo_todos",
    "spreadsheet_dashboard.spreadsheet_dashboard_menu_root",
    "base.menu_management",
)


class IrUiMenu(models.Model):
    _inherit = "ir.ui.menu"

    def _load_menus_blacklist(self):
        blacklisted_menu_ids = super()._load_menus_blacklist()
        for xmlid in DEEMPHASIZED_ROOT_MENU_XMLIDS:
            menu = self.env.ref(xmlid, raise_if_not_found=False)
            if menu:
                blacklisted_menu_ids.append(menu.id)
        return blacklisted_menu_ids
