"""Fiscal statement presentation derived from the unchanged posted ledger."""

import hashlib
import json
from decimal import Decimal

from odoo import fields, models

from .report_export_wizard import _amount, _amount_text


class RebuildAccountReportExportWizard(models.TransientModel):
    _inherit = 'rebuild.account.report.export.wizard'

    def _fiscal_result_partition(self, trial_balance, *, french=False):
        """Separate FY P&L from historical P&L plus actual appropriations."""
        self.ensure_one()
        fiscal_from, _fiscal_to = self.company_id.rebuild_compute_fiscalyear_dates(self.date_to)
        fiscal = self.new(self._report_clone_values(self.company_id, fiscal_from, self.date_to))
        fiscal_rows = fiscal._trial_balance_rows()

        def is_result(item):
            if french:
                return str(item.get('account_code') or '').startswith(('6', '7'))
            return str(item.get('account_type') or '').startswith(('income', 'expense'))

        def total(rows, key, predicate):
            return -sum((_amount(item.get(key)) for item in rows if predicate(item)), Decimal('0.00'))

        is_appropriation = lambda item: item.get('account_type') == 'equity_unaffected'
        current = total(fiscal_rows, 'balance', is_result)
        prior = total(fiscal_rows, 'opening_balance', is_result)
        appropriation = total(trial_balance, 'closing_balance', is_appropriation)
        opening_appropriation = total(fiscal_rows, 'opening_balance', is_appropriation)
        current_appropriation = total(fiscal_rows, 'balance', is_appropriation)
        return {
            'date_from': fields.Date.to_string(fiscal_from),
            'date_to': fields.Date.to_string(self.date_to),
            'current': current, 'prior': prior, 'appropriation': appropriation,
            'current_count': sum(int(item.get('move_line_count') or 0)
                                 for item in fiscal_rows if is_result(item)),
            'opening_appropriation': opening_appropriation,
            'current_appropriation': current_appropriation,
            'carryforward': prior + appropriation,
            'french': french, '_fiscal_rows': fiscal_rows,
            '_audit': self._fiscal_result_audit_context(fiscal_rows, fiscal_from, french=french),
        }

    def _fiscal_result_row_context(self, partition, kind):
        """Keep exact calculation evidence in the existing export audit data."""
        current = kind == 'current'
        dates = f"{partition['date_from']} — {partition['date_to']}"
        description = (
            f"Résultat net des charges et produits comptabilisés du {dates}. "
            "Les écritures de remise à zéro restent prises en compte."
            if current else
            "Résultats historiques et soldes des comptes d’affectation. "
            "L’exercice visé par une affectation n’est pas déduit de sa date ou de son libellé."
        )
        return {
            **partition['_audit'],
            'fiscal_result_kind': kind,
            'fiscal_result_french': partition['french'],
            'source_fiscal_date_from': partition['date_from'],
            'source_fiscal_date_to': partition['date_to'],
            'source_fiscal_prior_result': _amount_text(partition['prior']),
            'source_fiscal_opening_appropriation': _amount_text(partition['opening_appropriation']),
            'source_fiscal_current_appropriation': _amount_text(partition['current_appropriation']),
            'source_formula': description,
            'source_description': (
                'Résultats historiques et soldes des comptes d’affectation ; '
                'l’exercice visé par une affectation n’est pas déduit de sa date.'
                if not current else ''
            ),
        }

    def _fiscal_result_source_base(self, company_ids, source_to):
        base = [term for term in self._journal_item_domain(company_ids=company_ids)
                if not (isinstance(term, (tuple, list)) and len(term) == 3
                        and term[0] == 'move_id.date')]
        base.append(('move_id.date', '<=', source_to))
        if self.analytic_plan_ids:
            plan_accounts = self.env['account.analytic.account'].search([
                ('plan_id', 'in', self.analytic_plan_ids.ids),
            ])
            base.append(('analytic_distribution', 'in', plan_accounts.ids))
        if self.analytic_account_ids:
            base.append(('analytic_distribution', 'in', self.analytic_account_ids.ids))
        return base

    @staticmethod
    def _bounded_fiscal_audit_rows(rows, total_count=None):
        """Bound one XLSX audit cell; omission is explicit, never a total."""
        retained = []
        size = 2
        for row in rows:
            row_size = len(json.dumps(row, ensure_ascii=False, separators=(',', ':')))
            if len(retained) < 80 and size + row_size + 1 <= 22000:
                retained.append(row)
                size += row_size + 1
        count = len(rows) if total_count is None else total_count
        return retained, count, len(retained) == count

    def _fiscal_result_audit_context(self, fiscal_rows, fiscal_from, *, french):
        """Reuse account aggregates; do not dump or requery ledger lines."""
        contributions = []
        for item in fiscal_rows:
            is_result = (str(item.get('account_code') or '').startswith(('6', '7')) if french
                         else str(item.get('account_type') or '').startswith(('income', 'expense')))
            if not (is_result or item.get('account_type') == 'equity_unaffected'):
                continue
            if not (int(item.get('move_line_count') or 0) or any(
                    _amount(item.get(key)) for key in ('opening_balance', 'balance', 'closing_balance'))):
                continue
            contributions.append({
                'company_id': self.company_id.id,
                'account_id': int(item['account_id']),
                'account_code': item.get('account_code') or '',
                'account_type': item.get('account_type') or '',
                'opening_balance': _amount_text(item.get('opening_balance')),
                'current_movement': _amount_text(item.get('balance')),
                'closing_balance': _amount_text(item.get('closing_balance')),
            })
        retained, count, complete = self._bounded_fiscal_audit_rows(contributions)
        base = fields.Domain(self._fiscal_result_source_base(self.company_id.ids, self.date_to))
        if french:
            result = (fields.Domain('account_id.code', '=like', '6%')
                      | fields.Domain('account_id.code', '=like', '7%'))
        else:
            types = [value for value, _label in self.env['account.account']._fields['account_type']._description_selection(self.env)
                     if value.startswith(('income', 'expense'))]
            result = fields.Domain('account_id.account_type', 'in', types)
        appropriation = fields.Domain('account_id.account_type', '=', 'equity_unaffected')
        earlier = fields.Domain('move_id.date', '<', fiscal_from)
        current = fields.Domain('move_id.date', '>=', fiscal_from)
        scope = {
            'complete': True,
            'context': {'allowed_company_ids': self.company_id.ids},
            'sign': 'native debit-positive balances',
            'domains': {
                'current_result': list(base & result & current),
                'prior_result': list(base & result & earlier),
                'appropriation_opening': list(base & appropriation & earlier),
                'appropriation_current': list(base & appropriation & current),
            },
        }
        encoded_scope = json.dumps(scope, ensure_ascii=False, separators=(',', ':'), default=str)
        if len(encoded_scope) > 28000:
            scope = {'complete': False, 'scope_sha256': hashlib.sha256(encoded_scope.encode()).hexdigest(),
                     'reason': 'Exact saved filters exceed one audit cell; inspect the saved report session.',
                     'company_id': self.company_id.id}
            encoded_scope = json.dumps(scope, separators=(',', ':'))
        return {
            'source_fiscal_account_contributions': json.dumps(retained, ensure_ascii=False, separators=(',', ':')),
            'source_fiscal_contribution_count': count,
            'source_fiscal_contributions_complete': complete,
            'source_fiscal_scope': encoded_scope,
            'source_fiscal_scope_complete': scope['complete'],
        }

    def _preview_journal_item_domain(self, row):
        kind = row.get('fiscal_result_kind')
        if kind not in {'current', 'carryforward'}:
            return super()._preview_journal_item_domain(row)
        # Reuse all native report filters, replacing only the selected-period
        # lower bound with the exact fiscal calculation's source dates.
        company_ids = (self._row_int_list(row, 'report_company_ids')
                       or [self._row_int(row, 'report_company_id') or self.company_id.id])
        source_to = (fields.Date.to_date(row.get('source_fiscal_date_to'))
                     if row.get('comparison_only') == 'true' else self.date_to)
        base = self._fiscal_result_source_base(company_ids, source_to)
        sources = []
        for company in self.env['res.company'].browse(company_ids):
            fiscal_from, _fiscal_to = company.rebuild_compute_fiscalyear_dates(source_to)
            Account = self.env['account.account'].with_company(company).with_context(active_test=False)
            if row.get('fiscal_result_french'):
                result_accounts = Account.search([
                    ('company_ids', 'in', company.ids),
                    '|', ('code', '=like', '6%'), ('code', '=like', '7%'),
                ])
            else:
                result_types = [value for value, _label in Account._fields['account_type']._description_selection(self.env)
                                if value.startswith(('income', 'expense'))]
                result_accounts = Account.search([
                    ('company_ids', 'in', company.ids), ('account_type', 'in', result_types),
                ])
            result_domain = fields.Domain('account_id', 'in', result_accounts.ids)
            if kind == 'current':
                source = result_domain & fields.Domain('move_id.date', '>=', fiscal_from)
            else:
                source = ((result_domain & fields.Domain('move_id.date', '<', fiscal_from))
                          | fields.Domain('account_id.account_type', '=', 'equity_unaffected'))
            sources.append(fields.Domain('company_id', '=', company.id) & source)
        domain = fields.Domain(base) & fields.Domain.OR(sources)
        if row.get('hierarchy_kind') in {'account', 'pcg_group'}:
            accounts = self._preview_accounts(row, source_account_ids=self._row_int_values(row, 'source_account_id'))
            domain &= fields.Domain('account_id', 'in', accounts.ids)
        return list(domain)

    def _statement_hierarchy_rows(self, row, *, section_group_key):
        rows = super()._statement_hierarchy_rows(row, section_group_key=section_group_key)
        if row.get('fiscal_result_kind'):
            for child in rows:
                child.update({key: row[key] for key in ('fiscal_result_kind', 'fiscal_result_french')})
        return rows

    def _aggregate_company_rows(self, rows):
        aggregated = super()._aggregate_company_rows(rows)
        for item in aggregated:
            kind = item.get('fiscal_result_kind')
            if not kind and item.get('line_code') != 'PASSIF_TOTAL':
                continue
            contributors = [row for row in rows if row.get('fiscal_result_kind') == 'current']
            if kind:
                # A derived zero contribution can be absent in one company;
                # the combined result still belongs to the full statement.
                item['report_company_ids'] = sorted({row['report_company_id'] for row in contributors})
                item['report_company_name'] = ', '.join(sorted({row['report_company_name'] for row in contributors}))
            for key in ('source_fiscal_prior_result', 'source_fiscal_opening_appropriation',
                        'source_fiscal_current_appropriation'):
                item[key] = _amount_text(sum((_amount(row.get(key)) for row in contributors), Decimal('0')))
            if len({row.get('source_fiscal_date_from') for row in contributors}) > 1:
                item['source_fiscal_date_from'] = False
            periods = [{
                key: row.get(key) for key in (
                    'report_company_id', 'report_company_name', 'source_fiscal_date_from',
                    'source_fiscal_date_to', 'source_fiscal_prior_result',
                    'source_fiscal_opening_appropriation', 'source_fiscal_current_appropriation',
                )
            } for row in contributors]
            retained_periods, period_count, periods_complete = self._bounded_fiscal_audit_rows(periods)
            item['source_fiscal_company_periods'] = retained_periods
            item['source_fiscal_company_period_count'] = period_count
            item['source_fiscal_company_periods_complete'] = periods_complete
            account_rows = [entry for row in contributors
                            for entry in json.loads(row['source_fiscal_account_contributions'])]
            retained, count, complete = self._bounded_fiscal_audit_rows(
                account_rows, sum(row['source_fiscal_contribution_count'] for row in contributors))
            item['source_fiscal_account_contributions'] = json.dumps(retained, ensure_ascii=False, separators=(',', ':'))
            item['source_fiscal_contribution_count'] = count
            item['source_fiscal_contributions_complete'] = complete
            scopes = [json.loads(row['source_fiscal_scope']) for row in contributors]
            encoded = json.dumps(scopes, ensure_ascii=False, separators=(',', ':'))
            item['source_fiscal_scope_complete'] = all(row['source_fiscal_scope_complete'] for row in contributors) and len(encoded) <= 28000
            item['source_fiscal_scope'] = (encoded if len(encoded) <= 28000 else json.dumps({
                'complete': False, 'scope_sha256': hashlib.sha256(encoded.encode()).hexdigest(),
                'reason': 'Exact combined saved filters exceed one audit cell; inspect the saved report session.',
            }))
            if kind == 'current' and len(contributors) > 1:
                label = 'Résultat de l’exercice — périodes propres à chaque société'
                for key in ('label', 'account_name', 'line_name'):
                    if key in item:
                        item[key] = label
                item['source_description'] = '; '.join(
                    f"{row['report_company_name']} : {row['source_fiscal_date_from']} — {row['source_fiscal_date_to']}"
                    for row in contributors)
                item['source_formula'] = '; '.join(
                    f"{row['report_company_name']} : {row['source_formula']}"
                    for row in contributors)
        for item in list(aggregated):
            if item.get('fiscal_result_kind') != 'carryforward':
                continue
            subtotal = next((row for row in aggregated
                             if row.get('line_code') == 'PASSIF_CAPITAUX_PROPRES'
                             and row.get('statement_key') == item.get('statement_key')), None)
            if subtotal is not None:
                aggregated.remove(item)
                aggregated.insert(aggregated.index(subtotal), item)
        return aggregated
