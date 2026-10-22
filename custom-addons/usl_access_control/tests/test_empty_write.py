"""Native empty mutations do not grant authority over actual records or RPCs."""

import json

from odoo import Command
from odoo.exceptions import AccessError
from odoo.tests import HttpCase, tagged
from odoo.tests.common import new_test_user
from odoo.tools import SQL

from ..models.agent_credential import UslAgentKeyWizard


@tagged("post_install", "-at_install", "usl_empty_write")
class TestAgentEmptyWrite(HttpCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.company = cls.env.company
        cls.foreign = cls.env["res.company"].create({"name": "[QA empty write] Foreign"})
        cls.owner = new_test_user(
            cls.env, login="qa.empty.write.owner", groups="account.group_account_manager",
            company_id=cls.company.id, company_ids=[Command.set(cls.company.ids)],
            usl_identity_classification="active",
        )
        cls.accounting_group = cls.env.ref("account.group_account_manager")
        cls.writer = cls.env["usl.agent"].with_user(cls.owner).create({
            "name": "[QA empty write] Accountant", "purpose": "Native accounting workflow",
            "company_id": cls.company.id, "company_ids": [Command.set(cls.company.ids)],
            "access_mode": "read_write", "delegated_group_ids": [Command.set(cls.accounting_group.ids)],
        })
        cls.reader = cls.env["usl.agent"].with_user(cls.owner).create({
            "name": "[QA empty write] Reader", "purpose": "Read-only boundary",
            "company_id": cls.company.id, "company_ids": [Command.set(cls.company.ids)],
        })
        cls.partner = cls.env["res.partner"].create({"name": "[QA empty write] Customer"})
        cls.order = cls.env["sale.order"].create({"partner_id": cls.partner.id})
        cls.line = cls.env["sale.order.line"].create({
            "order_id": cls.order.id, "name": "[QA empty write] Line",
            "product_uom_qty": 1, "price_unit": 10,
        })
        cls.parameter = cls.env["ir.config_parameter"].create({
            "key": "qa.empty.write.fixture", "value": "Unchanged",
        })
        cls.journal = cls.env["account.journal"].create({
            "name": "[QA empty write] Misc", "code": "QEW", "type": "general",
            "company_id": cls.company.id,
        })
        cls.move = cls.env["account.move"].create({"journal_id": cls.journal.id})
        foreign_journal = cls.env["account.journal"].create({
            "name": "[QA empty write] Foreign", "code": "QEF", "type": "general",
            "company_id": cls.foreign.id,
        })
        cls.foreign_move = cls.env["account.move"].with_company(cls.foreign).create({
            "journal_id": foreign_journal.id,
        })

    def _fingerprints(self):
        self.env.flush_all()
        result = {}
        for table in ("account_move", "account_move_line", "sale_order", "sale_order_line",
                      "ir_config_parameter", "res_users", "ir_attachment", "mail_message", "usl_audit_event",
                      "rating_rating", "account_asset"):
            self.cr.execute(SQL(
                "SELECT md5(COALESCE(string_agg(to_jsonb(t)::text, '' ORDER BY id), '')) FROM %s t",
                SQL.identifier(table),
            ))
            result[table] = self.cr.fetchone()[0]
        return result

    def test_empty_native_write_and_sudo_match_human_without_mutation(self):
        self.assertFalse(self.writer.user_id.has_group("sales_team.group_sale_salesman"))
        before = self._fingerprints()
        values = {"qty_delivered": 0, "product_uom_qty": 0, "expense_ids": [Command.clear()]}
        for actor in (self.owner, self.writer.user_id, self.reader.user_id):
            for sudo in (False, True):
                with self.subTest(actor=actor.id, sudo=sudo):
                    empty = self.env["sale.order.line"].with_user(actor).sudo(sudo)
                    self.assertTrue(empty.write(dict(values)))
                    # Native Base.write returns before checking field names.
                    self.assertTrue(empty.write({"qa_field_does_not_exist": "ignored"}))
        self.assertEqual(before, self._fingerprints())

    def test_empty_native_unlink_and_sudo_match_human_without_mutation(self):
        before = self._fingerprints()
        for model in ("rating.rating", "account.asset", "res.users", "ir.config_parameter"):
            for actor in (self.owner, self.writer.user_id, self.reader.user_id):
                for sudo in (False, True):
                    with self.subTest(model=model, actor=actor.id, sudo=sudo):
                        self.assertTrue(self.env[model].with_user(actor).sudo(sudo).unlink())
        self.assertEqual(before, self._fingerprints())

    def test_nonempty_unlink_authority_remains_required(self):
        before = self._fingerprints()
        for record in (
            self.owner.with_user(self.writer.user_id),
            self.move.with_user(self.writer.user_id),
            self.move.with_user(self.reader.user_id),
            self.line.with_user(self.writer.user_id).sudo(),
            self.foreign_move.with_user(self.writer.user_id).with_context(
                allowed_company_ids=self.foreign.ids,
            ),
        ):
            with self.subTest(model=record._name, uid=record.env.uid):
                with self.assertRaises(AccessError):
                    record.unlink()
        self.assertEqual(before, self._fingerprints())
        self.owner.group_ids = [Command.set(self.env.ref("base.group_user").ids)]
        with self.assertRaises(AccessError):
            self.move.with_user(self.writer.user_id).unlink()
        self.assertTrue(self.move.exists())

    def test_empty_native_create_and_sudo_match_human_without_mutation(self):
        before = self._fingerprints()
        for model in ("account.move", "sale.order.line", "res.users", "ir.config_parameter"):
            for actor in (self.owner, self.writer.user_id, self.reader.user_id):
                for sudo in (False, True):
                    with self.subTest(model=model, actor=actor.id, sudo=sudo):
                        created = self.env[model].with_user(actor).sudo(sudo).create([])
                        self.assertEqual(created._name, model)
                        self.assertFalse(created)
        self.assertEqual(before, self._fingerprints())

    def test_real_create_values_retain_authority_checks(self):
        before = self._fingerprints()
        for values in ({}, [{}]):
            for model, actor, sudo in (
                ("account.move", self.reader.user_id, False),
                ("account.move", self.writer.user_id, True),
                ("res.users", self.writer.user_id, False),
                ("ir.config_parameter", self.writer.user_id, False),
            ):
                with self.subTest(values=values, model=model, sudo=sudo):
                    with self.assertRaises(AccessError):
                        self.env[model].with_user(actor).sudo(sudo).create(values)
        self.assertEqual(before, self._fingerprints())
        actual = self.env["account.account.tag"].with_user(self.writer.user_id).create({
            "name": "[QA empty create] Actual account tag",
        })
        self.assertTrue(actual.exists())
        self.assertEqual(actual.create_uid, self.writer.user_id)

    def test_empty_identity_and_configuration_writes_retain_native_noop(self):
        before = self._fingerprints()
        for model in ("res.users", "ir.config_parameter"):
            for actor in (self.owner, self.writer.user_id):
                with self.subTest(model=model, actor=actor.id):
                    self.assertTrue(self.env[model].with_user(actor).write({"qa_field_does_not_exist": "ignored"}))
        self.assertEqual(before, self._fingerprints())

    def test_nonempty_sales_identity_configuration_and_readonly_remain_denied(self):
        before = self._fingerprints()
        user = self.writer.user_id
        for record, values in (
            (self.line.with_user(user).sudo(), {"name": "Forbidden"}),
            (self.owner.with_user(user), {"name": "Forbidden"}),
            (self.parameter.with_user(user), {"value": "Forbidden"}),
        ):
            with self.subTest(model=record._name):
                with self.assertRaises(AccessError):
                    record.write(values)
        with self.assertRaises(AccessError):
            self.move.with_user(self.reader.user_id).write({"ref": "Forbidden"})
        self.assertEqual(before, self._fingerprints())

    def test_nonempty_foreign_company_and_current_owner_loss_remain_denied(self):
        user = self.writer.user_id
        with self.assertRaises(AccessError):
            self.foreign_move.with_user(user).with_context(
                allowed_company_ids=self.foreign.ids,
            ).write({"ref": "Forbidden"})
        self.owner.group_ids = [Command.set(self.env.ref("base.group_user").ids)]
        with self.assertRaises(AccessError):
            self.move.with_user(user).write({"ref": "Forbidden"})
        self.assertFalse(self.move.ref)
        self.assertFalse(self.foreign_move.ref)

    def test_public_readonly_write_stays_denied_with_empty_ids(self):
        wizard = self.env["usl.agent.key.wizard"].with_user(self.owner).create({
            "agent_id": self.reader.id, "name": "[QA empty write] HTTP",
        })
        key = UslAgentKeyWizard.action_generate.__wrapped__(wizard)["context"]["default_key"]
        before = self._fingerprints()
        before.pop("usl_audit_event")
        response = self.url_open(
            "/json/2/account.move/write", data=json.dumps({"ids": [], "vals": {"ref": "Forbidden"}}),
            headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json",
                     "X-Odoo-Database": self.env.cr.dbname},
        )
        self.assertEqual(response.status_code, 403, response.text)
        self.assertIn("agent_read_only_action_denied", response.text)
        after = self._fingerprints()
        after.pop("usl_audit_event")
        self.assertEqual(before, after)

    def test_public_empty_unlink_retains_readonly_and_irreversible_checks(self):
        for agent, code in (
            (self.reader, "agent_read_only_action_denied"),
            (self.writer, "agent_read_only_action_denied"),
        ):
            wizard = self.env["usl.agent.key.wizard"].with_user(self.owner).create({
                "agent_id": agent.id, "name": "[QA empty unlink] HTTP",
            })
            key = UslAgentKeyWizard.action_generate.__wrapped__(wizard)["context"]["default_key"]
            before = self._fingerprints()
            before.pop("usl_audit_event")
            response = self.url_open(
                "/json/2/account.move/unlink", data=json.dumps({"ids": []}),
                headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json",
                         "X-Odoo-Database": self.env.cr.dbname},
            )
            self.assertEqual(response.status_code, 403, response.text)
            self.assertIn(code, response.text)
            after = self._fingerprints()
            after.pop("usl_audit_event")
            self.assertEqual(before, after)

    def test_public_readonly_create_stays_denied_with_empty_values(self):
        wizard = self.env["usl.agent.key.wizard"].with_user(self.owner).create({
            "agent_id": self.reader.id, "name": "[QA empty create] HTTP",
        })
        key = UslAgentKeyWizard.action_generate.__wrapped__(wizard)["context"]["default_key"]
        before = self._fingerprints()
        before.pop("usl_audit_event")
        response = self.url_open(
            "/json/2/account.move/create", data=json.dumps({"vals_list": []}),
            headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json",
                     "X-Odoo-Database": self.env.cr.dbname},
        )
        self.assertEqual(response.status_code, 403, response.text)
        self.assertIn("agent_read_only_action_denied", response.text)
        after = self._fingerprints()
        after.pop("usl_audit_event")
        self.assertEqual(before, after)
