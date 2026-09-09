from odoo.tests import TransactionCase, tagged

from odoo.addons.usl_locale.models.ir_ui_menu import (
    DEEMPHASIZED_ROOT_MENU_XMLIDS,
    PRIMARY_ROOT_MENU_XMLIDS,
    SECONDARY_ROOT_MENU_XMLIDS,
    TRAILING_ROOT_MENU_XMLIDS,
    order_root_menu_items,
)


@tagged("post_install", "-at_install", "usl_locale")
class TestFocusedAppLauncher(TransactionCase):
    def test_home_is_the_first_primary_destination_when_installed(self):
        self.assertEqual(PRIMARY_ROOT_MENU_XMLIDS[0], "usl_home.menu_usl_home_root")

    def test_distribution_root_menu_order_preserves_unspecified_apps(self):
        unspecified = "an_unranked_module.menu_root"
        unordered = [
            *reversed(PRIMARY_ROOT_MENU_XMLIDS),
            *reversed(TRAILING_ROOT_MENU_XMLIDS),
            *reversed(SECONDARY_ROOT_MENU_XMLIDS),
            unspecified,
        ]

        self.assertEqual(
            order_root_menu_items(unordered, lambda xmlid: xmlid),
            [
                *PRIMARY_ROOT_MENU_XMLIDS,
                unspecified,
                *SECONDARY_ROOT_MENU_XMLIDS,
                *TRAILING_ROOT_MENU_XMLIDS,
            ],
        )

    def test_commerce_apps_are_contiguous(self):
        """B2C, sales, purchase, inventory and manufacturing must not be split."""
        commerce = (
            "usl_b2c.menu_b2c_root",
            "sale.sale_menu_root",
            "purchase.menu_purchase_root",
            "stock.menu_stock_root",
            "mrp.menu_mrp_root",
        )
        positions = [PRIMARY_ROOT_MENU_XMLIDS.index(xmlid) for xmlid in commerce]

        self.assertEqual(positions, list(range(positions[0], positions[0] + len(commerce))))

    def test_employees_immediately_precede_tese_payroll(self):
        """Payroll belongs with the people apps, not with the signing tools."""
        employees_position = SECONDARY_ROOT_MENU_XMLIDS.index("hr.menu_hr_root")
        tese_position = SECONDARY_ROOT_MENU_XMLIDS.index(
            "usl_tese_payroll.menu_tese_payroll_root"
        )

        self.assertEqual(employees_position + 1, tese_position)

    def test_sign_closes_the_supporting_apps(self):
        self.assertEqual(
            SECONDARY_ROOT_MENU_XMLIDS[-1], "sign_oca.sign_oca_root_menu"
        )

    def test_loaded_launcher_keeps_commerce_before_supporting_apps(self):
        """End-to-end: the order the web client receives, not just the constants."""
        menus = self.env["ir.ui.menu"].load_menus(debug=False)
        loaded = [
            menus[menu_id].get("xmlid")
            for menu_id in menus["root"]["children"]
            if menus.get(menu_id, {}).get("xmlid")
        ]
        ranked = [
            xmlid
            for xmlid in loaded
            if xmlid in PRIMARY_ROOT_MENU_XMLIDS + SECONDARY_ROOT_MENU_XMLIDS
        ]
        expected = [
            xmlid
            for xmlid in PRIMARY_ROOT_MENU_XMLIDS + SECONDARY_ROOT_MENU_XMLIDS
            if xmlid in set(ranked)
        ]

        self.assertEqual(ranked, expected)
        self.assertIn("sale.sale_menu_root", ranked)
        self.assertIn("sign_oca.sign_oca_root_menu", ranked)
        self.assertLess(
            ranked.index("sale.sale_menu_root"),
            ranked.index("sign_oca.sign_oca_root_menu"),
        )

    def test_contacts_immediately_precede_employees(self):
        contacts_position = SECONDARY_ROOT_MENU_XMLIDS.index(
            "contacts.menu_contacts"
        )
        employees_position = SECONDARY_ROOT_MENU_XMLIDS.index("hr.menu_hr_root")

        self.assertEqual(contacts_position + 1, employees_position)

    def test_settings_closes_the_launcher(self):
        self.assertEqual(TRAILING_ROOT_MENU_XMLIDS, ("base.menu_administration",))

    def test_distribution_deemphasizes_expected_root_menus(self):
        self.assertEqual(
            DEEMPHASIZED_ROOT_MENU_XMLIDS,
            (
                "mail.menu_root_discuss",
                "project_todo.menu_todo_todos",
                "spreadsheet_dashboard.spreadsheet_dashboard_menu_root",
                "base.menu_management",
                "utm.menu_link_tracker_root",
                "usl_feedback.menu_feedback_root",
                "base.menu_tests",
            ),
        )

    def test_official_documents_is_reachable_without_a_launcher_tile(self):
        """Blacklisting the root alone left its children reachable by URL only.

        Correspondence, Templates and Output Inventory exist nowhere else in the
        menu tree, so the root now hangs under Settings instead: off the
        launcher, but reachable.
        """
        menu_model = self.env["ir.ui.menu"]
        root = self.env.ref(
            "usl_document_templates.menu_official_documents_root",
            raise_if_not_found=False,
        )
        self.assertTrue(root, "the Official Documents root should exist")
        settings = self.env.ref("base.menu_administration")
        self.assertEqual(root.parent_id, settings)
        self.assertNotIn(root.id, menu_model._load_menus_blacklist())

        loaded = menu_model.load_menus(debug=False)
        self.assertNotIn(
            root.id, loaded["root"]["children"],
            "Official Documents must not claim a launcher tile",
        )
        self.assertIn(root.id, loaded, "its subtree must still load, under Settings")
        for xmlid in (
            "usl_document_templates.menu_document_letters",
            "usl_document_templates.menu_document_templates",
            "usl_document_templates.menu_report_output_inventory",
        ):
            child = self.env.ref(xmlid, raise_if_not_found=False)
            self.assertTrue(child, f"{xmlid} should exist")
            self.assertIn(child.id, loaded, f"{xmlid} must be reachable from the menu")

    def test_ungated_core_test_menu_never_reaches_the_launcher(self):
        """`base.menu_tests` carries no group, so only the blacklist hides it."""
        menu_model = self.env["ir.ui.menu"]
        tests_menu = self.env.ref("base.menu_tests", raise_if_not_found=False)
        self.assertTrue(tests_menu, "base.menu_tests should exist in every database")
        self.assertFalse(
            tests_menu.group_ids,
            "if core ever gates this menu, the blacklist entry can be revisited",
        )

        self.assertNotIn(tests_menu.id, menu_model.load_menus(debug=False))
        self.assertNotIn(tests_menu.id, menu_model.load_menus(debug=True))

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
