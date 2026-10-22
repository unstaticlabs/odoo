"""Native actions retain business authority on immutable carrier models."""

import json
from datetime import timedelta

from odoo import Command, fields
from odoo.tests import HttpCase, tagged
from odoo.tests.common import new_test_user
from odoo.addons.account.tests.common import AccountTestInvoicingCommon

from ..models.agent_credential import UslAgentKeyWizard


@tagged("post_install", "-at_install", "usl_native_action_dispatch")
class TestNativeActionDispatch(AccountTestInvoicingCommon, HttpCase):
    @classmethod
    def get_default_groups(cls):
        return (super().get_default_groups()
                | cls.env.ref("usl_access_control.group_distribution_administrator")
                | cls.env.ref("usl_access_control.group_irreversible_actions"))

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.company = cls.company_data["company"]
        cls.other_data = cls.setup_other_company(name="[QA action] Foreign")
        cls.other_company = cls.other_data["company"]
        cls.company.currency_id = cls.env.ref("base.EUR")
        cls.day = fields.Date.today() - timedelta(days=10)
        cls.currency = cls.setup_other_currency("USD", rates=[(cls.day, 10 / 9)])
        cls.other_company.currency_id = cls.env.ref("base.EUR")
        cls.env["res.currency.rate"].create({"name": cls.day, "currency_id": cls.currency.id,
                                             "company_id": cls.other_company.id, "rate": 10 / 9})
        cls.bank = cls.company_data["default_journal_bank"]
        cls.bank.reconcile_mode = "edit"
        cls.bank.suspense_account_id.reconcile = True
        cls.accounting = cls.env.ref("account.group_account_manager")
        cls.project = cls.env.ref("project.group_project_manager")
        cls.owner = new_test_user(
            cls.env, login="qa.native.action.owner", groups="account.group_account_manager",
            company_id=cls.company.id, company_ids=[Command.set(cls.company.ids)],
            usl_identity_classification="active", tz="Europe/Paris",
        )
        cls.writer = cls._agent(cls.owner, "writer", cls.accounting)
        cls.reader = cls._agent(cls.owner, "reader", cls.accounting, cls.accounting)
        mixed_owner = new_test_user(
            cls.env, login="qa.native.action.mixed.owner",
            groups="account.group_account_manager,project.group_project_manager",
            company_id=cls.company.id, company_ids=[Command.set(cls.company.ids)],
            usl_identity_classification="active",
        )
        cls.mixed = cls._agent(mixed_owner, "mixed", cls.accounting | cls.project, cls.accounting)

    @classmethod
    def _agent(cls, owner, label, groups, readonly=None):
        return cls.env["usl.agent"].with_user(owner).create({
            "name": "[QA action] " + label, "purpose": "Native action authority",
            "company_id": cls.company.id, "company_ids": [Command.set(cls.company.ids)],
            "delegated_group_ids": [Command.set(groups.ids)],
            "read_only_group_ids": [Command.set((readonly or cls.env["res.groups"]).ids)],
            "access_mode": "mixed" if readonly and readonly != groups else "read_only" if readonly else "read_write",
        })

    def _key(self, actor):
        if actor._name == "res.users":
            return self.env["res.users.apikeys"].with_user(actor)._generate(
                "rpc", "[QA action] Native human", fields.Datetime.now() + timedelta(days=1),
            )
        wizard = self.env["usl.agent.key.wizard"].with_user(actor.owner_id).create({
            "agent_id": actor.id, "name": "[QA action] Fresh HTTP",
        })
        return UslAgentKeyWizard.action_generate.__wrapped__(wizard)["context"]["default_key"]

    def _http(self, key, path, body=None):
        return self.url_open(path, data=json.dumps({
            **(body or {}), "context": {"allowed_company_ids": self.company.ids, "lang": "en_US"},
        }), headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json",
                     "X-Odoo-Database": self.cr.dbname})

    def _settlement(self, label, data=None, taxed=False):
        data = data or self.company_data
        company = data["company"]
        bank = data["default_journal_bank"]
        bank.reconcile_mode = "edit"
        bank.suspense_account_id.reconcile = True
        local = self.env["account.move"].with_company(company).with_context(allowed_company_ids=company.ids).env
        tax = local["account.tax"].create({
            "name": "[QA action] Purchase VAT 20%", "amount": 20,
            "type_tax_use": "purchase", "company_id": company.id,
        }) if taxed else local["account.tax"]
        bill = local["account.move"].create({
            "move_type": "in_invoice", "partner_id": self.partner_a.id,
            "journal_id": data["default_journal_purchase"].id,
            "currency_id": self.currency.id, "invoice_date": self.day, "date": self.day,
            "invoice_currency_rate": 1.25, "ref": "[QA action] " + label,
            "invoice_line_ids": [Command.create({
                "name": "[QA action] Service", "account_id": data["default_account_expense"].id,
                "quantity": 1, "price_unit": 100, "tax_ids": [Command.set(tax.ids)],
            })],
        })
        bill._post(soft=False)
        original = bill._payment_rate_document_snapshot()
        statement = local["account.bank.statement.line"].with_context(rebuild_skip_partner_inference=True).create({
            "journal_id": bank.id, "date": self.day + timedelta(days=1),
            "payment_ref": "[QA action] Independent transfer " + str(bill.id), "amount": -108 if taxed else -90,
        })
        self.env.flush_all()
        source = statement.line_ids.filtered(lambda line: line.account_id != bank.default_account_id)
        self.assertEqual(len(source), 1)
        result = bill.with_user(self.owner).js_use_payment_rate_outstanding_line(source.id)
        audit = local["account.immediate.settlement"].browse(result["settlement_id"])
        self.assertEqual(audit.state, "settled")
        self.assertEqual(bill.payment_state, "paid")
        # Complete native deferred tracking before a later authority change,
        # as the public settlement request does at its transaction boundary.
        self.cr.flush()
        return bill, statement, audit, original

    def _snapshot(self, bill, statement, audit):
        self.env.flush_all()
        (bill | statement.move_id).invalidate_recordset()
        audit.invalidate_recordset()
        return (bill._payment_rate_document_snapshot(), bill.invoice_currency_rate,
                bill.amount_residual, bill.payment_state, audit.state,
                statement.foreign_currency_id.id, statement.amount_currency,
                statement.active_immediate_settlement_id.id,
                (bill.line_ids.matched_debit_ids | bill.line_ids.matched_credit_ids).ids)

    def _assert_restored(self, bill, statement, audit, original, residual=100):
        actual = self._snapshot(bill, statement, audit)
        self.assertEqual(actual[:5], (original, 1.25, residual, "not_paid", "reversed"))
        self.assertEqual(actual[5:], (False, 0, False, []))

    def test_actual_human_and_agent_reverse_with_readonly_carrier(self):
        self.assertEqual(self.writer.owner_id, self.owner)
        self.assertEqual(self.writer.delegated_group_ids, self.accounting)
        for actor in (self.owner, self.writer):
            bill, statement, audit, original = self._settlement(actor._name)
            user = actor if actor._name == "res.users" else actor.user_id
            self.assertTrue(audit.with_user(user).has_access("read"))
            self.assertFalse(audit.with_user(user).has_access("write"))
            key = self._key(actor)
            path = "/json/2/account.immediate.settlement/action_reverse"
            response = self._http(key, path, {"ids": audit.ids})
            self.assertEqual(response.status_code, 200, response.text)
            self.assertIs(response.json(), True)
            self._assert_restored(bill, statement, audit, original)
            before = self._snapshot(bill, statement, audit)
            repeated = self._http(key, path, {"ids": audit.ids})
            self.assertEqual(repeated.status_code, 200, repeated.text)
            self.assertEqual(before, self._snapshot(bill, statement, audit))

    def test_agent_reverses_taxed_original_rate_correction_through_native_audit_action(self):
        bill, statement, audit, original = self._settlement("Taxed correction", taxed=True)
        self.assertAlmostEqual(bill.invoice_currency_rate, 100 / 90, places=6)
        self.assertEqual(bill.line_ids.filtered("tax_line_id").balance, 18)
        settled = self._snapshot(bill, statement, audit)
        path = "/json/2/account.immediate.settlement/action_reverse"
        refused = self._http(self._key(self.reader), path, {"ids": audit.ids})
        self._assert_access_refusal(refused)
        self.assertEqual(self._snapshot(bill, statement, audit), settled)
        response = self._http(self._key(self.writer), path, {"ids": audit.ids})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertIs(response.json(), True)
        self._assert_restored(bill, statement, audit, original, residual=120)
        self.assertEqual(bill.line_ids.filtered("tax_line_id").balance, 16)

    def test_actual_api_document_exposes_action_without_carrier_crud(self):
        key = self._key(self.writer)
        # Prime native credential telemetry on its ordinary write-cursor identity
        # route. Fresh readonly authentication is separately qualified with PR322.
        identity = self._http(key, "/json/2/usl.agent/current_identity")
        self.assertEqual(identity.status_code, 200, identity.text)
        response = self._http(key, "/doc-bearer/account.immediate.settlement.json")
        self.assertEqual(response.status_code, 200, response.text)
        doc = response.json()
        self.assertIn("action_reverse", doc["methods"])
        self.assertTrue(doc["access"]["read"])
        for operation in ("create", "write", "unlink"):
            self.assertFalse(doc["access"][operation])
        reader_key = self._key(self.reader)
        identity = self._http(reader_key, "/json/2/usl.agent/current_identity")
        self.assertEqual(identity.status_code, 200, identity.text)
        reader = self._http(reader_key, "/doc-bearer/account.immediate.settlement.json")
        self.assertEqual(reader.status_code, 200, reader.text)
        self.assertNotIn("action_reverse", reader.json()["methods"])

    def test_readonly_and_mixed_delegation_cannot_reverse(self):
        self.assertTrue(self.mixed._allows_model_operation("project.task", "write"))
        for actor in (self.reader, self.mixed):
            bill, statement, audit, _original = self._settlement(actor.name)
            before = self._snapshot(bill, statement, audit)
            response = self._http(self._key(actor), "/json/2/account.immediate.settlement/action_reverse", {"ids": audit.ids})
            self.assertEqual(response.status_code, 403, response.text)
            self.assertEqual(before, self._snapshot(bill, statement, audit))

    def test_audit_crud_remains_immutable_for_human_and_agent(self):
        bill, statement, audit, _original = self._settlement("Immutable")
        before = self._snapshot(bill, statement, audit)
        for actor in (self.owner, self.writer):
            key = self._key(actor)
            for method, body in (("create", {"vals_list": [{}]}),
                                 ("write", {"ids": audit.ids, "vals": {"provenance": "Forbidden"}}),
                                 ("unlink", {"ids": audit.ids})):
                response = self._http(key, "/json/2/account.immediate.settlement/" + method, body)
                self._assert_access_refusal(response)
        self.assertEqual(before, self._snapshot(bill, statement, audit))

    def test_writable_root_implication_retains_existing_semantics(self):
        accountant = self.env.ref("account.group_account_user")
        self.writer.with_user(self.owner).write({
            "delegated_group_ids": [Command.set((self.accounting | accountant).ids)],
            "read_only_group_ids": [Command.set(accountant.ids)],
        })
        self.assertTrue(self.writer._allows_model_operation("account.move", "write"))
        self.assertEqual(self.writer._api_method_access("account.immediate.settlement", "action_reverse"), "write")

    def test_generic_and_groupless_carrier_acl_do_not_grant_action(self):
        model = self.env["ir.model"]._get("account.immediate.settlement")
        self.env["ir.model.access"].search([("model_id", "=", model.id)]).unlink()
        for group in (self.env.ref("base.group_user"), self.env["res.groups"]):
            acl = self.env["ir.model.access"].create({
                "name": "[QA action] Readonly carrier", "model_id": model.id,
                "group_id": group.id, "perm_read": True,
                "perm_create": False, "perm_write": False, "perm_unlink": False,
            })
            acl.call_cache_clearing_methods()
            self.assertIsNone(self.writer._api_method_access("account.immediate.settlement", "action_reverse"))
            self.assertTrue(self.env["account.immediate.settlement"].with_user(self.owner).has_access("read"))
            acl.unlink()

    def _http_company(self, key, audit, company):
        return self.url_open(
            "/json/2/account.immediate.settlement/action_reverse",
            data=json.dumps({"ids": audit.ids, "context": {"allowed_company_ids": company.ids}}),
            headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json", "X-Odoo-Database": self.cr.dbname},
        )

    def _assert_access_refusal(self, response):
        self.assertEqual(response.status_code, 403, response.text)
        self.assertIn(response.json()["name"], (
            "odoo.exceptions.AccessError", "odoo.exceptions.AccessDenied",
            "odoo.addons.usl_access_control.exceptions.AgentPolicyAccessError",
        ))

    def test_actual_foreign_carrier_and_forged_company_are_refused(self):
        self.owner.company_ids = [Command.set((self.company | self.other_company).ids)]
        bill, statement, audit, _original = self._settlement("Foreign", self.other_data)
        before = self._snapshot(bill, statement, audit)
        key = self._key(self.writer)
        self._assert_access_refusal(self._http(key, "/json/2/account.immediate.settlement/action_reverse", {"ids": audit.ids}))
        self._assert_access_refusal(self._http_company(key, audit, self.other_company))
        self.assertEqual(before, self._snapshot(bill, statement, audit))

    def test_native_hard_lock_refuses_human_and_agent(self):
        bill, statement, audit, _original = self._settlement("Locked")
        before = self._snapshot(bill, statement, audit)
        self.company.hard_lock_date = self.day + timedelta(days=1)
        for actor in (self.owner, self.writer):
            response = self._http(self._key(actor), "/json/2/account.immediate.settlement/action_reverse", {"ids": audit.ids})
            self.assertEqual(response.status_code, 422, response.text)
            self.assertEqual(response.json()["name"], "odoo.exceptions.UserError")
        self.assertEqual(before, self._snapshot(bill, statement, audit))

    def test_current_owner_group_reduction_refuses_unlocked_action(self):
        bill, statement, audit, original = self._settlement("Authority control")
        key = self._key(self.writer)
        control = self._http(key, "/json/2/account.immediate.settlement/action_reverse", {"ids": audit.ids})
        self.assertEqual(control.status_code, 200, control.text)
        self._assert_restored(bill, statement, audit, original)
        bill, statement, audit, _original = self._settlement("Reduced groups")
        before = self._snapshot(bill, statement, audit)
        self.assertFalse(self.company.hard_lock_date)
        self.owner.group_ids = [Command.set(self.env.ref("base.group_user").ids)]
        response = self._http(key, "/json/2/account.immediate.settlement/action_reverse", {"ids": audit.ids})
        self._assert_access_refusal(response)
        self.assertEqual(before, self._snapshot(bill, statement, audit))

    def test_current_owner_company_reduction_refuses_unlocked_action(self):
        self.owner.company_ids = [Command.set((self.company | self.other_company).ids)]
        self.writer.with_user(self.owner).write({"company_ids": [Command.set((self.company | self.other_company).ids)]})
        bill, statement, audit, original = self._settlement("Company control", self.other_data)
        key = self._key(self.writer)
        control = self._http_company(key, audit, self.other_company)
        self.assertEqual(control.status_code, 200, control.text)
        self._assert_restored(bill, statement, audit, original)
        bill, statement, audit, _original = self._settlement("Reduced company", self.other_data)
        before = self._snapshot(bill, statement, audit)
        self.owner.company_ids = [Command.set(self.company.ids)]
        self.assertNotIn(self.other_company, self.writer.company_ids)
        response = self._http_company(key, audit, self.other_company)
        self._assert_access_refusal(response)
        self.assertEqual(before, self._snapshot(bill, statement, audit))

    def test_offline_document_operation_uses_same_native_action_contract(self):
        group = self.env.ref("usl_documents.group_documents_manager")
        owner = new_test_user(self.env, login="qa.native.action.documents", groups="usl_documents.group_documents_manager",
                              company_id=self.company.id, company_ids=[Command.set(self.company.ids)],
                              usl_identity_classification="active")
        agent = self._agent(owner, "Documents", group)
        operation = self.env["usl.document.operation"].sudo().create({
            "name": "[QA action] Offline acknowledgement", "checksum": "qa3271-offline",
            "company_id": self.company.id, "user_id": owner.id, "state": "failed",
        })
        self.assertFalse(operation.with_user(agent.user_id).has_access("write"))
        response = self._http(self._key(agent), "/json/2/usl.document.operation/acknowledge", {"ids": operation.ids})
        self.assertEqual(response.status_code, 200, response.text)
        operation.invalidate_recordset()
        self.assertTrue(operation.acknowledged)
