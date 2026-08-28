import io
import zipfile

from odoo import fields, models, _
from odoo.exceptions import UserError


class ResConfigSettings(models.TransientModel):
    _inherit = "res.config.settings"

    usl_document_currency_id = fields.Many2one(
        string="Document currency",
        related="company_id.currency_id",
        readonly=True,
    )
    usl_document_legal_form = fields.Char(
        related="company_id.usl_document_legal_form",
        readonly=False,
    )
    usl_document_share_capital = fields.Monetary(
        related="company_id.usl_document_share_capital",
        currency_field="usl_document_currency_id",
        readonly=False,
    )
    usl_document_rcs_city = fields.Char(
        related="company_id.usl_document_rcs_city",
        readonly=False,
    )
    usl_document_identity_ready = fields.Boolean(
        related="company_id.usl_document_identity_ready",
    )
    usl_document_identity_message = fields.Char(
        related="company_id.usl_document_identity_message",
    )
    usl_document_renderer_status = fields.Selection(
        related="company_id.usl_document_renderer_status",
    )
    usl_document_renderer_checked_at = fields.Datetime(
        related="company_id.usl_document_renderer_checked_at",
    )
    usl_document_renderer_revision = fields.Char(
        related="company_id.usl_document_renderer_revision",
    )
    usl_document_renderer_version = fields.Char(
        related="company_id.usl_document_renderer_version",
    )
    usl_document_renderer_message = fields.Char(
        related="company_id.usl_document_renderer_message",
    )
    usl_document_renderer_url = fields.Char(
        string="Renderer URL",
        config_parameter="usl_document_templates.renderer_url",
    )
    usl_document_renderer_expected_revision = fields.Char(
        string="Pinned template revision",
        config_parameter="usl_document_templates.renderer_expected_revision",
    )
    usl_document_renderer_ca_path = fields.Char(
        string="Client CA path",
        config_parameter="usl_document_templates.renderer_ca_path",
    )
    usl_document_renderer_certificate_path = fields.Char(
        string="Client certificate path",
        config_parameter="usl_document_templates.renderer_certificate_path",
    )
    usl_document_renderer_private_key_path = fields.Char(
        string="Client private-key path",
        config_parameter="usl_document_templates.renderer_private_key_path",
    )
    usl_document_renderer_timeout = fields.Integer(
        string="Renderer timeout",
        config_parameter="usl_document_templates.renderer_timeout",
        default=35,
    )

    def action_usl_document_check_renderer(self):
        self.ensure_one()
        healthy = self.company_id.action_usl_document_check_renderer()
        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {
                "title": (
                    _("Renderer is healthy")
                    if healthy
                    else _("Renderer is unavailable")
                ),
                "message": self.company_id.usl_document_renderer_message,
                "type": "success" if healthy else "danger",
                "sticky": not healthy,
            },
        }

    def _preview_documents(self, locale):
        document_env = self.with_context(lang=locale).env
        today = fields.Date.to_string(fields.Date.context_today(self))
        return {
            "invoice.v1": {
                "qualification_label": document_env._(
                    "SYNTHETIC QUALIFICATION SAMPLE - NOT A REAL DOCUMENT"
                ),
                "kind": "invoice",
                "number": "PREVIEW/2026/0001",
                "date": today,
                "due_date_label": document_env._("Due:"),
                "due_date": today,
                "customer": {
                    "name": document_env._("Preview customer"),
                    "address_lines": [
                        document_env._("1 Example Street"),
                        document_env._("75000 Paris"),
                        document_env._("France"),
                    ],
                },
                "lines": [
                    {
                        "description": document_env._(
                            "Professional services - preview data"
                        ),
                        "quantity": "1",
                        "unit_price": "1 000,00 €",
                        "taxes": "20 %",
                        "total": "1 000,00 €",
                    }
                ],
                "totals": [
                    {"label": document_env._("Subtotal"), "amount": "1 000,00 €"},
                    {"label": document_env._("VAT 20%"), "amount": "200,00 €"},
                    {"label": document_env._("Total"), "amount": "1 200,00 €"},
                ],
                "payment_terms": document_env._(
                    "Synthetic preview - not an invoice."
                ),
                "legal_mentions": [
                    document_env._(
                        "Preview document - no legal or accounting value."
                    )
                ],
            },
            "accounting_statement.v2": {
                "qualification_label": document_env._(
                    "SYNTHETIC QUALIFICATION SAMPLE - NOT A REAL DOCUMENT"
                ),
                "title": document_env._("Income statement preview"),
                "reference": "PREVIEW-ACCOUNTING",
                "date": today,
                "layout_variant": "statement",
                "columns": [
                    {"key": "label", "label": document_env._("Label"), "kind": "label"},
                    {"key": "current", "label": document_env._("Current period"), "kind": "amount"},
                    {"key": "prior", "label": document_env._("Prior period"), "kind": "amount"},
                ],
                "context": [document_env._("Synthetic preview data")],
                "sections": [{
                    "key": "income_statement",
                    "title": document_env._("Operating result"),
                    "rows": [
                        {
                            "role": "group",
                            "keep_with_next": True,
                            "values": {"label": document_env._("Operating income"), "current": "42 000", "prior": "38 000"},
                        },
                        {
                            "role": "detail",
                            "level": 1,
                            "values": {"label": document_env._("Services"), "current": "42 000", "prior": "38 000"},
                        },
                        {
                            "role": "total",
                            "values": {"label": document_env._("Operating result"), "current": "14 200", "prior": "11 800"},
                        },
                    ],
                }],
                "basis_note": document_env._(
                    "Synthetic preview - not an accounting statement."
                ),
            },
            "official_letter.v1": {
                "qualification_label": document_env._(
                    "SYNTHETIC QUALIFICATION SAMPLE - NOT A REAL DOCUMENT"
                ),
                "reference": "PREVIEW-LETTER",
                "date": today,
                "recipient": {
                    "name": document_env._("Preview recipient"),
                    "address_lines": [
                        document_env._("1 Example Street"),
                        document_env._("75000 Paris"),
                        document_env._("France"),
                    ],
                },
                "subject": document_env._("Official correspondence preview"),
                "body": [
                    {
                        "type": "paragraph",
                        "text": document_env._(
                            "This synthetic document previews the official correspondence layout."
                        ),
                    },
                    {
                        "type": "heading",
                        "level": 2,
                        "text": document_env._("Clear, governed content"),
                    },
                    {
                        "type": "bullet_list",
                        "items": [
                            document_env._("Company identity synchronized with Odoo"),
                            document_env._("Immutable template revision"),
                            document_env._("Searchable professional PDF"),
                        ],
                    },
                ],
                "closing": document_env._("Sincerely,"),
                "signatory_name": self.env.user.name,
                "signatory_title": document_env._("Authorized signatory"),
                "attachments": [document_env._("Preview attachment list")],
            },
            "sign_completion.v1": {
                "qualification_label": document_env._(
                    "SYNTHETIC QUALIFICATION SAMPLE - NOT A REAL DOCUMENT"
                ),
                "reference": "PREVIEW-SIGN",
                "completed_at": today,
                "summary": document_env._(
                    "Synthetic completion evidence for layout review only; no signature is represented."
                ),
                "signers": [
                    {
                        "name": document_env._("Preview signer"),
                        "role": document_env._("Signatory"),
                        "signed_at": today,
                        "status": document_env._("Completed"),
                    }
                ],
                "evidence": [
                    {"label": "SHA-256", "value": "0" * 64},
                    {
                        "label": document_env._("Event-chain head"),
                        "value": "1" * 64,
                    },
                ],
                "disclaimer": document_env._(
                    "Preview only. The production certificate is generated from retained signing evidence."
                ),
            },
        }

    def action_usl_document_preview_pack(self):
        self.ensure_one()
        company = self.company_id
        if not company.usl_document_identity_ready:
            company._usl_document_raise_configuration_error(
                company.usl_document_identity_message
            )
        locale = "fr_FR" if (company.partner_id.lang or "").startswith("fr") else "en_US"
        company_payload, assets = company._usl_document_renderer_company_payload(locale)
        documents = self._preview_documents(locale)
        output = io.BytesIO()
        with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            for template in self.env["usl.document.template"].search(
                [("active", "=", True)], order="key"
            ):
                if template.key not in documents:
                    continue
                try:
                    rendered = self.env["usl.document.renderer"].render(
                        template,
                        company_payload,
                        documents[template.key],
                        locale,
                        assets=assets,
                    )
                except UserError as error:
                    company._usl_document_raise_configuration_error(str(error))
                archive.writestr(f"{template.key}.pdf", rendered["pdf"])
        attachment = self.env["ir.attachment"].create(
            {
                "name": f"usl-document-preview-{company.id}.zip",
                "raw": output.getvalue(),
                "mimetype": "application/zip",
                "res_model": "res.company",
                "res_id": company.id,
                "description": _("Synthetic preview pack - no legal value"),
            }
        )
        return {
            "type": "ir.actions.act_url",
            "url": f"/web/content/{attachment.id}?download=true",
            "target": "self",
        }
