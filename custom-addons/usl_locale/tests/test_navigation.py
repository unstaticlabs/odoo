from odoo.tests import TransactionCase, tagged

from odoo.addons.usl_locale.models.ir_ui_menu import DEEMPHASIZED_ROOT_MENU_XMLIDS


@tagged("post_install", "-at_install", "usl_locale")
class TestFocusedAppLauncher(TransactionCase):
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
