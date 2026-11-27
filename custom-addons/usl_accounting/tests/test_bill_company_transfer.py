from types import MethodType, SimpleNamespace
from unittest.mock import patch

from odoo import Command, api
from odoo.addons.account.tests.common import AccountTestInvoicingCommon
from odoo.exceptions import AccessError, UserError
from odoo.tests import tagged
from odoo.tests.common import new_test_user


@tagged("post_install", "-at_install")
class TestDraftBillCompanyTransfer(AccountTestInvoicingCommon):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.target_data = cls.setup_other_company(name="Amber Workshop")
        cls.target = cls.target_data["company"]
        cls.partner_a.with_company(cls.target).property_account_payable_id = cls.target_data["default_account_payable"]

    def _bill(self, *, taxed=False, kind="in_invoice"):
        return self.env["account.move"].create({
            "move_type": kind,
            "partner_id": self.partner_a.id,
            "invoice_date": "2026-04-07",
            "journal_id": self.company_data["default_journal_purchase"].id,
            "invoice_payment_term_id": self.env.ref("account.account_payment_term_advance_60days").id,
            "invoice_line_ids": [Command.create({
                "name": "Studio rehearsal",
                "account_id": self.company_data["default_account_expense"].id,
                "quantity": 2, "price_unit": 42.50,
                "tax_ids": [Command.set(self.company_data["default_tax_purchase"].ids if taxed else [])],
            })],
        })

    def _mapping(self, bill, *, taxed=False):
        action = bill.action_change_bill_company()
        wizard = bill.env[action["res_model"]].browse(action["res_id"])
        wizard.target_company_id = self.target
        wizard.action_prepare_mapping()
        wizard.journal_id = self.target_data["default_journal_purchase"]
        wizard.mapping_ids.target_account_id = self.target_data["default_account_expense"]
        if taxed:
            wizard.mapping_ids.target_tax_ids = self.target_data["default_tax_purchase"]
        return wizard

    def test_transfer_keeps_original_evidence_installments_and_line_identity(self):
        bill = self._bill(taxed=True)
        source = bill.company_id
        evidence = self.env["ir.attachment"].create({
            "name": "Rehearsal evidence.txt", "raw": b"Synthetic booking",
            "res_model": "account.move", "res_id": bill.id, "company_id": source.id,
        })
        line = bill.invoice_line_ids
        old_dates = sorted(bill.line_ids.filtered(lambda ln: ln.display_type == "payment_term").mapped("date_maturity"))
        wizard = self._mapping(bill, taxed=True)
        self.assertEqual(bill.company_id, source)
        self.assertEqual(wizard.mapping_ids.source_account_id, self.company_data["default_account_expense"])
        self.assertEqual(wizard.mapping_ids.source_tax_ids, self.company_data["default_tax_purchase"])
        wizard.action_apply()
        self.assertEqual((bill.company_id, bill.journal_id), (self.target, self.target_data["default_journal_purchase"]))
        self.assertEqual((bill.state, bill.invoice_line_ids, line.quantity, line.price_unit), ("draft", line, 2, 42.5))
        self.assertEqual((evidence.res_id, evidence.company_id, evidence.raw.content), (bill.id, source, b"Synthetic booking"))
        self.assertEqual(line.tax_ids, self.target_data["default_tax_purchase"])
        self.assertEqual(bill.amount_untaxed, 85)
        terms = bill.line_ids.filtered(lambda ln: ln.display_type == "payment_term")
        self.assertEqual(sorted(terms.mapped("date_maturity")), old_dates)
        self.assertEqual(terms.account_id, self.target_data["default_account_payable"])
        self.assertFalse(bill.company_currency_id.round(sum(bill.line_ids.mapped("balance"))))
        self.assertEqual(wizard.action_apply()["res_id"], bill.id)

    def test_foreign_payment_is_preserved_and_explained(self):
        bill = self._bill(kind="in_refund")
        source = bill.company_id
        payment = self.env["account.payment"].create({
            "payment_type": "outbound", "partner_type": "supplier", "partner_id": self.partner_a.id,
            "amount": 85, "journal_id": self.company_data["default_journal_bank"].id,
        })
        bill.matched_payment_ids = [Command.link(payment.id)]
        wizard = self._mapping(bill)
        with self.assertRaisesRegex(UserError, "payment"):
            wizard.action_apply()
        self.assertEqual((payment.company_id, bill.company_id, bill.matched_payment_ids), (source, source, payment))

    def test_later_edit_requires_a_new_preview(self):
        bill = self._bill()
        source = bill.company_id
        wizard = self._mapping(bill)
        bill.invoice_line_ids.quantity = 3
        with self.assertRaisesRegex(UserError, "changed"):
            wizard.action_apply()
        self.assertEqual((bill.company_id, bill.invoice_line_ids.quantity), (source, 3))

    def test_foreign_currency_and_commercial_prices_survive_destination_recalculation(self):
        bill = self._bill()
        foreign = self.setup_other_currency("CHF", rates=[("2026-01-01", 1.2)])
        bill.currency_id = foreign
        dates_and_amounts = sorted((line.date_maturity, line.amount_currency) for line in bill.line_ids
                                   if line.display_type == "payment_term")
        wizard = self._mapping(bill)
        wizard.action_apply()
        self.assertEqual((bill.currency_id, bill.amount_total, bill.invoice_line_ids.price_unit), (foreign, 85, 42.5))
        self.assertEqual(dates_and_amounts, sorted((line.date_maturity, line.amount_currency) for line in bill.line_ids
                                                   if line.display_type == "payment_term"))
        self.assertFalse(bill.company_currency_id.round(sum(bill.line_ids.mapped("balance"))))

    def test_taxed_line_needs_an_explicit_choice_to_remove_its_taxes(self):
        bill = self._bill(taxed=True)
        wizard = self._mapping(bill)
        with self.assertRaisesRegex(UserError, "explicitly"):
            wizard.action_apply()
        self.assertEqual(bill.invoice_line_ids.tax_ids, self.company_data["default_tax_purchase"])
        wizard.mapping_ids.remove_taxes = True
        wizard.action_apply()
        self.assertFalse(bill.invoice_line_ids.tax_ids)
        self.assertEqual((bill.amount_total, bill.amount_tax), (85, 0))

    def test_selected_company_must_be_available_to_the_operator(self):
        bill = self._bill()
        user = new_test_user(self.env, login="studio-billing-only",
                             groups="account.group_account_invoice", company_id=bill.company_id.id,
                             company_ids=[Command.set(bill.company_id.ids)])
        bill = bill.with_user(user).with_context(allowed_company_ids=bill.company_id.ids)
        action = bill.action_change_bill_company()
        wizard = bill.env[action["res_model"]].browse(action["res_id"])
        wizard.target_company_id = self.target.id
        with self.assertRaises(AccessError):
            wizard.action_prepare_mapping()

    def test_archive_timeout_after_permission_write_restores_original_access(self):
        self._assert_archive_recovery(recover_immediately=True)

    def test_scheduled_archive_retry_recovers_after_a_second_outage(self):
        self._assert_archive_recovery(recover_immediately=False)

    def _linked_archive(self, bill):
        if "usl.document" not in self.env:
            self.skipTest("The optional Documents archive is not installed.")
        source = bill.company_id
        reader = new_test_user(self.env, login="studio-source-reader",
                               groups="usl_documents.group_documents_accountant,account.group_account_invoice",
                               company_id=source.id, company_ids=[Command.set(source.ids)])
        self.env.ref("usl_pocketid.provider_pocketid").sudo()._usl_pocketid_environment_write({"enabled": False})
        document = self.env["usl.document"].sudo().create({
            "name": "Studio booking receipt", "paperless_id": 87431, "company_id": source.id,
            "review_state": "classified", "confidentiality": "accounting", "access_scope": "linked_record",
            "accounting_evidence": True,
        })
        document.with_context(usl_documents_defer_access_sync=True).link_to_record("account.move", bill.id)
        document._recompute_linked_record_access(sync_permissions=False)
        self.assertIn(reader, document.permitted_user_ids)
        self.env["usl.paperless.user.mapping"].sudo().with_context(usl_documents_mapping_no_sync=True).create({
            "user_id": reader.id, "paperless_user_id": 927, "paperless_username": "studio-source-reader",
            "sync_state": "synchronized",
        })
        return document

    def _assert_archive_recovery(self, *, recover_immediately):
        bill = self._bill()
        document = self._linked_archive(bill)
        from odoo.addons.usl_documents.models.paperless_client import PaperlessClient, PaperlessUnavailable

        source = bill.company_id
        provider_acl = {}
        attempts = []
        def set_permissions(_client, ids, *, view_users, change_users):
            if not recover_immediately and len(attempts) == 2:
                attempts.append(dict(provider_acl))
                raise PaperlessUnavailable("The synthetic archive is still offline.")
            for doc_id in ids:
                provider_acl[doc_id] = (tuple(view_users), tuple(change_users))
            attempts.append(dict(provider_acl))
            if len(attempts) == 2:
                raise PaperlessUnavailable("The acknowledgement was lost.")

        with patch.object(PaperlessClient, "set_documents_permissions", set_permissions):
            document.with_user(self.env.ref("base.user_root")).action_sync_permissions()
            original = dict(provider_acl)
            self.assertIn(927, original[document.paperless_id][0])
            wizard = self._mapping(bill)
            action = wizard.action_apply()
            self.assertEqual(action["res_model"], wizard._name)
            self.assertEqual(bill.company_id, source)
            self.assertNotEqual(attempts[1], original)
            if recover_immediately:
                self.assertIn("restored", wizard.warning)
            else:
                self.assertIn("retry", wizard.warning)
                self.assertEqual(document.permission_sync_state, "failed")
                self.assertNotEqual(provider_acl, original)
                self.assertGreaterEqual(document._retry_failed_permission_syncs(), 1)
            self.assertEqual(provider_acl, original)
            self.assertEqual(document.permission_sync_state, "synchronized")
            self.assertEqual(document.link_ids.res_id, bill.id)
            self.assertEqual(document.company_id, source)
            wizard.action_apply()
            self.assertEqual(bill.company_id, self.target)
            self.assertNotIn(927, provider_acl[document.paperless_id][0])

    def test_archive_matches_committed_bill_after_outer_batch_cancels_transfer(self):
        self._assert_archive_at_commit(cancel_transfer=True)

    def test_archive_matches_committed_bill_after_outer_batch_accepts_transfer(self):
        self._assert_archive_at_commit(cancel_transfer=False)

    def test_archive_retry_restores_committed_policy_after_postcommit_outage(self):
        self._assert_archive_at_commit(cancel_transfer=True, fail_confirmation=True)

    def _assert_archive_at_commit(self, *, cancel_transfer, fail_confirmation=False):
        bill = self._bill()
        document = self._linked_archive(bill)
        from odoo.addons.usl_documents.models.paperless_client import PaperlessClient, PaperlessUnavailable
        from odoo.sql_db import Cursor

        source = bill.company_id
        archive = {}
        attempts = []

        def record_permissions(_client, ids, *, view_users, change_users):
            attempts.append(tuple(ids))
            if fail_confirmation and len(attempts) == 3:
                raise PaperlessUnavailable("Synthetic maintenance during final permission synchronization.")
            archive.update({paperless_id: (tuple(view_users), tuple(change_users)) for paperless_id in ids})

        self.env.cr.flush()
        self.registry_enter_test_mode()
        with patch.object(PaperlessClient, "set_documents_permissions", record_permissions):
            document.with_user(self.env.ref("base.user_root")).action_sync_permissions()
            self.env.cr.flush()
            before = archive[document.paperless_id]
            self.assertIn(927, before[0])
            with self.registry.cursor() as cr:
                env = api.Environment(cr, self.env.uid, self.env.context)
                batch_bill = bill.with_env(env)
                with cr.savepoint() as batch:
                    self._mapping(batch_bill).action_apply()
                    self.assertEqual(batch_bill.company_id.id, self.target.id)
                    self.assertNotIn(927, archive[document.paperless_id][0])
                    if cancel_transfer:
                        batch.rollback()
                unrelated = env["res.partner"].create({"name": "Synthetic rehearsal schedule"})
                # Exercise Odoo's real commit callbacks. TestCursor normally skips them,
                # and its connection still keeps every write inside the test transaction.
                Cursor.commit(cr)
            with self.registry.cursor() as cr:
                env = api.Environment(cr, self.env.uid, self.env.context)
                self.assertTrue(env["res.partner"].browse(unrelated.id).exists())
                self.assertEqual(bill.with_env(env).company_id.id, source.id if cancel_transfer else self.target.id)
                observed = document.with_env(env).sudo()
                if fail_confirmation:
                    self.assertEqual(observed.permission_sync_state, "failed")
                    self.assertNotEqual(archive[document.paperless_id], before)
                    self.assertGreaterEqual(observed._retry_failed_permission_syncs(), 1)
                self.assertEqual(observed.permission_sync_state, "synchronized")
            if cancel_transfer:
                self.assertEqual(archive[document.paperless_id], before)
            else:
                self.assertNotIn(927, archive[document.paperless_id][0])

    def test_owner_and_delegated_operator_transfer_with_the_same_native_rights(self):
        if "usl.agent" not in self.env:
            self.skipTest("The optional delegated identity service is not installed.")
        source = self.company_data["company"]
        companies = source | self.target
        operator = new_test_user(self.env, login="studio-billing-operator",
                                 groups="account.group_account_invoice", company_id=source.id,
                                 company_ids=[Command.set(companies.ids)])
        grants = self.env["usl.agent"].with_user(operator)
        values = {"name": "Studio billing", "purpose": "Reassign synthetic draft bills",
                  "owner_id": operator.id, "company_id": source.id, "company_ids": [Command.set(companies.ids)],
                  "delegated_group_ids": [Command.set(self.env.ref("account.group_account_invoice").ids)]}
        delegated = grants.create({**values, "access_mode": "read_write"})
        for actor in (operator, delegated.user_id):
            bill = self._bill(kind="in_refund").with_user(actor).with_context(allowed_company_ids=companies.ids)
            wizard = self._mapping(bill)
            if actor == operator:
                wizard.action_apply()
            else:
                from odoo.addons.usl_access_control.controllers.json2 import UslAgentJson2Controller
                from odoo.http.requestlib import Request, _request_stack
                request = SimpleNamespace(env=self.env(user=actor, context={}), db=self.env.cr.dbname,
                                          registry=self.env.registry, httprequest=SimpleNamespace(
                                              headers={}, remote_addr="127.0.0.1",
                                              user_agent=SimpleNamespace(string="bill-transfer-fixture")))
                request.update_env = MethodType(Request.update_env, request)
                previous = self.env.transaction.default_env
                controller = UslAgentJson2Controller()
                _request_stack.push(request)
                try:
                    # The audit connection cannot see this fixture's uncommitted grant.
                    with patch.object(controller, "_record_agent_api_call") as audit:
                        controller.web_json_2_rpc.__wrapped__(
                            controller, wizard._name, "action_apply", ids=wizard.ids,
                            context={"allowed_company_ids": companies.ids})
                    self.assertEqual(audit.call_args.kwargs["outcome"], "succeeded")
                finally:
                    _request_stack.pop()
                    self.env.transaction.default_env = previous
            self.assertEqual((bill.company_id, bill.state, bill.move_type), (self.target, "draft", "in_refund"))
        readonly = grants.create({**values, "name": "Studio billing reader", "access_mode": "read_only"}).user_id
        bill = self._bill().with_user(readonly).with_context(allowed_company_ids=companies.ids)
        with self.assertRaises(AccessError):
            bill.action_change_bill_company()

    def test_posted_bill_keeps_its_native_reconciliation(self):
        bill = self._bill()
        bill.action_post()
        payment = self.env["account.payment.register"].with_context(active_model="account.move", active_ids=bill.ids).create({})._create_payments()
        matches = bill.line_ids.matched_debit_ids | bill.line_ids.matched_credit_ids
        self.assertTrue(matches)
        with self.assertRaisesRegex(UserError, "draft"):
            bill.action_change_bill_company()
        self.assertEqual((bill.company_id, bill.state, payment.company_id), (self.company_data["company"], "posted", bill.company_id))
        self.assertEqual(bill.line_ids.matched_debit_ids | bill.line_ids.matched_credit_ids, matches)
