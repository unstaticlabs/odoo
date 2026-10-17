"""Invented posted-ledger cases for balance-sheet fiscal result presentation."""

import hashlib
import json
from decimal import Decimal

from odoo import Command
from odoo.tests import TransactionCase, tagged


@tagged('post_install', '-at_install', 'fiscal_result_presentation')
class TestFiscalResultPresentation(TransactionCase):
    def _fixture(self, name='Synthetic fiscal statement'):
        company = self.env['res.company'].create({
            'name': name, 'currency_id': self.env.ref('base.EUR').id,
            'fiscalyear_last_day': 30, 'fiscalyear_last_month': '9',
        })
        own = self.env(context={**self.env.context, 'allowed_company_ids': company.ids})
        accounts = {}
        for key, code, kind in [
            ('cash', '512900', 'asset_cash'),
            ('income', '706900', 'income'),
            ('reserve', '106900', 'equity'),
            ('result', '120900', 'equity'),
            ('appropriation', '129900', 'equity_unaffected'),
            ('appropriation2', '129901', 'equity_unaffected'),
            ('dividend', '457900', 'liability_current'),
        ]:
            accounts[key] = own['account.account'].with_company(company).create({
                'name': 'Synthetic ' + key, 'code': code, 'account_type': kind,
                'company_ids': [Command.set(company.ids)],
            })
        journal = own['account.journal'].with_company(company).create({
            'name': 'Synthetic fiscal ledger', 'code': 'FYQA', 'type': 'general',
            'company_id': company.id,
        })
        return company, own, accounts, journal

    def _post(self, fixture, when, balances):
        company, own, accounts, journal = fixture
        move = own['account.move'].with_company(company).create({
            'date': when, 'journal_id': journal.id,
            'line_ids': [Command.create({
                'name': 'Synthetic fiscal evidence', 'account_id': accounts[key].id,
                'debit': max(amount, 0), 'credit': max(-amount, 0),
            }) for key, amount in balances.items() if amount],
        })
        move.action_post()
        return move

    def _wizard(self, fixture, report='balance_sheet', **values):
        company, own, _accounts, _journal = fixture
        return own['rebuild.account.report.export.wizard'].with_company(company).create({
            'company_id': company.id, 'company_ids': [Command.set(company.ids)],
            'report_type': report, 'date_from': '2030-10-01', 'date_to': '2031-09-30',
            'target_move': 'posted', 'group_by': 'none', **values,
        })

    def _ledger_fingerprint(self, fixture):
        company, own, _accounts, _journal = fixture
        values = own['account.move.line'].search([
            ('company_id', '=', company.id), ('move_id.state', '=', 'posted'),
        ], order='id').read(['account_id', 'move_id', 'date', 'debit', 'credit',
                            'balance', 'amount_currency', 'currency_id',
                            'tax_ids', 'analytic_distribution', 'full_reconcile_id'])
        return hashlib.sha256(json.dumps(values, sort_keys=True, default=str).encode()).hexdigest()

    def test_allocated_prior_result_is_not_current_fiscal_profit(self):
        fixture = self._fixture()
        self._post(fixture, '2030-09-20', {'cash': 100, 'income': -100})
        self._post(fixture, '2031-02-20', {'cash': 30, 'income': -30})
        self._post(fixture, '2031-03-20', {
            'appropriation': 100, 'reserve': -60, 'dividend': -40,
        })
        before = self._ledger_fingerprint(fixture)
        for report, current_code, value_key in [
            ('balance_sheet', 'RESULT', 'amount'),
            ('french_balance_sheet_2024', 'PASSIF_RESULTAT', 'amount'),
        ]:
            with self.subTest(report=report):
                rows = self._wizard(fixture, report)._report_rows()
                current = next(row for row in rows if (
                    row.get('line_code') or row.get('account_code')) == current_code)
                self.assertEqual(Decimal(current[value_key]), Decimal('30.00'))
                totals = {row['line_code']: Decimal(row[value_key]) for row in rows
                          if row.get('line_code') in {'ACTIF_TOTAL', 'PASSIF_TOTAL'}}
                self.assertEqual(totals['ACTIF_TOTAL'], Decimal('130.00'))
                self.assertEqual(totals['PASSIF_TOTAL'], Decimal('130.00'))
        self.assertEqual(self._ledger_fingerprint(fixture), before)

    def test_posted_result_and_appropriation_classes(self):
        # Ordinary posted input classes; no entry is classified by its reference.
        cases = [
            ('current profit', 0, 30, {}, 30, 0),
            ('unallocated prior profit', 100, 30, {}, 30, 100),
            ('prior loss', -10, 30, {}, 30, -10),
            ('fully allocated prior profit', 100, 30,
             {'appropriation': 100, 'reserve': -60, 'dividend': -40}, 30, 0),
            ('partial allocation', 100, 30,
             {'appropriation': 40, 'reserve': -40}, 30, 60),
            ('current appropriation', 0, 30,
             {'appropriation': 30, 'reserve': -30}, 30, -30),
            ('posted reset into ordinary equity', 0, 30,
             {'income': 30, 'result': -30}, 0, 0),
            ('posted reset into appropriation', 0, 30,
             {'income': 30, 'appropriation': -30}, 0, 30),
            ('manual appropriation adjustment', 0, 30,
             {'cash': 20, 'appropriation': -20}, 30, 20),
            ('multiple appropriation accounts', 100, 30,
             {'appropriation': 60, 'appropriation2': 40, 'reserve': -100}, 30, 0),
        ]
        for label, prior, current, adjustment, expected_current, expected_report in cases:
            with self.subTest(input_class=label):
                fixture = self._fixture('Synthetic ' + label)
                if prior:
                    self._post(fixture, '2030-09-20', {'cash': prior, 'income': -prior})
                if current:
                    self._post(fixture, '2031-02-20', {'cash': current, 'income': -current})
                if adjustment:
                    self._post(fixture, '2031-03-20', adjustment)
                before = self._ledger_fingerprint(fixture)
                expected_assets = prior + current + adjustment.get('cash', 0)
                for report, current_code, report_code in [
                    ('balance_sheet', 'RESULT', 'RESULT_CARRYFORWARD'),
                    ('french_balance_sheet_2024', 'PASSIF_RESULTAT', 'PASSIF_REPORT_AFFECTATIONS'),
                ]:
                    rows = self._wizard(fixture, report)._report_rows()
                    by_code = {(row.get('line_code') or row.get('account_code')): row
                               for row in rows if row.get('presentation_role') not in {'group', 'section'}}
                    self.assertEqual(Decimal(by_code[current_code]['amount']), Decimal(expected_current))
                    report_value = Decimal(by_code.get(report_code, {}).get('amount', '0'))
                    self.assertEqual(report_value, Decimal(expected_report))
                    self.assertEqual(Decimal(by_code['ACTIF_TOTAL']['amount']), Decimal(expected_assets))
                    self.assertEqual(Decimal(by_code['PASSIF_TOTAL']['amount']), Decimal(expected_assets))
                self.assertEqual(self._ledger_fingerprint(fixture), before)

    def test_partial_period_result_uses_company_fiscal_year(self):
        fixture = self._fixture()
        self._post(fixture, '2030-09-20', {'cash': 100, 'income': -100})
        self._post(fixture, '2031-02-20', {'cash': 30, 'income': -30})
        self._post(fixture, '2031-10-02', {'cash': 70, 'income': -70})
        wizard = self._wizard(fixture, date_from='2031-03-01', date_to='2031-03-31')
        rows = wizard._report_rows()
        current = next(row for row in rows if row.get('account_code') == 'RESULT')
        self.assertEqual(Decimal(current['amount']), Decimal('30'))

    def test_source_domains_preserve_fiscal_and_historical_evidence(self):
        fixture = self._fixture()
        old = self._post(fixture, '2030-09-20', {'cash': 100, 'income': -100})
        current = self._post(fixture, '2031-02-20', {'cash': 30, 'income': -30})
        allocation = self._post(fixture, '2031-03-20', {'appropriation': 40, 'reserve': -40})
        future = self._post(fixture, '2031-10-02', {'cash': 70, 'income': -70})
        company, own, accounts, _journal = fixture
        before = self._ledger_fingerprint(fixture)
        for report in ('balance_sheet', 'french_balance_sheet_2024'):
            with self.subTest(report=report):
                wizard = self._wizard(fixture, report, date_from='2031-03-01', date_to='2031-03-31')
                rows = wizard._report_rows()
                for kind, expected_ids, expected_signed in [
                    ('current', current.line_ids.filtered(lambda line: line.account_id == accounts['income']).ids, -30),
                    ('carryforward', (old.line_ids.filtered(lambda line: line.account_id == accounts['income'])
                                     | allocation.line_ids.filtered(lambda line: line.account_id == accounts['appropriation'])).ids, -60),
                ]:
                    row = next(item for item in rows if item.get('fiscal_result_kind') == kind)
                    domain = wizard._preview_journal_item_domain(row)
                    actual = own['account.move.line'].search(domain)
                    self.assertEqual(set(actual.ids), set(expected_ids))
                    self.assertEqual(sum(actual.mapped('balance')), expected_signed)
                    self.assertFalse(actual & future.line_ids)
                wizard.action_preview_report()
                payload = wizard._report_client_payload()
                descriptive = [line for line in payload['lines'] if line.get('source_description')]
                self.assertTrue(descriptive)
                self.assertTrue(all('affectation' in line['source_description'] for line in descriptive))
        self.assertEqual(self._ledger_fingerprint(fixture), before)

    def test_native_filters_can_show_an_unbalanced_subset(self):
        fixture = self._fixture()
        self._post(fixture, '2031-02-20', {'cash': 30, 'income': -30})
        _company, own, accounts, _journal = fixture
        wizard = self._wizard(fixture, account_ids=[Command.set(accounts['income'].ids)])
        rows = wizard._report_rows()
        totals = {row['line_code']: Decimal(row['amount']) for row in rows
                  if row.get('line_code') in {'ACTIF_TOTAL', 'PASSIF_TOTAL'}}
        self.assertEqual(totals, {'ACTIF_TOTAL': Decimal(0), 'PASSIF_TOTAL': Decimal(30)})
        result = next(row for row in rows if row.get('fiscal_result_kind') == 'current')
        lines = own['account.move.line'].search(wizard._preview_journal_item_domain(result))
        self.assertEqual(lines.account_id, accounts['income'])
        self.assertEqual(sum(lines.mapped('balance')), -30)

    def test_native_analytic_filter_matches_fiscal_source_entries(self):
        fixture = self._fixture()
        company, own, _accounts, _journal = fixture
        plan = own['account.analytic.plan'].create({'name': 'Synthetic fiscal plan'})
        analytic = own['account.analytic.account'].create({
            'name': 'Synthetic fiscal dimension', 'plan_id': plan.id, 'company_id': company.id,
        })
        selected = self._post(fixture, '2031-02-20', {'cash': 30, 'income': -30})
        selected.line_ids.write({'analytic_distribution': {str(analytic.id): 100}})
        self._post(fixture, '2031-02-21', {'cash': 70, 'income': -70})
        wizard = self._wizard(fixture, analytic_account_ids=[Command.set(analytic.ids)])
        rows = wizard._report_rows()
        current = next(row for row in rows if row.get('fiscal_result_kind') == 'current')
        self.assertEqual(Decimal(current['amount']), Decimal(30))
        sources = own['account.move.line'].search(wizard._preview_journal_item_domain(current))
        self.assertEqual(set(sources.move_id.ids), set(selected.ids))
        self.assertEqual(sum(sources.mapped('balance')), -30)

    def test_exceptional_first_fiscal_year_uses_native_company_dates(self):
        fixture = self._fixture()
        company = fixture[0]
        company.write({'rebuild_first_fiscalyear_start': '2029-02-10',
                       'rebuild_first_fiscalyear_end': '2030-09-30'})
        self._post(fixture, '2029-03-20', {'cash': 40, 'income': -40})
        self._post(fixture, '2030-02-20', {'cash': 30, 'income': -30})
        wizard = self._wizard(fixture, date_from='2030-03-01', date_to='2030-03-31')
        current = next(row for row in wizard._report_rows() if row.get('fiscal_result_kind') == 'current')
        self.assertEqual(Decimal(current['amount']), Decimal(70))
        self.assertEqual(current['source_fiscal_date_from'], '2029-02-10')
        self.assertEqual(current['source_fiscal_date_to'], '2030-03-31')

    def test_human_and_delegated_agent_read_same_native_fiscal_report(self):
        if 'usl.agent' not in self.env:
            self.skipTest('Agent parity requires the full distribution registry; canonical qualification runs it.')
        from odoo.addons.usl_access_control.controllers.json2 import UslAgentJson2Controller
        fixture = self._fixture()
        company, own, _accounts, _journal = fixture
        self._post(fixture, '2030-09-20', {'cash': 100, 'income': -100})
        self._post(fixture, '2031-02-20', {'cash': 30, 'income': -30})
        before = self._ledger_fingerprint(fixture)
        group = self.env.ref('account.group_account_readonly')
        owner = own['res.users'].with_context(no_reset_password=True,
                    usl_governed_identity_provisioning=True).create({
            'name': 'Synthetic fiscal reviewer', 'login': 'synthetic.fiscal.reviewer',
            'email': 'synthetic.fiscal.reviewer@example.test', 'company_id': company.id,
            'company_ids': [Command.set(company.ids)],
            'group_ids': [Command.set((self.env.ref('base.group_user') | group).ids)],
            'usl_identity_classification': 'active',
        })
        agent = own['usl.agent'].with_user(owner).create({
            'name': 'Synthetic fiscal collaborator', 'purpose': 'Read fiscal statements with the owner.',
            'owner_id': owner.id, 'company_id': company.id, 'company_ids': [Command.set(company.ids)],
            'delegated_group_ids': [Command.set(group.ids)], 'access_mode': 'read_write',
        })
        Model = own['rebuild.account.report.export.wizard']
        scope = UslAgentJson2Controller._agent_call_context(
            context={'allowed_company_ids': company.ids}, agent=agent, model_name=Model._name,
            method_name='report_client_load', access='write',
        )
        filters = {'company_id': company.id, 'company_ids': company.ids,
                   'date_from': '2030-10-01', 'date_to': '2031-09-30',
                   'period_preset': 'custom', 'group_by': 'none'}
        for actor, context in [(owner, {'allowed_company_ids': company.ids}), (agent.user_id, scope)]:
            with self.subTest(actor=actor.usl_is_ai_agent):
                payload = Model.with_user(actor).with_context(context).report_client_load('balance_sheet', filters=filters)
                result = next(line for line in payload['lines'] if line['account_code'] == 'RESULT')
                self.assertEqual(Decimal(str(result['values']['amount'])), Decimal(30))
                wizard = Model.with_user(actor).with_context(context).browse(payload['wizard_id'])
                preview = wizard.preview_line_ids.filtered(lambda line: line.account_code == 'RESULT')[:1]
                sources = wizard._preview_source_action(preview)
                actual = own['account.move.line'].with_user(actor).search(sources['domain'])
                self.assertEqual(sum(actual.mapped('balance')), -30)
        self.assertEqual(self._ledger_fingerprint(fixture), before)

    def test_xlsx_audit_and_pdf_payload_retain_fiscal_partition(self):
        import io
        import zipfile
        from unittest.mock import patch
        fixture = self._fixture()
        self._post(fixture, '2030-09-20', {'cash': 100, 'income': -100})
        self._post(fixture, '2031-02-20', {'cash': 30, 'income': -30})
        before = self._ledger_fingerprint(fixture)
        for report in ('balance_sheet', 'french_balance_sheet_2024'):
            wizard = self._wizard(fixture, report)
            rows = wizard._report_rows()
            xlsx = wizard._xlsx_payload(rows)
            with zipfile.ZipFile(io.BytesIO(xlsx)) as archive:
                strings = archive.read('xl/sharedStrings.xml').decode()
                self.assertIn('source_fiscal_prior_result', strings)
                self.assertIn('source_fiscal_current_appropriation', strings)
                self.assertIn('Report et affectations du résultat', strings)
                self.assertIn('2030-10-01', strings)
            renderer = wizard.env['usl.document.renderer']
            with (
                patch.object(type(renderer), 'render', return_value={'pdf': b'%PDF synthetic payload only'}) as render,
                patch.object(type(wizard.company_id), '_usl_document_renderer_company_payload',
                             return_value=({'name': 'Synthetic statement company',
                                            'legal_identity_lines': ['Synthetic QA only'],
                                            'primary_color': '714B67', 'footer_label': 'Synthetic QA'}, [])),
            ):
                wizard._pdf_payload(rows)
            payload = render.call_args.args[2]
            serialized = json.dumps(payload, ensure_ascii=False)
            self.assertIn('Report et affectations du résultat', serialized)
            self.assertIn('2030-10-01', serialized)
            self.assertEqual([control['status'] for control in payload['controls']],
                             ['neutral', 'neutral', 'success'])
            self.assertEqual(payload['controls'][-1]['label'], 'Écart actif − passif')
        self.assertEqual(self._ledger_fingerprint(fixture), before)

    def test_combined_company_result_resolves_each_fiscal_year(self):
        first = self._fixture('Synthetic fiscal September')
        second = self._fixture('Synthetic fiscal December')
        second[0].write({'fiscalyear_last_day': 31, 'fiscalyear_last_month': '12'})
        self._post(first, '2030-11-20', {'cash': 40, 'income': -40})
        self._post(first, '2031-02-20', {'cash': 30, 'income': -30})
        self._post(second, '2030-11-20', {'cash': 80, 'income': -80})
        self._post(second, '2031-02-20', {'cash': 20, 'income': -20})
        companies = first[0] | second[0]
        wizard = self._wizard(first).with_context(allowed_company_ids=companies.ids)
        wizard.company_ids = [Command.set(companies.ids)]
        rows = wizard._report_rows()
        current = next(row for row in rows if row.get('fiscal_result_kind') == 'current')
        self.assertEqual(Decimal(current['amount']), Decimal(90))
        periods = {entry['report_company_id']: entry['source_fiscal_date_from']
                   for entry in current['source_fiscal_company_periods']}
        self.assertEqual(periods, {first[0].id: '2030-10-01', second[0].id: '2031-01-01'})
        self.assertIn('périodes propres', current['account_name'])
        actual = wizard.env['account.move.line'].search(wizard._preview_journal_item_domain(current))
        self.assertEqual(sum(actual.mapped('balance')), -90)

    def test_comparison_only_appropriation_sources_keep_comparator_dates(self):
        fixture = self._fixture()
        old = self._post(fixture, '2030-09-20', {'cash': 100, 'income': -100})
        self._post(fixture, '2031-02-20', {'cash': 30, 'income': -30})
        self._post(fixture, '2032-02-20', {'cash': 20, 'income': -20})
        self._post(fixture, '2032-03-20', {'appropriation': 130, 'reserve': -130})
        wizard = self._wizard(fixture, date_from='2031-10-01', date_to='2032-09-30',
                              comparison_mode='custom', comparison_date_from='2030-10-01',
                              comparison_date_to='2031-09-30')
        rows = wizard._report_rows()
        current = next(row for row in rows if row.get('fiscal_result_kind') == 'current')
        self.assertEqual(Decimal(current['amount']), Decimal(20))
        self.assertEqual(Decimal(current['comparison_value']), Decimal(30))
        carried = next(row for row in rows if row.get('comparison_only') == 'true'
                       and row.get('fiscal_result_kind') == 'carryforward')
        self.assertEqual(Decimal(carried['amount']), Decimal(0))
        self.assertEqual(Decimal(carried['comparison_value']), Decimal(100))
        sources = wizard.env['account.move.line'].search(wizard._preview_journal_item_domain(carried))
        self.assertEqual(set(sources.ids), set(old.line_ids.filtered(
            lambda line: line.account_id == fixture[2]['income']).ids))
        self.assertEqual(sum(sources.mapped('balance')), -100)

    def test_combined_partial_period_pdf_keeps_each_company_fiscal_scope(self):
        from unittest.mock import patch
        first = self._fixture('Synthetic fiscal September PDF')
        second = self._fixture('Synthetic fiscal December PDF')
        second[0].write({'fiscalyear_last_day': 31, 'fiscalyear_last_month': '12'})
        self._post(first, '2030-11-20', {'cash': 40, 'income': -40})
        self._post(first, '2031-02-20', {'cash': 30, 'income': -30})
        self._post(second, '2030-11-20', {'cash': 80, 'income': -80})
        self._post(second, '2031-02-20', {'cash': 20, 'income': -20})
        companies = first[0] | second[0]
        for report in ('balance_sheet', 'french_balance_sheet_2024'):
            with self.subTest(report=report):
                wizard = self._wizard(first, report, date_from='2031-03-01', date_to='2031-03-31')
                wizard = wizard.with_context(allowed_company_ids=companies.ids)
                wizard.company_ids = [Command.set(companies.ids)]
                rows = wizard._report_rows()
                current = next(row for row in rows if row.get('fiscal_result_kind') == 'current')
                renderer = wizard.env['usl.document.renderer']
                with (
                    patch.object(type(renderer), 'render', return_value={'pdf': b'%PDF payload probe'}) as render,
                    patch.object(type(wizard.company_id), '_usl_document_renderer_company_payload',
                                 return_value=({'name': 'Synthetic PDF QA', 'legal_identity_lines': ['Synthetic only'],
                                                'primary_color': '714B67', 'footer_label': 'Synthetic QA'}, [])),
                ):
                    wizard._pdf_payload(rows)
                payload = render.call_args.args[2]
                current_labels = [row['values']['label'] for section in payload['sections']
                                  for row in section['rows']
                                  if 'Résultat de l’exercice' in row['values']['label']]
                self.assertEqual(len(current_labels), 1)
                for required in (first[0].name, second[0].name, '2030-10-01', '2031-01-01', '2031-03-31'):
                    self.assertIn(required, current_labels[0])
                    self.assertIn(required, current['source_formula'])
                self.assertEqual(Decimal(current['amount']), Decimal(90))

    def test_fully_offset_history_keeps_exact_technical_account_sources(self):
        fixture = self._fixture()
        prior = self._post(fixture, '2030-09-20', {'cash': 100, 'income': -100})
        current = self._post(fixture, '2031-02-20', {'cash': 30, 'income': -30})
        allocation = self._post(fixture, '2031-03-20', {'appropriation': 100, 'reserve': -100})
        before = self._ledger_fingerprint(fixture)
        company, own, accounts, _journal = fixture
        wizard = self._wizard(fixture)
        rows = wizard._report_rows()
        self.assertFalse(any(row.get('fiscal_result_kind') == 'carryforward' for row in rows))
        result = next(row for row in rows if row.get('fiscal_result_kind') == 'current')
        self.assertEqual(Decimal(result['amount']), Decimal(30))
        self.assertIn('source_fiscal_account_contributions', result)
        evidence = json.loads(result['source_fiscal_account_contributions'])
        by_account = {item['account_id']: item for item in evidence}
        self.assertEqual(by_account[accounts['income'].id]['opening_balance'], '-100.00')
        self.assertEqual(by_account[accounts['income'].id]['current_movement'], '-30.00')
        self.assertEqual(by_account[accounts['appropriation'].id]['current_movement'], '100.00')
        self.assertTrue(result['source_fiscal_contributions_complete'])
        scope = json.loads(result['source_fiscal_scope'])
        self.assertEqual(scope['context']['allowed_company_ids'], company.ids)
        native_sources = own['account.move.line'].search(scope['domains']['appropriation_current'])
        self.assertEqual(set(native_sources.ids), set(allocation.line_ids.filtered(
            lambda line: line.account_id == accounts['appropriation']).ids))
        prior_sources = own['account.move.line'].search(scope['domains']['prior_result'])
        self.assertEqual(set(prior_sources.ids), set(prior.line_ids.filtered(
            lambda line: line.account_id == accounts['income']).ids))
        visible_sources = own['account.move.line'].search(wizard._preview_journal_item_domain(result))
        self.assertEqual(set(visible_sources.ids), set(current.line_ids.filtered(
            lambda line: line.account_id == accounts['income']).ids))
        import io
        import zipfile
        with zipfile.ZipFile(io.BytesIO(wizard._xlsx_payload(rows))) as archive:
            strings = archive.read('xl/sharedStrings.xml').decode()
            self.assertIn('source_fiscal_account_contributions', strings)
            self.assertIn('source_fiscal_scope', strings)
        self.assertEqual(self._ledger_fingerprint(fixture), before)

    def test_combined_french_appropriation_stays_in_statement_before_equity_total(self):
        first = self._fixture('Synthetic zero historical net')
        second = self._fixture('Synthetic surviving historical net')
        second[0].write({'fiscalyear_last_day': 31, 'fiscalyear_last_month': '12'})
        first_prior = self._post(first, '2030-09-20', {'cash': 100, 'income': -100})
        self._post(first, '2031-02-20', {'cash': 30, 'income': -30})
        allocated = self._post(first, '2031-03-20', {'appropriation': 100, 'reserve': -60, 'dividend': -40})
        second_prior = self._post(second, '2030-11-20', {'cash': 80, 'income': -80})
        self._post(second, '2031-02-20', {'cash': 20, 'income': -20})
        companies = first[0] | second[0]
        wizard = self._wizard(first, 'french_balance_sheet_2024',
                              date_from='2031-03-01', date_to='2031-03-31', group_by='section')
        wizard = wizard.with_context(allowed_company_ids=companies.ids)
        wizard.company_ids = [Command.set(companies.ids)]
        before = [self._ledger_fingerprint(fixture) for fixture in (first, second)]
        rows = wizard._report_rows()
        carried = next(row for row in rows if row.get('fiscal_result_kind') == 'carryforward')
        current = next(row for row in rows if row.get('fiscal_result_kind') == 'current')
        subtotal = next(row for row in rows if row.get('line_code') == 'PASSIF_CAPITAUX_PROPRES')
        total = next(row for row in rows if row.get('line_code') == 'PASSIF_TOTAL')
        self.assertEqual(carried['parent_group_key'], current['parent_group_key'])
        self.assertEqual(carried['report_company_ids'], companies.ids)
        self.assertLess(rows.index(carried), rows.index(subtotal))
        self.assertLess(rows.index(subtotal), rows.index(total))
        self.assertEqual((Decimal(current['amount']), Decimal(carried['amount']),
                          Decimal(subtotal['amount']), Decimal(total['amount'])),
                         (Decimal(50), Decimal(80), Decimal(190), Decimal(230)))
        sources = wizard.env['account.move.line'].search(wizard._preview_journal_item_domain(carried))
        expected = (first_prior.line_ids.filtered(lambda line: line.account_id == first[2]['income'])
                    | allocated.line_ids.filtered(lambda line: line.account_id == first[2]['appropriation'])
                    | second_prior.line_ids.filtered(lambda line: line.account_id == second[2]['income']))
        self.assertEqual(set(sources.ids), set(expected.ids))
        self.assertEqual(sum(sources.mapped('balance')), -80)
        self.assertEqual([self._ledger_fingerprint(fixture) for fixture in (first, second)], before)

    def test_hidden_zero_results_keep_offsetting_history_in_actual_xlsx(self):
        import io
        import zipfile
        from xml.etree import ElementTree

        first = self._fixture('Synthetic fully allocated September')
        second = self._fixture('Synthetic fully allocated December')
        second[0].write({'fiscalyear_last_day': 31, 'fiscalyear_last_month': '12'})
        for fixture in (first, second):
            self._post(fixture, '2030-09-20', {'cash': 100, 'income': -100})
            self._post(fixture, '2031-03-20', {'appropriation': 100, 'reserve': -100})
        fingerprints = [self._ledger_fingerprint(fixture) for fixture in (first, second)]
        ns = {'m': 'http://schemas.openxmlformats.org/spreadsheetml/2006/main'}
        for report in ('balance_sheet', 'french_balance_sheet_2024'):
            for companies in (first[0], first[0] | second[0]):
                with self.subTest(report=report, companies=len(companies)):
                    wizard = self._wizard(first, report, hide_zero_accounts=True)
                    wizard = wizard.with_context(allowed_company_ids=companies.ids)
                    wizard.company_ids = [Command.set(companies.ids)]
                    rows = wizard._report_rows()
                    self.assertFalse(any(row.get('fiscal_result_kind') for row in rows))
                    total = next(row for row in rows if row.get('line_code') == 'PASSIF_TOTAL')
                    self.assertEqual(Decimal(total['amount']), Decimal(100 * len(companies)))
                    native_domain = wizard._preview_journal_item_domain(total)
                    self.assertTrue(native_domain)
                    with zipfile.ZipFile(io.BytesIO(wizard._xlsx_payload(rows))) as archive:
                        strings = ElementTree.fromstring(archive.read('xl/sharedStrings.xml'))
                        shared = [''.join(item.itertext()) for item in strings]
                        sheet = ElementTree.fromstring(archive.read('xl/worksheets/sheet3.xml'))
                        cells = []
                        for xmlrow in sheet.findall('m:sheetData/m:row', ns):
                            values = {}
                            for cell in xmlrow.findall('m:c', ns):
                                value = cell.find('m:v', ns)
                                if value is not None:
                                    values[''.join(c for c in cell.attrib['r'] if c.isalpha())] = (
                                        shared[int(value.text)] if cell.attrib.get('t') == 's' else value.text)
                            cells.append(values)
                    headers = cells[0]
                    self.assertIn('source_fiscal_scope', headers.values())
                    records = [{headers[key]: value for key, value in row.items()} for row in cells[1:]]
                    audit = next(row for row in records if row.get('line_code') == 'PASSIF_TOTAL')
                    self.assertEqual(Decimal(audit['source_fiscal_prior_result']), Decimal(100 * len(companies)))
                    self.assertEqual(Decimal(audit['source_fiscal_current_appropriation']), Decimal(-100 * len(companies)))
                    contributions = json.loads(audit['source_fiscal_account_contributions'])
                    self.assertEqual({row['company_id'] for row in contributions}, set(companies.ids))
                    expected_accounts = {fixture[2][key].id for fixture in (first, second)
                                         if fixture[0] in companies for key in ('income', 'appropriation')}
                    self.assertEqual({row['account_id'] for row in contributions}, expected_accounts)
                    scope = json.loads(audit['source_fiscal_scope'])
                    scopes = scope if isinstance(scope, list) else [scope]
                    self.assertEqual({item['context']['allowed_company_ids'][0] for item in scopes}, set(companies.ids))
                    for item in scopes:
                        native = wizard.env['account.move.line'].search(item['domains']['appropriation_current'])
                        self.assertEqual(sum(native.mapped('balance')), 100)
                    self.assertEqual(wizard._preview_journal_item_domain(total), native_domain)
        self.assertEqual([self._ledger_fingerprint(fixture) for fixture in (first, second)], fingerprints)

    def test_audit_cell_omission_is_explicit_and_does_not_truncate_values(self):
        Model = self.env['rebuild.account.report.export.wizard']
        entries = [{'account_id': i, 'account_code': str(i)} for i in range(81)]
        retained, count, complete = Model._bounded_fiscal_audit_rows(entries)
        self.assertEqual((len(retained), count, complete), (80, 81, False))
        self.assertEqual(retained, entries[:80])
        oversized = {'account_id': 999, 'account_code': 'X' * 30000}
        retained, count, complete = Model._bounded_fiscal_audit_rows([oversized, entries[0]])
        self.assertEqual((retained, count, complete), ([entries[0]], 2, False))
