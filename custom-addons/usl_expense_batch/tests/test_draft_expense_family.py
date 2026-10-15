from odoo import Command
from odoo.exceptions import AccessError, UserError, ValidationError
from odoo.tests import tagged
from odoo.addons.hr_expense.tests.common import TestExpenseCommon


@tagged('post_install', '-at_install', 'usl_expense_family')
class TestDraftExpenseFamily(TestExpenseCommon):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.target_company = cls.company_data_2['company']
        cls.allowed_companies = (cls.env.company | cls.target_company).ids
        for user in cls.expense_user_employee | cls.expense_user_manager:
            user.company_ids = [Command.set(cls.allowed_companies)]
        cls.target_employee = cls.env['hr.employee'].sudo().create({
            'name': '[QA] Split target employee', 'company_id': cls.target_company.id,
            'user_id': cls.expense_user_employee.id,
            'expense_manager_id': cls.expense_user_manager.id,
        }).sudo(False)
        cls.import_vat_account = cls.env['account.account'].create({
            'name': '[QA] Recoverable import VAT', 'code': 'QASPLVAT',
            'account_type': 'asset_current', 'company_ids': [Command.set(cls.env.company.ids)],
        })
        cls.service_vat = cls.tax_purchase_a.copy({'name': '[QA] Service VAT 20%', 'amount': 20})

    def _source(self):
        expense = self.env['hr.expense'].with_user(self.expense_user_employee).create({
            'name': '[QA] Mixed import receipt', 'employee_id': self.expense_employee.id,
            'product_id': self.product_c.id, 'total_amount_currency': 160,
            'tax_ids': [Command.clear()], 'date': '2026-10-14',
            'description': '[QA] Source notes',
        })
        attachment = self.env['ir.attachment'].create({
            'name': 'qa-split-source.pdf', 'raw': b'%PDF-1.4\n[QA] Split evidence\n%%EOF\n',
            'res_model': 'hr.expense', 'res_id': expense.id,
            'company_id': expense.company_id.id,
        })
        expense.message_main_attachment_id = attachment
        return expense.with_context(allowed_company_ids=self.allowed_companies)

    def _lines(self):
        return [
            {'name': '[QA] Duty', 'total_amount_currency': 33, 'tax_ids': []},
            {'name': '[QA] Clearance service', 'total_amount_currency': 47,
             'tax_ids': self.service_vat.ids, 'description': '[QA] Native service VAT'},
            {'name': '[QA] Import VAT', 'total_amount_currency': 80,
             'account_id': self.import_vat_account.id, 'tax_ids': [],
             'analytic_distribution': {str(self.analytic_account_1.id): 100}},
        ]

    def _family(self, source):
        return source.search([('split_expense_origin_id', '=', source.id)], order='id')

    def _family_choices(self, family):
        return [{'expense_id': expense.id, 'values': {
            'product_id': False, 'account_id': self.company_data_2['default_account_expense'].id,
            'tax_ids': [], 'analytic_distribution': {}, 'expense_batch_id': False,
        }} for expense in family]

    def test_mixed_components_preserve_gross_defaults_evidence_and_provenance(self):
        source = self._source().with_user(self.env.user)
        before = source.message_main_attachment_id.checksum
        result = source.split_draft_expense(self._lines())
        family = self._family(source)
        self.assertEqual(len(family), 3)
        self.assertEqual(family.mapped('total_amount_currency'), [33, 47, 80])
        self.assertEqual(result['before_total'], 160)
        self.assertEqual(result['after_total'], 160)
        self.assertEqual(result['origin_id'], source.id)
        for part in family:
            self.assertEqual(part.employee_id, source.employee_id)
            self.assertEqual(part.company_id, source.company_id)
            self.assertEqual(part.currency_id, source.currency_id)
            self.assertEqual(str(part.date), '2026-10-14')
            self.assertEqual(part.payment_mode, 'own_account')
            self.assertEqual(part.state, 'draft')
            self.assertFalse(part.account_move_id)
            self.assertIn(before, part.attachment_ids.mapped('checksum'))
        self.assertFalse(family[0].tax_ids)
        self.assertEqual(family[1].tax_ids, self.service_vat)
        self.assertAlmostEqual(family[1].tax_amount_currency, 7.83)
        self.assertAlmostEqual(family[1].untaxed_amount_currency, 39.17)
        self.assertEqual(family[1].description, '[QA] Native service VAT')
        self.assertEqual(family[2].account_id, self.import_vat_account)
        self.assertFalse(family[2].tax_ids)
        self.assertEqual(family[2].account_context_source, 'explicit')
        self.assertEqual(family[2].analytic_distribution, {str(self.analytic_account_1.id): 100})

    def test_native_wizard_rejects_mismatched_gross_sum_server_side(self):
        source = self._source().with_user(self.env.user)
        action = source.action_split_wizard()
        wizard = source.env['hr.expense.split.wizard'].browse(action['res_id'])
        wizard.expense_split_line_ids[0].total_amount_currency = 1
        with self.assertRaisesRegex(ValidationError, 'total'):
            wizard.action_split_expense()
        self.assertEqual(source.total_amount_currency, 160)
        self.assertFalse(source.split_expense_origin_id)

    def test_native_split_preview_never_writes_governed_expenses(self):
        source = self._source().with_user(self.env.user)
        fixed = self.product_c.copy({'name': '[QA] Fixed preview category', 'standard_price': 25})
        self.env.cr.flush()
        before = source.read(['write_date', 'total_amount_currency', 'currency_id', 'message_ids', 'attachment_ids'])
        with patch.object(type(source), 'write', side_effect=AssertionError('Split preview must not write governed expenses')):
            action = source.action_split_wizard()
            wizard = source.env['hr.expense.split.wizard'].browse(action['res_id'])
            line = wizard.expense_split_line_ids[0]
            line.product_id = fixed
            line.with_company(source.company_id)._compute_from_product_id()
            self.assertEqual(line.total_amount_currency, 25)
        self.assertEqual(source.read(['write_date', 'total_amount_currency', 'currency_id', 'message_ids', 'attachment_ids']), before)

    def test_native_wizard_preserves_batch_main_evidence_and_supersedes_receipt_work(self):
        source = self._source().with_user(self.env.user)
        batch = self.env['usl.expense.batch'].create({
            'name': '[QA] Native split batch', 'purpose': '[QA] Preserve native split defaults',
            'company_id': source.company_id.id, 'employee_id': source.employee_id.id,
        })
        source.expense_batch_id = batch
        retrieval = self.env['usl.mail.pdf.retrieval'].sudo().create({'expense_id': source.id, 'state': 'queued', 'generation': 4}) if 'usl.mail.pdf.retrieval' in self.env else None
        checksum = source.message_main_attachment_id.checksum
        action = source.action_split_wizard()
        source.env['hr.expense.split.wizard'].browse(action['res_id']).action_split_expense()
        family = self._family(source)
        self.assertTrue(all(part.expense_batch_id == batch for part in family))
        self.assertTrue(all(part.message_main_attachment_id.checksum == checksum for part in family))
        if retrieval:
            self.assertEqual(retrieval.generation, 5)
            self.assertNotEqual(retrieval.state, 'queued')

    def test_split_refuses_a_second_creation_without_duplicate_parts(self):
        source = self._source()
        source.split_draft_expense(self._lines())
        before = self._family(source).ids
        with self.assertRaises(UserError):
            source.split_draft_expense(self._lines())
        self.assertEqual(self._family(source).ids, before)

    def test_complete_family_can_be_corrected_without_changing_document_totals(self):
        source = self._source()
        source.split_draft_expense(self._lines())
        family = self._family(source)
        before = {e.id: (e.total_amount_currency, e.currency_id.id, e.attachment_ids.mapped('checksum')) for e in family}
        result = source.configure_draft_expense_family(
            family.ids, self.target_company.id, self.target_employee.id, self._family_choices(family))
        self.assertEqual(len(result['expenses']), 3)
        self.assertEqual(result['totals'], [{'currency_id': source.currency_id.id, 'before_total': 160, 'after_total': 160}])
        for part in family:
            self.assertEqual(part.company_id, self.target_company)
            self.assertEqual(part.employee_id, self.target_employee)
            self.assertEqual((part.total_amount_currency, part.currency_id.id, part.attachment_ids.mapped('checksum')), before[part.id])
            self.assertEqual(part.split_expense_origin_id, source)

    def test_incomplete_family_input_preserves_every_part(self):
        source = self._source()
        source.split_draft_expense(self._lines())
        family = self._family(source)
        with self.assertRaises(UserError):
            source.configure_draft_expense_family(
                family.ids[:2], self.target_company.id, self.target_employee.id, self._family_choices(family)[:2])
        self.assertEqual(family.mapped('company_id'), self.env.company)

    def test_late_invalid_family_mapping_rolls_back_earlier_parts(self):
        source = self._source()
        source.split_draft_expense(self._lines())
        family = self._family(source)
        choices = self._family_choices(family)
        choices[-1]['values']['account_id'] = self.company_data['default_account_expense'].id
        before = family.read(['company_id', 'employee_id', 'total_amount_currency', 'message_ids'])
        with self.assertRaises(ValidationError):
            source.configure_draft_expense_family(family.ids, self.target_company.id, self.target_employee.id, choices)
        self.assertEqual(family.read(['company_id', 'employee_id', 'total_amount_currency', 'message_ids']), before)

    def test_completed_native_wizard_cannot_replay_a_matching_remaining_total(self):
        source = self._source().with_user(self.env.user)
        action = source.action_split_wizard()
        wizard = source.env['hr.expense.split.wizard'].browse(action['res_id'])
        template = source._get_split_values()[0]
        wizard.expense_split_line_ids = [Command.clear()] + [Command.create(template | {'total_amount_currency': amount}) for amount in (80, 40, 20, 20)]
        wizard.action_split_expense()
        before = self._family(source).read(['id', 'total_amount_currency', 'message_ids'])
        with self.assertRaisesRegex(UserError, 'already applied'):
            wizard.action_split_expense()
        self.assertEqual(self._family(source).read(['id', 'total_amount_currency', 'message_ids']), before)

    def test_invalid_late_split_mapping_leaves_source_receipt_and_history_unchanged(self):
        source = self._source().with_user(self.env.user)
        lines = self._lines()
        lines[-1]['account_id'] = self.company_data_2['default_account_expense'].id
        before = source.read(['name', 'total_amount_currency', 'message_ids', 'attachment_ids'])
        with self.assertRaises(ValidationError):
            source.split_draft_expense(lines)
        self.assertEqual(source.read(['name', 'total_amount_currency', 'message_ids', 'attachment_ids']), before)
        self.assertFalse(self._family(source))

    def test_amount_mismatch_and_nonfinite_or_forged_values_never_create_parts(self):
        source = self._source()
        for lines in (
            [{'total_amount_currency': 60}, {'total_amount_currency': 99}],
            [{'total_amount_currency': float('nan')}, {'total_amount_currency': 160}],
            [{'total_amount_currency': True}, {'total_amount_currency': 159}],
            [{'total_amount_currency': 80, 'state': 'approved'}, {'total_amount_currency': 80}],
            [{'total_amount_currency': 1e308}, {'total_amount_currency': 1e308}],
        ):
            with self.assertRaises(ValidationError):
                source.split_draft_expense(lines)
            self.assertEqual(source.total_amount_currency, 160)
            self.assertFalse(self._family(source))

    def test_currency_precision_and_negative_refund_defaults_use_native_amounts(self):
        source = self._source()
        result = source.split_draft_expense([{'total_amount_currency': 79.999}, {'total_amount_currency': 80.001}])
        self.assertEqual(result['after_total'], 160)
        refund = self._source()
        refund.write({'total_amount_currency': -160})
        refund.split_draft_expense([{'total_amount_currency': -60}, {'total_amount_currency': -100}])
        self.assertEqual(sum(self._family(refund).mapped('total_amount_currency')), -160)

    def test_native_signed_discount_and_zero_component_preserve_the_explicit_net_total(self):
        for amounts in ((190, -30), (0, 160)):
            source = self._source()
            source.split_draft_expense([{'total_amount_currency': amount} for amount in amounts])
            family = self._family(source)
            self.assertEqual(family.mapped('total_amount_currency'), list(amounts))
            self.assertEqual(sum(family.mapped('total_amount_currency')), 160)

    def test_submitted_source_cannot_split_and_submitted_member_cannot_move(self):
        source = self._source().with_user(self.env.user)
        source.action_submit()
        with self.assertRaises(UserError):
            source.split_draft_expense(self._lines())
        self.assertFalse(self._family(source))
        source = self._source().with_user(self.env.user)
        source.split_draft_expense(self._lines())
        family = self._family(source)
        family[-1].action_submit()
        self.assertEqual(family[-1].state, 'submitted')
        self.env.cr.flush()
        before = family.read(['state', 'company_id', 'employee_id', 'message_ids'])
        with self.assertRaises(UserError):
            source.configure_draft_expense_family(family.ids, self.target_company.id, self.target_employee.id, self._family_choices(family))
        self.assertEqual(family.read(['state', 'company_id', 'employee_id', 'message_ids']), before)

    def test_native_wizard_rejects_submitted_source_and_foreign_line_currency(self):
        source = self._source().with_user(self.env.user)
        wizard = source.env['hr.expense.split.wizard'].browse(source.action_split_wizard()['res_id'])
        source.action_submit()
        with self.assertRaises(UserError):
            wizard.action_split_expense()
        source = self._source().with_user(self.env.user)
        wizard = source.env['hr.expense.split.wizard'].browse(source.action_split_wizard()['res_id'])
        wizard.expense_split_line_ids[-1].currency_id = self.other_currency
        with self.assertRaises(ValidationError):
            wizard.action_split_expense()
        self.assertFalse(self._family(source))

    def test_unselected_target_and_forged_family_scope_preserve_all_parts(self):
        source = self._source()
        source.split_draft_expense([{'total_amount_currency': 60}, {'total_amount_currency': 100}])
        family = self._family(source)
        with self.assertRaises(AccessError):
            source.with_context(allowed_company_ids=self.env.company.ids, usl_agent_collaboration_token=True,
                                usl_batch_context_internal=True, family_members=family.ids).configure_draft_expense_family(
                family.ids, self.target_company.id, self.target_employee.id, self._family_choices(family))
        self.assertEqual(family.mapped('company_id'), self.env.company)
        with self.assertRaises(UserError):
            source.with_context(family_members=family.ids).configure_draft_expense({'employee_id': self.target_employee.id, 'company_id': self.target_company.id})
        self.assertEqual(family.mapped('company_id'), self.env.company)

    def test_hidden_family_member_denial_discloses_no_private_identity(self):
        source = self._source().with_user(self.env.user)
        source.split_draft_expense(self._lines())
        family = self._family(source)
        hidden_employee = self.env['hr.employee'].sudo().create({'name': '[QA] Other split owner', 'company_id': self.env.company.id})
        hidden = family[-1]
        hidden.write({'name': '[QA] Hidden split part', 'employee_id': hidden_employee.id})
        self.env.cr.flush()
        before = family.read(['name', 'company_id', 'employee_id', 'message_ids'])
        with self.assertRaises(AccessError) as caught:
            source.with_user(self.expense_user_employee).configure_draft_expense_family(
                family.ids, self.target_company.id, self.target_employee.id, self._family_choices(family))
        self.assertNotIn(hidden.name, str(caught.exception))
        self.assertNotIn(str(hidden.id), str(caught.exception))
        self.assertEqual(family.read(['name', 'company_id', 'employee_id', 'message_ids']), before)

    def test_split_preserves_batch_and_explicit_choices_with_native_auto_sync(self):
        source = self._source().with_user(self.env.user)
        batch = self.env['usl.expense.batch'].create({'name': '[QA] Split batch', 'purpose': '[QA] Split accounting context', 'company_id': self.env.company.id,
                                                    'employee_id': source.employee_id.id})
        source.expense_batch_id = batch
        source.split_draft_expense(self._lines())
        family = self._family(source)
        self.assertEqual(family.mapped('expense_batch_id'), batch)
        self.assertEqual(family[-1].account_id, self.import_vat_account)
        self.assertEqual(family[-1].analytic_distribution, {str(self.analytic_account_1.id): 100})
        self.assertEqual(sum(family.mapped('total_amount_currency')), 160)

    def test_success_supersedes_only_source_receipt_work_and_denial_keeps_it(self):
        if 'usl.mail.pdf.retrieval' not in self.env:
            self.skipTest('Receipt retrieval is not installed in this standalone module registry.')
        source = self._source().with_user(self.env.user)
        retrieval = self.env['usl.mail.pdf.retrieval'].sudo().create({'expense_id': source.id, 'state': 'queued', 'generation': 4}).sudo(False)
        with self.assertRaises(ValidationError):
            source.split_draft_expense([{'total_amount_currency': 20}, {'total_amount_currency': 30}])
        self.assertEqual((retrieval.state, retrieval.generation), ('queued', 4))
        source.split_draft_expense(self._lines())
        self.assertEqual((retrieval.state, retrieval.generation), ('superseded', 5))

    def test_managed_agent_split_and_family_move_without_generic_wizard_grant(self):
        if 'usl.agent' not in self.env:
            self.skipTest('Managed Agent is not installed in this standalone module registry.')
        owner = self.expense_user_manager
        for mode in ('read_only', 'read_write'):
            source = self._source()
            agent = self.env['usl.agent'].with_user(owner).create({'name': '[QA] Split assistant', 'purpose': '[QA] Prepare drafts',
                'owner_id': owner.id, 'company_id': self.env.company.id, 'company_ids': [Command.set(self.allowed_companies)],
                'delegated_group_ids': [Command.set(self.env.ref('hr_expense.group_hr_expense_manager').ids)], 'access_mode': mode})
            records = source.with_user(agent.user_id)
            with self.assertRaises(AccessError):
                records.with_context(usl_agent_collaboration_token=True).env['hr.expense.split'].create(source._get_split_values())
            with self.assertRaises(AccessError), self.env.cr.savepoint():
                records.with_context(usl_agent_collaboration_token=True).copy({'name': '[QA] Forged generic copy'})
            with self.assertRaises(AccessError):
                records.with_context(usl_agent_collaboration_token=True).env['mail.followers'].create({
                    'res_model': 'hr.expense', 'res_id': source.id, 'partner_id': owner.partner_id.id})
            if mode == 'read_only':
                with self.assertRaises(AccessError):
                    records.split_draft_expense([{'total_amount_currency': 60}, {'total_amount_currency': 100}])
                self.assertFalse(self._family(source))
            else:
                records.split_draft_expense([{'total_amount_currency': 60}, {'total_amount_currency': 100}])
                family = self._family(source)
                choices = [{'expense_id': part.id, 'values': {'product_id': False, 'account_id': False,
                    'tax_ids': [], 'analytic_distribution': {}, 'expense_batch_id': False}} for part in family]
                records.configure_draft_expense_family(family.ids, self.target_company.id, self.target_employee.id, choices)
                self.assertEqual(family.mapped('company_id'), self.target_company)
                self.assertEqual(sum(family.mapped('total_amount_currency')), 160)

    def test_actual_posted_and_paid_sources_preserve_journal_and_evidence(self):
        for payment_mode in ('own_account', 'company_account'):
            source = self._source().with_user(self.expense_user_manager)
            source.payment_mode = payment_mode
            source.action_submit()
            source._do_approve()
            if payment_mode == 'own_account':
                self.post_expenses_with_wizard(source.with_user(self.env.user))
                self.assertEqual(source.state, 'posted')
            else:
                source.with_user(self.env.user).action_post()
                self.assertEqual(source.state, 'paid')
            move = source.account_move_id
            before = move.read(['state', 'name', 'amount_total', 'line_ids'])
            receipt = source.attachment_ids.mapped('checksum')
            with self.assertRaises(UserError):
                source.sudo().split_draft_expense([{'total_amount_currency': 60}, {'total_amount_currency': 100}])
            self.assertEqual(move.read(['state', 'name', 'amount_total', 'line_ids']), before)
            self.assertEqual(source.attachment_ids.mapped('checksum'), receipt)
            self.assertFalse(source.split_expense_origin_id)

    def test_posted_family_part_refuses_atomic_move_and_keeps_ledger(self):
        source = self._source().with_user(self.env.user)
        source.split_draft_expense([{'total_amount_currency': 60}, {'total_amount_currency': 100}])
        family = self._family(source)
        posted = family[-1].with_user(self.expense_user_manager)
        posted.action_submit()
        posted._do_approve()
        self.post_expenses_with_wizard(posted.with_user(self.env.user))
        self.assertEqual(posted.state, 'posted')
        self.env.cr.flush()
        before = family.read(['company_id', 'employee_id', 'state', 'account_move_id', 'total_amount_currency', 'message_ids'])
        move_before = posted.account_move_id.line_ids.read(['account_id', 'debit', 'credit', 'amount_currency', 'currency_id'], load=False)
        with self.assertRaises(UserError):
            source.configure_draft_expense_family(family.ids, self.target_company.id, self.target_employee.id, self._family_choices(family))
        self.assertEqual(family.read(['company_id', 'employee_id', 'state', 'account_move_id', 'total_amount_currency', 'message_ids']), before)
        self.assertEqual(posted.account_move_id.line_ids.read(['account_id', 'debit', 'credit', 'amount_currency', 'currency_id'], load=False), move_before)

    def test_target_historical_rate_preserves_each_document_gross_and_currency(self):
        source = self._source().with_user(self.env.user)
        source.split_draft_expense([{'total_amount_currency': 60}, {'total_amount_currency': 100}])
        family = self._family(source)
        self.target_company.currency_id = self.other_currency
        self.env['res.currency.rate'].create({'name': '2026-10-14', 'currency_id': source.currency_id.id,
                                             'company_id': self.target_company.id, 'rate': 0.5})
        before = {part.id: (part.currency_id, part.total_amount_currency) for part in family}
        source.configure_draft_expense_family(family.ids, self.target_company.id, self.target_employee.id, self._family_choices(family))
        for part in family:
            currency, gross = before[part.id]
            self.assertEqual(part.currency_id, currency)
            self.assertEqual(part.total_amount_currency, gross)
            self.assertAlmostEqual(part.total_amount, currency._convert(gross, self.other_currency, self.target_company, part.date))

    def test_same_company_family_employee_change_and_repeat_preserve_owned_identity(self):
        source = self._source().with_user(self.env.user)
        source.split_draft_expense([{'total_amount_currency': 60}, {'total_amount_currency': 100}])
        family = self._family(source)
        target = self.env['hr.employee'].sudo().create({'name': '[QA] Other same-company employee', 'company_id': self.env.company.id})
        choices = [{'expense_id': part.id, 'values': {}} for part in family]
        source.configure_draft_expense_family(family.ids, self.env.company.id, target.id, choices)
        self.assertEqual(family.mapped('employee_id'), target)
        self.env.cr.flush()
        before = family.read(['id', 'message_ids', 'attachment_ids', 'total_amount_currency'])
        source.configure_draft_expense_family(family.ids, self.env.company.id, target.id, choices)
        self.assertEqual(family.read(['id', 'message_ids', 'attachment_ids', 'total_amount_currency']), before)

    def test_family_plan_rejects_duplicates_sensitive_fields_and_wrong_target_employee(self):
        source = self._source().with_user(self.env.user)
        source.split_draft_expense([{'total_amount_currency': 60}, {'total_amount_currency': 100}])
        family = self._family(source)
        choices = self._family_choices(family)
        for bad_ids, bad_lines in (
            ([family[0].id, family[0].id], choices),
            (family.ids, [choices[0], choices[0]]),
            (family.ids, [choices[0], {'expense_id': family[-1].id, 'values': {'total_amount': 999}}]),
            (family.ids, [choices[0], {'expense_id': True, 'values': {}}]),
        ):
            with self.assertRaises(ValidationError):
                source.configure_draft_expense_family(bad_ids, self.target_company.id, self.target_employee.id, bad_lines)
        with self.assertRaises(ValidationError):
            source.configure_draft_expense_family(family.ids, self.target_company.id, source.employee_id.id, choices)
        self.assertEqual(family.mapped('company_id'), self.env.company)

    def test_only_source_owner_can_split_and_serialized_context_cannot_grant_copy(self):
        source = self._source()
        outsider = self.expense_user_manager_2
        outsider.group_ids = [Command.set(self.env.ref('base.group_user').ids)]
        with self.assertRaises(AccessError):
            source.with_user(outsider).with_context(usl_agent_collaboration_token=True).split_draft_expense([
                {'total_amount_currency': 60}, {'total_amount_currency': 100}])
        self.assertFalse(source.split_expense_origin_id)

    def test_native_split_retains_selected_secondary_company_context(self):
        source = self.env['hr.expense'].with_company(self.target_company).with_context(
            allowed_company_ids=self.allowed_companies).create({
                'name': '[QA] Secondary-company receipt', 'company_id': self.target_company.id,
                'employee_id': self.target_employee.id, 'product_id': self.product_c.id,
                'account_id': self.company_data_2['default_account_expense'].id,
                'total_amount_currency': 160, 'tax_ids': [Command.clear()], 'date': '2026-10-14',
            })
        # The ordinary accounting actor's default company is still the first company.
        self.assertNotEqual(source.company_id, self.env.user.company_id)
        wizard = source.env['hr.expense.split.wizard'].browse(source.action_split_wizard()['res_id'])
        wizard.action_split_expense()
        family = self._family(source)
        self.assertEqual(len(family), 2)
        self.assertEqual(family.mapped('company_id'), self.target_company)
        self.assertEqual(sum(family.mapped('total_amount_currency')), 160)

    def test_incomplete_source_can_use_explicit_categories_and_keep_native_defaults(self):
        source = self._source()
        source.configure_draft_expense({'product_id': False, 'account_id': False})
        source.split_draft_expense([
            {'product_id': self.product_c.id, 'total_amount_currency': 60},
            {'product_id': self.product_c.id, 'total_amount_currency': 100},
        ])
        family = self._family(source)
        for part in family:
            self.assertEqual(part.product_id, self.product_c)
            self.assertEqual(part.account_id, part._native_product_account())
            self.assertEqual(part.account_context_source, 'product')
        self.assertEqual(sum(family.mapped('total_amount_currency')), 160)

    def test_category_cost_change_cannot_change_the_original_document_currency(self):
        source = self._source().with_user(self.env.user)
        free = self.product_c.copy({'name': '[QA] Variable-price category', 'standard_price': 0})
        fixed = free.copy({'name': '[QA] Fixed-price category', 'standard_price': 12.5})
        source.configure_draft_expense({'product_id': free.id})
        source.write({'currency_id': self.other_currency.id, 'total_amount_currency': 160})
        before_currency = source.currency_id
        before_company_total = source.total_amount
        with self.assertRaisesRegex(ValidationError, 'variable-price category'):
            source.split_draft_expense([
                {'product_id': fixed.id, 'name': '[QA] Fixed component', 'total_amount_currency': 60},
                {'product_id': free.id, 'name': '[QA] Variable component', 'total_amount_currency': 100},
            ])
        self.assertEqual(source.currency_id, before_currency)
        self.assertEqual(source.total_amount_currency, 160)
        self.assertFalse(source.split_expense_origin_id)
        source.split_draft_expense([
            {'product_id': free.id, 'name': '[QA] Variable component one', 'total_amount_currency': 60},
            {'product_id': free.id, 'name': '[QA] Variable component two', 'total_amount_currency': 100},
        ])
        family = self._family(source)
        self.assertEqual(family.mapped('currency_id'), before_currency)
        self.assertEqual(family.mapped('total_amount_currency'), [60, 100])
        self.assertAlmostEqual(sum(family.mapped('total_amount')), before_company_total)

    def test_fixed_cost_category_keeps_native_company_currency_gross(self):
        source = self._source().with_user(self.env.user)
        free = self.product_c.copy({'name': '[QA] Variable-price category', 'standard_price': 0})
        fixed = free.copy({'name': '[QA] Fixed-price category', 'standard_price': 25})
        source.configure_draft_expense({'product_id': free.id, 'quantity': 1})
        self.assertEqual(fixed.with_company(source.company_id).standard_price, 25)
        with self.assertRaisesRegex(ValidationError, 'native split amount'):
            source.split_draft_expense([
                {'product_id': fixed.id, 'total_amount_currency': 60},
                {'product_id': free.id, 'total_amount_currency': 100},
            ])
        self.assertEqual(source.total_amount_currency, 160)
        self.assertFalse(source.split_expense_origin_id)
        source.split_draft_expense([
            {'product_id': fixed.id, 'total_amount_currency': 25},
            {'product_id': free.id, 'total_amount_currency': 135},
        ])
        family = self._family(source)
        self.assertEqual(family.mapped('total_amount_currency'), [25, 135])
        self.assertEqual(source.price_unit, 25)
        self.assertEqual(source.quantity, 1)
from unittest.mock import patch
