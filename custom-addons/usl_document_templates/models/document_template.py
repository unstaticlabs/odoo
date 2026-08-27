from odoo import api, fields, models, _
from odoo.exceptions import UserError, ValidationError


class UslDocumentTemplate(models.Model):
    _name = "usl.document.template"
    _description = "Governed Document Template"
    _order = "family, key"

    name = fields.Char(required=True, translate=True)
    key = fields.Char(required=True, index=True)
    schema_version = fields.Char(required=True, default="1.0")
    family = fields.Selection(
        selection=[
            ("invoice", "Invoices and credit notes"),
            ("accounting", "Accounting statements"),
            ("letter", "Official correspondence"),
            ("sign", "Signature completion"),
        ],
        required=True,
        index=True,
    )
    output_profile = fields.Selection(
        selection=[
            ("standard", "Standard PDF"),
            ("pdfa-2b", "PDF/A-2b"),
            ("factur-x-base", "Factur-X visual base"),
        ],
        required=True,
    )
    active = fields.Boolean(default=True)
    report_ids = fields.One2many("ir.actions.report", "usl_document_template_id")

    _key_unique = models.Constraint("UNIQUE(key)", "A document template key must be unique.")

    @api.constrains("key", "schema_version")
    def _check_governed_identifiers(self):
        for template in self:
            if template.key not in {
                "invoice.v1",
                "accounting_statement.v1",
                "official_letter.v1",
                "sign_completion.v1",
            }:
                raise ValidationError(_("The template key is not part of the governed catalog."))
            if template.schema_version != "1.0":
                raise ValidationError(_("Only schema version 1.0 is supported."))

    @api.ondelete(at_uninstall=False)
    def _unlink_except_module_uninstall(self):
        raise UserError(_("Governed templates are upgrade-managed and cannot be deleted."))

