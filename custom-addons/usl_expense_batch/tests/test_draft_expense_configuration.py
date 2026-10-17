from odoo import Command
from odoo.exceptions import AccessError, UserError, ValidationError
from odoo.tests import tagged
from odoo.addons.hr_expense.tests.common import TestExpenseCommon


@tagged('post_install', '-at_install', 'usl_expense_draft_configuration')
class TestDraftExpenseConfiguration(TestExpenseCommon):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.target_company = cls.company_data_2['company']
        cls.allowed_companies = (cls.env.company | cls.target_company).ids
        for user in cls.expense_user_employee | cls.expense_user_manager:
            user.company_ids = [Command.set(cls.allowed_companies)]
        cls.target_employee = cls.env['hr.employee'].sudo().create({
            'name': '[QA] Target employee', 'user_id': cls.expense_user_employee.id,
            'company_id': cls.target_company.id,
            'expense_manager_id': cls.expense_user_manager.id,
        }).sudo(False)

    def _expense(self):
        expense = self.env['hr.expense'].with_user(self.expense_user_employee).create({
            'name': '[QA] Draft correction', 'employee_id': self.expense_employee.id,
            'product_id': self.product_c.id, 'total_amount_currency': 42,
            'tax_ids': [Command.clear()], 'date': '2026-10-14',
        })
        attachment = self.env['ir.attachment'].create({
            'name': 'qa-draft-evidence.pdf', 'raw': b'%PDF-1.4\n[QA] Draft evidence\n%%EOF\n',
            'res_model': 'hr.expense', 'res_id': expense.id,
            'company_id': expense.company_id.id,
        })
        expense.message_main_attachment_id = attachment
        return expense.with_context(allowed_company_ids=self.allowed_companies)

    def _move_values(self):
        return {'company_id': self.target_company.id, 'employee_id': self.target_employee.id,
                'product_id': False, 'account_id': False, 'tax_ids': [], 'analytic_distribution': {}}

    def test_financial_comparison_reports_native_included_tax_before_and_after(self):
        eur = self.env.ref("base.EUR")
        self.env.company.currency_id = eur
        tax = self.env["account.tax"].create({
            "name": "[QA] Included purchase VAT", "amount": 20,
            "type_tax_use": "purchase", "company_id": self.env.company.id,
        })
        expense = self._expense()
        expense.write({"currency_id": eur.id, "tax_ids": [Command.set(tax.ids)],
                       "total_amount_currency": 120})
        self.assertEqual(expense.untaxed_amount_currency, 100)
        self.assertEqual(expense.tax_amount_currency, 20)
        result = expense.configure_draft_expense({"total_amount": 60})
        comparison = result["financial_comparison"]
        for snapshot, total, net, vat in ((comparison["before"], 120, 100, 20),
                                         (comparison["after"], 60, 50, 10)):
            self.assertEqual(snapshot["id"], expense.id)
            self.assertEqual(snapshot["state"], "draft")
            self.assertEqual(snapshot["currency_id"][0], eur.id)
            self.assertEqual(snapshot["company_currency_id"][0], eur.id)
            self.assertEqual(snapshot["tax_ids"], tax.ids)
            self.assertEqual(snapshot["total_amount_currency"], total)
            self.assertEqual(snapshot["untaxed_amount_currency"], net)
            self.assertEqual(snapshot["tax_amount_currency"], vat)
            self.assertEqual(snapshot["total_amount"], total)
            self.assertEqual(snapshot["untaxed_amount"], net)
            self.assertEqual(snapshot["tax_amount"], vat)
        expense.invalidate_recordset()
        self.assertEqual(expense.total_amount_currency, 60)
        self.assertEqual(expense.untaxed_amount_currency, 50)
        self.assertEqual(expense.tax_amount_currency, 10)

    def test_financial_comparison_keeps_original_batch_before_explicit_clears(self):
        expense = self._expense()
        baseline = expense.account_id
        override = self.env["account.account"].create({
            "name": "[QA] Shared expense account", "code": "QA6112", "account_type": "expense",
        })
        batch = self.env["usl.expense.batch"].create({
            "name": "[QA] Receipt context", "purpose": "[QA] Verify original context",
            "company_id": self.env.company.id, "employee_id": self.expense_employee.id,
            "account_override_id": override.id,
        })
        batch.add_expenses(expense.ids)
        batch.apply_context(force_expense_ids=expense.ids)
        self.assertEqual(expense.pre_batch_account_id, baseline)
        self.assertEqual(expense.account_id, override)
        comparison = expense.configure_draft_expense({
            "expense_batch_id": False, "product_id": False, "account_id": False,
            "tax_ids": [], "analytic_distribution": {},
        })["financial_comparison"]
        self.assertEqual(comparison["before"]["expense_batch_id"][0], batch.id)
        self.assertEqual(comparison["before"]["account_id"][0], override.id)
        self.assertFalse(comparison["after"]["expense_batch_id"])
        self.assertFalse(comparison["after"]["product_id"])
        self.assertFalse(comparison["after"]["account_id"])
        self.assertEqual(comparison["after"]["tax_ids"], [])
        self.assertFalse(comparison["after"]["analytic_distribution"])
        self.assertEqual(expense.total_amount_currency, 42)
        self.assertFalse(expense.account_move_id)

    def test_owned_pair_correction_preserves_identity_document_amount_and_receipt(self):
        expense = self._expense()
        attachment = expense.message_main_attachment_id
        checksum, amount, currency = attachment.checksum, expense.total_amount_currency, expense.currency_id
        result = expense.configure_draft_expense(self._move_values())
        self.assertEqual(result['expense']['id'], expense.id)
        self.assertEqual(result['expense']['company_id'][0], self.target_company.id)
        self.assertEqual(result['expense']['employee_id'][0], self.target_employee.id)
        self.assertEqual(expense.state, 'draft')
        self.assertEqual(expense.total_amount_currency, amount)
        self.assertEqual(expense.currency_id, currency)
        self.assertEqual(expense.message_main_attachment_id, attachment)
        self.assertEqual(attachment.checksum, checksum)
        self.assertEqual(attachment.company_id, self.target_company)
        self.assertFalse(expense.product_id)
        self.assertFalse(expense.account_id)

    def test_mismatched_employee_leaves_everything_unchanged(self):
        expense = self._expense()
        attachment = expense.message_main_attachment_id
        with self.assertRaisesRegex(ValidationError, 'employee belonging'):
            expense.configure_draft_expense({'company_id': self.target_company.id})
        self.assertEqual(expense.company_id, self.env.company)
        self.assertEqual(expense.employee_id, self.expense_employee)
        self.assertEqual(attachment.company_id, self.env.company)

    def test_unselected_target_company_and_forged_context_are_denied(self):
        expense = self._expense()
        with self.assertRaises(AccessError):
            expense.with_context(allowed_company_ids=[self.env.company.id],
                                 usl_batch_context_internal=True, usl_agent_origin='odoo-mcp').configure_draft_expense(self._move_values())
        self.assertEqual(expense.company_id, self.env.company)
        self.assertEqual(expense.message_main_attachment_id.company_id, self.env.company)

    def test_company_bound_account_requires_explicit_replacement_or_clear(self):
        expense = self._expense()
        self.assertTrue(expense.account_id)
        values = self._move_values()
        values.pop('account_id')
        with self.assertRaisesRegex(ValidationError, 'account_id'):
            expense.configure_draft_expense(values)
        self.assertEqual(expense.company_id, self.env.company)
        target_account = self.company_data_2['default_account_expense']
        values['account_id'] = target_account.id
        result = expense.configure_draft_expense(values)
        self.assertEqual(result['expense']['account_id'][0], target_account.id)

    def test_foreign_tax_and_analytics_are_rejected_without_losing_receipt(self):
        expense = self._expense()
        expense.tax_ids = self.tax_purchase_a
        values = self._move_values()
        values.pop('tax_ids')
        with self.assertRaisesRegex(ValidationError, 'taxes'):
            expense.configure_draft_expense(values)
        self.assertEqual(expense.tax_ids, self.tax_purchase_a)
        analytic = self.env['account.analytic.account'].create({
            'name': '[QA] Source analytic', 'plan_id': self.env['account.analytic.plan'].create({'name': '[QA] Plan'}).id,
            'company_id': self.env.company.id,
        })
        expense.analytic_distribution = {str(analytic.id): 100}
        values = self._move_values()
        values.pop('analytic_distribution')
        with self.assertRaisesRegex(ValidationError, 'analytics'):
            expense.configure_draft_expense(values)
        self.assertEqual(expense.company_id, self.env.company)
        self.assertTrue(expense.message_main_attachment_id)

    def test_batch_detach_and_late_validation_rollback_as_one_transaction(self):
        expense = self._expense()
        batch = self.env['usl.expense.batch'].with_user(self.expense_user_employee).create({
            'name': '[QA] Source batch', 'company_id': self.env.company.id, 'employee_id': self.expense_employee.id,
            'purpose': '[QA] Draft correction review',
        })
        expense.expense_batch_id = batch
        values = self._move_values()
        with self.assertRaisesRegex(ValidationError, 'batch'):
            expense.configure_draft_expense(values)
        values.update({'expense_batch_id': False, 'analytic_distribution': {'999999999': 100}})
        with self.assertRaisesRegex(ValidationError, 'analytics'):
            expense.configure_draft_expense(values)
        self.assertEqual(expense.expense_batch_id, batch)
        self.assertEqual(expense.company_id, self.env.company)
        values['analytic_distribution'] = {}
        expense.configure_draft_expense(values)
        self.assertFalse(expense.expense_batch_id)
        self.assertEqual(expense.company_id, self.target_company)

    def test_submitted_and_approved_expenses_remain_unchanged(self):
        expense = self._expense()
        expense.action_submit()
        self.assertEqual(expense.state, 'submitted')
        with self.assertRaises((UserError, AccessError)):
            expense.configure_draft_expense({'name': '[QA] Must not change'})
        with self.assertRaisesRegex(UserError, 'Only draft'):
            expense.sudo().configure_draft_expense({'name': '[QA] Must not change'})
        expense.with_user(self.expense_user_manager).action_approve()
        self.assertEqual(expense.state, 'approved')
        with self.assertRaisesRegex(UserError, 'Only draft'):
            expense.sudo().configure_draft_expense(self._move_values())
        with self.assertRaises((UserError, AccessError)):
            expense.configure_draft_expense(self._move_values())
        self.assertEqual(expense.company_id, self.env.company)
        self.assertEqual(expense.name, '[QA] Draft correction')

    def test_extra_fields_and_invalid_inputs_are_rejected(self):
        expense = self._expense()
        for values in ({'state': 'approved'}, {'company_id': True}, {'employee_id': False},
                       {'quantity': float('nan')}, {'tax_ids': [True]},
                       {'analytic_distribution': {'0': 100}}, {'name': ''}):
            with self.subTest(values=values), self.assertRaises(ValidationError):
                expense.configure_draft_expense(values)
        self.assertEqual(expense.state, 'draft')

    def test_target_native_rate_preserves_document_currency_and_explicit_amount(self):
        expense = self._expense()
        self.target_company.currency_id = self.other_currency
        self.env['res.currency.rate'].create({
            'currency_id': self.other_currency.id, 'company_id': self.target_company.id,
            'name': '2026-10-14', 'rate': 2,
        })
        document_currency, document_amount = expense.currency_id, expense.total_amount_currency
        comparison = expense.configure_draft_expense(self._move_values())["financial_comparison"]
        self.assertEqual(comparison["before"]["company_currency_id"][0], self.env.company.currency_id.id)
        self.assertEqual(comparison["after"]["company_currency_id"][0], self.other_currency.id)
        self.assertEqual(comparison["after"]["currency_id"][0], document_currency.id)
        self.assertEqual(comparison["before"]["total_amount_currency"], 42)
        self.assertEqual(comparison["after"]["total_amount_currency"], 42)
        self.assertEqual(expense.currency_id, document_currency)
        self.assertEqual(expense.total_amount_currency, document_amount)
        expected = document_currency._convert(document_amount, self.other_currency, self.target_company, expense.date)
        self.assertAlmostEqual(expense.total_amount, expected)
        expense.configure_draft_expense({'total_amount': 77})
        self.assertAlmostEqual(expense.total_amount, 77)

    def test_source_access_and_target_ownership_remain_native(self):
        expense = self._expense()
        self.expense_user_manager_2.group_ids = [Command.set(self.env.ref('base.group_user').ids)]
        self.expense_user_manager_2.company_ids = [Command.set(self.allowed_companies)]
        with self.assertRaises(AccessError):
            expense.with_user(self.expense_user_manager_2).configure_draft_expense(self._move_values())
        foreign_employee = self.env['hr.employee'].sudo().create({
            'name': '[QA] Other target employee', 'company_id': self.target_company.id,
        })
        values = self._move_values() | {'employee_id': foreign_employee.id}
        with self.assertRaises(AccessError):
            expense.configure_draft_expense(values)
        self.assertEqual(expense.company_id, self.env.company)
        self.assertEqual(expense.message_main_attachment_id.company_id, self.env.company)

    def test_managed_agent_correction_and_readonly_denial(self):
        if 'usl.agent' not in self.env:
            self.skipTest('Managed Agent module is not installed.')
        owner = self.expense_user_manager
        expense = self._expense()
        for mode in ('read_only', 'read_write'):
            agent = self.env['usl.agent'].with_user(owner).create({
                'name': '[QA] Expense correction assistant', 'purpose': '[QA] Prepare draft expenses',
                'owner_id': owner.id, 'company_id': self.env.company.id,
                'company_ids': [Command.set(self.allowed_companies)],
                'delegated_group_ids': [Command.set(self.env.ref('hr_expense.group_hr_expense_manager').ids)],
                'access_mode': mode,
            })
            if mode == 'read_only':
                with self.assertRaises(AccessError):
                    expense.with_user(agent.user_id).configure_draft_expense(self._move_values())
                self.assertEqual(expense.company_id, self.env.company)
            else:
                result = expense.with_user(agent.user_id).configure_draft_expense(self._move_values())
                self.assertEqual(result['expense']['company_id'][0], self.target_company.id)
                self.assertEqual(expense.message_main_attachment_id.company_id, self.target_company)

    def test_foreign_company_outside_user_authority_is_denied(self):
        expense = self._expense()
        self.expense_user_employee.company_ids = [Command.set(self.env.company.ids)]
        with self.assertRaises(AccessError):
            expense.configure_draft_expense(self._move_values())
        source = expense.with_context(allowed_company_ids=self.env.company.ids)
        self.assertEqual(source.company_id, self.env.company)
        self.assertEqual(source.message_main_attachment_id.company_id, self.env.company)

    def test_queued_receipt_is_superseded_only_after_authorized_correction(self):
        if 'usl.mail.pdf.retrieval' not in self.env:
            self.skipTest('Receipt retrieval module is not installed.')
        expense = self._expense()
        retrieval = self.env['usl.mail.pdf.retrieval'].sudo().create({
            'expense_id': expense.id, 'state': 'queued', 'generation': 4,
        })
        with self.assertRaises(ValidationError):
            expense.configure_draft_expense({'company_id': self.target_company.id})
        self.assertEqual((retrieval.state, retrieval.generation), ('queued', 4))
        expense.configure_draft_expense(self._move_values())
        self.assertEqual((retrieval.state, retrieval.generation), ('superseded', 5))
        self.assertEqual(retrieval.company_id, self.target_company)
        self.assertEqual(retrieval.expense_id, expense)
        self.assertFalse(retrieval.attachment_id)

    def test_repeated_matching_patch_does_not_duplicate_evidence_or_company_note(self):
        expense = self._expense()
        expense.configure_draft_expense(self._move_values())
        messages, attachments = expense.message_ids.ids, expense.attachment_ids.ids
        expense.configure_draft_expense(self._move_values())
        self.assertEqual(expense.message_ids.ids, messages)
        self.assertEqual(expense.attachment_ids.ids, attachments)

    def test_posted_and_paid_native_expenses_cannot_be_corrected(self):
        expense = self._expense().with_user(self.expense_user_manager)
        expense.action_submit()
        expense.action_approve()
        self.post_expenses_with_wizard(expense.with_user(self.env.user))
        self.assertEqual(expense.state, 'posted')
        move = expense.account_move_id
        with self.assertRaisesRegex(UserError, 'Only draft'):
            expense.sudo().configure_draft_expense(self._move_values())
        self.assertEqual(expense.account_move_id, move)
        paid = self._expense().with_user(self.expense_user_manager)
        paid.payment_mode = 'company_account'
        paid.action_submit()
        paid._do_approve()  # Native test delegate skips the intentional duplicate receipt wizard.
        paid.with_user(self.env.user).action_post()
        self.assertEqual(paid.state, 'paid')
        paid_move = paid.account_move_id
        with self.assertRaisesRegex(UserError, 'Only draft'):
            paid.sudo().configure_draft_expense(self._move_values())
        self.assertEqual(paid.account_move_id, paid_move)

    def test_target_tax_and_shared_analytic_mapping_are_kept(self):
        expense = self._expense()
        self.analytic_account_1.company_id = False
        values = self._move_values() | {
            'account_id': self.company_data_2['default_account_expense'].id,
            'tax_ids': self.company_data_2['default_tax_purchase'].ids,
            'analytic_distribution': {str(self.analytic_account_1.id): 100},
        }
        self.assertFalse(self.analytic_account_1.company_id)
        result = expense.configure_draft_expense(values)
        self.assertEqual(result['expense']['tax_ids'], values['tax_ids'])
        self.assertEqual(result['expense']['analytic_distribution'], values['analytic_distribution'])

    def test_same_company_employee_correction_uses_native_target_access(self):
        expense = self._expense().with_user(self.expense_user_manager)
        another = self.env['hr.employee'].sudo().create({
            'name': '[QA] Correct employee', 'company_id': self.env.company.id,
        })
        result = expense.configure_draft_expense({'employee_id': another.id})
        self.assertEqual(result['expense']['employee_id'][0], another.id)
        self.assertEqual(expense.company_id, self.env.company)
        self.assertTrue(expense.message_main_attachment_id)

    def test_ordinary_label_edit_does_not_require_reading_an_unchanged_supplier_bill(self):
        expense = self._expense()
        partner = self.env['res.partner'].create({'name': '[QA] Bill supplier'})
        bill = self.env['account.move'].create({
            'move_type': 'in_invoice', 'partner_id': partner.id, 'company_id': self.env.company.id,
            'invoice_line_ids': [Command.create({
                'name': '[QA] Existing supplier bill', 'quantity': 1, 'price_unit': 42,
                'account_id': self.company_data['default_account_expense'].id, 'tax_ids': [Command.clear()],
            })],
        })
        expense.with_user(self.env.user).write({'has_existing_bill': True, 'vendor_id': partner.id, 'existing_bill_id': bill.id})
        with self.assertRaises(AccessError):
            bill.with_user(self.expense_user_employee).check_access('read')
        result = expense.configure_draft_expense({'name': '[QA] Corrected label'})
        self.assertEqual(result['expense']['display_name'], '[QA] Corrected label')
        self.assertEqual(expense.existing_bill_id, bill)

    def test_native_split_parts_keep_ownership_together_but_can_prepare_labels(self):
        expense = self._expense()
        action = expense.action_split_wizard()
        wizard = expense.env['hr.expense.split.wizard'].browse(action['res_id'])
        wizard.with_user(self.env.user).action_split_expense()
        family = expense.search([('split_expense_origin_id', '=', expense.id)])
        self.assertEqual(len(family), 2)
        self.assertEqual(sum(family.mapped('total_amount_currency')), 42)
        for part in family:
            with self.assertRaisesRegex(UserError, 'Open Split Expenses'):
                part.configure_draft_expense(self._move_values())
        expense.configure_draft_expense({'name': '[QA] Prepared split label'})
        self.assertEqual(expense.name, '[QA] Prepared split label')
        self.assertEqual(family.company_id, self.env.company)
        self.assertEqual(sum(family.mapped('total_amount_currency')), 42)

    def test_clearing_existing_bill_flag_clears_the_native_link_and_payable_default(self):
        expense = self._expense()
        partner = self.env['res.partner'].create({'name': '[QA] Flag correction supplier'})
        bill = self.env['account.move'].create({
            'move_type': 'in_invoice', 'partner_id': partner.id, 'company_id': self.env.company.id,
            'invoice_line_ids': [Command.create({
                'name': '[QA] Bill flag control', 'quantity': 1, 'price_unit': 42,
                'account_id': self.company_data['default_account_expense'].id, 'tax_ids': [Command.clear()],
            })],
        })
        expense.with_user(self.env.user).write({'has_existing_bill': True, 'vendor_id': partner.id, 'existing_bill_id': bill.id})
        expense.configure_draft_expense({'has_existing_bill': False})
        self.assertFalse(expense.existing_bill_id)
        self.assertFalse(expense.has_existing_bill)
        self.assertEqual(expense.account_id, expense._native_product_account())
        with self.assertRaisesRegex(ValidationError, 'Clear existing_bill_id'):
            expense.configure_draft_expense({'has_existing_bill': False, 'existing_bill_id': bill.id})
        self.assertFalse(expense.existing_bill_id)
