"""An ordinary operator reads a batch without generic source-row write rights."""
from hashlib import sha256
from unittest.mock import patch

from odoo import Command
from odoo.exceptions import AccessError
from odoo.tests import TransactionCase, new_test_user, tagged
from odoo.tools.binary import BinaryBytes

from . import fixtures


@tagged("post_install", "-at_install", "b2c_import_operator_read")
class TestImportOperatorRead(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.company = cls.env["res.company"].create({
            "name": "[QA] Import workshop", "currency_id": cls.env.ref("base.EUR").id,
        })
        cls.other_company = cls.env["res.company"].create({"name": "[QA] Other import workshop"})
        cls.operator = new_test_user(
            cls.env, login="qa.import.operator", groups="base.group_user,usl_b2c.group_b2c_operator",
            company_id=cls.company.id, company_ids=[Command.set((cls.company | cls.other_company).ids)],
        )
        cls.reader = new_test_user(
            cls.env, login="qa.import.reader", groups="base.group_user,usl_b2c.group_b2c_reader",
            company_id=cls.company.id, company_ids=[Command.set(cls.company.ids)],
        )
        cls.manager = new_test_user(
            cls.env, login="qa.import.evidence.manager",
            groups="base.group_user,usl_b2c.group_b2c_manager,usl_b2c.group_b2c_sensitive_evidence",
            company_id=cls.company.id, company_ids=[Command.set(cls.company.ids)],
        )
        plan = cls.env["account.analytic.plan"].search([("name", "=", "Channel")], limit=1)
        if not plan:
            plan = cls.env["account.analytic.plan"].create({"name": "Channel"})
        account = cls.env["account.analytic.account"].create({
            "name": "[QA] Import workshop channel", "plan_id": plan.id, "company_id": cls.company.id,
        })
        cls.channel = cls.env["b2c.channel"].create({
            "name": "[QA] Import workshop", "code": "medusa", "company_id": cls.company.id,
            "analytic_account_id": account.id,
        })
        cls.content = fixtures.medusa_orders([{
            "order_number": "QA-OP-101", "date": "2026-11-03", "order_status": "pending",
            "customer_email": "qa.operator@example.invalid", "currency": "EUR",
            "item_1_sku": "QA-PAPER-BLUE", "item_1_product": "Invented paper model",
            "item_1_variant": "Blue", "item_1_qty": "1", "item_1_unit_price": "17.00",
            "item_1_total": "17.00",
        }])

    def _as(self, record, actor):
        return record.with_user(actor).with_context(allowed_company_ids=self.company.ids)

    def _batch(self, *, company=None, actor=None, content=None):
        model = self.env["b2c.import.batch"]
        if actor:
            model = self._as(model, actor)
        batch = model.create({"name": "[QA] Read and reread", "company_id": (company or self.company).id})
        batch.env["b2c.import.file"].create({
            "batch_id": batch.id, "name": "synthetic-workshop.csv",
            "content": BinaryBytes(self.content if content is None else content),
        })
        return batch

    def _snapshot(self, batch):
        batch = batch.with_env(self.env)
        batch.invalidate_recordset()
        return {
            "batch": batch.read(["state", "report", "period_start", "period_end"]),
            "rows": batch.row_ids.read(["batch_id", "file_id", "values", "resolution", "order_id", "payload_digest"]),
            "issues": batch.issue_ids.read(["batch_id", "kind", "name", "note"]),
            "files": batch.file_ids.read(["checksum", "state", "row_count", "order_count", "line_count"]),
            "bytes": [sha256(source._raw_content()).hexdigest() for source in batch.file_ids],
        }

    def test_operator_reads_uploaded_file_and_sees_normalized_results(self):
        for state in ("draft", "parsed", "resolved"):
            batch = self._batch(actor=self.operator)
            batch.write({"state": state})
            original_ids = batch.file_ids.ids
            with patch.object(type(self.env["account.move"]), "_write", side_effect=AssertionError("Reading wrote Accounting")):
                self.assertTrue(batch.action_parse())
            self.assertEqual(batch.state, "parsed")
            self.assertTrue(batch.row_ids)
            self.assertIn("order_money_missing", batch.issue_ids.mapped("kind"))
            self.assertEqual(batch.file_ids.ids, original_ids)
            self.assertEqual(batch.file_ids._raw_content(), self.content)
            self.assertEqual(batch.file_ids.checksum, sha256(self.content).hexdigest())
            self.assertTrue(batch.row_ids.read(["values"]))
            self.assertNotIn("payload", batch.row_ids.fields_get(["payload"]))
            with self.assertRaises(AccessError):
                batch.row_ids.read(["payload"])

    def test_operator_rereads_without_accumulating_results_or_changing_original(self):
        batch = self._batch()
        self._as(batch, self.manager).action_parse()
        old_rows = batch.row_ids.ids
        facts = batch.row_ids.read(["grain", "external_order_id", "values", "payload_digest", "resolution"])
        kinds = batch.issue_ids.mapped("kind")
        self.assertTrue(self._as(batch, self.operator).action_parse())
        self.assertFalse(set(old_rows) & set(batch.row_ids.ids))
        after = batch.row_ids.read(["grain", "external_order_id", "values", "payload_digest", "resolution"])
        self.assertEqual([{k: v for k, v in row.items() if k != "id"} for row in facts],
                         [{k: v for k, v in row.items() if k != "id"} for row in after])
        self.assertEqual(batch.issue_ids.mapped("kind"), kinds)
        self.assertEqual(batch.file_ids._raw_content(), self.content)

    def test_operator_gets_an_unreadable_file_finding_instead_of_an_acl_error(self):
        batch = self._batch(actor=self.operator, content=b"fictional,unknown\nblue,paper\n")
        self.assertTrue(batch.action_parse())
        self.assertEqual(batch.state, "parsed")
        self.assertFalse(batch.row_ids)
        self.assertEqual(batch.issue_ids.mapped("kind"), ["unreadable_file"])
        self.assertEqual(batch.file_ids.state, "unreadable")

    def test_reader_and_wrong_selected_company_preserve_the_batch(self):
        for actor, companies in ((self.reader, self.company), (self.operator, self.other_company)):
            batch = self._batch()
            before = self._snapshot(batch)
            with self.assertRaises(AccessError):
                batch.with_user(actor).with_context(allowed_company_ids=companies.ids).action_parse()
            self.assertEqual(self._snapshot(batch), before)

    def test_applied_batch_gets_no_new_operator_privilege_and_manager_keeps_native_path(self):
        batch = self._batch()
        self._as(batch, self.manager).action_parse()
        batch.write({"state": "applied"})
        before = self._snapshot(batch)
        with self.assertRaises(AccessError):
            self._as(batch, self.operator).action_parse()
        self.assertEqual(self._snapshot(batch), before)
        # Existing native CRUD authority, not a newly delegated applied workflow.
        self.assertTrue(self._as(batch, self.manager).action_parse())
        self.assertEqual(batch.state, "parsed")
        self.assertEqual(batch.file_ids._raw_content(), self.content)

    def test_direct_row_and_finding_permissions_remain_native(self):
        batch = self._batch()
        self._as(batch, self.manager).action_parse()
        row = self._as(batch.row_ids[:1], self.operator)
        issue = self._as(batch.issue_ids[:1], self.operator)
        self.assertTrue(row.read(["values"]))
        for record, operation, values in ((row, "write", {"note": "Synthetic direct edit"}),
                                           (row, "unlink", None), (issue, "unlink", None)):
            with self.assertRaises(AccessError), self.cr.savepoint():
                getattr(record, operation)(values) if values else getattr(record, operation)()
        with self.assertRaises(AccessError), self.cr.savepoint():
            self._as(self.env["b2c.import.issue"], self.operator).create({
                "batch_id": batch.id, "kind": "unreadable_file", "name": "[QA] Direct finding",
            })

    def test_failed_reread_preserves_report_rows_and_original(self):
        batch = self._batch(actor=self.operator)
        batch.action_parse()
        before = self._snapshot(batch)
        with patch.object(type(batch), "_build_report", side_effect=ValueError("Report unavailable")):
            with self.assertRaisesRegex(ValueError, "Report unavailable"):
                batch.action_parse()
        self.assertEqual(self._snapshot(batch), before)

    def test_matching_order_uses_only_orders_the_operator_can_read(self):
        order = self.env["b2c.order"].create({
            "name": "[QA] Existing order", "canonical_key": "synthetic:workshop-101",
            "company_id": self.company.id, "channel_id": self.channel.id, "source_provider": "medusa",
            "external_order_id": "QA-OP-101", "order_date": "2026-11-03",
            "currency_id": self.company.currency_id.id,
        })
        batch = self._batch(actor=self.operator)
        batch.action_parse()
        self.assertEqual(batch.row_ids.filtered(lambda row: row.grain == "order").order_id.id, order.id)
        self.env["ir.rule"].create({
            "name": "[QA] Orders outside this review", "model_id": self.env["ir.model"]._get_id("b2c.order"),
            "domain_force": repr([("id", "!=", order.id)]),
        })
        batch.action_parse()
        header = batch.row_ids.filtered(lambda row: row.grain == "order")
        self.assertFalse(header.order_id)
        self.assertEqual(header.resolution, "new")

    def test_native_agent_delegation_follows_the_operator(self):
        if "usl.agent" not in self.env:
            self.skipTest("Agent delegation requires the distribution registry.")
        from odoo.addons.usl_access_control.controllers.json2 import UslAgentJson2Controller

        for mode in ("read_write", "read_only"):
            agent = self.env["usl.agent"].with_user(self.operator).create({
                "name": f"[QA] Import reader {mode}", "purpose": "Review synthetic shop imports",
                "owner_id": self.operator.id, "company_id": self.company.id,
                "company_ids": [Command.set(self.company.ids)], "access_mode": mode,
                "delegated_group_ids": [Command.set(self.env.ref("usl_b2c.group_b2c_operator").ids)],
            })
            batch = self._batch(actor=self.operator)
            delegated = self._as(batch, agent.user_id)
            call = {"agent": agent, "model_name": batch._name, "method_name": "action_parse"}
            if mode == "read_only":
                before = self._snapshot(batch)
                with self.assertRaises(AccessError):
                    UslAgentJson2Controller._check_agent_call(**call, kwargs={})
                self.assertEqual(self._snapshot(batch), before)
            else:
                access = UslAgentJson2Controller._check_agent_call(**call, kwargs={})
                context = UslAgentJson2Controller._agent_call_context(
                    **call, access=access, context={"allowed_company_ids": self.company.ids},
                )
                self.assertTrue(delegated.with_context(context).action_parse())
                self.assertEqual(batch.state, "parsed")
                self.assertTrue(delegated.row_ids.read(["values"]))
