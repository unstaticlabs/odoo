import hashlib
from base64 import b64decode

from odoo import api, fields, models
from odoo.exceptions import ValidationError


class SignEvidence(models.Model):
    _name = "usl.sign.evidence"
    _description = "Signature Evidence"
    _order = "create_date, id"

    request_id = fields.Many2one(
        "sign.oca.request", required=True, ondelete="cascade", index=True
    )
    signer_id = fields.Many2one(
        "sign.oca.request.signer", ondelete="set null", index=True
    )
    company_id = fields.Many2one(related="request_id.company_id", store=True)
    kind = fields.Selection(
        [
            ("original", "Original document"),
            ("signed", "Signed document"),
            ("audit_trail", "Provider audit trail"),
            ("completion_evidence", "Completion evidence"),
            ("decline", "Decline evidence"),
            ("cancellation", "Cancellation evidence"),
            ("expiration", "Expiration evidence"),
        ],
        required=True,
    )
    name = fields.Char(required=True)
    data = fields.Binary(required=True, attachment=True)
    mimetype = fields.Char(required=True, default="application/pdf")
    sha256 = fields.Char(required=True, readonly=True, index=True)
    provider_reference = fields.Char(readonly=True)
    retrieved_at = fields.Datetime(readonly=True)
    validation_status = fields.Selection(
        [
            ("not_checked", "Not checked"),
            ("valid", "Valid"),
            ("invalid", "Invalid"),
            ("unknown", "Unknown"),
        ],
        required=True,
        default="not_checked",
    )
    active = fields.Boolean(default=True)

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get("data"):
                vals["sha256"] = hashlib.sha256(b64decode(vals["data"])).hexdigest()
        return super().create(vals_list)

    def write(self, vals):
        protected = {"data", "sha256", "request_id", "signer_id", "kind"}
        if protected.intersection(vals):
            raise ValidationError(
                self.env._("Stored signature evidence cannot be replaced.")
            )
        return super().write(vals)

    def unlink(self):
        raise ValidationError(self.env._("Signature evidence cannot be deleted."))

