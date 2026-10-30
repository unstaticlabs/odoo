"""Original-valuation correction uses native French taxes and native recovery."""
from datetime import timedelta
from unittest.mock import patch

from odoo import Command, fields
from odoo.exceptions import AccessError, UserError
from odoo.tests import tagged
from odoo.tests.common import new_test_user
from odoo.addons.account.tests.common import AccountTestInvoicingCommon


@tagged('post_install', '-at_install', 'usl_taxed_payment_rate', 'usl_accounting_unit')
class TestTaxedPaymentRate(AccountTestInvoicingCommon):
    chart_template = 'fr_comp'
    country_code = 'FR'

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.env.user.group_ids = [Command.link(cls.env.ref('account.group_account_user').id)]
        cls.company = cls.env.company
        cls.day = fields.Date.today() - timedelta(days=10)
        cls.usd = cls.setup_other_currency('USD', rates=[(cls.day, 10 / 9)])
        cls.bank = cls.company_data['default_journal_bank']
        cls.bank.reconcile_mode = 'edit'
        cls.bank.suspense_account_id.reconcile = True
        cls.company.tax_exigibility = True
        cls.company.account_cash_basis_base_account_id = cls.company_data['default_account_assets']

    def _fixture(self, tax_ref='tva_acq_normale', *, move_type='in_invoice', price=100, prices=None):
        tax = self.env['account.chart.template'].ref(tax_ref) if tax_ref else self.env['account.tax']
        bill = self.env['account.move'].create({
            'move_type': move_type, 'partner_id': self.partner_a.id,
            'journal_id': self.company_data['default_journal_purchase'].id,
            'invoice_date': self.day, 'date': self.day, 'currency_id': self.usd.id,
            'invoice_currency_rate': 1.25,
            'invoice_line_ids': [Command.create({
                'name': '[QA2574] Invented purchase', 'quantity': 1, 'price_unit': p,
                'account_id': self.company_data['default_account_expense'].id,
                'tax_ids': [Command.set(tax.ids)],
            }) for p in (prices or [price])],
        })
        bill._post(soft=False)
        return bill

    def _statement(self, bill, amount):
        sign = 1 if bill.move_type == 'in_refund' else -1
        statement = self.env['account.bank.statement.line'].create({
            'journal_id': self.bank.id, 'date': self.day + timedelta(days=1),
            'payment_ref': '[QA2574] Invented bank transaction',
            'partner_id': bill.partner_id.id, 'amount': sign * amount,
        })
        return statement, statement._seek_for_lines()[1]

    def _report(self):
        wizard = self.env['rebuild.account.report.export.wizard'].create({
            'report_type': 'tax_report', 'company_id': self.company.id,
            'company_ids': [Command.set(self.company.ids)],
            'date_from': self.day, 'date_to': fields.Date.today(),
            'target_move': 'posted', 'group_by': 'section',
        })
        return {k[0]: float(v) for k, v in wizard._french_tax_expression_values(
            wizard._french_tax_report()).items() if k[1] == 'balance'}

    def _line_values(self, bill):
        return [(l.id, l.account_id.id, l.balance, l.amount_currency,
                 l.tax_base_amount, l.tax_ids.ids, l.tax_line_id.id,
                 l.tax_tag_ids.ids, l.tax_repartition_line_id.id,
                 bool(l.filtered_domain(l._get_tax_exigible_domain())))
                for l in bill.line_ids.sorted('id')]

    def _correct_and_reverse(self, tax_ref, gross, expected_balances):
        bill = self._fixture(tax_ref)
        original = self._line_values(bill)
        original_name = bill.name
        original_report = self._report()
        statement, source = self._statement(bill, gross)
        bank_values = (statement.amount, statement.foreign_currency_id.id,
                       statement.amount_currency, statement.date, statement.payment_ref)
        preview_before = (bill.write_date, self._line_values(bill))
        eligibility = bill._get_payment_rate_settlement_eligibility(source)
        self.assertTrue(eligibility['eligible'], eligibility['reason'])
        self.assertEqual((bill.write_date, self._line_values(bill)), preview_before)
        result = bill.js_use_payment_rate_outstanding_line(source.id)
        settlement = self.env['account.immediate.settlement'].browse(result['settlement_id'])
        self.assertEqual([l.balance for l in bill.line_ids.sorted('id')], expected_balances)
        self.assertEqual(bill.name, original_name)
        self.assertEqual(bill.amount_residual, 0)
        self.assertFalse(settlement.exchange_move_ids)
        self.assertFalse(settlement.adjustment_move_id)
        self.assertEqual(settlement.repriced_document_company_amount, gross)
        self.assertEqual([l.amount_currency for l in bill.line_ids.sorted('id')], [x[3] for x in original])
        self.assertTrue(all(l.tax_base_amount == 90 for l in bill.line_ids.filtered('tax_line_id')))
        self.assertEqual(self._report()['box_20'], 18)
        if tax_ref == 'tva_intra_normale_biens':
            self.assertEqual(self._report()['box_B2'], 90)
            self.assertEqual(self._report()['box_08_taxe'], 18)
        caba = self.env['account.move'].search([('tax_cash_basis_origin_move_id', '=', bill.id)])
        self.assertEqual(len(caba), int(tax_ref == 'tva_acq_encaissement'))
        settlement.action_reverse()
        self.assertEqual(settlement.state, 'reversed')
        self.assertEqual(self._line_values(bill), original)
        self.assertEqual(bill.invoice_currency_rate, 1.25)
        self.assertFalse(statement.is_reconciled)
        self.assertEqual((statement.amount, statement.foreign_currency_id.id,
                          statement.amount_currency, statement.date, statement.payment_ref), bank_values)
        self.assertFalse(bill.line_ids.matched_debit_ids | bill.line_ids.matched_credit_ids)
        self.assertEqual(self._report(), original_report)
        if caba:
            reversal = self.env['account.move'].search([('reversed_entry_id', 'in', caba.ids)])
            self.assertEqual(len(reversal), 1)
            self.assertEqual((caba | reversal).mapped('state'), ['posted', 'posted'])
        return bill, statement, settlement

    def test_native_french_ordinary_tax_correction_and_full_restore(self):
        self._correct_and_reverse('tva_acq_normale', 108, [90, 18, -108])

    def test_native_french_reverse_charge_correction_and_full_restore(self):
        self._correct_and_reverse('tva_intra_normale_biens', 90, [90, 18, -18, -90])

    def test_native_french_cash_basis_correction_and_full_restore(self):
        self._correct_and_reverse('tva_acq_encaissement', 108, [90, 18, -108])

    def test_included_tax_and_split_rounding_use_native_gross(self):
        for included, prices in [(False, [33.33, 33.33, 33.34]), (True, [120])]:
            with self.subTest(included=included):
                tax = self.env['account.chart.template'].ref('tva_acq_normale')
                tax.price_include_override = 'tax_included' if included else 'tax_excluded'
                bill = self._fixture(prices=prices)
                original = self._line_values(bill)
                statement, source = self._statement(bill, 108)
                result = bill.js_use_payment_rate_outstanding_line(source.id)
                settlement = self.env['account.immediate.settlement'].browse(result['settlement_id'])
                self.assertEqual(settlement.repriced_document_company_amount, 108)
                self.assertEqual(sum(bill.line_ids.filtered('tax_line_id').mapped('balance')), 18)
                settlement.action_reverse()
                self.assertEqual(self._line_values(bill), original)

    def test_refund_uses_native_signs_and_restores(self):
        bill = self._fixture(move_type='in_refund')
        original = self._line_values(bill)
        statement, source = self._statement(bill, 108)
        result = bill.js_use_payment_rate_outstanding_line(source.id)
        settlement = self.env['account.immediate.settlement'].browse(result['settlement_id'])
        self.assertEqual([l.balance for l in bill.line_ids.sorted('id')], [-90, -18, 108])
        settlement.action_reverse()
        self.assertEqual(self._line_values(bill), original)
        self.assertEqual(statement.amount, 108)

    def test_taxed_original_valuation_remains_when_using_ordinary_settle(self):
        bill = self._fixture()
        original = self._line_values(bill)
        statement, source = self._statement(bill, 108)
        # Seed payment-day reference independently, so Odoo's inferred foreign
        # differs from the exact foreign invoice amount and Settle is available.
        self.env['res.currency.rate'].search([('currency_id','=',self.usd.id), ('company_id','=',self.company.id), ('name','=',self.day)]).rate = 1.25
        result = bill.js_settle_outstanding_line(source.id)
        settlement = self.env['account.immediate.settlement'].browse(result['settlement_id'])
        self.assertEqual(settlement.settlement_difference, 12)
        self.assertEqual([x[:10] for x in self._line_values(bill)], [x[:10] for x in original])
        self.assertEqual(self._report()['box_20'], 16)
        settlement.action_reverse()
        self.assertEqual(self._line_values(bill), original)

    def test_never_paid_required_and_readonly_native_authority(self):
        bill = self._fixture()
        terms = bill.line_ids.filtered(lambda l:l.display_type=='payment_term')
        payment = self.env['account.move'].create({
            'journal_id':self.bank.id, 'date':self.day+timedelta(days=1),
            'line_ids':[Command.create({'name':'[QA2574] Actual partial payment',
                'account_id':terms.account_id.id,'partner_id':bill.partner_id.id,
                'balance':48,'currency_id':self.usd.id,'amount_currency':60}),
                Command.create({'name':'[QA2574] Bank partial',
                'account_id':self.bank.default_account_id.id,'balance':-48})],
        })
        payment._post(soft=False)
        (terms|payment.line_ids.filtered(lambda l:l.account_id==terms.account_id)).reconcile()
        statement2, source2 = self._statement(bill, 54)
        eligibility = bill._get_payment_rate_settlement_eligibility(source2)
        self.assertFalse(eligibility['eligible'])
        self.assertIn('unpaid', eligibility['reason'])
        reader = new_test_user(self.env, login='qa2574.reader',
            groups='account.group_account_readonly', company_id=self.company.id,
            company_ids=[Command.set(self.company.ids)])
        with self.assertRaises(AccessError), self.env.cr.savepoint():
            bill.with_user(reader).js_use_payment_rate_outstanding_line(source2.id)

    def test_actual_gross_mismatch_rolls_back_every_write(self):
        bill = self._fixture()
        statement, source = self._statement(bill, 108)
        original = self._line_values(bill)
        original_method = type(bill)._get_payment_rate_settlement_eligibility
        def changed_gross(records, payment_line, **kwargs):
            result = original_method(records, payment_line, **kwargs)
            return {**result, 'applied_invoice_currency_rate': 1.2}
        with patch.object(type(bill), '_get_payment_rate_settlement_eligibility', changed_gross):
            with self.assertRaisesRegex(UserError, 'exact bank amount'), self.env.cr.savepoint():
                bill.js_use_payment_rate_outstanding_line(source.id)
        self.assertEqual(self._line_values(bill), original)
        self.assertFalse(statement.is_reconciled)
        self.assertFalse(bill.immediate_settlement_ids)

    def test_late_cash_basis_error_rolls_back_correction_and_bank(self):
        bill = self._fixture('tva_acq_encaissement')
        statement, source = self._statement(bill, 108)
        original = self._line_values(bill)
        all_partials = self.env['account.partial.reconcile'].search([]).ids
        original_method = type(bill)._foreign_settlement_announce
        def fail_after_financial_writes(records, *args):
            original_method(records, *args)
            raise UserError('Synthetic late failure after CABA and reconciliation')
        with patch.object(type(bill), '_foreign_settlement_announce', fail_after_financial_writes):
            with self.assertRaisesRegex(UserError, 'Synthetic late'), self.env.cr.savepoint():
                bill.js_use_payment_rate_outstanding_line(source.id)
        self.assertEqual(self._line_values(bill), original)
        self.assertFalse(statement.is_reconciled)
        self.assertFalse(statement.foreign_currency_id)
        self.assertFalse(bill.immediate_settlement_ids)
        self.assertEqual(self.env['account.partial.reconcile'].search([]).ids, all_partials)
        self.assertFalse(self.env['account.move'].search([('tax_cash_basis_origin_move_id', '=', bill.id)]))

    def test_native_missing_cash_basis_journal_fails_atomically(self):
        bill = self._fixture('tva_acq_encaissement')
        statement, source = self._statement(bill, 108)
        self.company.tax_cash_basis_journal_id = False
        original = self._line_values(bill)
        with self.assertRaises(UserError), self.env.cr.savepoint():
            bill.js_use_payment_rate_outstanding_line(source.id)
        self.assertEqual(self._line_values(bill), original)
        self.assertFalse(statement.is_reconciled)
        self.assertFalse(bill.immediate_settlement_ids)

    def test_reused_click_returns_same_settlement_and_reverse_is_idempotent(self):
        bill = self._fixture('tva_acq_encaissement')
        statement, source = self._statement(bill, 108)
        line_id = source.id
        first = bill.js_use_payment_rate_outstanding_line(line_id)
        self.assertEqual(bill.js_use_payment_rate_outstanding_line(line_id), first)
        settlement = self.env['account.immediate.settlement'].browse(first['settlement_id'])
        settlement.action_reverse()
        caba_ids = self.env['account.move'].search([('tax_cash_basis_origin_move_id', '=', bill.id)]).ids
        settlement.action_reverse()
        self.assertEqual(self.env['account.move'].search([('tax_cash_basis_origin_move_id', '=', bill.id)]).ids, caba_ids)

    def test_tax_configuration_change_cannot_be_silently_restored(self):
        bill = self._fixture()
        statement, source = self._statement(bill, 108)
        result = bill.js_use_payment_rate_outstanding_line(source.id)
        settlement = self.env['account.immediate.settlement'].browse(result['settlement_id'])
        bill.invoice_line_ids.tax_ids.amount = 21
        before = self._line_values(bill)
        with self.assertRaisesRegex(UserError, 'no longer matches'), self.env.cr.savepoint():
            settlement.action_reverse()
        self.assertEqual(self._line_values(bill), before)
        self.assertTrue(statement.is_reconciled)
        self.assertEqual(settlement.state, 'settled')

    def test_historical_snapshot_without_new_tax_keys_can_be_reversed(self):
        from odoo.addons.usl_accounting.models.immediate_settlement import _as_settlement_service
        bill = self._fixture(False)
        statement, source = self._statement(bill, 90)
        original = self._line_values(bill)
        result = bill.js_use_payment_rate_outstanding_line(source.id)
        settlement = self.env['account.immediate.settlement'].browse(result['settlement_id'])
        _as_settlement_service(settlement).write({
            field: [{k:v for k,v in line.items() if k not in ('tax_exigible','tax_configuration')}
                    for line in settlement[field]]
            for field in ['original_document_line_snapshot','repriced_document_line_snapshot']
        })
        settlement.action_reverse()
        self.assertEqual(self._line_values(bill), original)

    def test_analytic_split_tax_lines_have_truthful_aggregate_impact(self):
        tax = self.env['account.chart.template'].ref('tva_acq_normale')
        tax.analytic = True
        plan = self.env['account.analytic.plan'].create({'name':'[QA2574] Split plan'})
        accounts = self.env['account.analytic.account'].create([
            {'name':'[QA2574] Split A', 'plan_id':plan.id},
            {'name':'[QA2574] Split B', 'plan_id':plan.id},
        ])
        bill = self._fixture(prices=[40,60])
        bill.button_draft()
        for line, analytic in zip(bill.invoice_line_ids, accounts):
            line.analytic_distribution = {str(analytic.id):100}
        bill._post(soft=False)
        self.assertEqual(len(bill.line_ids.filtered('tax_line_id')), 2)
        statement, source = self._statement(bill,108)
        eligibility = bill._get_payment_rate_settlement_eligibility(source)
        self.assertTrue(eligibility['eligible'], eligibility['reason'])
        self.assertEqual(len(eligibility['tax_impacts']),1)
        impact = eligibility['tax_impacts'][0]
        self.assertEqual((impact['base_before'],impact['base_after'],impact['tax_before'],impact['tax_after']), (80,90,16,18))
        result = bill.js_use_payment_rate_outstanding_line(source.id)
        self.env['account.immediate.settlement'].browse(result['settlement_id']).action_reverse()

    def test_compound_group_order_change_rejects_automatic_restore(self):
        bill = self._fixture()
        original_tax = bill.invoice_line_ids.tax_ids
        second = original_tax.copy({'name':'[QA2574] Compound second','amount':10,'sequence':20})
        original_tax.write({'sequence':10,'include_base_amount':True})
        group = self.env['account.tax'].create({'name':'[QA2574] Compound group',
            'amount_type':'group','type_tax_use':'purchase','tax_scope':original_tax.tax_scope,'company_id':self.company.id,
            'children_tax_ids':[Command.set((original_tax|second).ids)]})
        bill.button_draft();bill.invoice_line_ids.tax_ids=group;bill._post(soft=False)
        statement,source=self._statement(bill,118.80)
        result=bill.js_use_payment_rate_outstanding_line(source.id)
        settlement=self.env['account.immediate.settlement'].browse(result['settlement_id'])
        second.sequence=5
        with self.assertRaisesRegex(UserError,'no longer matches'),self.env.cr.savepoint():
            settlement.action_reverse()
        self.assertTrue(statement.is_reconciled)
        self.assertEqual(settlement.state,'settled')

    def test_foreign_reported_payment_currency_is_not_silently_converted(self):
        bill=self._fixture()
        statement,source=self._statement(bill,108)
        statement.write({'foreign_currency_id':self.env.ref('base.GBP').id,'amount_currency':-75})
        result=bill._get_payment_rate_settlement_eligibility(source)
        self.assertFalse(result['eligible'])
        self.assertFalse(bill.immediate_settlement_ids)

    def test_managed_actors_share_native_taxed_correction_authority(self):
        from .test_immediate_settlement import TestImmediateSettlement
        for mode in ('read_write','read_only'):
            agent=TestImmediateSettlement._post_match_managed_agent(self,mode)
            bill=self._fixture('tva_acq_encaissement')
            statement,source=self._statement(bill,108)
            caller=bill.with_user(agent.user_id)
            if mode=='read_only':
                with self.assertRaises(AccessError),self.env.cr.savepoint():
                    caller.js_use_payment_rate_outstanding_line(source.id)
                self.assertFalse(statement.is_reconciled)
            else:
                self.assertFalse(caller.env.user.has_group('usl_access_control.group_irreversible_actions'))
                from odoo.addons.usl_access_control.controllers.json2 import UslAgentJson2Controller
                access=UslAgentJson2Controller._check_agent_call(agent=agent,
                    model_name='account.move',method_name='js_use_payment_rate_outstanding_line',
                    kwargs={'line_id':source.id})
                context=UslAgentJson2Controller._agent_call_context(context=caller.env.context,
                    agent=agent,model_name='account.move',
                    method_name='js_use_payment_rate_outstanding_line',access=access)
                outcome=caller.with_context(context).js_use_payment_rate_outstanding_line(source.id)
                self.assertEqual(bill.write_uid,agent.user_id)
                self.assertEqual(bill.amount_residual,0)
                # The native bank Undo delegates to the full immutable audit recovery.
                access=UslAgentJson2Controller._check_agent_call(agent=agent,
                    model_name='account.bank.statement.line',method_name='action_undo_reconciliation',kwargs={})
                context=UslAgentJson2Controller._agent_call_context(context=caller.env.context,
                    agent=agent,model_name='account.bank.statement.line',
                    method_name='action_undo_reconciliation',access=access)
                statement.with_user(agent.user_id).with_context(context).action_undo_reconciliation()
                self.assertEqual(self.env['account.immediate.settlement'].browse(outcome['settlement_id']).state,'reversed')
                self.assertEqual(bill.invoice_currency_rate,1.25)

    def test_taxed_locks_and_foreign_company_authority(self):
        bill=self._fixture()
        statement,source=self._statement(bill,108)
        self.company.sudo().tax_lock_date=self.day
        eligibility=bill._get_payment_rate_settlement_eligibility(source)
        self.assertFalse(eligibility['eligible'])
        self.assertIn('locked',eligibility['reason'])
        with self.assertRaises(UserError),self.env.cr.savepoint():
            bill.js_use_payment_rate_outstanding_line(source.id)
        self.company.sudo().tax_lock_date=False
        other=self.setup_other_company()
        outsider=new_test_user(self.env,login='qa2574.outsider',groups='account.group_account_manager',
            company_id=other['company'].id,company_ids=[Command.set(other['company'].ids)])
        with self.assertRaises(AccessError),self.env.cr.savepoint():
            bill.with_user(outsider).with_context(allowed_company_ids=other['company'].ids).js_use_payment_rate_outstanding_line(source.id)

    def test_recovery_view_exposes_action_and_keeps_raw_snapshots_in_technical_tab(self):
        from lxml import etree
        view=self.env.ref('usl_accounting.view_account_immediate_settlement_form')
        arch=etree.fromstring(self.env['account.immediate.settlement'].with_context(lang='en_US').get_view(view.id,'form')['arch'])
        self.assertEqual(arch.xpath('//header/button[@name="action_reverse"]/@string'),['Undo settlement'])
        technical=arch.xpath('//page[@name="technical_details"]')
        self.assertEqual(len(technical),1)
        self.assertFalse(technical[0].get('groups'))
        for field in ['provenance_details','original_document_line_snapshot','repriced_document_line_snapshot']:
            self.assertEqual(len(technical[0].xpath('.//field[@name="%s"]'%field)),1)
        self.assertEqual(len(arch.xpath('//field[@name="original_document_company_amount"]')),1)
