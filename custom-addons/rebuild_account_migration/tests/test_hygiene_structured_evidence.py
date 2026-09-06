import json
from unittest.mock import patch

from odoo import Command
from odoo.exceptions import AccessError
from odoo.tests import new_test_user, tagged
from odoo.addons.account.tests.common import AccountTestInvoicingCommon


@tagged("post_install", "-at_install", "usl_hygiene_structured_evidence")
class TestHygieneStructuredEvidence(AccountTestInvoicingCommon):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Issue = cls.env["rebuild.account.hygiene.issue"]
        cls.Payout = cls.env["usl.platform.billing.payout"].sudo()
        cls.platform = cls.env["usl.platform.billing.platform"].sudo().create({
            "name": "Synthetic evidence platform",
            "company_id": cls.env.company.id,
            "partner_id": cls.partner_a.id,
            "currency_id": cls.env.company.currency_id.id,
            "revenue_product_id": cls.product_a.id,
            "commission_product_id": cls.product_b.id,
            "sale_journal_id": cls.company_data["default_journal_sale"].id,
            "purchase_journal_id": cls.company_data["default_journal_purchase"].id,
            "compensation_journal_id": cls.company_data["default_journal_misc"].id,
            "bank_journal_id": cls.company_data["default_journal_bank"].id,
        })
        cls.session = cls.env["usl.platform.billing.session"].sudo().create({
            "name": "Synthetic evidence session",
            "company_id": cls.env.company.id,
            "period_month": "2026-08-01",
            "invoice_date": "2026-08-31",
        })

    def _bill(self):
        return self.env["account.move"].create({
            "move_type": "in_invoice",
            "partner_id": self.partner_a.id,
            "invoice_date": "2026-08-31",
            "invoice_line_ids": [Command.create({
                "name": "Synthetic commission",
                "account_id": self.company_data["default_account_expense"].id,
                "price_unit": 20,
            })],
        })

    def _payout(self, bill, state):
        # Synthetic historical workflow states; no provider or ledger mutation.
        return self.Payout.create({
            "session_id": self.session.id,
            "platform_id": self.platform.id,
            "payout_date": "2026-08-31",
            "platform_reference": f"synthetic-{bill.id}-{state}",
            "net_platform_amount": 80,
            "vendor_bill_id": bill.id,
            "state": state,
        })

    def _missing_ids(self):
        return {
            record_id
            for result in self.Issue._evaluate_builtin_hygiene(self.env.company)
            if result["issue_key"] == "vendor-evidence:missing"
            for record_id in json.loads(result["target_res_ids_json"])
        }

    def test_attachment_and_payout_state_evidence(self):
        normal = self._bill()
        attached = self._bill()
        attached.message_main_attachment_id = self.env["ir.attachment"].create({
            "name": "synthetic-receipt.pdf",
            "raw": b"%PDF-1.4 synthetic test evidence",
            "res_model": "account.move",
            "res_id": attached.id,
        })
        bills = {}
        for state in ("posted", "paid", "draft", "cancelled", "generated"):
            bills[state] = self._bill()
            self._payout(bills[state], state)
        missing = self._missing_ids()
        self.assertIn(normal.id, missing)
        self.assertNotIn(attached.id, missing)
        for state, bill in bills.items():
            with self.subTest(state=state):
                self.assertEqual(bill.id in missing, state not in {"posted", "paid"})
        self.Issue.sync_for_company(self.env.company)
        issue = self.Issue.search([
            ("company_id", "=", self.env.company.id),
            ("issue_key", "=", "vendor-evidence:missing"),
        ])
        self.assertEqual(set(json.loads(issue.target_res_ids_json)), missing)
        self.assertEqual(issue.status, "open")

    def test_batched_lookup_and_state_revocation(self):
        bills = self._bill() | self._bill()
        payout = self._payout(bills[0], "posted")
        self._payout(bills[0], "paid")
        self._payout(bills[1], "paid")
        with patch.object(
            type(self.Payout), "search_fetch", autospec=True,
            side_effect=type(self.Payout).search_fetch,
        ) as search:
            self.assertEqual(self.Issue._structured_evidence_move_ids(bills), set(bills.ids))
            self.assertEqual(search.call_count, 1)
            domain = search.call_args.args[1]
            self.assertIn(("vendor_bill_id", "in", bills.ids), domain)
            self.assertIn(("company_id", "in", bills.company_id.ids), domain)
        payout.state = "cancelled"
        self.assertNotIn(bills[0].id, self._missing_ids())
        self.Payout.search([("vendor_bill_id", "=", bills[0].id)]).write({"state": "draft"})
        self.assertIn(bills[0].id, self._missing_ids())

    def test_empty_batch_does_not_query_payouts(self):
        with patch.object(type(self.Payout), "search_fetch") as search:
            self.assertEqual(self.Issue._structured_evidence_move_ids(self.env["account.move"]), set())
            search.assert_not_called()

    def test_accountant_without_platform_access_and_company_boundary(self):
        bill = self._bill()
        self._payout(bill, "posted")
        accountant = new_test_user(
            self.env, login="synthetic-evidence-accountant",
            groups="account.group_account_user",
            company_id=self.env.company.id,
            company_ids=[Command.set(self.env.company.ids)],
        )
        self.assertFalse(accountant.has_group(
            "usl_platform_billing.group_platform_billing_reader",
        ))
        issue = self.Issue.with_user(accountant)
        self.assertEqual(
            issue._structured_evidence_move_ids(bill.with_user(accountant)),
            {bill.id},
        )
        other = self.setup_other_company()["company"]
        foreign_bill = self.env["account.move"].with_company(other).create({
            "move_type": "in_invoice", "invoice_date": "2026-08-31",
        })
        with patch.object(type(self.Payout), "search_fetch") as search:
            with self.assertRaises(AccessError):
                issue._structured_evidence_move_ids(foreign_bill.with_user(accountant))
            search.assert_not_called()
