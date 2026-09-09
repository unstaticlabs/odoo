"""Keep the Distribution launcher focused and ordered by daily workflow."""

from odoo import api, models

# The menus remain active so intentional workflow links continue to work.
DEEMPHASIZED_ROOT_MENU_XMLIDS = (
    "mail.menu_root_discuss",
    "project_todo.menu_todo_todos",
    "spreadsheet_dashboard.spreadsheet_dashboard_menu_root",
    "base.menu_management",
    "utm.menu_link_tracker_root",
    # A Project board reached from the Notifications menu, not a daily app.
    "usl_feedback.menu_feedback_root",
    # Core ships this ungated, so every internal user would otherwise see it.
    "base.menu_tests",
)

PRIMARY_ROOT_MENU_XMLIDS = (
    "usl_home.menu_usl_home_root",
    "project.menu_main_pm",
    "usl_documents.menu_usl_documents_root",
    "account.menu_finance",
    "usl_platform_billing.menu_platform_billing_root",
    "usl_b2c.menu_b2c_root",
    "sale.sale_menu_root",
    "purchase.menu_purchase_root",
    "stock.menu_stock_root",
    "mrp.menu_mrp_root",
)

# Supporting apps, kept together after the workflow block so they never split
# the commerce apps above. Contacts and Employees lead so payroll sits with the
# people apps rather than with the signing tools.
SECONDARY_ROOT_MENU_XMLIDS = (
    "contacts.menu_contacts",
    "hr.menu_hr_root",
    "usl_tese_payroll.menu_tese_payroll_root",
    "hr_expense.menu_hr_expense_root",
    "sign_oca.sign_oca_root_menu",
)

TRAILING_ROOT_MENU_XMLIDS = ("base.menu_administration",)


def order_root_menu_items(items, xmlid_getter):
    """Apply the Distribution app hierarchy without disturbing other apps."""
    primary_ranks = {
        xmlid: rank for rank, xmlid in enumerate(PRIMARY_ROOT_MENU_XMLIDS)
    }
    secondary_ranks = {
        xmlid: rank for rank, xmlid in enumerate(SECONDARY_ROOT_MENU_XMLIDS)
    }
    trailing_ranks = {
        xmlid: rank for rank, xmlid in enumerate(TRAILING_ROOT_MENU_XMLIDS)
    }

    def sort_key(item):
        xmlid = xmlid_getter(item)
        if xmlid in primary_ranks:
            return (0, primary_ranks[xmlid])
        if xmlid in secondary_ranks:
            return (2, secondary_ranks[xmlid])
        if xmlid in trailing_ranks:
            return (3, trailing_ranks[xmlid])
        return (1, 0)

    return sorted(items, key=sort_key)


class IrUiMenu(models.Model):
    _inherit = "ir.ui.menu"

    def _load_menus_blacklist(self):
        blacklisted_menu_ids = super()._load_menus_blacklist()
        for xmlid in DEEMPHASIZED_ROOT_MENU_XMLIDS:
            menu = self.env.ref(xmlid, raise_if_not_found=False)
            if menu:
                blacklisted_menu_ids.append(menu.id)
        return blacklisted_menu_ids

    @api.model
    def load_menus_root(self):
        menu_root = super().load_menus_root()
        return {
            **menu_root,
            "children": order_root_menu_items(
                menu_root["children"],
                lambda menu: menu.get("xmlid", ""),
            ),
        }

    @api.model
    def load_menus(self, debug):
        menus = super().load_menus(debug)
        root = menus["root"]
        return {
            **menus,
            "root": {
                **root,
                "children": order_root_menu_items(
                    root["children"],
                    lambda menu_id: menus[menu_id].get("xmlid", ""),
                ),
            },
        }
