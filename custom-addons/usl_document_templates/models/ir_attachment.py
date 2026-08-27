from odoo import fields, models


class IrAttachment(models.Model):
    _inherit = "ir.attachment"

    usl_document_template_id = fields.Many2one(
        "usl.document.template",
        string="Document template",
        index=True,
        ondelete="restrict",
        readonly=True,
    )
    usl_document_template_revision = fields.Char(
        string="Template revision",
        readonly=True,
        index=True,
    )
    usl_document_payload_sha256 = fields.Char(
        string="Payload SHA-256",
        readonly=True,
        index=True,
    )
    usl_document_renderer_version = fields.Char(
        string="Renderer version",
        readonly=True,
    )
    usl_document_company_id = fields.Many2one(
        "res.company",
        string="Rendered company",
        index=True,
        ondelete="restrict",
        readonly=True,
    )
    usl_document_rendered_at = fields.Datetime(
        string="Rendered at",
        readonly=True,
    )
