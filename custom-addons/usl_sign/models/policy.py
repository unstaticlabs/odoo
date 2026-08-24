from odoo import api, fields, models
from odoo.exceptions import ValidationError

from .constants import TRUST_LEVELS


class SignPolicy(models.Model):
    _name = "usl.sign.policy"
    _description = "Signature Trust Recommendation Policy"
    _order = "company_id, sequence, id"

    name = fields.Char(required=True, translate=True)
    sequence = fields.Integer(default=10, required=True)
    active = fields.Boolean(default=True)
    company_id = fields.Many2one("res.company", index=True, ondelete="cascade")
    version = fields.Char(required=True, default="1")
    document_category = fields.Selection(
        [
            ("internal_decision", "Corporate decision document"),
            ("routine_agreement", "Routine agreement"),
            ("employment", "Employment document"),
            ("intellectual_property", "Intellectual property"),
            ("commercial", "Commercial agreement"),
            ("finance_guarantee", "Financing or guarantee"),
            ("mandate", "Mandate"),
            ("other", "Other"),
        ],
    )
    signer_type = fields.Selection(
        [
            ("internal", "Internal user"),
            ("recurring", "Known recurring signer"),
            ("occasional", "Occasional external signer"),
            ("any", "Any signer"),
        ],
        default="any",
        required=True,
    )
    risk_level = fields.Selection(
        [("low", "Low"), ("material", "Material"), ("maximum", "Maximum")],
        default="low",
        required=True,
    )
    formal_qes_required = fields.Boolean()
    recommendation = fields.Selection(TRUST_LEVELS, required=True)
    reason = fields.Text(required=True, translate=True)
    consequence = fields.Text(required=True, translate=True)
    default_authentication = fields.Selection(
        [
            ("secure_link", "Secure invitation link"),
            ("email_otp", "Secure link plus email verification code"),
            ("pocket_id", "Pocket ID"),
            ("portal", "Odoo portal account"),
        ],
        default="secure_link",
        required=True,
    )
    expiration_days = fields.Integer(default=30, required=True)
    reminder_days = fields.Integer(default=3, required=True)
    max_reminders = fields.Integer(default=5, required=True)

    @api.constrains("expiration_days", "reminder_days", "max_reminders")
    def _check_timing(self):
        for policy in self:
            if policy.expiration_days < 1:
                msg = "A signing policy must expire after at least one day."
                raise ValidationError(msg)
            if not 0 <= policy.reminder_days < policy.expiration_days:
                msg = "Reminder delay must be within the request lifetime."
                raise ValidationError(msg)
            if not 0 <= policy.max_reminders <= 20:
                msg = "Maximum reminders must be between 0 and 20."
                raise ValidationError(msg)

    @api.constrains("formal_qes_required", "recommendation")
    def _check_qes_policy(self):
        if self.filtered(
            lambda policy: policy.formal_qes_required
            and policy.recommendation != "qualified_external",
        ):
            msg = "A formal QES policy must recommend Qualified external."
            raise ValidationError(msg)

    @api.model
    def recommend(self, company, *, category, signer_type, risk_level, formal_qes):
        domain = [
            ("active", "=", True),
            ("company_id", "in", [False, company.id]),
        ]
        candidates = self.search(domain, order="company_id desc, sequence, id")
        if formal_qes:
            qualified = candidates.filtered("formal_qes_required")
            if qualified:
                return qualified[0]
        matches = candidates.filtered(
            lambda policy: (
                not policy.document_category or policy.document_category == category
            )
            and policy.signer_type in {"any", signer_type}
            and policy.risk_level == risk_level
            and not policy.formal_qes_required,
        )
        if matches:
            return matches[0]
        fallback = candidates.filtered(
            lambda policy: not policy.document_category
            and policy.signer_type == "any"
            and not policy.formal_qes_required,
        )
        return fallback[:1]
