import base64
import binascii
import hashlib
import io
import re

from odoo import api, fields, models, _
from odoo.exceptions import UserError, ValidationError
from odoo.tools import html2plaintext
from odoo.tools.misc import formatLang, format_amount, format_date
from odoo.tools.pdf import OdooPdfFileReader

PROVENANCE_PATTERN = re.compile(
    r"Template invoice\.v1@(?P<revision>[^;]+); "
    r"payload sha256:(?P<digest>[0-9a-f]{64}); "
    r"engine usl-document-renderer/(?P<version>[0-9.]+)"
)


class ResCompany(models.Model):
    _inherit = "res.company"

    usl_invoice_late_penalty_text = fields.Char(
        string="Late-payment penalty wording",
        help="Rate or calculation rule printed on French customer invoices.",
    )
    usl_invoice_recovery_fee = fields.Monetary(
        string="Recovery fee",
        currency_field="currency_id",
        default=40.0,
        help="Fixed indemnity for recovery costs printed on French business invoices.",
    )

    @api.constrains("usl_invoice_recovery_fee")
    def _check_usl_invoice_recovery_fee(self):
        for company in self:
            if company.usl_invoice_recovery_fee < 0:
                raise ValidationError(_("The invoice recovery fee cannot be negative."))


class ResConfigSettings(models.TransientModel):
    _inherit = "res.config.settings"

    usl_invoice_late_penalty_text = fields.Char(
        related="company_id.usl_invoice_late_penalty_text",
        readonly=False,
    )
    usl_invoice_recovery_fee = fields.Monetary(
        related="company_id.usl_invoice_recovery_fee",
        currency_field="usl_document_currency_id",
        readonly=False,
    )


class AccountMove(models.Model):
    _inherit = "account.move"

    @staticmethod
    def _usl_invoice_address_lines(partner):
        return [
            line
            for line in (
                partner.street,
                partner.street2,
                " ".join(value for value in (partner.zip, partner.city) if value),
                partner.state_id.name,
                partner.country_id.name,
            )
            if line
        ]

    def _usl_invoice_partner_payload(self, locale):
        self.ensure_one()
        document_env = self.with_context(lang=locale).env
        partner = self.commercial_partner_id
        address_lines = self._usl_invoice_address_lines(partner)
        if not partner.name or not partner.street or not partner.zip or not partner.city:
            raise UserError(
                _(
                    "The invoice recipient needs a name and complete postal address "
                    "before an official invoice can be generated."
                )
            )
        if partner.vat:
            address_lines.append(document_env._("VAT: %s", partner.vat))
        if partner.country_id.code == "FR" and partner.is_company:
            registry = re.sub(r"\D", "", partner.company_registry or "")
            if len(registry) not in {9, 14}:
                raise UserError(
                    _(
                        "Set the French customer SIREN or SIRET before generating "
                        "an official invoice."
                    )
                )
            address_lines.append(document_env._("SIREN: %s", registry[:9]))
        return {"name": partner.name, "address_lines": address_lines}

    def _usl_invoice_operation_category(self, locale):
        self.ensure_one()
        tax_scopes = set(self.invoice_line_ids.tax_ids.mapped("tax_scope"))
        has_services = "service" in tax_scopes
        has_goods = "consu" in tax_scopes
        if not has_services and not has_goods:
            product_types = set(
                self.invoice_line_ids.filtered(
                    lambda line: line.display_type
                    not in {"line_section", "line_subsection", "line_note"}
                    and line.product_id
                ).product_id.mapped("type")
            )
            has_services = "service" in product_types
            has_goods = bool(product_types - {"service"})
        if not has_services and not has_goods:
            raise UserError(
                _(
                    "Classify invoice lines with service or goods products/taxes "
                    "before generating a French official invoice."
                )
            )
        if locale == "fr_FR":
            return (
                "prestations de services et livraisons de biens"
                if has_services and has_goods
                else "prestations de services"
                if has_services
                else "livraisons de biens"
            )
        return (
            "services and goods"
            if has_services and has_goods
            else "services"
            if has_services
            else "goods"
        )

    def _usl_invoice_metadata(self, locale, *, proforma=False):
        self.ensure_one()
        metadata = []
        supply_date = self.delivery_date or self.invoice_date
        if supply_date:
            label = "Date de livraison / prestation" if locale == "fr_FR" else "Supply date"
            metadata.append(
                f"{label}: {format_date(self.env, supply_date, lang_code=locale)}"
            )
        if self.invoice_origin:
            label = "Commande" if locale == "fr_FR" else "Purchase order"
            metadata.append(f"{label}: {self.invoice_origin}")
        if self.move_type == "out_refund" and self.reversed_entry_id:
            label = "Corrige" if locale == "fr_FR" else "Corrects"
            metadata.append(f"{label}: {self.reversed_entry_id.name}")
        if self.company_id.country_id.code == "FR" and not proforma:
            label = "Nature des opérations" if locale == "fr_FR" else "Transaction type"
            metadata.append(f"{label}: {self._usl_invoice_operation_category(locale)}")
        shipping = self.partner_shipping_id
        if shipping and shipping not in {self.partner_id, self.commercial_partner_id}:
            if not shipping.street or not shipping.zip or not shipping.city:
                raise UserError(
                    _("The distinct delivery address must be complete before rendering.")
                )
            label = "Livraison" if locale == "fr_FR" else "Delivery"
            metadata.append(
                f"{label}: {', '.join(self._usl_invoice_address_lines(shipping))}"
            )
        return metadata

    def _usl_invoice_legal_mentions(self, locale, *, proforma=False):
        self.ensure_one()
        document_env = self.with_context(lang=locale).env
        mentions = []
        if self.company_id.country_id.code == "FR" and not proforma:
            if not self.company_id.usl_invoice_late_penalty_text:
                raise UserError(
                    _(
                        "Configure the late-payment penalty wording in Document Templates "
                        "settings before generating a French invoice."
                    )
                )
            fee = self.company_id.currency_id.format(
                self.company_id.usl_invoice_recovery_fee
            )
            if locale == "fr_FR":
                mentions.extend(
                    [
                        document_env._(
                            "Pénalités de retard : %s.",
                            self.company_id.usl_invoice_late_penalty_text,
                        ),
                        document_env._(
                            "Indemnité forfaitaire pour frais de recouvrement en cas "
                            "de retard de paiement : %s.",
                            fee,
                        ),
                    ]
                )
            else:
                mentions.extend(
                    [
                        document_env._(
                            "Late-payment penalties: %s.",
                            self.company_id.usl_invoice_late_penalty_text,
                        ),
                        document_env._(
                            "Fixed recovery-cost indemnity for late payment: %s.", fee
                        ),
                    ]
                )
            if not self.invoice_payment_term_id.note:
                mentions.append(
                    "Pas d’escompte pour paiement anticipé."
                    if locale == "fr_FR"
                    else "No discount for early payment."
                )
            if any(
                line.product_id.type == "service"
                and any(
                    tax.tax_exigibility == "on_invoice" and tax.tax_scope == "consu"
                    for tax in line.tax_ids
                )
                for line in self.invoice_line_ids
            ):
                mentions.append(
                    "Option pour le paiement de la taxe d’après les débits."
                    if locale == "fr_FR"
                    else "VAT payment option on debits."
                )
        for value in (
            self.fiscal_position_id.note,
            self.taxes_legal_notes,
            self.narration,
        ):
            text = html2plaintext(value or "").strip()
            if text:
                mentions.append(text)
        if proforma:
            mentions.insert(
                0,
                (
                    document_env._("Pro forma document - not an accounting invoice.")
                    if locale == "en_US"
                    else document_env._(
                        "Document pro forma - ne constitue pas une facture comptable."
                    )
                ),
            )
        return mentions

    def _usl_invoice_payment_text(self, locale):
        self.ensure_one()
        document_env = self.with_context(lang=locale).env
        parts = []
        if self.invoice_payment_term_id:
            parts.append(self.invoice_payment_term_id.display_name)
            if self.invoice_payment_term_id.note:
                parts.append(html2plaintext(self.invoice_payment_term_id.note).strip())
        if self.payment_term_details and len(self.payment_term_details) > 1:
            for index, term in enumerate(self.payment_term_details, start=1):
                parts.append(
                    document_env._(
                        "Installment %(number)s: %(amount)s due %(date)s",
                        number=index,
                        amount=format_amount(
                            self.env,
                            term["amount"],
                            self.currency_id,
                            lang_code=locale,
                        ),
                        date=format_date(
                            self.env,
                            term["date"],
                            lang_code=locale,
                        ),
                    )
                )
        if self.payment_reference:
            parts.append(
                document_env._("Payment reference: %s", self.payment_reference)
            )
        if self.partner_bank_id:
            parts.append(
                document_env._("Bank account: %s", self.partner_bank_id.acc_number)
            )
        return "\n".join(part for part in parts if part)

    def _usl_invoice_qr_asset(self):
        self.ensure_one()
        data_uri = None
        if self.display_qr_code and self.amount_residual:
            data_uri = self._generate_qr_code(silent_errors=True)
        elif self.display_link_qr_code and self.amount_residual:
            data_uri = self._generate_portal_payment_qr()
        if not data_uri or "," not in data_uri:
            return None, []
        header, encoded = data_uri.split(",", 1)
        mimetype = "image/png" if "image/png" in header else "image/jpeg"
        try:
            content = base64.b64decode(encoded, validate=True)
        except (binascii.Error, ValueError):
            return None, []
        digest = hashlib.sha256(content).hexdigest()
        return digest, [
            {
                "sha256": digest,
                "mime_type": mimetype,
                "data": base64.b64encode(content).decode(),
            }
        ]

    def _usl_document_render_payload(self, _report, template, data, locale):
        self.ensure_one()
        data = data or {}
        localized_move = self.with_context(lang=locale)
        locale_env = localized_move.env
        if template.key != "invoice.v1":
            raise UserError(_("Invoices can only use invoice.v1."))
        if not self.is_sale_document():
            raise UserError(
                _("Vendor originals and non-customer accounting documents remain source passthrough.")
            )
        proforma = bool(data.get("proforma") or data.get("proforma_invoice"))
        if not proforma and self.state != "posted":
            raise UserError(
                _("Post the invoice or use Pro Forma before generating this document.")
            )
        if not proforma and not self.invoice_date:
            raise UserError(_("Set the invoice date before generating the official invoice."))
        document_lines = []
        for line in localized_move.invoice_line_ids:
            if line.display_type in {"line_section", "line_subsection", "line_note"}:
                document_lines.append(
                    {
                        "kind": (
                            "section"
                            if line.display_type in {"line_section", "line_subsection"}
                            else "note"
                        ),
                        "description": line.name or "",
                        "quantity": "",
                        "unit_price": "",
                        "discount": "",
                        "taxes": "",
                        "total": "",
                    }
                )
                continue
            document_lines.append(
                {
                    "kind": "line",
                    "description": line.name or line.product_id.display_name,
                    "quantity": formatLang(locale_env, line.quantity),
                    "unit_price": format_amount(
                        self.env,
                        line.price_unit,
                        self.currency_id,
                        lang_code=locale,
                    ),
                    "discount": (
                        f"{formatLang(locale_env, line.discount)} %"
                        if line.discount
                        else ""
                    ),
                    "taxes": ", ".join(line.tax_ids.mapped("name")),
                    "total": format_amount(
                        self.env,
                        line.price_subtotal,
                        self.currency_id,
                        lang_code=locale,
                    ),
                }
            )
        if not any(line["kind"] == "line" for line in document_lines):
            raise UserError(_("The invoice needs at least one printable invoice line."))
        totals = [
            {
                "label": locale_env._("Untaxed amount"),
                "amount": format_amount(
                    self.env,
                    self.amount_untaxed,
                    self.currency_id,
                    lang_code=locale,
                ),
            }
        ]
        for subtotal in (self.tax_totals or {}).get("subtotals", []):
            for tax_group in subtotal.get("tax_groups", []):
                totals.append(
                    {
                        "label": tax_group["group_name"],
                        "amount": format_amount(
                            self.env,
                            tax_group["tax_amount_currency"],
                            self.currency_id,
                            lang_code=locale,
                        ),
                    }
                )
        totals.append(
            {
                "label": locale_env._("Total"),
                "amount": format_amount(
                    self.env,
                    self.amount_total,
                    self.currency_id,
                    lang_code=locale,
                ),
            }
        )
        if self.amount_residual != self.amount_total:
            totals.append(
                {
                    "label": locale_env._("Amount due"),
                    "amount": format_amount(
                        self.env,
                        self.amount_residual,
                        self.currency_id,
                        lang_code=locale,
                    ),
                }
            )
        qr_digest, qr_assets = self._usl_invoice_qr_asset()
        date = self.invoice_date or fields.Date.context_today(self)
        customer = localized_move._usl_invoice_partner_payload(locale)
        payload = {
            "kind": (
                "proforma"
                if proforma
                else "credit_note"
                if self.move_type == "out_refund"
                else "invoice"
            ),
            "number": (
                self.name
                if self.name and self.name != "/"
                else self._get_report_base_filename()
            ),
            "date": format_date(self.env, date, lang_code=locale),
            "due_date_label": locale_env._("Due:"),
            "due_date": (
                format_date(self.env, self.invoice_date_due, lang_code=locale)
                if self.invoice_date_due
                else ""
            ),
            "metadata": localized_move._usl_invoice_metadata(
                locale, proforma=proforma
            ),
            "customer": customer,
            "lines": document_lines,
            "totals": totals,
            "payment_terms": localized_move._usl_invoice_payment_text(locale),
            "notes": "",
            "legal_mentions": localized_move._usl_invoice_legal_mentions(
                locale,
                proforma=proforma,
            ),
            "qr_asset": qr_digest,
        }
        return payload, qr_assets


class AccountMoveSend(models.AbstractModel):
    _inherit = "account.move.send"

    @api.model
    def _usl_invoice_attachment_provenance(self, invoice, values):
        raw = values.get("raw")
        if not raw:
            return
        try:
            metadata = OdooPdfFileReader(io.BytesIO(raw), strict=False).metadata or {}
            subject = str(metadata.get("/Subject") or "")
        except Exception:
            return
        match = PROVENANCE_PATTERN.fullmatch(subject)
        if not match:
            return
        values.update(
            {
                "usl_document_template_id": self.env.ref(
                    "usl_document_templates.template_invoice_v1"
                ).id,
                "usl_document_template_revision": match.group("revision"),
                "usl_document_payload_sha256": match.group("digest"),
                "usl_document_renderer_version": match.group("version"),
                "usl_document_company_id": invoice.company_id.id,
                "usl_document_rendered_at": fields.Datetime.now(),
            }
        )

    @api.model
    def _prepare_invoice_pdf_report(self, invoices_data):
        result = super()._prepare_invoice_pdf_report(invoices_data)
        for invoice, invoice_data in invoices_data.items():
            values = invoice_data.get("pdf_attachment_values")
            if values:
                self._usl_invoice_attachment_provenance(invoice, values)
        return result

    @api.model
    def _prepare_invoice_proforma_pdf_report(self, invoice, invoice_data):
        result = super()._prepare_invoice_proforma_pdf_report(invoice, invoice_data)
        values = invoice_data.get("proforma_pdf_attachment_values")
        if values:
            self._usl_invoice_attachment_provenance(invoice, values)
        return result
