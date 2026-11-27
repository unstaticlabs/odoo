import json
from types import MethodType, SimpleNamespace
from unittest.mock import patch

from odoo import Command
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
        cls.target_same_code = cls._account(cls.target, cls.company_data["default_account_expense"].code)

    @classmethod
    def _account(cls, company, code):
        """The company's account with this code, created when it has none."""
        Account = cls.env["account.account"].with_company(company)
        found = Account.search([*Account._check_company_domain(company), ("code", "=", code)], limit=1)
        return found or Account.create({"name": "Synthetic %s" % code, "code": code, "account_type": "expense",
                                        "company_ids": [Command.link(company.id)]})

    def _bill(self, *, taxed=False, kind="in_invoice", product=None):
        line = {
            "name": "Studio rehearsal",
            "account_id": self.company_data["default_account_expense"].id,
            "quantity": 2, "price_unit": 42.50,
            "tax_ids": [Command.set(self.company_data["default_tax_purchase"].ids if taxed else [])],
        }
        if product:
            line["product_id"] = product.id
        return self.env["account.move"].create({
            "move_type": kind,
            "partner_id": self.partner_a.id,
            "invoice_date": "2026-04-07",
            "journal_id": self.company_data["default_journal_purchase"].id,
            "invoice_payment_term_id": self.env.ref("account.account_payment_term_advance_60days").id,
            "invoice_line_ids": [Command.create(line)],
        })

    def _wizard(self, bill):
        action = bill.action_change_bill_company()
        wizard = bill.env[action["res_model"]].browse(action["res_id"])
        wizard.target_company_id = self.target
        return wizard

    def _mapping(self, bill):
        """The dialog path: the proposal arrives pre-filled, the operator only reviews it."""
        wizard = self._wizard(bill)
        wizard.action_prepare_mapping()
        return wizard

    # --- one method for the dialog and for agents -----------------------------------

    def test_agent_moves_a_taxed_two_installment_bill_in_two_calls(self):
        bill = self._bill(taxed=True)
        source = bill.company_id
        old_dates = sorted(bill.line_ids.filtered(lambda ln: ln.display_type == "payment_term").mapped("date_maturity"))
        self.assertEqual(len(old_dates), 2)

        preview = bill.change_bill_company(self.target.id)

        json.dumps(preview)  # an agent receives exactly this
        self.assertEqual((preview["status"], preview["blockers"], preview["unresolved"]), ("ready", [], []))
        self.assertEqual(bill.company_id, source, "a preview writes nothing")
        proposal = preview["proposal"]
        self.assertEqual(proposal["journal"]["id"], self.target_data["default_journal_purchase"].id)
        self.assertEqual(proposal["payable_account"]["id"], self.target_data["default_account_payable"].id)
        (line,) = proposal["lines"]
        self.assertEqual([tax["id"] for tax in line["from"]["taxes"]], self.company_data["default_tax_purchase"].ids)
        self.assertEqual([tax["id"] for tax in line["to"]["taxes"]], self.target_data["default_tax_purchase"].ids)
        self.assertEqual(line["to"]["account"]["id"], self.target_same_code.id)
        destination = preview["destination"]
        self.assertEqual(destination["amount_untaxed"], 85)
        self.assertEqual(destination["amount_tax"], 12.75)
        self.assertEqual(destination["amount_total"], 97.75)
        self.assertEqual([due["date"] for due in destination["due_dates"]], [date.isoformat() for date in old_dates])
        self.assertEqual(round(sum(due["amount"] for due in destination["due_dates"]), 2), 97.75)
        self.assertEqual(preview["current"]["company"]["id"], source.id)

        done = bill.change_bill_company(self.target.id, confirm=True, preview_id=preview["preview_id"])

        self.assertEqual(done["status"], "done")
        self.assertEqual((bill.company_id, bill.state, bill.invoice_line_ids.tax_ids),
                         (self.target, "draft", self.target_data["default_tax_purchase"]))
        self.assertEqual((done["before"]["company"]["id"], done["after"]["company"]["id"]), (source.id, self.target.id))
        self.assertEqual(done["after"]["amount_total"], 97.75)
        self.assertEqual((done["before"]["payment_state"], done["after"]["payment_state"]), ("not_paid", "not_paid"))
        self.assertEqual(done["after"]["payable_account"]["id"], self.target_data["default_account_payable"].id)
        self.assertEqual([due["date"] for due in done["after"]["due_dates"]], [date.isoformat() for date in old_dates])
        self.assertTrue(done["bill"]["url"].endswith("/odoo/account.move/%s" % bill.id))
        # A repeated confirmation after a lost reply reports the finished move.
        again = bill.change_bill_company(self.target.id, confirm=True, preview_id=preview["preview_id"])
        self.assertEqual((again["status"], again["after"]["company"]["id"]), ("already_done", self.target.id))
        # Without a preview id there is nothing to repeat: a bill already there is not a new request.
        with self.assertRaises(UserError):
            bill.change_bill_company(self.target.id, confirm=True)

    def test_confirmation_needs_the_matching_preview(self):
        bill = self._bill()
        source = bill.company_id
        with self.assertRaisesRegex(UserError, "Preview"):
            bill.change_bill_company(self.target.id, confirm=True)
        preview = bill.change_bill_company(self.target.id)
        bill.invoice_line_ids.quantity = 3
        with self.assertRaisesRegex(UserError, "differ"):
            bill.change_bill_company(self.target.id, confirm=True, preview_id=preview["preview_id"])
        self.assertEqual((bill.company_id, bill.invoice_line_ids.quantity), (source, 3))

    def test_dialog_and_method_apply_the_same_result(self):
        bill = self._bill(taxed=True)
        wizard = self._mapping(bill)
        self.assertEqual(wizard.mapping_ids.target_tax_ids, self.target_data["default_tax_purchase"])
        self.assertIn("97.75", wizard.destination_summary)
        wizard.action_apply()
        self.assertEqual((bill.company_id, bill.amount_total), (self.target, 97.75))

    # --- blockers ---------------------------------------------------------------------

    def _blocked_by_payment(self, bill):
        payment = self.env["account.payment"].create({
            "payment_type": "outbound", "partner_type": "supplier", "partner_id": self.partner_a.id,
            "amount": 85, "journal_id": self.company_data["default_journal_bank"].id,
        })
        bill.matched_payment_ids = [Command.link(payment.id)]
        return payment

    def _blocked_by_statement_line(self, bill):
        statement_line = self.env["account.bank.statement.line"].create({
            "journal_id": self.company_data["default_journal_bank"].id, "amount": -85,
            "payment_ref": "Studio rehearsal", "partner_id": self.partner_a.id,
        })
        bill.statement_line_id = statement_line
        return statement_line

    def _blocked_by_purchase_order(self, bill):
        if "purchase_line_id" not in self.env["account.move.line"]._fields:
            self.skipTest("Purchase is not installed.")
        product = self.env["product.product"].create({"name": "Rehearsal hour"})
        order = self.env["purchase.order"].create({
            "partner_id": self.partner_a.id,
            "order_line": [Command.create({"product_id": product.id, "product_qty": 2, "price_unit": 42.5})],
        })
        bill.invoice_line_ids.purchase_line_id = order.order_line
        return order

    def _blocked_by_expense(self, bill):
        employee = self.env["hr.employee"].sudo().create({"name": "Synthetic employee", "company_id": bill.company_id.id})
        expense = self.env["hr.expense"].sudo().create({
            "name": "Studio taxi", "employee_id": employee.id, "total_amount_currency": 85,
            "company_id": bill.company_id.id,
        })
        expense.existing_bill_id = bill
        return expense

    def _blocked_by_recurrence(self, bill):
        origin = self._bill()
        bill.auto_post_origin_id = origin
        return origin

    def _blocked_by_history(self, bill):
        bill.posted_before = True
        return bill

    def _blocked_by_cash_rounding(self, bill):
        rounding = self.env["account.cash.rounding"].create({
            "name": "Synthetic rounding", "rounding": 0.05, "strategy": "add_invoice_line",
            "profit_account_id": self.company_data["default_account_revenue"].id,
            "loss_account_id": self.company_data["default_account_expense"].id,
        })
        bill.invoice_cash_rounding_id = rounding
        return rounding

    def _blocked_by_partner(self, bill):
        self.partner_a.company_id = bill.company_id
        return self.partner_a

    def _blocked_by_product(self, bill):
        product = self.env["product.product"].create({"name": "Restricted hour", "company_id": bill.company_id.id})
        bill.invoice_line_ids.product_id = product
        return product

    def _blocked_by_reversal(self, bill):
        original = self._bill()
        bill.reversed_entry_id = original
        return original

    def test_every_native_blocker_is_named_with_a_link_and_a_next_step(self):
        cases = [
            ("payment_linked", self._blocked_by_payment),
            ("statement_line_linked", self._blocked_by_statement_line),
            ("purchase_order_linked", self._blocked_by_purchase_order),
            ("expense_linked", self._blocked_by_expense),
            ("recurring_copy", self._blocked_by_recurrence),
            ("posted_before", self._blocked_by_history),
            ("cash_rounding", self._blocked_by_cash_rounding),
            ("partner_restricted", self._blocked_by_partner),
            ("product_restricted", self._blocked_by_product),
            ("reversal", self._blocked_by_reversal),
        ]
        for code, make in cases:
            with self.subTest(code=code):
                bill = self._bill()
                culprit = make(bill)
                source = bill.company_id
                # The first step shows it, before any mapping exists.
                wizard = self._wizard(bill)
                self.assertTrue(wizard.has_blockers)
                # The preview reports it, and a confirmation refuses it.
                preview = bill.change_bill_company(self.target.id)
                self.assertEqual(preview["status"], "blocked")
                blocker = next(item for item in preview["blockers"] if item["code"] == code)
                self.assertTrue(blocker["next_step"])
                if code not in ("posted_before",):
                    named = blocker["records"][0]
                    self.assertEqual((named["model"], named["id"]), (culprit._name, culprit.id))
                    self.assertIn("/odoo/%s/%s" % (culprit._name, culprit.id), named["url"])
                    self.assertIn(culprit.display_name, blocker["message"] + named["display_name"])
                    self.assertIn(named["url"], wizard.blockers_html)
                self.assertIn(blocker["message"], str(wizard.blockers_html))
                with self.assertRaises(UserError):
                    bill.change_bill_company(self.target.id, confirm=True, preview_id=preview["preview_id"])
                with self.assertRaises(UserError):
                    wizard.action_prepare_mapping()
                self.assertEqual(bill.company_id, source)

    def test_an_expense_the_operator_cannot_read_is_found_but_not_named(self):
        bill = self._bill()
        self._blocked_by_expense(bill)
        operator = new_test_user(self.env, login="studio-no-expense-access", groups="base.group_user,account.group_account_invoice",
                                 company_id=bill.company_id.id, company_ids=[Command.set((bill.company_id | self.target).ids)])
        blockers = bill.with_user(operator)._company_transfer_blockers(self.target)
        (expense,) = [item for item in blockers if item["code"] == "expense_linked"]
        self.assertNotIn("Studio taxi", str(expense))

    def test_paid_draft_is_refused_at_the_first_step_with_the_payment_named(self):
        bill = self._bill(kind="in_refund")
        payment = self._blocked_by_payment(bill)
        source = bill.company_id
        wizard = self._wizard(bill)
        self.assertTrue(wizard.has_blockers)
        self.assertIn(payment.display_name, wizard.blockers_html)
        self.assertIn("unreconcile", wizard.blockers_html)
        with self.assertRaisesRegex(UserError, payment.display_name):
            wizard.action_prepare_mapping()
        self.assertEqual((payment.company_id, bill.company_id, bill.matched_payment_ids), (source, source, payment))

    # --- native proposals -------------------------------------------------------------

    def test_destination_taxes_come_from_the_product_when_no_equivalent_exists(self):
        target_tax = self.target_data["default_tax_purchase"]
        target_tax.amount = 21
        product = self.env["product.product"].create({
            "name": "Rehearsal hour",
            "supplier_taxes_id": [Command.set((self.company_data["default_tax_purchase"] | target_tax).ids)],
        })
        product.product_tmpl_id.with_company(self.target).property_account_expense_id = self.target_data["default_account_expense"]
        bill = self._bill(taxed=True, product=product)
        (line,) = bill.change_bill_company(self.target.id)["proposal"]["lines"]
        self.assertEqual([tax["id"] for tax in line["to"]["taxes"]], target_tax.ids)
        self.assertEqual(line["unresolved"], [])

    def test_a_tax_without_an_equivalent_is_left_for_the_operator(self):
        self.target_data["default_tax_purchase"].amount = 21
        bill = self._bill(taxed=True)
        source = bill.company_id
        preview = bill.change_bill_company(self.target.id)
        self.assertEqual(preview["status"], "needs_input")
        self.assertEqual([item["code"] for item in preview["unresolved"]], ["tax"])
        with self.assertRaisesRegex(UserError, "tax"):
            bill.change_bill_company(self.target.id, confirm=True, preview_id=preview["preview_id"])
        mapping = {"lines": [{"line_id": bill.invoice_line_ids.id, "account_id": self.target_data["default_account_expense"].id,
                              "tax_ids": []}]}
        preview = bill.change_bill_company(self.target.id, mapping)
        self.assertEqual(preview["status"], "ready")
        bill.change_bill_company(self.target.id, mapping, confirm=True, preview_id=preview["preview_id"])
        self.assertEqual((bill.company_id, bill.amount_tax, bill.amount_total), (self.target, 0, 85))
        self.assertNotEqual(source, bill.company_id)

    def test_dialog_needs_an_explicit_choice_to_remove_taxes(self):
        self.target_data["default_tax_purchase"].amount = 21
        bill = self._bill(taxed=True)
        wizard = self._mapping(bill)
        self.assertFalse(wizard.mapping_ids.target_tax_ids)
        with self.assertRaisesRegex(UserError, "explicitly"):
            wizard.action_apply()
        self.assertEqual(bill.invoice_line_ids.tax_ids, self.company_data["default_tax_purchase"])
        wizard.mapping_ids.remove_taxes = True
        wizard.action_apply()
        self.assertFalse(bill.invoice_line_ids.tax_ids)
        self.assertEqual((bill.amount_total, bill.amount_tax), (85, 0))

    def test_journal_is_active_and_fits_the_bill_currency(self):
        Journal = self.env["account.journal"]
        foreign = self.setup_other_currency("CHF", rates=[("2026-01-01", 1.2)])
        common = {"type": "purchase", "company_id": self.target.id, "sequence": -10}
        Journal.create({**common, "name": "Closed journal", "code": "CLS", "active": False})
        Journal.create({**common, "name": "Swiss journal", "code": "CHF", "currency_id": foreign.id})
        bill = self._bill()
        proposed = bill.change_bill_company(self.target.id)["proposal"]["journal"]
        self.assertEqual(proposed["id"], self.target_data["default_journal_purchase"].id)
        mapping = {"journal_id": Journal.search([("code", "=", "CHF")]).id}
        preview = bill.change_bill_company(self.target.id, mapping)
        self.assertEqual(preview["status"], "needs_input")
        self.assertEqual(preview["unresolved"][0]["code"], "invalid_choice")

    def test_fiscal_position_is_the_one_the_destination_finds_for_the_supplier(self):
        country = self.env.ref("base.be")
        self.partner_a.country_id = country
        position = self.env["account.fiscal.position"].create({
            "name": "Belgian suppliers", "company_id": self.target.id, "auto_apply": True,
            "country_id": country.id,
        })
        bill = self._bill()
        preview = bill.change_bill_company(self.target.id)
        self.assertEqual(preview["proposal"]["fiscal_position"]["id"], position.id)
        none = bill.change_bill_company(self.target.id, {"fiscal_position_id": False})
        self.assertIsNone(none["proposal"]["fiscal_position"])

    def test_same_account_code_in_the_destination_is_proposed_before_any_default(self):
        source_account = self._account(self.company_data["company"], "601000")
        target_account = self._account(self.target, "601000")
        bill = self._bill()
        bill.invoice_line_ids.account_id = source_account
        preview = bill.change_bill_company(self.target.id)
        (line,) = preview["proposal"]["lines"]
        self.assertEqual((preview["status"], line["to"]["account"]["id"]), ("ready", target_account.id))

    def test_a_journal_default_account_is_a_guess_the_agent_must_confirm(self):
        source_account = self._account(self.company_data["company"], "699999")
        bill = self._bill()
        bill.invoice_line_ids.account_id = source_account
        guess = self.target_data["default_journal_purchase"].default_account_id
        preview = bill.change_bill_company(self.target.id)
        (line,) = preview["proposal"]["lines"]
        self.assertEqual((preview["status"], line["to"]["account"]["id"]), ("needs_input", guess.id))
        self.assertEqual([item["code"] for item in preview["unresolved"]], ["account_guess"])
        self.assertTrue(preview["destination"], "the totals are still shown for the guess")
        with self.assertRaisesRegex(UserError, "guess"):
            bill.change_bill_company(self.target.id, confirm=True, preview_id=preview["preview_id"])
        mapping = {"lines": [{"line_id": bill.invoice_line_ids.id, "account_id": guess.id}]}
        confirmed = bill.change_bill_company(self.target.id, mapping)
        self.assertEqual(confirmed["status"], "ready")
        bill.change_bill_company(self.target.id, mapping, confirm=True, preview_id=confirmed["preview_id"])
        self.assertEqual(bill.company_id, self.target)

    def test_confirmation_must_repeat_the_previewed_choices(self):
        bill = self._bill(taxed=True)
        source = bill.company_id
        other = self._account(self.target, "602000")
        previewed = {"lines": [{"line_id": bill.invoice_line_ids.id, "account_id": self.target_same_code.id}]}
        different = {"lines": [{"line_id": bill.invoice_line_ids.id, "account_id": other.id}]}
        preview = bill.change_bill_company(self.target.id, previewed)
        with self.assertRaisesRegex(UserError, "differ"):
            bill.change_bill_company(self.target.id, different, confirm=True, preview_id=preview["preview_id"])
        with self.assertRaisesRegex(UserError, "differ"):
            bill.change_bill_company(self.target.id, confirm=True, preview_id=bill.change_bill_company(
                self.target.id, different)["preview_id"])
        self.assertEqual(bill.company_id, source)
        bill.change_bill_company(self.target.id, previewed, confirm=True, preview_id=preview["preview_id"])
        self.assertEqual((bill.company_id, bill.invoice_line_ids.account_id), (self.target, self.target_same_code))

    def test_due_dates_without_payment_terms_are_the_bills_own(self):
        bill = self._bill()
        bill.invoice_payment_term_id = False
        bill.invoice_date_due = "2026-05-20"
        destination = bill.change_bill_company(self.target.id)["destination"]
        self.assertEqual([due["date"] for due in destination["due_dates"]], ["2026-05-20"])

    def test_destination_scope_comes_first_so_its_account_codes_show(self):
        bill = self._bill()
        wizard = self._mapping(bill)
        scoped = wizard._in_both_companies()
        self.assertEqual(scoped.env.context["allowed_company_ids"], [self.target.id, bill.company_id.id])
        self.assertEqual(wizard.scope_company_ids[0], self.target.id)
        account = scoped.env["account.account"].browse(self.target_same_code.id)
        self.assertTrue(account.code)
        self.assertTrue(account.display_name.startswith(account.code))

    # --- the dialog ---------------------------------------------------------------------

    def test_transfer_keeps_original_evidence_installments_and_line_identity(self):
        bill = self._bill(taxed=True)
        source = bill.company_id
        evidence = self.env["ir.attachment"].create({
            "name": "Rehearsal evidence.txt", "raw": b"Synthetic booking",
            "res_model": "account.move", "res_id": bill.id, "company_id": source.id,
        })
        line = bill.invoice_line_ids
        old_dates = sorted(bill.line_ids.filtered(lambda ln: ln.display_type == "payment_term").mapped("date_maturity"))
        wizard = self._mapping(bill)
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
        action = wizard.action_apply()
        self.assertEqual((action["type"], action["url"]), ("ir.actions.act_url", "/mail/view?model=account.move&res_id=%s" % bill.id))

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

    # --- companies ----------------------------------------------------------------------

    def test_operator_needs_access_to_both_companies(self):
        bill = self._bill()
        user = new_test_user(self.env, login="studio-billing-only",
                             groups="account.group_account_invoice", company_id=bill.company_id.id,
                             company_ids=[Command.set(bill.company_id.ids)])
        bill = bill.with_user(user).with_context(allowed_company_ids=bill.company_id.ids)
        wizard = self._wizard(bill)
        self.assertNotIn(self.target.id, wizard.operator_company_ids)
        with self.assertRaises(AccessError):
            wizard.action_prepare_mapping()
        with self.assertRaises(AccessError):
            bill.change_bill_company(self.target.id)

    def test_destination_only_needs_to_be_a_company_of_the_operator_not_an_active_one(self):
        source = self.company_data["company"]
        operator = new_test_user(self.env, login="studio-billing-two-companies",
                                 groups="base.group_user,account.group_account_invoice", company_id=source.id,
                                 company_ids=[Command.set((source | self.target).ids)])
        bill = self._bill().with_user(operator).with_context(allowed_company_ids=source.ids)
        action = bill.action_change_bill_company()
        wizard = bill.env[action["res_model"]].browse(action["res_id"])
        self.assertEqual(set(wizard.operator_company_ids), {source.id, self.target.id})
        self.assertEqual(wizard.target_company_id, self.target, "one other company needs no choice")
        wizard.target_company_id = source
        with self.assertRaisesRegex(UserError, "different"):
            wizard.action_prepare_mapping()
        wizard.target_company_id = self.target
        wizard.action_prepare_mapping()
        action = wizard.action_apply()
        self.assertEqual(bill.sudo().company_id, self.target)
        # The native link switches to the bill's company, whatever the selector shows.
        self.assertEqual(action["type"], "ir.actions.act_url")

    def test_edits_made_in_the_dialog_are_what_gets_applied(self):
        bill = self._bill(taxed=True)
        wizard = self._mapping(bill)
        other = self._account(self.target, "602000")
        wizard.mapping_ids.target_account_id = other
        wizard.action_apply()
        self.assertEqual((bill.company_id, bill.invoice_line_ids.account_id), (self.target, other))

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
            bill = self._bill(kind="in_refund", taxed=True).with_user(actor).with_context(allowed_company_ids=companies.ids)
            if actor == operator:
                preview = bill.change_bill_company(self.target.id)
                bill.change_bill_company(self.target.id, confirm=True, preview_id=preview["preview_id"])
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
                        def call(**kwargs):
                            return controller.web_json_2_rpc.__wrapped__(
                                controller, "account.move", "change_bill_company", ids=bill.ids,
                                context={"allowed_company_ids": source.ids}, target_company_id=self.target.id, **kwargs)
                        preview = call()
                        self.assertEqual(preview["status"], "ready")
                        done = call(confirm=True, preview_id=preview["preview_id"])
                    self.assertEqual(done["status"], "done")
                    self.assertEqual(audit.call_args.kwargs["outcome"], "succeeded")
                finally:
                    _request_stack.pop()
                    self.env.transaction.default_env = previous
            self.assertEqual((bill.company_id, bill.state, bill.move_type), (self.target, "draft", "in_refund"))
        readonly = grants.create({**values, "name": "Studio billing reader", "access_mode": "read_only"}).user_id
        bill = self._bill().with_user(readonly).with_context(allowed_company_ids=companies.ids)
        with self.assertRaises(AccessError):
            bill.action_change_bill_company()
        with self.assertRaises(AccessError):
            bill.change_bill_company(self.target.id)

    def test_posted_bill_keeps_its_native_reconciliation(self):
        bill = self._bill()
        bill.action_post()
        payment = self.env["account.payment.register"].with_context(active_model="account.move", active_ids=bill.ids).create({})._create_payments()
        matches = bill.line_ids.matched_debit_ids | bill.line_ids.matched_credit_ids
        self.assertTrue(matches)
        with self.assertRaisesRegex(UserError, "draft"):
            bill.action_change_bill_company()
        with self.assertRaisesRegex(UserError, "draft"):
            bill.change_bill_company(self.target.id)
        self.assertEqual((bill.company_id, bill.state, payment.company_id), (self.company_data["company"], "posted", bill.company_id))
        self.assertEqual(bill.line_ids.matched_debit_ids | bill.line_ids.matched_credit_ids, matches)
