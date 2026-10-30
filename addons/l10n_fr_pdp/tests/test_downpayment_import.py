from unittest.mock import patch

from lxml import etree

from odoo import Command
from odoo.exceptions import UserError
from odoo.tests import tagged
from odoo.addons.account.tests.common import AccountTestInvoicingCommon


@tagged('post_install', '-at_install')
class TestFrenchDownpaymentImport(AccountTestInvoicingCommon):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.builder = cls.env['account.edi.xml.ubl_21_fr']
        cls.other_company_data = cls.setup_other_company()

    def _bill(self, reference='SYNTHETIC-DEPOSIT', **kwargs):
        bill = self.init_invoice(
            kwargs.pop('move_type', 'in_invoice'),
            partner=kwargs.pop('partner', self.partner_a),
            amounts=kwargs.pop('amounts', [100]), taxes=[], **kwargs,
        )
        bill.ref = reference
        return bill

    def _values(self, invoice, references=('SYNTHETIC-DEPOSIT',), **kwargs):
        tree = etree.Element('Invoice')
        etree.SubElement(tree, 'ProfileID').text = 'B4'
        total = etree.SubElement(tree, 'LegalMonetaryTotal')
        etree.SubElement(total, 'PrepaidAmount').text = '100'
        for reference in references:
            node = etree.SubElement(etree.SubElement(tree, 'BillingReference'), 'InvoiceDocumentReference')
            etree.SubElement(node, 'ID').text = reference
        return {
            'invoice': invoice, 'tree': tree, 'logs': [], 'file_document_sign': 1,
            'currency_values': {'currency': invoice.currency_id},
            'are_taxes_complete': False, 'to_write': {}, **kwargs,
        }

    def _deductions(self, values):
        invoice = values['invoice']
        original_lines = invoice.invoice_line_ids
        self.builder._import_ubl_invoice_add_prepaid_amount(values)
        # This is the native ordering: collected identity/currency values are
        # written before the untaxed-amount correction attaches deductions.
        invoice.write(values['to_write'])
        self.builder._import_ubl_invoice_fix_untaxed_amount(values)
        return invoice.invoice_line_ids - original_lines

    def test_reference_scope_excludes_unrelated_supplier_company_currency_type_cancel_and_self(self):
        target = self._bill(amounts=[1000])
        other_currency = self.env['res.currency'].with_context(active_test=False).search([('id', '!=', target.currency_id.id)], limit=1)
        other_currency.active = True
        excluded = self._bill(partner=self.partner_b)
        excluded |= self._bill(company=self.other_company_data['company'])
        excluded |= self._bill(currency=other_currency)
        excluded |= self._bill(move_type='out_invoice')
        excluded |= self._bill(move_type='out_refund')
        cancelled = self._bill()
        cancelled.button_cancel()
        excluded |= cancelled
        entries = self.env['account.move'].create({
            'move_type': 'entry', 'ref': target.ref, 'partner_id': self.partner_a.id,
            'journal_id': self.company_data['default_journal_misc'].id,
        })
        excluded |= entries
        snapshots = {move.id: move.invoice_line_ids.ids for move in excluded | target}
        values = self._values(target)
        self.assertFalse(self._deductions(values))
        self.assertTrue(any('not found' in log for log in values['logs']))
        self.assertEqual(snapshots, {move.id: move.invoice_line_ids.ids for move in excluded | target})
        self.assertEqual(values['prepaid_amount'], 100)

    def test_unknown_supplier_does_not_match_and_sales_target_does_not_deduct(self):
        self._bill()
        target = self._bill(reference='TARGET')
        target.partner_id = False
        values = self._values(target)
        self.assertFalse(self._deductions(values))
        self.assertTrue(any('not found' in log for log in values['logs']))
        target.partner_id = self.partner_a
        values = self._values(target, to_write={'move_type': 'out_invoice'})
        self.assertFalse(self._deductions(values))
        self.assertEqual(values['prepaid_amount'], 100)

    def test_authoritative_collected_company_supplier_currency_and_purchase_type(self):
        currency = self.env['res.currency'].with_context(active_test=False).search([('id', '!=', self.env.company.currency_id.id)], limit=1)
        currency.active = True
        supplier = self.partner_b
        source = self._bill(partner=supplier, company=self.other_company_data['company'], currency=currency)
        target = self._bill(reference='TARGET', move_type='out_invoice')
        values = self._values(
            target, company=source.company_id, customer_values={'customer': supplier},
            currency_values={'currency': currency}, to_write={'move_type': 'in_invoice'},
        )
        self.builder._import_ubl_invoice_add_prepaid_amount(values)
        self.assertEqual(values['downpayment_moves'], source)
        self.assertEqual(target.partner_id, self.partner_a)
        self.assertEqual(target.currency_id, self.env.company.currency_id)
        self.assertNotEqual(target.company_id, source.company_id)
        self.assertEqual(len(source.invoice_line_ids), 1)

    def test_contact_of_same_commercial_supplier_is_eligible(self):
        contact = self.env['res.partner'].create({'name': 'Synthetic billing contact', 'parent_id': self.partner_a.id})
        source = self._bill(partner=contact)
        target = self._bill(reference='TARGET', amounts=[1000])
        source_lines = source.invoice_line_ids
        deductions = self._deductions(self._values(target))
        self.assertRecordValues(deductions, [{'quantity': -1, 'price_unit': 100}])
        self.assertEqual(source.invoice_line_ids, source_lines)
        self.assertEqual(source.amount_untaxed, 100)
        self.assertEqual(target.amount_untaxed, 900)

    def test_draft_posted_and_refund_sources_preserve_originals_and_deduction_sign(self):
        for move_type, post, expected_quantity in (
            ('in_invoice', False, -1), ('in_invoice', True, -1),
            ('in_refund', False, 1), ('in_refund', True, 1),
        ):
            with self.subTest(move_type=move_type, posted=post):
                reference = f'SYNTHETIC-{move_type}-{post}'
                source = self._bill(reference=reference, move_type=move_type, post=post)
                if post:
                    counterpart = self._bill(
                        reference='SYNTHETIC-SETTLEMENT',
                        move_type='in_refund' if move_type == 'in_invoice' else 'in_invoice', post=True,
                    )
                    (source.line_ids | counterpart.line_ids).filtered(
                        lambda line: line.account_id.account_type == 'liability_payable'
                    ).reconcile()
                original_matches = [(line.id, line.matched_debit_ids.ids, line.matched_credit_ids.ids) for line in source.line_ids]
                original_lines = source.line_ids
                original_balances = [(line.id, line.balance, line.amount_currency) for line in original_lines]
                target = self._bill(reference='TARGET', amounts=[1000])
                deductions = self._deductions(self._values(target, references=(reference,)))
                self.assertRecordValues(deductions, [{'quantity': expected_quantity, 'price_unit': 100}])
                self.assertEqual(source.line_ids, original_lines)
                self.assertEqual([(line.id, line.balance, line.amount_currency) for line in source.line_ids], original_balances)
                self.assertEqual(source.state, 'posted' if post else 'draft')
                self.assertEqual(
                    [(line.id, line.matched_debit_ids.ids, line.matched_credit_ids.ids) for line in source.line_ids],
                    original_matches,
                )
                self.assertAlmostEqual(sum(target.line_ids.mapped('balance')), 0)
                self.assertEqual(target.amount_untaxed, 1000 + expected_quantity * 100)

    def test_identical_duplicate_reference_and_repeated_xml_reference_deduct_once(self):
        source = self._bill()
        duplicate = source.copy({'ref': source.ref})
        target = self._bill(reference='TARGET', amounts=[1000])
        deductions = self._deductions(self._values(target, references=(source.ref, source.ref)))
        self.assertEqual(len(deductions), 1)
        self.assertEqual(target.amount_untaxed, 900)
        self.assertEqual(source.amount_untaxed, 100)
        self.assertEqual(duplicate.amount_untaxed, 100)

    def test_conflicting_duplicates_refused_before_any_line_copy(self):
        valid = self._bill(reference='SYNTHETIC-FIRST')
        source = self._bill()
        conflicting = self._bill(amounts=[101])
        target = self._bill(reference='TARGET', amounts=[1000])
        values = self._values(target, references=(valid.ref, source.ref))
        with patch.object(type(self.env['account.move.line']), 'copy_data', autospec=True) as copy:
            with self.assertRaisesRegex(UserError, 'review the referenced downpayment'):
                self.builder._import_ubl_invoice_add_prepaid_amount(values)
            copy.assert_not_called()
        self.assertEqual(target.amount_untaxed, 1000)
        self.assertEqual(source.amount_untaxed, 100)
        self.assertEqual(conflicting.amount_untaxed, 101)

    def test_collected_supplier_is_resolved_by_real_import_before_invoice_write(self):
        source = self._bill(partner=self.partner_b)
        target = self._bill(reference='TARGET', partner=self.partner_a, amounts=[1000])
        values = self._values(target)
        party = etree.SubElement(etree.SubElement(values['tree'], 'AccountingSupplierParty'), 'Party')
        etree.SubElement(etree.SubElement(party, 'PartyName'), 'Name').text = source.partner_id.name
        values = self.builder._import_ubl_init_collected_values(target, {'xml_tree': values['tree']}) | {
            'file_document_sign': 1, 'currency_values': {'currency': target.currency_id},
            'are_taxes_complete': False,
        }
        self.builder._import_ubl_invoice_add_customer_values(values)
        self.builder._import_ubl_retrieve_customer(values)
        self.assertEqual(values['customer_values']['customer'], source.partner_id)
        self.assertEqual(values['to_write']['partner_id'], source.partner_id.id)
        self.assertEqual(target.partner_id, self.partner_a)
        deductions = self._deductions(values)
        self.assertEqual(target.partner_id, source.partner_id)
        self.assertRecordValues(deductions, [{'quantity': -1, 'price_unit': 100}])

    def test_to_write_supplier_is_used_before_stale_invoice_supplier(self):
        source = self._bill(partner=self.partner_b)
        self._bill(partner=self.partner_a, amounts=[731])
        target = self._bill(reference='TARGET', amounts=[1000])
        deductions = self._deductions(self._values(target, to_write={'partner_id': source.partner_id.id}))
        self.assertRecordValues(deductions, [{'quantity': -1, 'price_unit': 100}])

    def test_duplicate_signature_distinguishes_financial_line_values_and_document_type(self):
        changes = [
            {'quantity': 2}, {'price_unit': 101}, {'discount': 5},
            {'account_id': self.company_data['default_account_revenue'].id},
            {'product_id': self.product_a.id},
            {'tax_ids': [(6, 0, self.tax_purchase_a.ids)]},
        ]
        for index, changed_values in enumerate(changes):
            with self.subTest(fields=sorted(changed_values)):
                source = self._bill(reference=f'SYNTHETIC-SIGNATURE-{index}')
                duplicate = source.copy({'ref': source.ref})
                duplicate.invoice_line_ids.write(changed_values)
                target = self._bill(reference='TARGET', amounts=[1000])
                with patch.object(type(self.env['account.move.line']), 'copy_data', autospec=True) as copy:
                    with self.assertRaisesRegex(UserError, 'review the referenced downpayment'):
                        self.builder._import_ubl_invoice_add_prepaid_amount(self._values(target, references=(source.ref,)))
                    copy.assert_not_called()
        source = self._bill(reference='SYNTHETIC-NOTE')
        duplicate = source.copy({'ref': source.ref})
        duplicate.invoice_line_ids.unlink()
        duplicate.write({'invoice_line_ids': [Command.create({
            'display_type': 'line_note', 'name': 'Synthetic import note',
        })]})
        target = self._bill(reference='TARGET', amounts=[1000])
        with patch.object(type(self.env['account.move.line']), 'copy_data', autospec=True) as copy:
            with self.assertRaisesRegex(UserError, 'review the referenced downpayment'):
                self.builder._import_ubl_invoice_add_prepaid_amount(self._values(target, references=(source.ref,)))
            copy.assert_not_called()
        source = self._bill(reference='SYNTHETIC-TYPE')
        self._bill(reference=source.ref, move_type='in_refund')
        target = self._bill(reference='TARGET', amounts=[1000])
        with self.assertRaisesRegex(UserError, 'review the referenced downpayment'):
            self.builder._import_ubl_invoice_add_prepaid_amount(self._values(target, references=(source.ref,)))

    def test_full_native_import_uses_resolved_supplier_and_preserves_posted_source(self):
        source = self._bill(partner=self.partner_b, post=True)
        original_lines = source.line_ids
        original_balances = source.line_ids.mapped('balance')
        target = self.env['account.move'].create({
            'move_type': 'in_invoice', 'partner_id': self.partner_a.id,
            'journal_id': self.company_data['default_journal_purchase'].id,
        })
        xml = etree.fromstring(b'''<Invoice xmlns="urn:oasis:names:specification:ubl:schema:xsd:Invoice-2"
            xmlns:cbc="urn:oasis:names:specification:ubl:schema:xsd:CommonBasicComponents-2"
            xmlns:cac="urn:oasis:names:specification:ubl:schema:xsd:CommonAggregateComponents-2">
            <cbc:ProfileID>B4</cbc:ProfileID><cbc:ID>SYNTHETIC-FINAL</cbc:ID>
            <cbc:IssueDate>2019-01-01</cbc:IssueDate><cbc:InvoiceTypeCode>380</cbc:InvoiceTypeCode>
            <cbc:DocumentCurrencyCode/>
            <cac:BillingReference><cac:InvoiceDocumentReference><cbc:ID>SYNTHETIC-DEPOSIT</cbc:ID></cac:InvoiceDocumentReference></cac:BillingReference>
            <cac:AccountingSupplierParty><cac:Party><cac:PartyName><cbc:Name/></cac:PartyName></cac:Party></cac:AccountingSupplierParty>
            <cac:LegalMonetaryTotal><cbc:LineExtensionAmount>1000</cbc:LineExtensionAmount>
                <cbc:TaxExclusiveAmount>1000</cbc:TaxExclusiveAmount><cbc:TaxInclusiveAmount>1000</cbc:TaxInclusiveAmount>
                <cbc:PrepaidAmount>100</cbc:PrepaidAmount><cbc:PayableAmount>900</cbc:PayableAmount></cac:LegalMonetaryTotal>
            <cac:InvoiceLine><cbc:ID>1</cbc:ID><cbc:InvoicedQuantity>1</cbc:InvoicedQuantity>
                <cbc:LineExtensionAmount>1000</cbc:LineExtensionAmount>
                <cac:Item><cbc:Name>Synthetic final service</cbc:Name></cac:Item>
                <cac:Price><cbc:PriceAmount>1000</cbc:PriceAmount></cac:Price>
            </cac:InvoiceLine>
        </Invoice>''')
        xml.find('./{*}DocumentCurrencyCode').text = target.currency_id.name
        xml.find('./{*}AccountingSupplierParty/{*}Party/{*}PartyName/{*}Name').text = source.partner_id.name
        # Rendering unrelated substitute PDFs is outside this financial import test.
        with patch.object(type(self.builder), '_import_attachments', return_value=self.env['ir.attachment']):
            self.builder._ubl_import_invoice(target, {'xml_tree': xml, 'attachment': self.env['ir.attachment']}, new=True)
        self.assertEqual(target.partner_id, source.partner_id)
        self.assertEqual(target.amount_untaxed, 900)
        self.assertEqual(target.amount_total, 900)
        self.assertRecordValues(target.invoice_line_ids.sorted('quantity'), [
            {'quantity': -1, 'price_unit': 100}, {'quantity': 1, 'price_unit': 1000},
        ])
        self.assertEqual(source.line_ids, original_lines)
        self.assertEqual(source.line_ids.mapped('balance'), original_balances)
        self.assertEqual(source.state, 'posted')
        self.assertAlmostEqual(sum(target.line_ids.mapped('balance')), 0)

    def test_foreign_currency_deduction_uses_target_date_rate_and_preserves_source(self):
        currency = self.env['res.currency'].with_context(active_test=False).search([
            ('id', '!=', self.env.company.currency_id.id),
        ], limit=1)
        currency.active = True
        self.env['res.currency.rate'].create([
            {'currency_id': currency.id, 'company_id': self.env.company.id, 'name': '2019-01-01', 'rate': 2},
            {'currency_id': currency.id, 'company_id': self.env.company.id, 'name': '2019-02-01', 'rate': 4},
        ])
        # Native rates apply strictly after their rate date.
        source = self._bill(currency=currency, invoice_date='2019-01-02', post=True)
        target = self._bill(reference='TARGET', currency=currency, invoice_date='2019-02-02', amounts=[1000])
        source_balances = source.line_ids.mapped('balance')
        self.assertNotEqual(source.invoice_currency_rate, target.invoice_currency_rate)
        deductions = self._deductions(self._values(target))
        self.assertEqual(deductions.amount_currency, -100)
        self.assertAlmostEqual(deductions.balance, -100 / target.invoice_currency_rate)
        self.assertNotEqual(deductions.balance, -source.invoice_line_ids.balance)
        self.assertEqual(source.line_ids.mapped('balance'), source_balances)
        self.assertEqual(target.amount_total, 900)
        self.assertAlmostEqual(sum(target.line_ids.mapped('balance')), 0)

    def _manual_tax_source(self, tax_amount=17):
        tax = self.tax_purchase_a.copy({'name': 'Synthetic manual downpayment VAT20', 'amount': 20})
        source = self._bill()
        line = source.invoice_line_ids
        line.tax_ids = tax
        line.extra_tax_data = {
            'currency_id': source.currency_id.id,
            'price_unit': line.price_unit,
            'discount': line.discount,
            'quantity': line.quantity,
            'rate': source.invoice_currency_rate,
            'manual_total_excluded_currency': 100,
            'manual_total_excluded': 100,
            'manual_tax_amounts': {
                str(tax.id): {
                    'base_amount_currency': 100, 'base_amount': 100,
                    'tax_amount_currency': tax_amount, 'tax_amount': tax_amount,
                },
            },
        }
        self.assertEqual(source.amount_tax, tax_amount)
        return source

    def test_manual_tax_deduction_keeps_custom_amount_and_posted_source(self):
        source = self._manual_tax_source()
        source.action_post()
        columns = ['id', 'move_id', 'balance', 'amount_currency', 'extra_tax_data']
        original_lines = source.line_ids
        snapshot = original_lines.read(columns)
        target = self._bill(reference='TARGET', amounts=[1000])
        deductions = self._deductions(self._values(target))
        self.assertEqual(deductions.quantity, -1)
        self.assertEqual(deductions.price_subtotal, -100)
        self.assertEqual(deductions.price_total, -117)
        self.assertEqual(target.amount_tax, -17)
        self.assertEqual(target.amount_total, 883)
        self.assertEqual(deductions.extra_tax_data['quantity'], -1)
        self.assertEqual(source.state, 'posted')
        self.assertEqual(source.line_ids, original_lines)
        self.assertEqual(source.line_ids.read(columns), snapshot)
        self.assertAlmostEqual(sum(target.line_ids.mapped('balance')), 0)

    def test_manual_tax_conflicting_duplicate_refused_before_copy(self):
        source = self._manual_tax_source()
        duplicate = source.copy({'ref': source.ref})
        data = dict(duplicate.invoice_line_ids.extra_tax_data)
        data['manual_tax_amounts'] = {
            tax_id: {**amounts, 'tax_amount_currency': 18, 'tax_amount': 18}
            for tax_id, amounts in data['manual_tax_amounts'].items()
        }
        duplicate.invoice_line_ids.extra_tax_data = data
        self.assertEqual(source.amount_tax, 17)
        self.assertEqual(duplicate.amount_tax, 18)
        target = self._bill(reference='TARGET', amounts=[1000])
        with patch.object(type(self.env['account.move.line']), 'copy_data', autospec=True) as copy:
            with self.assertRaisesRegex(UserError, 'review the referenced downpayment'):
                self.builder._import_ubl_invoice_add_prepaid_amount(self._values(target))
            copy.assert_not_called()

    def test_analytic_allocation_conflicting_duplicate_refused_before_copy(self):
        plan = self.env['account.analytic.plan'].create({'name': 'Synthetic downpayment allocation'})
        accounts = self.env['account.analytic.account'].create([
            {'name': 'Synthetic allocation A', 'plan_id': plan.id, 'company_id': self.env.company.id},
            {'name': 'Synthetic allocation B', 'plan_id': plan.id, 'company_id': self.env.company.id},
        ])
        source = self._bill()
        source.invoice_line_ids.analytic_distribution = {str(accounts[0].id): 100}
        duplicate = source.copy({'ref': source.ref})
        duplicate.invoice_line_ids.analytic_distribution = {str(accounts[1].id): 100}
        target = self._bill(reference='TARGET', amounts=[1000])
        with patch.object(type(self.env['account.move.line']), 'copy_data', autospec=True) as copy:
            with self.assertRaisesRegex(UserError, 'review the referenced downpayment'):
                self.builder._import_ubl_invoice_add_prepaid_amount(self._values(target))
            copy.assert_not_called()


@tagged('post_install', '-at_install')
class TestFrenchCiiDownpaymentImport(AccountTestInvoicingCommon):
    """CII imports preserve the same scoped deductions as French UBL imports."""

    _bill = TestFrenchDownpaymentImport._bill
    _manual_tax_source = TestFrenchDownpaymentImport._manual_tax_source

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.builder = cls.env['account.edi.xml.cii']
        cls.other_company_data = cls.setup_other_company()

    def _values(self, invoice, references=('SYNTHETIC-DEPOSIT',), **kwargs):
        tree = etree.Element('CrossIndustryInvoice')
        context = etree.SubElement(tree, 'ExchangedDocumentContext')
        etree.SubElement(etree.SubElement(context, 'BusinessProcessSpecifiedDocumentContextParameter'), 'ID').text = 'B4'
        settlement = etree.SubElement(etree.SubElement(tree, 'SupplyChainTradeTransaction'), 'ApplicableHeaderTradeSettlement')
        etree.SubElement(etree.SubElement(settlement, 'SpecifiedTradeSettlementHeaderMonetarySummation'), 'TotalPrepaidAmount').text = '100'
        for reference in references:
            etree.SubElement(etree.SubElement(settlement, 'InvoiceReferencedDocument'), 'IssuerAssignedID').text = reference
        return {
            'invoice': invoice, 'tree': tree, 'logs': [], 'file_document_sign': 1,
            'company': invoice.company_id, 'customer_values': {'customer': invoice.partner_id},
            'currency_values': {'currency': invoice.currency_id},
            'are_taxes_complete': False, 'to_write': {}, **kwargs,
        }

    def _deductions(self, values):
        invoice = values['invoice']
        original_lines = invoice.invoice_line_ids
        self.builder._import_cii_invoice_add_prepaid_amount(values)
        invoice.write(values['to_write'])
        self.builder._import_cii_invoice_fix_untaxed_amount(values)
        return invoice.invoice_line_ids - original_lines

    def test_cii_reference_scope_excludes_other_company_supplier_currency_type_cancel_and_self(self):
        target = self._bill(amounts=[1000])
        currency = self.env['res.currency'].with_context(active_test=False).search([('id', '!=', target.currency_id.id)], limit=1)
        currency.active = True
        excluded = self._bill(partner=self.partner_b)
        excluded |= self._bill(company=self.other_company_data['company'])
        excluded |= self._bill(currency=currency)
        excluded |= self._bill(move_type='out_invoice')
        excluded |= self._bill(move_type='out_refund')
        cancelled = self._bill()
        cancelled.button_cancel()
        excluded |= cancelled
        snapshot = (excluded | target).line_ids.read(['move_id', 'balance', 'amount_currency'])
        values = self._values(target)
        self.assertFalse(self._deductions(values))
        self.assertTrue(any('not found' in log for log in values['logs']))
        self.assertEqual((excluded | target).line_ids.read(['move_id', 'balance', 'amount_currency']), snapshot)

    def test_cii_collected_identity_is_authoritative_before_invoice_write(self):
        currency = self.env['res.currency'].with_context(active_test=False).search([('id', '!=', self.env.company.currency_id.id)], limit=1)
        currency.active = True
        source = self._bill(partner=self.partner_b, company=self.other_company_data['company'], currency=currency)
        target = self._bill(reference='TARGET', move_type='out_invoice')
        values = self._values(target, company=source.company_id,
                              customer_values={'customer': source.partner_id},
                              currency_values={'currency': currency}, to_write={'move_type': 'in_invoice'})
        self.builder._import_cii_invoice_add_prepaid_amount(values)
        self.assertEqual(values['downpayment_moves'], source)
        self.assertEqual(target.partner_id, self.partner_a)
        self.assertEqual(target.currency_id, self.env.company.currency_id)
        self.assertEqual(len(source.invoice_line_ids), 1)

    def test_cii_unknown_supplier_and_sales_target_do_not_deduct(self):
        self._bill()
        target = self._bill(reference='TARGET')
        target.partner_id = False
        self.assertFalse(self._deductions(self._values(target, customer_values={})))
        target.partner_id = self.partner_a
        self.assertFalse(self._deductions(self._values(target, to_write={'move_type': 'out_invoice'})))

    def test_cii_contact_and_identical_duplicate_references_deduct_once(self):
        contact = self.env['res.partner'].create({'name': 'Synthetic CII billing contact', 'parent_id': self.partner_a.id})
        source = self._bill(partner=contact)
        duplicate = source.copy({'ref': source.ref})
        target = self._bill(reference='TARGET', amounts=[1000])
        original_lines = source.line_ids
        deductions = self._deductions(self._values(target, references=(source.ref, source.ref)))
        self.assertRecordValues(deductions, [{'quantity': -1, 'price_unit': 100}])
        self.assertEqual(source.line_ids, original_lines)
        self.assertEqual(target.amount_untaxed, 900)
        self.assertEqual(duplicate.amount_untaxed, 100)

    def test_cii_conflicting_duplicates_are_refused_before_any_line_copy(self):
        valid = self._bill(reference='SYNTHETIC-FIRST')
        source = self._bill()
        conflicting = self._bill(amounts=[101])
        target = self._bill(reference='TARGET', amounts=[1000])
        with patch.object(type(self.env['account.move.line']), 'copy_data', autospec=True) as copy:
            with self.assertRaisesRegex(UserError, 'review the referenced downpayment'):
                self.builder._import_cii_invoice_add_prepaid_amount(self._values(target, references=(valid.ref, source.ref)))
            copy.assert_not_called()
        self.assertEqual(target.amount_untaxed, 1000)
        self.assertEqual(source.amount_untaxed, 100)
        self.assertEqual(conflicting.amount_untaxed, 101)

    def test_cii_posted_bill_and_refund_sources_remain_unchanged(self):
        for move_type, expected in (('in_invoice', -1), ('in_refund', 1)):
            with self.subTest(move_type=move_type):
                source = self._bill(reference='SYNTHETIC-' + move_type, move_type=move_type, post=True)
                snapshot = source.line_ids.read(['move_id', 'balance', 'amount_currency', 'matched_debit_ids', 'matched_credit_ids'])
                target = self._bill(reference='TARGET', amounts=[1000])
                deductions = self._deductions(self._values(target, references=(source.ref,)))
                self.assertRecordValues(deductions, [{'quantity': expected, 'price_unit': 100}])
                self.assertEqual(source.line_ids.read(['move_id', 'balance', 'amount_currency', 'matched_debit_ids', 'matched_credit_ids']), snapshot)
                self.assertEqual(source.state, 'posted')
                self.assertEqual(target.amount_untaxed, 1000 + expected * 100)
                self.assertAlmostEqual(sum(target.line_ids.mapped('balance')), 0)

    def test_cii_foreign_deduction_uses_target_rate_and_preserves_source(self):
        currency = self.env['res.currency'].with_context(active_test=False).search([('id', '!=', self.env.company.currency_id.id)], limit=1)
        currency.active = True
        self.env['res.currency.rate'].create([
            {'currency_id': currency.id, 'company_id': self.env.company.id, 'name': '2019-01-01', 'rate': 2},
            {'currency_id': currency.id, 'company_id': self.env.company.id, 'name': '2019-02-01', 'rate': 4},
        ])
        source = self._bill(currency=currency, invoice_date='2019-01-02', post=True)
        target = self._bill(reference='TARGET', currency=currency, invoice_date='2019-02-02', amounts=[1000])
        snapshot = source.line_ids.read(['move_id', 'balance', 'amount_currency'])
        deductions = self._deductions(self._values(target))
        self.assertEqual(deductions.amount_currency, -100)
        self.assertAlmostEqual(deductions.balance, -100 / target.invoice_currency_rate)
        self.assertNotEqual(deductions.balance, -source.invoice_line_ids.balance)
        self.assertEqual(source.line_ids.read(['move_id', 'balance', 'amount_currency']), snapshot)
        self.assertEqual(target.amount_total, 900)
        self.assertAlmostEqual(sum(target.line_ids.mapped('balance')), 0)

    def test_cii_manual_tax_deduction_preserves_tax_and_source(self):
        source = self._manual_tax_source()
        source.action_post()
        snapshot = source.line_ids.read(['move_id', 'balance', 'amount_currency', 'extra_tax_data'])
        target = self._bill(reference='TARGET', amounts=[1000])
        deductions = self._deductions(self._values(target))
        self.assertEqual(deductions.quantity, -1)
        self.assertEqual(deductions.price_total, -117)
        self.assertEqual(target.amount_total, 883)
        self.assertEqual(deductions.extra_tax_data['quantity'], -1)
        self.assertEqual(source.line_ids.read(['move_id', 'balance', 'amount_currency', 'extra_tax_data']), snapshot)
        self.assertAlmostEqual(sum(target.line_ids.mapped('balance')), 0)

    def test_cii_manual_tax_conflicts_are_refused_before_copy(self):
        source = self._manual_tax_source()
        duplicate = source.copy({'ref': source.ref})
        data = dict(duplicate.invoice_line_ids.extra_tax_data)
        data['manual_tax_amounts'] = {
            tax_id: {**amounts, 'tax_amount_currency': 18, 'tax_amount': 18}
            for tax_id, amounts in data['manual_tax_amounts'].items()
        }
        duplicate.invoice_line_ids.extra_tax_data = data
        target = self._bill(reference='TARGET', amounts=[1000])
        with patch.object(type(self.env['account.move.line']), 'copy_data', autospec=True) as copy:
            with self.assertRaisesRegex(UserError, 'review the referenced downpayment'):
                self.builder._import_cii_invoice_add_prepaid_amount(self._values(target))
            copy.assert_not_called()

    def test_cii_analytic_conflicts_are_refused_before_copy(self):
        plan = self.env['account.analytic.plan'].create({'name': 'Synthetic CII allocation'})
        accounts = self.env['account.analytic.account'].create([
            {'name': 'Synthetic CII allocation A', 'plan_id': plan.id, 'company_id': self.env.company.id},
            {'name': 'Synthetic CII allocation B', 'plan_id': plan.id, 'company_id': self.env.company.id},
        ])
        source = self._bill()
        source.invoice_line_ids.analytic_distribution = {str(accounts[0].id): 100}
        duplicate = source.copy({'ref': source.ref})
        duplicate.invoice_line_ids.analytic_distribution = {str(accounts[1].id): 100}
        target = self._bill(reference='TARGET', amounts=[1000])
        with patch.object(type(self.env['account.move.line']), 'copy_data', autospec=True) as copy:
            with self.assertRaisesRegex(UserError, 'review the referenced downpayment'):
                self.builder._import_cii_invoice_add_prepaid_amount(self._values(target))
            copy.assert_not_called()
