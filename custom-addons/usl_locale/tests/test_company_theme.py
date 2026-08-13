import json
from uuid import uuid4

from odoo import Command
from odoo.exceptions import AccessError, ValidationError
from odoo.tests import HttpCase, new_test_user, tagged


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

    def test_explicit_and_automatic_colors_are_stable(self):
        color = self.company_a._get_usl_ui_theme_color()
        self.assertRegex(color, r"^#[0-9A-F]{6}$")
        self.assertEqual(color, self.company_a._get_usl_ui_theme_color())
        self.assertEqual(self.company_b._get_usl_ui_theme_color(), "#1A2B3C")
        self.company_b.action_use_automatic_usl_ui_theme_color()
        self.assertFalse(self.company_b.usl_ui_theme_color)
        self.assertRegex(self.company_b._get_usl_ui_theme_color(), r"^#[0-9A-F]{6}$")

        equivalent = self.env["res.company"].create({
            "name": "Different display name",
            "company_registry": self.company_a.company_registry,
        })
        self.assertEqual(color, equivalent._get_usl_ui_theme_color())

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
