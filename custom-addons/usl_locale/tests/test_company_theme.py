import json
from uuid import uuid4

from odoo import Command
from odoo.exceptions import AccessError, ValidationError
from odoo.tests import HttpCase, new_test_user, tagged

from odoo.addons.usl_locale.models.res_company import _AUTOMATIC_THEME_COLORS


@tagged("post_install", "-at_install", "usl_locale")
class TestCompanyTheme(HttpCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.company_a = cls.env["res.company"].create({
            "name": "Theme Company A",
            "company_registry": "THEME-A",
        })
        cls.company_b = cls.env["res.company"].create({
            "name": "Theme Company B",
            "company_registry": "THEME-B",
            "usl_ui_theme_color": "#1a2b3c",
        })
        cls.user_password = "theme"
        cls.user = new_test_user(
            cls.env,
            login="company-theme-user",
            password=cls.user_password,
            company_id=cls.company_a.id,
            company_ids=[Command.set((cls.company_a | cls.company_b).ids)],
            groups="base.group_user",
        )

    def test_automatic_palette_uses_company_id_modulo(self):
        companies = self.env["res.company"].create([
            {"name": f"Automatic Theme Company {index}"}
            for index in range(16)
        ])

        for company in companies:
            self.assertEqual(
                company._get_usl_ui_theme_color(),
                _AUTOMATIC_THEME_COLORS[
                    (company.id - 1) % len(_AUTOMATIC_THEME_COLORS)
                ],
            )
        self.assertEqual(
            companies[0]._get_usl_ui_theme_color(),
            companies[15]._get_usl_ui_theme_color(),
        )
        self.assertNotIn("#714B67", _AUTOMATIC_THEME_COLORS)

    def test_explicit_color_and_reset(self):
        self.assertEqual(self.company_b._get_usl_ui_theme_color(), "#1A2B3C")
        self.company_b.usl_ui_theme_color = "#714b67"
        self.assertEqual(self.company_b._get_usl_ui_theme_color(), "#714B67")

        self.company_b.action_use_automatic_usl_ui_theme_color()
        self.assertFalse(self.company_b.usl_ui_theme_color)
        self.assertEqual(
            self.company_b._get_usl_ui_theme_color(),
            _AUTOMATIC_THEME_COLORS[
                (self.company_b.id - 1) % len(_AUTOMATIC_THEME_COLORS)
            ],
        )

    def test_branch_inherits_until_customized(self):
        other_parent = self.env["res.company"].create({
            "name": "Other Theme Parent",
            "usl_ui_theme_color": "#B07A2A",
        })
        branch = self.env["res.company"].create({
            "name": "Theme Branch",
            "parent_id": self.company_a.id,
        })

        self.assertEqual(
            branch._get_usl_ui_theme_color(),
            self.company_a._get_usl_ui_theme_color(),
        )
        self.company_a.usl_ui_theme_color = "#4F7A3A"
        self.assertEqual(branch._get_usl_ui_theme_color(), "#4F7A3A")

        branch.usl_ui_theme_color = "#B44F7A"
        self.company_a.usl_ui_theme_color = "#536A7A"
        self.assertEqual(branch._get_usl_ui_theme_color(), "#B44F7A")

        branch.action_use_automatic_usl_ui_theme_color()
        self.assertEqual(branch._get_usl_ui_theme_color(), "#536A7A")

        # Odoo freezes company hierarchies after creation. Simulate a controlled
        # reconstruction changing the parent to verify that the color stays dynamic.
        self.env.cr.execute(
            "UPDATE res_company SET parent_id = %s WHERE id = %s",
            (other_parent.id, branch.id),
        )
        branch.invalidate_recordset(["parent_id", "usl_resolved_ui_theme_color"])
        self.assertEqual(branch._get_usl_ui_theme_color(), "#B07A2A")

    def test_native_company_color_does_not_change_interface_color(self):
        expected = self.company_a._get_usl_ui_theme_color()

        self.company_a.color = 11 if self.company_a.color != 11 else 10

        self.assertEqual(self.company_a._get_usl_ui_theme_color(), expected)

    def test_invalid_color_is_rejected(self):
        for invalid_color in ("1A2B3C", "#123", "#GGGGGG", "blue"):
            with self.assertRaises(ValidationError):
                self.company_a.usl_ui_theme_color = invalid_color
        self.company_a.usl_ui_theme_color = False

    def test_company_permissions_remain_native(self):
        self.assertEqual(
            self.company_a.with_user(self.user).usl_ui_theme_color,
            self.company_a.usl_ui_theme_color,
        )
        self.assertEqual(
            self.company_a.with_user(self.user).usl_resolved_ui_theme_color,
            self.company_a.usl_resolved_ui_theme_color,
        )
        with self.assertRaises(AccessError):
            self.company_a.with_user(self.user).usl_ui_theme_color = "#112233"

        restricted = self.env["res.company"].create({"name": "Restricted Theme Company"})
        self.assertFalse(
            self.env["res.company"].with_user(self.user).search_count([
                ("id", "=", restricted.id),
            ]),
        )

    def test_session_payload_includes_resolved_colors(self):
        self.company_b.usl_ui_theme_color = "#1a2b3c"
        self.authenticate(self.user.login, self.user_password)
        self.env["res.users.settings"]._find_or_create_for_user(self.user)
        payload = json.dumps({"jsonrpc": "2.0", "method": "call", "id": str(uuid4())})
        response = self.url_open(
            "/web/session/get_session_info",
            data=payload,
            headers={"Content-Type": "application/json"},
        )
        self.assertEqual(response.status_code, 200)
        companies = response.json()["result"]["user_companies"]["allowed_companies"]
        self.assertEqual(
            companies[str(self.company_a.id)]["usl_ui_theme_color"],
            self.company_a._get_usl_ui_theme_color(),
        )
        self.assertEqual(
            companies[str(self.company_b.id)]["usl_ui_theme_color"],
            "#1A2B3C",
        )

    def test_company_color_field_is_the_user_facing_color(self):
        fields_description = self.env["res.company"].fields_get([
            "color",
            "usl_ui_theme_color",
        ])

        self.assertEqual(fields_description["usl_ui_theme_color"]["string"], "Color")
        self.assertEqual(fields_description["color"]["type"], "integer")
        self.assertEqual(fields_description["color"]["string"], "Technical color index")

        company_arch = self.env.ref("base.view_company_form")._get_combined_arch()
        native_color = company_arch.xpath("//field[@name='color']")
        self.assertEqual(len(native_color), 1)
        self.assertEqual(native_color[0].get("invisible"), "1")
        self.assertTrue(company_arch.xpath("//field[@name='usl_ui_theme_color']"))

        user_arch = self.env.ref("base.view_users_form")._get_combined_arch()
        company_tags = user_arch.xpath("//field[@name='company_ids']")
        self.assertEqual(len(company_tags), 1)
        self.assertEqual(company_tags[0].get("widget"), "many2many_tags_color_dot")
        self.assertIn(
            "'color_field': 'usl_resolved_ui_theme_color'",
            company_tags[0].get("options"),
        )
