"""Bounded native facts for an operator considering an ordinary company change."""
import datetime
import json
import uuid

from odoo import api, models
from odoo.exceptions import AccessError, MissingError, ValidationError


HEADER_FIELDS = '''display_name move_type state write_date company_id journal_id
    name posted_before sequence_prefix sequence_number review_state partner_id
    commercial_partner_id partner_shipping_id invoice_date date invoice_date_due
    taxable_supply_date invoice_payment_term_id partner_bank_id preferred_payment_method_line_id
    invoice_cash_rounding_id fiscal_position_id currency_id company_currency_id
    invoice_currency_rate expected_currency_rate invoice_date amount_untaxed amount_tax amount_total amount_residual
    amount_untaxed_signed amount_tax_signed amount_total_signed amount_residual_signed
    payment_state auto_post auto_post_until auto_post_origin_id reversed_entry_id
    reversal_move_ids vendor_bill_merge_target_id
    origin_payment_id matched_payment_ids statement_line_id statement_line_ids
    tax_cash_basis_rec_id tax_cash_basis_origin_move_id tax_cash_basis_created_move_ids
    message_main_attachment_id invoice_pdf_report_id invoice_origin'''.split()
LINE_FIELDS = '''sequence display_type name write_date company_id move_id product_id
    product_uom_id quantity price_unit discount price_subtotal price_total currency_id
    company_currency_id amount_currency balance debit credit amount_residual
    amount_residual_currency date date_maturity account_id tax_ids tax_line_id
    tax_repartition_line_id tax_tag_ids tax_base_amount analytic_distribution
    reconciled full_reconcile_id matched_debit_ids matched_credit_ids
    statement_line_id payment_id purchase_line_id is_imported'''.split()
COMPANY_FIELDS = '''name parent_id currency_id fiscalyear_lock_date tax_lock_date
    sale_lock_date purchase_lock_date hard_lock_date'''.split()
PARTIAL_FIELDS = '''debit_move_id credit_move_id amount debit_amount_currency
    credit_amount_currency debit_currency_id credit_currency_id company_id
    full_reconcile_id exchange_move_id max_date'''.split()


class AccountMove(models.Model):
    _inherit = 'account.move'

    def _company_context_reference(self, record, target):
        """Only return references whose records and labels the caller can read."""
        record.check_access('read')
        label = record.read(['display_name'], load=None)[0]['display_name']
        result = {'model': record._name, 'id': record.id, 'display_name': str(label)[:512]}
        if len(str(label)) > 512:
            result['display_name_status'] = 'truncated'
        if record.fields_get(['company_id', 'company_ids'], attributes=['type']):
            result['target_compatible'] = bool(record.filtered_domain(record._check_company_domain(target)))
        return result

    def _company_context_record(self, record, names, target, relation_cache=None):
        """Serialize this explicit field list, without following a relation graph."""
        try:
            record.check_access('read')
        except (AccessError, MissingError):
            return {'status': 'unavailable', 'reason': 'access'}
        result = {'model': record._name, 'id': record.id, 'fields': {}, 'unavailable_fields': {}}
        relation_cache = {} if relation_cache is None else relation_cache
        metadata = record.fields_get(names, attributes=['type'])
        if 'company_id' in metadata or 'company_ids' in metadata:
            result['target_compatible'] = bool(record.filtered_domain(record._check_company_domain(target)))
        for name in dict.fromkeys(names):
            if name not in record._fields:
                result['unavailable_fields'][name] = 'not_installed'
                continue
            if name not in metadata:
                result['unavailable_fields'][name] = 'field_access'
                continue
            field = record._fields[name]
            try:
                if field.relational:
                    related = record[name]
                    if not related:
                        result['fields'][name] = False if field.type == 'many2one' else {
                            'records': [], 'visible_count': 0, 'status': 'complete', 'scope': 'readable_only',
                        }
                        continue
                    # Memoize only within this RPC and the same native company context.
                    # Repeated account/tax references must not repeat rule/count/label queries per line.
                    related = related.with_context(active_test=False)
                    cache_key = (related._name, tuple(related.ids), related.env.uid,
                                 related.env.company.id, tuple(related.env.companies.ids))
                    if cache_key not in relation_cache:
                        visible = related.search([('id', 'in', related.ids)], order='id', limit=101)
                        references = []
                        for item in visible[:100]:
                            try:
                                references.append(self._company_context_reference(item, target))
                            except (AccessError, MissingError):
                                pass
                        relation_cache[cache_key] = (references, related.search_count([('id', 'in', related.ids)]), len(visible) > 100)
                    references, visible_count, truncated = relation_cache[cache_key]
                    if field.type == 'many2one':
                        if references:
                            result['fields'][name] = references[0]
                        else:
                            result['unavailable_fields'][name] = 'relation_access'
                    else:
                        result['fields'][name] = {
                            'records': references, 'visible_count': visible_count,
                            'status': 'truncated' if truncated else 'complete',
                            'scope': 'readable_only', 'hidden_records': 'unknown', 'limit': 100,
                        }
                else:
                    value = record[name]
                    if name == 'analytic_distribution' and value:
                        analytic_ids = {int(part) for key in value for part in key.split(',')}
                        readable = record.env['account.analytic.account'].with_context(active_test=False).search([('id', 'in', list(analytic_ids))])
                        if set(readable.ids) != analytic_ids:
                            result['unavailable_fields'][name] = 'relation_access'
                            continue
                        if len(value) > 100:
                            result['unavailable_fields'][name] = 'budget_exceeded'
                            continue
                    if isinstance(value, (datetime.date, datetime.datetime)):
                        value = value.isoformat()
                    if isinstance(value, str) and len(value) > 1024:
                        result['unavailable_fields'][name] = 'text_truncated_at_1024'
                        value = value[:1024]
                    result['fields'][name] = value
            except (AccessError, MissingError):
                result['unavailable_fields'][name] = 'access'
        return result

    def _company_context_page(self, model_name, domain, names, target, offset, limit, relation_cache=None):
        if model_name not in self.env:
            return {'status': 'unavailable', 'reason': 'not_installed', 'records': []}
        model = self.env[model_name].with_context(active_test=False)
        try:
            model.check_access('read')
            count = model.search_count(domain)
            records = model.search(domain, offset=offset, limit=limit, order='id')
            values = [self._company_context_record(record, names, target, relation_cache) for record in records]
            return {'records': values, 'visible_count': count, 'offset': offset, 'limit': limit,
                    'next_offset': offset + len(values) if offset + len(values) < count else False,
                    'status': 'truncated' if offset + len(values) < count else 'complete',
                    'scope': 'readable_only', 'hidden_records': 'unknown'}
        except (AccessError, MissingError):
            return {'status': 'unavailable', 'reason': 'access', 'records': []}

    @api.readonly
    def get_company_change_context(self, target_company_id, offset=0, limit=50):
        """Read one native transaction; neither propose mappings nor change a bill.

        Each page is a new snapshot. No token grants authority to a later write.
        """
        self.ensure_one()
        self.check_access('read')
        if type(target_company_id) is not int or type(offset) is not int or type(limit) is not int:
            raise ValidationError(self.env._('Company, offset and limit must be integers.'))
        if offset < 0 or offset > 1000000 or not 1 <= limit <= 100:
            raise ValidationError(self.env._('Use an offset from 0 to 1000000 and a limit from 1 to 100.'))
        # Evaluating env.companies also verifies a forged allowed_company_ids context.
        if target_company_id not in self.env.companies.ids:
            raise AccessError(self.env._('Select an authorized target company in the current company scope.'))
        target = self.env['res.company'].browse(target_company_id)
        target.check_access('read')
        relation_cache = {}
        header = self._company_context_record(self, HEADER_FIELDS, target, relation_cache)
        page = lambda model, domain, names: self._company_context_page(model, domain, names, target, offset, limit, relation_cache)
        result = {
            'schema_version': 1,
            'snapshot': {'isolation': 'repeatable_read', 'boundary': 'single_native_rpc', 'snapshot_id': str(uuid.uuid4()),
                         'captured_at_utc': datetime.datetime.now(datetime.timezone.utc).isoformat(),
                         'write_date': header['fields'].get('write_date'), 'move_id': self.id,
                         'pages_are_independent_snapshots': True},
            'scope': {'active_company_id': self.env.company.id, 'allowed_company_ids': self.env.companies.ids,
                      'records': 'native_readable_only', 'hidden_records': 'unknown'},
            'source_company': self._company_context_record(self.company_id, COMPANY_FIELDS, target, relation_cache),
            'target_company': self._company_context_record(target, COMPANY_FIELDS, target, relation_cache),
            'invoice': header,
            'currency_context': {
                'rate_date': self._get_invoice_currency_rate_date().isoformat(),
                'target_rate': self.env['res.currency']._get_conversion_rate(
                    from_currency=target.currency_id, to_currency=self.currency_id,
                    company=target, date=self._get_invoice_currency_rate_date()),
                'meaning': 'native_company_to_document_rate_not_a_repricing_or_write_preview',
            },
            'supplier_properties_source': self._company_context_record(self.partner_id.with_company(self.company_id),
                ['property_account_payable_id', 'property_account_receivable_id', 'property_supplier_payment_term_id',
                 'property_purchase_currency_id', 'property_account_position_id'], target, relation_cache) if self.partner_id else False,
            'supplier_properties_target': self._company_context_record(self.partner_id.with_company(target),
                ['property_account_payable_id', 'property_account_receivable_id', 'property_supplier_payment_term_id',
                 'property_purchase_currency_id', 'property_account_position_id'], target, relation_cache) if self.partner_id else False,
            'counterpart_lines': page('account.move.line', ['|',
                ('matched_debit_ids.credit_move_id.move_id', '=', self.id),
                ('matched_credit_ids.debit_move_id.move_id', '=', self.id)],
                ['move_id', 'company_id', 'account_id', 'balance', 'amount_currency', 'currency_id',
                 'full_reconcile_id', 'statement_line_id', 'payment_id']),
            'full_reconciliations': page('account.full.reconcile', [('reconciled_line_ids.move_id', '=', self.id)],
                ['partial_reconcile_ids', 'reconciled_line_ids']),
            'lines': page('account.move.line', [('move_id', '=', self.id)], LINE_FIELDS),
            'reconciliations': page('account.partial.reconcile', [
                '|', ('debit_move_id.move_id', '=', self.id), ('credit_move_id.move_id', '=', self.id),
            ], PARTIAL_FIELDS),
            'payments': page('account.payment', [('id', 'in', self.matched_payment_ids.ids)],
                             ['name', 'state', 'company_id', 'currency_id', 'amount', 'move_id', 'payment_method_line_id']),
            'attachments': page('ir.attachment', [('res_model', '=', 'account.move'), ('res_id', '=', self.id)],
                                ['name', 'company_id', 'res_field', 'mimetype', 'file_size', 'checksum', 'create_date']),
            'document_links': page('usl.document.link', [('res_model', '=', 'account.move'), ('res_id', '=', self.id)],
                                   ['document_id', 'company_id', 'version_id', 'active', 'archive_mode', 'attachment_origin', 'document_role']),
            'expenses': page('hr.expense', ['|', ('account_move_id', '=', self.id), ('existing_bill_id', '=', self.id)]
                             if 'existing_bill_id' in self.env['hr.expense']._fields else [('account_move_id', '=', self.id)],
                             ['name', 'company_id', 'state', 'account_move_id', 'existing_bill_id', 'attachment_ids']),
            'merged_originals': page('account.move', [('vendor_bill_merge_target_id', '=', self.id)],
                                     ['name', 'company_id', 'move_type', 'state', 'write_date', 'vendor_bill_merge_target_id']),
            'purchase_orders': page('purchase.order', [('order_line.invoice_lines.move_id', '=', self.id)],
                                   ['name', 'company_id', 'currency_id', 'state', 'partner_id']),
            'credit_note_origins': page('account.move', [('id', 'in', (self.reversed_entry_id | self.reversal_move_ids).ids)],
                                       ['name', 'move_type', 'company_id', 'currency_id', 'state', 'write_date']),
            'purchase_lines': page('purchase.order.line', [('invoice_lines.move_id', '=', self.id)],
                                   ['name', 'company_id', 'order_id', 'product_id', 'currency_id']),
            'choices': {},
            'interpretation': {
                'mappings': 'unresolved_explicit_operator_choice',
                'financial_values': 'current_native_values_not_a_target_write_preview',
                'paid_labels': 'descriptive_not_proof_of_reconciliation',
                'missing_evidence': 'information_not_a_veto',
                'authority': 'native_read_and_write_rules_remain_in_force',
                'onchange_preview': 'optional_native_onchange_requires_write_access',
            },
            'extensions': {},
            'budget': {'json_utf8_bytes_max': 262144, 'text_characters_max': 1024,
                       'relation_records_max': 100, 'page_records_max': 100, 'status': 'complete'},
        }
        tax_usage = ('sale' if self.is_sale_document(include_receipts=True) else
                     'purchase' if self.is_purchase_document(include_receipts=True) else False)
        tax_direction = [('type_tax_use', '=', tax_usage)] if tax_usage else []
        for model_name, extra, names in [
            ('account.journal', [('type', 'in', self._get_valid_journal_types()), ('active', '=', True)],
             ['name', 'company_id', 'currency_id', 'default_account_id', 'type']),
            ('account.account', [('active', '=', True)], ['name', 'code', 'company_ids', 'account_type']),
            ('account.tax', tax_direction + [('active', '=', True)],
             ['name', 'company_id', 'type_tax_use', 'price_include', 'amount_type', 'amount']),
            ('account.payment.term', [('active', '=', True)], ['name', 'company_id']),
            ('account.fiscal.position', [('active', '=', True)], ['name', 'company_id']),
            ('account.analytic.account', [('active', '=', True)], ['name', 'company_id', 'plan_id']),
        ]:
            model = self.env[model_name]
            result['choices'][model_name] = self.with_company(target)._company_context_page(
                model_name, list(model._check_company_domain(target)) + extra, names,
                target, offset, limit, relation_cache)
        document_ids = [row.get('fields', {})['document_id']['id'] for row in result['document_links'].get('records', [])
                        if isinstance(row.get('fields', {}).get('document_id'), dict)]
        result['documents'] = self._company_context_page('usl.document', [('id', 'in', document_ids)],
            ['name', 'company_id', 'availability_state', 'review_state', 'checksum', 'archive_checksum',
             'version_ids', 'permission_sync_state'], target, 0, limit, relation_cache)
        result['documents']['scope'] = 'readable_documents_of_current_link_page'
        analytic_ids = sorted({int(part) for row in result['lines'].get('records', [])
                               for key in (row.get('fields', {}).get('analytic_distribution') or {})
                               for part in key.split(',')})
        result['line_analytic_accounts'] = self._company_context_page('account.analytic.account',
            [('id', 'in', analytic_ids)], ['name', 'company_id', 'plan_id'], target, 0, limit, relation_cache)
        result['line_analytic_accounts']['scope'] = 'readable_analytics_of_current_line_page'
        current_account_ids = [row.get('fields', {})['account_id']['id']
                               for row in result['lines'].get('records', [])
                               if isinstance(row.get('fields', {}).get('account_id'), dict)]
        current_tax_ids = {ref['id'] for row in result['lines'].get('records', [])
                           for ref in (row.get('fields', {}).get('tax_ids') or {}).get('records', [])}
        current_tax_ids.update(row.get('fields', {})['tax_line_id']['id']
                               for row in result['lines'].get('records', [])
                               if isinstance(row.get('fields', {}).get('tax_line_id'), dict))
        result['current_accounts'] = self._company_context_page('account.account', [('id', 'in', current_account_ids)],
            ['name', 'code', 'company_ids', 'account_type', 'currency_id', 'active'], target, 0, limit, relation_cache)
        result['current_taxes'] = self._company_context_page('account.tax', [('id', 'in', sorted(current_tax_ids))],
            ['name', 'company_id', 'type_tax_use', 'price_include', 'amount_type', 'amount', 'active'],
            target, 0, limit, relation_cache)
        for key in ('documents', 'line_analytic_accounts', 'current_accounts', 'current_taxes'):

            result[key].pop('next_offset', None)
            if key in ('current_accounts', 'current_taxes'):
                result[key]['scope'] = 'readable_relations_of_current_line_page'
            result[key]['continuation'] = 'generic_native_reads_from_parent_references'
        # Extension metadata is explicit; it does not silently claim every installed extension was understood.
        for model_name, covered in [('account.move', HEADER_FIELDS), ('account.move.line', LINE_FIELDS)]:
            model = self.env[model_name]
            available = model.fields_get(attributes=['type'])
            result['extensions'][model_name] = {
                'company_checked_fields': sorted(name for name, field in model._fields.items()
                                                 if getattr(field, 'check_company', False) and name in available),
                'uncovered_company_checked_fields': sorted(name for name, field in model._fields.items()
                                                           if getattr(field, 'check_company', False) and name in available and name not in covered),
                'status': 'baseline_only_extensions_require_native_review',
            }
        # Keep the exact JSON payload bounded; never silently call a clipped page complete.
        sections = [result[key] for key in ('lines', 'reconciliations', 'payments', 'attachments',
                                            'document_links', 'expenses', 'purchase_lines', 'purchase_orders', 'credit_note_origins',
                                            'counterpart_lines', 'full_reconciliations', 'merged_originals')]
        sections += list(result['choices'].values()) + [result['documents'], result['line_analytic_accounts'], result['current_accounts'], result['current_taxes']]
        while len(json.dumps(result, ensure_ascii=False, separators=(',', ':')).encode()) > 262144:
            populated = [section for section in sections if section.get('records')]
            if not populated:
                raise ValidationError(self.env._('The context header exceeds its response budget.'))
            section = max(populated, key=lambda item: len(json.dumps(item)))
            section['records'].pop()
            section['status'] = 'truncated'
            if section.get('continuation') != 'generic_native_reads_from_parent_references':
                section['next_offset'] = section.get('offset', 0) + len(section['records'])
            result['budget']['status'] = 'truncated'
        return result
