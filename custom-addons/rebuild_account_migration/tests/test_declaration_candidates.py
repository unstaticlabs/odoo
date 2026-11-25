from odoo import Command
from odoo.addons.account.tests.common import AccountTestInvoicingCommon
from odoo.exceptions import AccessError
from odoo.tests import tagged


@tagged("post_install", "-at_install")
class TestDeclarationCandidates(AccountTestInvoicingCommon):
    chart_template = "fr_comp"
    country_code = "FR"

    def _review(self, kind, start="2026-01-01", end="2026-12-31"):
        return self.env["rebuild.account.declaration.candidate.review"].new({
            "kind": kind, "company_id": self.env.company.id, "date_from": start, "date_to": end,
        })

    def test_des_explicit_tax_and_dates_without_a_declaration(self):
        partner = self.env["res.partner"].with_context(no_vat_validation=True).create({
            "name": "Example studio", "country_id": self.env.ref("base.es").id, "vat": "ES00000000X",
        })
        tax = self.tax_sale_a.copy({"name": "EU services – DES Requis", "amount": 0})
        plain = self.tax_sale_a.copy({"name": "EU goods", "amount": 0})
        def invoice(tax, date, kind="out_invoice", posted=True):
            return self._create_invoice_one_line(
                price_unit=120, tax_ids=tax, partner_id=partner, move_type=kind,
                invoice_date=date, post=posted,
            )

        original = invoice(tax, "2026-03-10")
        refund = invoice(tax, "2026-03-20", "out_refund")
        native = self.env.ref(f"account.{self.env.company.id}_tva_sale_service_intra_0")
        native_invoice = invoice(native, "2026-03-17")
        invoice(plain, "2026-03-13")
        invoice(tax, "2026-04-04")
        invoice(tax, "2026-03-14", posted=False)
        tax.active = False
        domain = [("rule_id.code", "=", "FR_DES"), ("company_id", "=", self.env.company.id)]
        before = self.env["rebuild.account.declaration"].search(domain).ids
        review = self._review("des", "2026-03-01", "2026-03-31")
        self.assertEqual([row["source_id"] for row in review.source_rows], [refund.id, native_invoice.id, original.id])
        self.assertEqual({r["period"] for r in review.source_rows}, {"2026-03"})
        self.assertIn(tax.name, review.source_rows[0]["reason"])
        self.assertIn(f"/odoo/account.move/{original.id}", review.evidence_html)
        self.assertEqual(self.env["rebuild.account.declaration"].search(domain).ids, before)

    def _bank_debit(self, when, debit_account, amount):
        journal = self.company_data["default_journal_bank"]
        entry = self.env["account.move"].create({
            "journal_id": journal.id, "date": when,
            "line_ids": [
                Command.create({"account_id": debit_account.id, "partner_id": self.partner_a.id, "debit": amount}),
                Command.create({"account_id": journal.default_account_id.id, "credit": amount}),
            ],
        })
        entry.action_post()
        return entry

    def test_das2_partial_and_direct_payments_use_cash_dates(self):
        services = self.env["account.account"].create({
            "code": "604991", "name": "Synthetic subcontracting", "account_type": "expense",
            "company_ids": [Command.set(self.env.company.ids)],
        })
        bill = self._create_invoice_one_line(
            price_unit=1200, account_id=services, partner_id=self.partner_a, move_type="in_invoice",
            tax_ids=self.env["account.tax"], invoice_date="2025-12-10", post=True,
        )
        payable = bill.line_ids.filtered(lambda line: line.account_id.account_type == "liability_payable")
        earlier = self._bank_debit("2025-12-20", payable.account_id, 140)
        current = self._bank_debit("2026-01-12", payable.account_id, 180)
        for entry in (earlier, current):
            (payable | entry.line_ids.filtered(lambda line: line.account_id == payable.account_id)).reconcile()
        direct = self._bank_debit("2026-01-21", services, 95)
        self._create_invoice_one_line(
            price_unit=2500, account_id=services, partner_id=self.partner_a, move_type="in_invoice",
            invoice_date="2026-01-02", post=True,
        )
        review = self._review("das2", "2026-01-01", "2026-01-31")
        self.assertEqual(
            [(r["date"], r["source_id"], r["payment_id"]) for r in review.source_rows],
            [("2026-01-21", direct.id, 0), ("2026-01-12", bill.id, current.id)],
        )
        self.assertEqual([r["payment_amount"] for r in review.source_rows], [95, 180])
        self.assertIn(f"/odoo/account.move/{current.id}", review.evidence_html)
        self.assertEqual(self._review("das2", "2025-12-01", "2025-12-31").source_rows[0]["payment_id"], earlier.id)

    def test_622_allocation_and_mixed_bill_show_one_settlement(self):
        accounts = self.env["account.account"].with_company(self.env.company)
        fees = accounts.search([("code", "=like", "622%")], limit=1)
        services = accounts.search([("code", "=like", "604%")], limit=1)
        bill = self._create_invoice(
            move_type="in_invoice", invoice_date="2025-11-10", partner_id=self.partner_a, post=True,
            invoice_line_ids=[
                self._prepare_invoice_line(name="Fee", price_unit=60, account_id=fees, tax_ids=self.env["account.tax"]),
                self._prepare_invoice_line(name="Service", price_unit=40, account_id=services, tax_ids=self.env["account.tax"]),
            ],
        )
        payable = bill.line_ids.filtered(lambda line: line.account_id.account_type == "liability_payable")
        payment = self._bank_debit("2026-02-12", payable.account_id, 50)
        (payable | payment.line_ids.filtered(lambda line: line.account_id == payable.account_id)).reconcile()
        direct = self._bank_debit("2026-02-20", fees, 18)
        fee_bill = self._create_invoice_one_line(
            price_unit=20, account_id=fees, partner_id=self.partner_a, move_type="in_invoice",
            tax_ids=self.env["account.tax"], invoice_date="2026-01-15", post=True,
        )
        fee_payable = fee_bill.line_ids.filtered(lambda line: line.account_id.account_type == "liability_payable")
        fee_payment = self._bank_debit("2026-02-21", fee_payable.account_id, 20)
        (fee_payable | fee_payment.line_ids.filtered(lambda line: line.account_id == fee_payable.account_id)).reconcile()
        review = self._review("das2", "2026-02-01", "2026-02-28")
        rows = review.source_rows
        self.assertEqual([row["source_id"] for row in rows], [fee_bill.id, direct.id, bill.id])
        self.assertEqual([row["account_prefixes"] for row in rows], [["622"], ["622"], ["604", "622"]])
        self.assertEqual([row["payment_amount"] for row in rows], [20, 18, 50])
        self.assertEqual([row["native_622_amount"] for row in rows], [20, 18, 30])
        totals = self.env["rebuild.account.declaration"]._das2_paid_totals(
            self.env.company, review.date_from, review.date_to,
        )
        self.assertEqual(totals[self.partner_a.commercial_partner_id.id], 68)
        self.assertIn("622 fees and a 604 service", rows[-1]["reason"])

    def test_622_advance_reports_both_cash_and_native_calendar_dates(self):
        fee = self.env["account.account"].search([("code", "=like", "622%")], limit=1)
        bill = self._create_invoice_one_line(
            price_unit=55, account_id=fee, partner_id=self.partner_a, move_type="in_invoice",
            tax_ids=self.env["account.tax"], invoice_date="2026-01-15", post=True,
        )
        payable = bill.line_ids.filtered(lambda line: line.account_id.account_type == "liability_payable")
        advance = self._bank_debit("2025-12-20", payable.account_id, 55)
        (payable | advance.line_ids.filtered(lambda line: line.account_id == payable.account_id)).reconcile()
        for year, in_calendar in [(2025, False), (2026, True)]:
            row, = self._review("das2", f"{year}-01-01", f"{year}-12-31").source_rows
            self.assertEqual(row["source_id"], bill.id)
            self.assertEqual(row["period"], str(year))
            self.assertEqual(row["date"], "2025-12-20")
            self.assertEqual(row["calendar_622_date"], "2026-01-15")
            self.assertEqual(row["calendar_622_in_period"], in_calendar)
        self.assertEqual(len(self._review("das2", "2025-01-01", "2026-12-31").source_rows), 1)

    def test_inactive_company_and_invalid_range(self):
        review = self._review("des", "2026-03-01", "2026-01-31")
        self.assertFalse(review.source_rows)
        self.assertIn("end date", review.evidence_html)
        other = self.env["res.company"].create({"name": "Other example company"})
        scoped = self.env["rebuild.account.declaration.candidate.review"].with_context(
            allowed_company_ids=self.env.company.ids,
        ).create({"company_id": other.id, "kind": "des"})
        with self.assertRaises(AccessError):
            scoped.read(["source_rows"])
