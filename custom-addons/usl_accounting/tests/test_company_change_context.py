import json
import logging
import time

from odoo import Command
from odoo.exceptions import AccessError, ValidationError
from odoo.tests import tagged
from odoo.tests.common import new_test_user
from odoo.addons.account.tests.common import AccountTestInvoicingCommon


@tagged('post_install', '-at_install', 'usl_company_change_context')
class TestCompanyChangeContext(AccountTestInvoicingCommon):
    def _bill(self, count=1):
        return self.env['account.move'].create({
            'move_type': 'in_invoice',
            'partner_id': self.partner_a.id,
            'journal_id': self.company_data['default_journal_purchase'].id,
            'invoice_line_ids': [Command.create({
                'name': '[QA] Context line %s' % i,
                'account_id': self.company_data['default_account_expense'].id,
                'quantity': 1, 'price_unit': 12, 'tax_ids': [Command.clear()],
            }) for i in range(count)],
        })

    def test_native_snapshot_contract(self):
        bill = self._bill(101)
        bill.invoice_line_ids = [Command.create({'display_type': 'line_section', 'name': '[QA] Section'})]
        self.env.flush_all()
        before = bill.read(['write_date', 'company_id', 'amount_total', 'line_ids'])
        self.env.cr.execute('SHOW transaction_isolation')
        self.assertEqual(self.env.cr.fetchone()[0], 'repeatable read')
        context = bill.get_company_change_context(bill.company_id.id, limit=100)
        self.assertEqual(context['lines']['visible_count'], len(bill.line_ids))
        self.assertEqual(context['lines']['status'], 'truncated')
        self.assertEqual(len(context['lines']['records']), 100)
        self.assertEqual(context['snapshot']['isolation'], 'repeatable_read')
        self.assertEqual(before, bill.read(['write_date', 'company_id', 'amount_total', 'line_ids']))

    def test_native_readonly_and_target_authority(self):
        bill = self._bill()
        reader = new_test_user(self.env, login='[QA] context.reader',
            groups='account.group_account_readonly', company_id=bill.company_id.id,
            company_ids=[Command.set(bill.company_id.ids)])
        scoped = bill.with_user(reader).with_context(allowed_company_ids=bill.company_id.ids)
        self.assertEqual(scoped.get_company_change_context(bill.company_id.id)['invoice']['fields']['state'], 'draft')
        with self.assertRaises(AccessError):
            scoped.write({'ref': '[QA] forbidden'})
        foreign = self.env['res.company'].create({'name': '[QA] inaccessible target'})
        with self.assertRaises(AccessError):
            scoped.get_company_change_context(foreign.id)
        with self.assertRaises(AccessError):
            scoped.with_context(allowed_company_ids=[foreign.id]).get_company_change_context(foreign.id)
        with self.assertRaises(ValidationError):
            scoped.get_company_change_context(bill.company_id.id, limit=101)

    def test_all_line_types_and_paging_remain_native(self):
        bill = self._bill(101)
        bill.invoice_line_ids = [Command.create({'display_type': 'line_section', 'name': '[QA] Section'}),
                                 Command.create({'display_type': 'line_note', 'name': '[QA] Note'})]
        self.env.flush_all()
        expected = bill.line_ids.sorted('id').ids
        actual = []
        snapshots = []
        for offset in range(0, len(expected), 50):
            context = bill.get_company_change_context(bill.company_id.id, offset=offset, limit=50)
            actual.extend(row['id'] for row in context['lines']['records'])
            snapshots.append(context['snapshot']['snapshot_id'])
            self.assertLessEqual(len(json.dumps(context, ensure_ascii=False, separators=(',', ':')).encode()), 262144)
        self.assertEqual(actual, expected)
        self.assertEqual(len(snapshots), len(set(snapshots)))
        self.assertEqual(context['lines']['status'], 'complete')
        self.assertTrue({'product', 'payment_term', 'line_section', 'line_note'} <= set(bill.line_ids.mapped('display_type')))

    def test_paid_label_and_actual_reconciliation_are_separate(self):
        bill = self._bill()
        bill.invoice_date = '2026-10-01'
        bill.action_post()
        bill.payment_state = 'paid'
        label_only = bill.get_company_change_context(bill.company_id.id)
        self.assertEqual(label_only['invoice']['fields']['payment_state'], 'paid')
        self.assertEqual(label_only['reconciliations']['visible_count'], 0)
        counterpart = self.env['account.move'].create({
            'journal_id': self.company_data['default_journal_misc'].id,
            'date': '2026-10-01', 'line_ids': [
                Command.create({'account_id': self.company_data['default_account_payable'].id,
                                'partner_id': bill.partner_id.id, 'debit': 5}),
                Command.create({'account_id': self.company_data['default_account_expense'].id, 'credit': 5}),
            ],
        })
        counterpart.action_post()
        (bill.line_ids + counterpart.line_ids).filtered(lambda l: l.account_id.account_type == 'liability_payable').reconcile()
        self.env.flush_all()
        before = self.env['account.partial.reconcile'].search_count([])
        actual = bill.get_company_change_context(bill.company_id.id)
        self.assertEqual(actual['reconciliations']['visible_count'], 1)
        self.assertEqual(actual['reconciliations']['records'][0]['fields']['amount'], 5)
        self.assertEqual(actual['counterpart_lines']['visible_count'], 1)
        self.assertEqual(self.env['account.partial.reconcile'].search_count([]), before)

    def test_native_target_choices_do_not_guess_mappings(self):
        bill = self._bill()
        other = self.setup_other_company()
        target = other['company']
        scoped = bill.with_context(allowed_company_ids=(bill.company_id | target).ids)
        context = scoped.get_company_change_context(target.id)
        journals = context['choices']['account.journal']['records']
        self.assertIn(other['default_journal_purchase'].id, [row['id'] for row in journals])
        self.assertNotIn(bill.journal_id.id, [row['id'] for row in journals])
        self.assertFalse(context['invoice']['fields']['journal_id']['target_compatible'])
        self.assertEqual(context['interpretation']['mappings'], 'unresolved_explicit_operator_choice')
        self.assertEqual(bill.company_id, self.company_data['company'])
        self.assertIn('purchase_lines', context)
        self.assertIn('document_links', context)

    def test_every_native_display_type_is_visible(self):
        types = self.env['account.move.line']._fields['display_type'].get_values(self.env)
        move = self.env['account.move'].create({
            'journal_id': self.company_data['default_journal_misc'].id,
            'line_ids': [Command.create(dict(name='[QA] '+kind, display_type=kind,
                **({} if kind in ('line_section', 'line_subsection', 'line_note') else
                   {'account_id': self.company_data['default_account_expense'].id}))) for kind in types],
        })
        result = move.get_company_change_context(move.company_id.id)
        self.assertEqual({row['fields']['display_type'] for row in result['lines']['records']}, set(types))
        self.assertEqual(result['lines']['visible_count'], len(types))

    def test_inaccessible_supplier_properties_have_no_identifiers(self):
        bill = self._bill()
        rule = self.env['ir.rule'].sudo().create({
            'name': '[QA] Hide context supplier',
            'model_id': self.env['ir.model']._get_id('res.partner'),
            'domain_force': "[('id', '!=', %s)]" % bill.partner_id.id,
        })
        context = bill.get_company_change_context(bill.company_id.id)
        for key in ('supplier_properties_source', 'supplier_properties_target'):
            self.assertEqual(context[key], {'status': 'unavailable', 'reason': 'access'})
        self.assertNotIn('partner_id', context['invoice']['fields'])
        self.assertEqual(context['invoice']['unavailable_fields']['partner_id'], 'relation_access')

    def test_dense_context_respects_the_exact_response_budget(self):
        account = self.company_data['default_account_expense']
        taxes = self.env['account.tax'].create([{
            'name': '[QA] ' + ('X' * 500) + str(i), 'amount': 1, 'type_tax_use': 'purchase',
            'company_id': self.env.company.id,
        } for i in range(6)])
        move = self.env['account.move'].create({
            'journal_id': self.company_data['default_journal_misc'].id,
            'line_ids': [Command.create({'name': '[QA] ' + ('L' * 1100),
                'account_id': account.id, 'tax_ids': [Command.set(taxes.ids)]}) for i in range(100)],
        })
        self.env.flush_all()
        query_start, started = self.env.cr.sql_log_count, time.perf_counter()
        result = move.get_company_change_context(move.company_id.id, limit=100)
        logging.getLogger(__name__).info('Dense 100-line native context: %.3fs, %s SQL statements',
            time.perf_counter() - started, self.env.cr.sql_log_count - query_start)
        self.assertNotIn('next_offset', result['documents'])
        self.assertNotIn('next_offset', result['line_analytic_accounts'])
        self.assertEqual(result['budget']['status'], 'truncated')
        self.assertLessEqual(len(json.dumps(result, ensure_ascii=False, separators=(',', ':')).encode()), 262144)
        self.assertEqual(result['lines']['next_offset'], len(result['lines']['records']))
        self.assertEqual(result['lines']['visible_count'], 100)
        self.assertEqual(result['lines']['status'], 'truncated')
        self.assertLess(len(result['lines']['records']), 100)

    def test_native_credit_note_and_former_posting_context(self):
        bill = self._bill()
        bill.invoice_date = '2026-10-01'
        bill.action_post()
        credit = bill._reverse_moves(default_values_list=[{'invoice_date': '2026-10-02'}])
        before = bill.read(['write_date', 'company_id', 'amount_total', 'line_ids', 'state'])
        result = credit.get_company_change_context(credit.company_id.id)
        self.assertEqual(result['invoice']['fields']['reversed_entry_id']['id'], bill.id)
        self.assertEqual(result['credit_note_origins']['visible_count'], 1)
        self.assertEqual(before, bill.read(['write_date', 'company_id', 'amount_total', 'line_ids', 'state']))
        bill.button_draft()
        result = bill.get_company_change_context(bill.company_id.id)
        self.assertEqual(result['invoice']['fields']['state'], 'draft')
        self.assertTrue(result['invoice']['fields']['posted_before'])

    def test_optional_purchase_origin_preserves_source_order(self):
        bill = self._bill()
        if 'purchase.order' not in self.env:
            self.assertEqual(bill.get_company_change_context(bill.company_id.id)['purchase_lines']['reason'], 'not_installed')
            return
        order = self.env['purchase.order'].create({'partner_id': bill.partner_id.id, 'company_id': bill.company_id.id,
            'order_line': [Command.create({'product_id': self.product_a.id, 'product_qty': 1, 'price_unit': 12})]})
        bill.invoice_line_ids[0].purchase_line_id = order.order_line
        before = order.read(['write_date', 'company_id', 'state', 'order_line'])
        result = bill.get_company_change_context(bill.company_id.id)
        self.assertEqual(result['purchase_lines']['records'][0]['id'], order.order_line.id)
        self.assertEqual(result['purchase_orders']['records'][0]['id'], order.id)
        self.assertEqual(result['purchase_orders']['records'][0]['fields']['company_id']['id'], bill.company_id.id)
        self.assertEqual(before, order.read(['write_date', 'company_id', 'state', 'order_line']))

    def test_native_parent_branch_candidates_and_currency_facts(self):
        bill = self._bill()
        target = self.setup_other_company(currency_id=self.env.ref('base.USD').id)
        branch = self.env['res.company'].create({'name': '[QA] Context branch', 'parent_id': target['company'].id})
        result = bill.with_context(allowed_company_ids=(bill.company_id | target['company'] | branch).ids).get_company_change_context(branch.id)
        self.assertEqual(result['target_company']['fields']['parent_id']['id'], target['company'].id)
        self.assertEqual(result['target_company']['fields']['currency_id']['id'], target['company'].currency_id.id)
        self.assertEqual(result['invoice']['fields']['currency_id']['id'], bill.currency_id.id)
        self.assertEqual(result['currency_context']['target_rate'], self.env['res.currency']._get_conversion_rate(
            target['company'].currency_id, bill.currency_id, branch, bill._get_invoice_currency_rate_date()))
        self.assertIn(target['default_journal_purchase'].id, [row['id'] for row in result['choices']['account.journal']['records']])

    def test_agent_context_keeps_the_current_owners_company_boundary(self):
        if 'usl.agent' not in self.env:
            self.skipTest('Delegated identities are qualified on the full Distribution registry')
        bill = self._bill()
        other = self.setup_other_company()['company']
        companies = bill.company_id | other
        owner = new_test_user(self.env, login='qa.context.current.owner', groups='account.group_account_manager',
            company_id=bill.company_id.id, company_ids=[Command.set(companies.ids)])
        agent = self.env['usl.agent'].with_user(owner).create({
            'name': '[QA] Context current owner', 'purpose': 'Read-only context boundary qualification',
            'owner_id': owner.id, 'company_id': bill.company_id.id,
            'company_ids': [Command.set(companies.ids)],
            'delegated_group_ids': [Command.set(self.env.ref('account.group_account_user').ids)],
            'access_mode': 'read_only',
        })
        scoped = bill.with_user(agent.user_id).with_context(allowed_company_ids=companies.ids)
        self.assertEqual(scoped.get_company_change_context(other.id)['target_company']['id'], other.id)
        owner.sudo().write({'company_ids': [Command.set(bill.company_id.ids)]})
        with self.assertRaises(AccessError):
            scoped.get_company_change_context(other.id)

    def test_outbound_credit_note_keeps_native_sales_tax_direction(self):
        credit = self.env['account.move'].create({
            'move_type': 'out_refund', 'partner_id': self.partner_a.id,
            'journal_id': self.company_data['default_journal_sale'].id,
            'invoice_line_ids': [Command.create({'name': '[QA] Sales credit',
                'account_id': self.company_data['default_account_revenue'].id,
                'quantity': 1, 'price_unit': 12, 'tax_ids': [Command.clear()]})],
        })
        result = credit.get_company_change_context(credit.company_id.id)
        self.assertTrue(result['choices']['account.tax']['records'])
        self.assertEqual({row['fields']['type_tax_use'] for row in result['choices']['account.tax']['records']}, {'sale'})
        self.assertEqual(result['invoice']['fields']['move_type'], 'out_refund')

    def test_readable_archived_analytics_are_retained(self):
        bill = self._bill()
        plan = self.env['account.analytic.plan'].sudo().create({'name': '[QA] Context analytics'})
        analytic = self.env['account.analytic.account'].sudo().create({
            'name': '[QA] Archived analytic', 'plan_id': plan.id,
            'company_id': bill.company_id.id, 'active': False,
        })
        bill.invoice_line_ids[0].analytic_distribution = {str(analytic.id): 100}
        result = bill.get_company_change_context(bill.company_id.id)
        product = next(row for row in result['lines']['records'] if row['id'] == bill.invoice_line_ids[0].id)
        self.assertEqual(product['fields']['analytic_distribution'], {str(analytic.id): 100})
        self.assertIn(analytic.id, [row['id'] for row in result['line_analytic_accounts']['records']])
