from odoo import api, fields, models
from odoo.exceptions import AccessError

from .constants import SOURCE_PROVIDERS


class B2cProviderEvidence(models.Model):
    _name = "b2c.provider.evidence"
    _description = "Restricted B2C Provider Business Evidence"
    _order = "occurred_at desc, id desc"
    _check_company_auto = True

    evidence_key = fields.Char(required=True, index=True, copy=False, readonly=True)
    company_id = fields.Many2one(
        "res.company",
        required=True,
        default=lambda self: self.env.company,
        index=True,
        ondelete="cascade",
        readonly=True,
    )
    source_provider = fields.Selection(
        SOURCE_PROVIDERS,
        required=True,
        index=True,
        readonly=True,
    )
    source_name = fields.Char(required=True, readonly=True)
    source_checksum = fields.Char(required=True, index=True, readonly=True)
    schema_digest = fields.Char(required=True, readonly=True)
    payload_digest = fields.Char(required=True, index=True, readonly=True)
    payload_json = fields.Json(
        required=True,
        readonly=True,
        groups="usl_b2c.group_b2c_sensitive_evidence",
        help=(
            "Immutable provider-business payload retained only when a source "
            "column has no typed destination. It is excluded from analytics."
        ),
    )
    contains_pii = fields.Boolean(required=True, default=True, readonly=True)
    occurred_at = fields.Datetime(index=True, readonly=True)
    attachment_id = fields.Many2one(
        "ir.attachment",
        string="Source Attachment",
        ondelete="restrict",
        copy=False,
        readonly=True,
    )

    _company_evidence_key_unique = models.Constraint(
        "UNIQUE(company_id, evidence_key)",
        "A B2C provider evidence key must be unique per company.",
    )
    _company_payload_unique = models.Constraint(
        "UNIQUE(company_id, source_checksum, payload_digest)",
        "The same provider-business payload cannot be retained twice.",
    )

    @api.model_create_multi
    def create(self, vals_list):
        if not self.env.context.get("b2c_evidence_import"):
            raise AccessError(
                self.env._("Raw provider evidence can only be created by the governed importer."),
            )
        return super().create(vals_list)

    def write(self, vals):
        if not self.env.context.get("b2c_evidence_import"):
            raise AccessError(self.env._("Raw provider evidence is immutable."))
        return super().write(vals)

    def unlink(self):
        if not (
            self.env.context.get("b2c_evidence_import")
            or self.env.context.get("module_uninstall")
        ):
            raise AccessError(self.env._("Raw provider evidence is immutable."))
        return super().unlink()
