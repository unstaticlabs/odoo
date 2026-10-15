import json

from odoo import _, api, models
from odoo.exceptions import UserError
from odoo.tools.misc import formatLang

from odoo.addons.account_edi_ubl_cii.models.account_edi_common import FloatFmt

PDP_CUSTOMIZATION_ID = 'urn:cen.eu:en16931:2017#conformant#urn.cpro.gouv.fr:1p0:extended-ctc-fr'  # Not accepted by SuperPDP due to missing validator

CPRO_CUSTOMIZATION_ID = 'urn:cen.eu:en16931:2017#conformant#urn.cpro.gouv.fr:1p0:extended-ctc-fr'
CPRO_INVOICE_IDENTIFIER = f'busdox-docid-qns::urn:oasis:names:specification:ubl:schema:xsd:Invoice-2::Invoice##{CPRO_CUSTOMIZATION_ID}::2.1'

PAID_STATES = frozenset({'in_payment', 'paid'})


class AccountEdiXmlUbl21Fr(models.AbstractModel):
    _name = "account.edi.xml.ubl_21_fr"
    _inherit = 'account.edi.xml.ubl_bis3'
    _description = "France UBL 2.1 E-Invoicing Format"

    @api.model
    def _pdp_can_invoice_b2g(self, customer):
        # We use fields added in this module for B2G
        return self.env['ir.module.module']._get('l10n_fr_facturx_chorus_pro').state == 'installed'

    @api.model
    def _pdp_is_b2g(self, customer):
        # We put the identifier for chorus pro invoices in `peppol_supported_documents`
        # to mark the partner as B2G / "behind Chorus Pro"
        return CPRO_INVOICE_IDENTIFIER in (customer.peppol_supported_documents or [])

    @api.model
    def _pdp_needs_b2g_fields(self, customer):
        return self._pdp_can_invoice_b2g(customer) and self._pdp_is_b2g(customer)

    # -------------------------------------------------------------------------
    # EXPORT
    # -------------------------------------------------------------------------

    def _export_invoice_filename(self, invoice):
        return f"{invoice.name.replace('/', '_')}_ubl_21_fr.xml"

    def _export_invoice_constraints(self, invoice, vals):
        # EXTENDS account.edi.xml.ubl_bis3
        constraints = super()._export_invoice_constraints(invoice, vals)

        for partner_type in ('supplier', 'customer'):
            partner = vals[partner_type]
            commercial_partner = partner.commercial_partner_id
            if commercial_partner.peppol_eas != '0225' or not commercial_partner.peppol_endpoint:
                constraints[f"ubl_21_fr_{partner_type}_pdp_identifier_required"] = self.env._("The following partner's French e-invoicing identifier is missing: %s", commercial_partner.display_name)
            id_type, id_value = commercial_partner._l10n_fr_pdp_get_base_identifier()
            if not id_type or not id_value:
                constraints[f"ubl_21_fr_{partner_type}_identifier_required"] = self.env._("The following partner's SIREN or SIRET is missing: %s", commercial_partner.display_name)

        if vals['document_type'] == 'credit_note' and not (invoice.reversed_entry_id.name or invoice.reversed_entry_id.invoice_date):
            constraints[f"ubl_21_fr_{partner_type}_refund_invoice_reference"] = self.env._("You cannot create a Credit Note without an original invoice: %s", vals['invoice'].name)

        customer = vals['customer'].commercial_partner_id
        if self._pdp_is_b2g(customer) and not self._pdp_can_invoice_b2g(customer):
            cpro_module_name = self.env['ir.module.module'].sudo()._get('l10n_fr_facturx_chorus_pro').display_name
            constraints["ubl_21_fr_cpro_module_missing"] = self.env._("This partner is behind Chorus PRO. Please install the module '%s'", cpro_module_name)

        return constraints

    def _add_invoice_header_nodes(self, document_node, vals):
        # EXTENDS account.edi.xml.ubl_bis3
        customer = vals['customer'].commercial_partner_id
        b2g = self._pdp_needs_b2g_fields(customer)

        invoice = vals['invoice']
        super()._add_invoice_header_nodes(document_node, vals)

        profile_id = self._l10n_fr_pdp_get_profile_id(vals)
        document_node.update({
            'cbc:CustomizationID': {'_text': CPRO_CUSTOMIZATION_ID if b2g else PDP_CUSTOMIZATION_ID},
            'cbc:ProfileID': {'_text': profile_id},
        })

        # [BR-FR-05] Add mandatory notes with defaults if not already present
        # Initialize / Listify 'cbc:Note'
        existing_note = document_node.get('cbc:Note')
        if not existing_note or not isinstance(document_node.get('cbc:Note'), list):
            document_node['cbc:Note'] = [existing_note] if existing_note else []
        # Add default notes
        for code, default_content in self._get_default_notes(vals).items():
            document_node['cbc:Note'].append({
                '_text': f"#{code}#{default_content}",
            })

        # [BR-FR-MAP-18] EN16931 caps the Note (BT-22) to 1024 characters.
        for note in document_node['cbc:Note']:
            if isinstance(note['_text'], str):
                note['_text'] = note['_text'][:1024]

        # Règles de gestion G1.52
        if vals['document_type'] == 'credit_note':
            document_node['cac:BillingReference'] = {
                'cac:InvoiceDocumentReference': {
                    'cbc:ID': {'_text': invoice.reversed_entry_id.name},
                    'cbc:IssueDate': {'_text': invoice.reversed_entry_id.invoice_date},
                }
            }

        # [BR-FR-CO-09/BT-23] : Si le cadre de facturation (BT-23) est B2, S2 ou M2, alors la date d'échéance (BT-9) doit être renseignée et correspondre à la date de paiement.
        # For credit notes, this is handled in `_add_invoice_payment_means_nodes` instead, as `cac:PaymentMeans` is
        # not populated yet at this point.
        if profile_id in ('B2', 'S2', 'M2') and vals['document_type'] != 'credit_note':
            document_node['cbc:DueDate'] = {'_text': invoice._pdp_get_payment_date() or invoice.invoice_date}

        # Profile ID X4 - Final invoice with downpayments
        if profile_id in ('B4', 'S4', 'M4'):
            downpayment_moves = invoice.invoice_line_ids._get_downpayment_lines().move_id.filtered(lambda m: m != invoice)
            document_node['cac:BillingReference'] = []
            for downpayment_move in downpayment_moves:
                document_node['cac:BillingReference'].append({
                    'cac:InvoiceDocumentReference': {
                        'cbc:ID': {'_text': downpayment_move.name},
                        'cbc:IssueDate': {'_text': downpayment_move.invoice_date},
                    },
                })

        # B2G
        if not b2g:
            return

        if invoice.buyer_reference:
            document_node['cbc:BuyerReference'] = {'_text': invoice.buyer_reference}

        if invoice.purchase_order_reference:
            document_node['cac:OrderReference'] = {
                'cbc:ID': {'_text': invoice.purchase_order_reference}
            }

    def _add_invoice_payment_means_nodes(self, document_node, vals):
        # EXTENDS account.edi.xml.ubl_bis3
        super()._add_invoice_payment_means_nodes(document_node, vals)

        if vals['document_type'] != 'credit_note':
            return

        profile_id = self._l10n_fr_pdp_get_profile_id(vals)
        # [BR-FR-CO-09/BT-23] : Si le cadre de facturation (BT-23) est B2, S2 ou M2, alors la date d'échéance (BT-9) doit être renseignée et correspondre à la date de paiement.
        if profile_id in ('B2', 'S2', 'M2'):
            invoice = vals['invoice']
            payment_due_date = invoice._pdp_get_payment_date() or invoice.invoice_date
            for node in document_node['cac:PaymentMeans']:
                node['cbc:PaymentDueDate'] = {'_text': payment_due_date}

    def _ubl_add_invoice_type_code_node(self, vals):
        # Override account_edi_ubl: [BR-FR-04] Downpayment code for invoice is 386
        invoice = vals['invoice']
        vals['document_node']['cbc:InvoiceTypeCode'] = {'_text': None}
        if self._is_document(vals, 'invoice') and invoice._is_downpayment():
            vals['document_node']['cbc:InvoiceTypeCode']['_text'] = 386
        else:
            super()._ubl_add_invoice_type_code_node(vals)

    def _ubl_add_credit_note_type_code_node(self, vals):
        # Override account_edi_ubl: [BR-FR-04] Downpayment code for credit note is 503
        invoice = vals['invoice']
        vals['document_node']['cbc:CreditNoteTypeCode'] = {'_text': None}
        if self._is_document(vals, 'credit_note') and invoice._is_downpayment():
            vals['document_node']['cbc:CreditNoteTypeCode']['_text'] = 503
        else:
            super()._ubl_add_credit_note_type_code_node(vals)

    def _ubl_add_party_identification_nodes(self, vals):
        super()._ubl_add_party_identification_nodes(vals)
        partner = vals['party_vals']['partner']
        commercial_partner = partner.commercial_partner_id

        id_type, party_id = commercial_partner._l10n_fr_pdp_get_base_identifier()
        if id_type == 'siret':
            party_id_scheme = "0009"
        else:  # id_type == 'siren'
            party_id_scheme = "0002"
        # [UBL-SR-16] Buyer identifier shall occur maximum once
        vals['party_node']['cac:PartyIdentification'] = {
            'cbc:ID': {'_text': party_id, 'schemeID': party_id_scheme},
        }

    def _ubl_add_party_legal_entity_nodes(self, vals):
        # EXTENDS account.edi.xml.ubl_bis3
        super()._ubl_add_party_legal_entity_nodes(vals)
        partner = vals['party_vals']['partner']
        commercial_partner = partner.commercial_partner_id

        vals['party_node']['cac:PartyLegalEntity'] = {
            'cbc:RegistrationName': {'_text': commercial_partner.name},
            'cbc:CompanyID': {
                '_text': commercial_partner._l10n_fr_pdp_get_siren(),
                'schemeID': '0002',
            },
        }

        # B2G
        customer = vals['customer'].commercial_partner_id
        if not self._pdp_needs_b2g_fields(customer):
            return

        id_type, party_id = commercial_partner._l10n_fr_pdp_get_base_identifier()
        if id_type == 'siret':
            vals['party_node']['cac:PartyLegalEntity'] = [{
                'cbc:RegistrationName': {'_text': commercial_partner.name},
                'cbc:CompanyID': {
                    '_text': party_id,
                    'schemeID': '0009',
                },
            }]

    def _ubl_add_line_price_node(self, vals, in_foreign_currency=True):
        # OVERRIDE
        line_node = vals['line_node']
        base_line = vals['line_vals']['base_line']
        suffix = '_currency' if in_foreign_currency else ''
        currency = base_line['currency_id'] if in_foreign_currency else vals['company_currency']
        price_amount = base_line['tax_details'][f'raw_gross_price_unit{suffix}']

        line_node['cac:Price'] = {
            'cbc:PriceAmount': {
                '_text': FloatFmt(price_amount, min_dp=1, max_dp=6),
                'currencyID': currency.name,
            },
            'cac:AllowanceCharge': {
                "cbc:ChargeIndicator": [{
                    "_text": 'false',
                }],
                # Discount amount
                "cbc:Amount": [{
                    "_text": FloatFmt(0, min_dp=1, max_dp=6),
                    "currencyID": currency.name,
                }],
                # Pre-discount amount
                'cbc:BaseAmount': {
                    '_text': FloatFmt(price_amount, min_dp=1, max_dp=6),
                    'currencyID': currency.name,
                },
            }
        }

    def _import_ubl_invoice_add_prepaid_amount(self, collected_values):
        # imported invoice is a final invoice following downpayments.
        # We assume all billing references are references to downpayments.
        invoice = collected_values['invoice']
        currency = collected_values['currency_values']['currency']
        tree = collected_values['tree']
        if tree.findtext('./{*}ProfileID') in ('B4', 'S4', 'M4'):
            prepaid_amount = float(tree.findtext('./{*}LegalMonetaryTotal/{*}PrepaidAmount') or 0)
            collected_values['prepaid_amount'] = prepaid_amount
            move_type = collected_values.get('to_write', {}).get('move_type', invoice.move_type)
            if move_type in ('in_invoice', 'in_refund') and not currency.is_zero(prepaid_amount):
                downpayments_names = tree.findall('./{*}BillingReference/{*}InvoiceDocumentReference/{*}ID')
                company = collected_values.get('company') or invoice.company_id
                to_write = collected_values.get('to_write', {})
                supplier = collected_values.get('customer_values', {}).get('customer')
                if not supplier and to_write.get('partner_id'):
                    supplier = self.env['res.partner'].browse(to_write['partner_id'])
                supplier = supplier or invoice.partner_id
                downpayments = self.env['account.move']
                if supplier:
                    downpayments = downpayments.search([
                        ('ref', 'in', [reference.text for reference in downpayments_names]),
                        ('company_id', '=', company.id),
                        ('commercial_partner_id', '=', supplier.commercial_partner_id.id),
                        ('currency_id', '=', currency.id),
                        ('move_type', 'in', ('in_invoice', 'in_refund')),
                        ('state', 'in', ('draft', 'posted')),
                        ('id', '!=', invoice.id),
                    ], order='id')
                grouped_downpayments = downpayments.grouped('ref')

                def financial_signature(move):
                    return (move.move_type, tuple(sorted(
                        (
                            line.product_id.id, line.account_id.id, tuple(sorted(line.tax_ids.ids)),
                            line.quantity, line.price_unit, line.discount, line.display_type or '',
                            line.currency_id.id,
                            json.dumps(line.extra_tax_data or {}, sort_keys=True),
                            json.dumps(line.analytic_distribution or {}, sort_keys=True),
                        )
                        for line in move.invoice_line_ids
                    )))

                # Validate every reference before copying any lines. Repeated imports with
                # identical financial content count once; conflicting bills need review.
                for reference, candidates in grouped_downpayments.items():
                    if len({financial_signature(candidate) for candidate in candidates}) > 1:
                        raise UserError(_(
                            "Several different downpayments use reference %(reference)s. "
                            "Please review the referenced downpayment before importing this invoice.",
                            reference=reference,
                        ))

                collected_values['downpayment_moves'] = self.env['account.move'].browse([
                    candidates[0].id for candidates in grouped_downpayments.values()
                ])

                # logging
                formatted_amount = formatLang(self.env, prepaid_amount, currency_obj=currency)
                collected_values['logs'].append(_("A downpayment of %s was detected.", formatted_amount))
                for downpayment_name in downpayments_names:
                    if downpayment_name.text not in grouped_downpayments:
                        collected_values['logs'].append(_("Downpayment %s not found. Imported amounts will probably be incorrect.", downpayment_name.text))
        super()._import_ubl_invoice_add_prepaid_amount(collected_values)

    def _import_ubl_invoice_fix_untaxed_amount(self, collected_values):
        # Downpayments may be reported as a prepaid amount.
        # In this case, the final invoice untaxed amount, tax amount and total amount
        # correspond to the amounts without any downpayments made.
        # We need to add the downpayment lines as negative lines in the final invoice
        # because we already imported and accounted for the downpayments.
        super()._import_ubl_invoice_fix_untaxed_amount(collected_values)
        invoice = collected_values['invoice']
        if downpayments := collected_values.get('downpayment_moves'):
            container = {'records': invoice}
            with (
                invoice._check_balanced(container),
                invoice._disable_discount_precision(),
                invoice._sync_dynamic_lines(container),
            ):
                for downpayment in downpayments:
                    for line in downpayment.invoice_line_ids:
                        line_values = line.copy_data({
                            'move_id': invoice.id,
                            'quantity': -line.quantity if downpayment.move_type == 'in_invoice' else line.quantity,
                        })[0]
                        if downpayment.move_type == 'in_invoice':
                            line_values['extra_tax_data'] = self.env['account.tax']._reverse_quantity_base_line_extra_tax_data(line.extra_tax_data)
                        # The source currency amount has the source document's sign.
                        # Let native invoice creation recompute it from the deduction.
                        line_values.pop('amount_currency', None)
                        self.env['account.move.line'].create(line_values)
