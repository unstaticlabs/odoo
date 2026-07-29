from urllib.parse import parse_qsl, urlsplit

from odoo import Command
from odoo.exceptions import AccessError, ValidationError
from odoo.tests import TransactionCase, new_test_user, tagged


@tagged("post_install", "-at_install", "usl_canonical_navigation")
class TestCanonicalNavigation(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.company = cls.env.company
        cls.other_company = cls.env["res.company"].create({"name": "Navigation Other"})
        cls.env.user.company_ids = [Command.link(cls.other_company.id)]
        cls.reader = new_test_user(
            cls.env,
            login="navigation-reader",
            groups="base.group_user",
            company_id=cls.company.id,
            company_ids=[Command.set([cls.company.id])],
        )
        cls.outsider = new_test_user(
            cls.env,
            login="navigation-outsider",
            groups="base.group_user",
            company_id=cls.company.id,
            company_ids=[Command.set([cls.company.id])],
        )
        cls.partner = cls.env["res.partner"].create({"name": "Navigation Target"})
        cls.link = cls.env["usl.navigation.link"]
        cls.workspace_model = cls.env["usl.navigation.workspace"]
        cls.env["ir.config_parameter"].set_str(
            "web.base.url",
            "https://odoo.example.test",
        )

    def test_link_builder_is_deterministic_and_agent_callable(self):
        first = self.link.build_record_url(
            "res.partner",
            self.partner.id,
            action="base.action_partner_form",
            company_ids=[self.company.id],
            state={
                "selection": [9, 2, 9],
                "groupBy": ["company_id", "country_id"],
                "orderBy": [{"name": "name", "asc": True}],
                "view_type": "list",
            },
        )
        second = self.link.build_record_url(
            "res.partner",
            self.partner.id,
            action="base.action_partner_form",
            company_ids=[self.company.id],
            state={
                "view_type": "list",
                "orderBy": [{"asc": True, "name": "name"}],
                "groupBy": ["company_id", "country_id"],
                "selection": [2, 9],
            },
        )

        self.assertEqual(first, second)
        split = urlsplit(first)
        self.assertEqual(
            split.path,
            f"/odoo/action-base.action_partner_form/{self.partner.id}",
        )
        self.assertEqual(
            [key for key, _value in parse_qsl(split.query)],
            ["nv", "cids", "view_type", "groupBy", "orderBy", "selection"],
        )
        self.assertIn("selection=2%2C9", split.query)

    def test_report_link_uses_semantic_options_without_wizard_identifier(self):
        url = self.link.build_report_url(
            "base.action_partner_form",
            "balance_sheet",
            company_ids=[self.company.id],
            filters={
                "period": "custom",
                "date_from": "2026-01-01",
                "date_to": "2026-06-30",
                "comparison": "previous_period",
                "moves": "posted",
            },
            absolute=False,
        )

        query = dict(parse_qsl(urlsplit(url).query))
        self.assertEqual(query["report"], "balance_sheet")
        self.assertEqual(query["date_from"], "2026-01-01")
        self.assertNotIn("res_id", query)
        self.assertNotIn("wizard", url)
        with self.assertRaises(ValidationError):
            self.link.build_report_url(
                "base.action_partner_form",
                "balance_sheet",
                company_ids=[self.company.id],
                filters={"company": self.other_company.id},
            )

    def test_link_builder_rejects_unknown_state_and_company_scope(self):
        with self.assertRaises(ValidationError):
            self.link.build_action_url("base.action_partner_form", state={"globalState": {}})
        with self.assertRaises(AccessError):
            self.link.with_user(self.outsider).build_action_url(
                "base.action_partner_form",
                company_ids=[self.other_company.id],
            )
        with self.assertRaises(AccessError):
            self.link.build_action_url("removed.navigation.action")
        with self.assertRaises(ValidationError):
            self.link.build_action_url(
                "base.action_partner_form",
                state={"selection": list(range(1, 42))},
            )
        with self.assertRaises(ValidationError):
            self.link.build_action_url(
                "base.action_partner_form",
                state={"domain": [["name", "=", "x" * 2_000]]},
            )

    def test_action_scoped_record_link_still_checks_target_access(self):
        owner_only = self.workspace_model.create({
            "name": "Private target",
            "company_ids": [Command.set([self.company.id])],
            "state_json": {"model": "res.partner"},
        })
        with self.assertRaises(AccessError):
            self.link.with_user(self.reader).build_record_url(
                "usl.navigation.workspace",
                owner_only.id,
                action="base.action_partner_form",
                company_ids=[self.company.id],
            )
        self.link.build_record_url(
            "usl.navigation.workspace",
            owner_only.id,
            action="base.action_partner_form",
            company_ids=[self.company.id],
        )

    def test_workspace_round_trip_and_explicit_user_sharing(self):
        result = self.workspace_model.create_workspace(
            {
                "action": "base.action_partner_form",
                "model": "res.partner",
                "domain": [["customer_rank", ">", 0]],
                "groupBy": ["country_id"],
                "selection": [self.partner.id],
            },
            name="Partner review",
            share_mode="users",
            permitted_user_ids=[self.reader.id],
            company_ids=[self.company.id],
        )

        restored = self.workspace_model.with_user(self.reader).read_workspace(
            result["public_id"],
        )
        self.assertEqual(restored["status"], "ok")
        self.assertFalse(restored["owner"])
        self.assertEqual(restored["state"]["nv"], 1)
        self.assertEqual(restored["state"]["selection"], [self.partner.id])
        self.assertIn(f"ws={result['public_id']}", result["url"])
        workspace = self.workspace_model.search([
            ("public_id", "=", result["public_id"]),
        ])
        self.assertTrue(workspace.last_used_at)

    def test_workspace_fails_closed_for_unshared_and_inaccessible_company(self):
        owner_only = self.workspace_model.create_workspace(
            {"action": "base.action_partner_form", "model": "res.partner"},
            company_ids=[self.company.id],
        )
        self.assertEqual(
            self.workspace_model.with_user(self.outsider).read_workspace(
                owner_only["public_id"],
            ),
            {"status": "unavailable"},
        )

        company_workspace = self.workspace_model.with_company(self.other_company).create_workspace(
            {"action": "base.action_partner_form", "model": "res.partner"},
            share_mode="internal",
            company_ids=[self.other_company.id],
        )
        self.assertEqual(
            self.workspace_model.with_user(self.reader).read_workspace(
                company_workspace["public_id"],
            ),
            {"status": "unavailable"},
        )

    def test_workspace_rejects_unsafe_or_oversized_state(self):
        with self.assertRaises(ValidationError):
            self.workspace_model.create_workspace(
                {"model": "res.partner", "access_token": "secret"},
                company_ids=[self.company.id],
            )
        with self.assertRaises(ValidationError):
            self.workspace_model.create_workspace(
                {"model": "res.partner", "domain": [["name", "=", "x" * 300_000]]},
                company_ids=[self.company.id],
            )
        with self.assertRaises(ValidationError):
            self.workspace_model.create_workspace(
                {"model": "res.partner", "panel": {"category_id": [1, 1]}},
                company_ids=[self.company.id],
            )

    def test_search_panel_state_is_semantic_and_deterministic(self):
        result = self.workspace_model.create_workspace(
            {
                "action": "base.action_partner_form",
                "model": "res.partner",
                "panel": {
                    "category_id": [12, 2],
                    "company_type": "company",
                },
            },
            company_ids=[self.company.id],
        )
        restored = self.workspace_model.read_workspace(result["public_id"])
        self.assertEqual(
            restored["state"]["panel"],
            {
                "category_id": [2, 12],
                "company_type": "company",
            },
        )
        self.assertEqual(
            self.workspace_model.validate_state(
                {
                    "action": "base.action_partner_form",
                    "panel": '{"not valid!": 1}',
                },
                [self.company.id],
            ),
            {"status": "unavailable"},
        )

    def test_domain_selection_requires_a_valid_filtered_target(self):
        partner_action = self.env["ir.actions.act_window"].create({
            "name": "Navigation path target",
            "path": "navigation-path-target",
            "res_model": "res.partner",
            "view_mode": "list,form",
        })
        result = self.workspace_model.create_workspace(
            {
                "action": partner_action.path,
                "domain": [["name", "ilike", "Navigation"]],
                "selection_mode": "domain",
            },
            company_ids=[self.company.id],
        )
        restored = self.workspace_model.read_workspace(result["public_id"])
        self.assertEqual(restored["state"]["selection_mode"], "domain")
        with self.assertRaises(ValidationError):
            self.workspace_model.create_workspace(
                {
                    "model": "res.partner",
                    "domain": "not a domain",
                    "selection_mode": "domain",
                },
                company_ids=[self.company.id],
            )

    def test_workspace_rejects_inaccessible_selection(self):
        missing_id = self.env["res.partner"].search([], order="id desc", limit=1).id + 10_000
        with self.assertRaises(AccessError):
            self.workspace_model.create_workspace(
                {
                    "model": "res.partner",
                    "selection": [self.partner.id, missing_id],
                },
                company_ids=[self.company.id],
            )

    def test_direct_state_validation_fails_closed_for_stale_record(self):
        missing_id = self.partner.id
        self.partner.unlink()
        result = self.workspace_model.validate_state(
            {
                "action": "base.action_partner_form",
                "res_id": missing_id,
                "nv": 1,
            },
            [self.company.id],
        )
        self.assertEqual(result, {"status": "unavailable"})

    def test_workspace_read_fails_closed_after_target_is_deleted(self):
        workspace = self.workspace_model.create_workspace(
            {
                "action": "base.action_partner_form",
                "model": "res.partner",
                "res_id": self.partner.id,
            },
            share_mode="users",
            permitted_user_ids=[self.reader.id],
            company_ids=[self.company.id],
        )
        self.partner.unlink()
        self.assertEqual(
            self.workspace_model.with_user(self.reader).read_workspace(
                workspace["public_id"],
            ),
            {"status": "unavailable"},
        )

    def test_missing_workspace_uses_generic_unavailable_result(self):
        self.assertEqual(
            self.workspace_model.with_user(self.reader).read_workspace(
                "00000000-0000-0000-0000-000000000000",
            ),
            {"status": "unavailable"},
        )
