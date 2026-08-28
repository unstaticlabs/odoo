from odoo.tests import TransactionCase, tagged

from odoo.addons.usl_locale.models.ir_ui_menu import (
    DEEMPHASIZED_ROOT_MENU_XMLIDS,
    PRIMARY_ROOT_MENU_XMLIDS,
    TRAILING_ROOT_MENU_XMLIDS,
    order_root_menu_items,
)


@tagged("post_install", "-at_install", "usl_locale")
class TestFocusedAppLauncher(TransactionCase):
    def test_home_is_the_first_primary_destination_when_installed(self):
        self.assertEqual(PRIMARY_ROOT_MENU_XMLIDS[0], "usl_home.menu_usl_home_root")

    def test_distribution_root_menu_order_preserves_unspecified_apps(self):
        unspecified = "usl_tese_payroll.menu_tese_payroll_root"
        unordered = [
            *reversed(PRIMARY_ROOT_MENU_XMLIDS),
            TRAILING_ROOT_MENU_XMLIDS[1],
            unspecified,
            TRAILING_ROOT_MENU_XMLIDS[0],
        ]

        self.assertEqual(
            order_root_menu_items(unordered, lambda xmlid: xmlid),
            [
                *PRIMARY_ROOT_MENU_XMLIDS,
                unspecified,
                *TRAILING_ROOT_MENU_XMLIDS,
            ],
        )

    def test_distribution_deemphasizes_expected_root_menus(self):
        self.assertEqual(
            DEEMPHASIZED_ROOT_MENU_XMLIDS,
            (
                "mail.menu_root_discuss",
                "project_todo.menu_todo_todos",
                "spreadsheet_dashboard.spreadsheet_dashboard_menu_root",
                "base.menu_management",
            ),
        )

    def test_installed_deemphasized_apps_are_not_loaded(self):
        menu_model = self.env["ir.ui.menu"]
        installed_menus = {
            xmlid: menu
            for xmlid in DEEMPHASIZED_ROOT_MENU_XMLIDS
            if (menu := self.env.ref(xmlid, raise_if_not_found=False))
        }
        self.assertIn("base.menu_management", installed_menus)

        blacklisted_ids = set(menu_model._load_menus_blacklist())
        menus_without_debug = menu_model.load_menus(debug=False)
        menus_with_debug = menu_model.load_menus(debug=True)
        for xmlid, menu in installed_menus.items():
            with self.subTest(xmlid=xmlid):
                self.assertTrue(menu.active)
                self.assertIn(menu.id, blacklisted_ids)
                self.assertNotIn(menu.id, menus_without_debug)
                self.assertNotIn(menu.id, menus_with_debug)
