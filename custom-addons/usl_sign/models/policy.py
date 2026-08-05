from odoo import api, fields, models
from odoo.exceptions import ValidationError


ASSURANCE_LEVELS = [
    ("standard", "Standard"),
    ("verified", "Verified"),
    ("qualified", "Qualified"),
]

AUTHENTICATION_METHODS = [
    ("no_otp", "No security code"),
    ("otp_email", "Email security code"),
    ("otp_sms", "SMS security code"),
    ("identity_verification", "Identity verification"),
    ("qualified_identity", "Qualified identity verification"),
]


class SignPolicy(models.Model):
    _name = "usl.sign.policy"
    _description = "Signature Assurance Policy"
    _order = "company_id, sequence, id"

    name = fields.Char(required=True, translate=True)
    sequence = fields.Integer(default=10)
    active = fields.Boolean(default=True)
    company_id = fields.Many2one(
        "res.company", required=True, default=lambda self: self.env.company
    )
    assurance_level = fields.Selection(ASSURANCE_LEVELS, required=True)
    authentication_method = fields.Selection(
        AUTHENTICATION_METHODS, required=True, default="no_otp"
    )
    provider_code = fields.Selection(
        [("yousign", "Yousign")], required=True, default="yousign"
    )
    is_default = fields.Boolean()
    public_link_allowed = fields.Boolean()
    expiration_days = fields.Integer(default=30, required=True)
    reminder_days = fields.Integer(default=3, required=True)
    description = fields.Text(translate=True)

    _company_level_unique = models.Constraint(
        "UNIQUE(company_id, assurance_level)",
        "Only one policy per assurance level is allowed for each company.",
    )

    @api.constrains(
        "assurance_level",
        "authentication_method",
        "public_link_allowed",
        "expiration_days",
        "reminder_days",
    )
    def _check_policy(self):
        for policy in self:
            if policy.expiration_days < 1:
                raise ValidationError(self.env._("Expiration must be at least one day."))
            if policy.reminder_days < 0:
                raise ValidationError(self.env._("Reminder delay cannot be negative."))
            allowed = {
                "standard": {"no_otp", "otp_email", "otp_sms"},
                "verified": {"otp_sms", "identity_verification"},
                "qualified": {"qualified_identity"},
            }
            if policy.authentication_method not in allowed[policy.assurance_level]:
                raise ValidationError(
                    self.env._(
                        "%(authentication)s is not available for %(assurance)s signatures.",
                        authentication=dict(AUTHENTICATION_METHODS)[
                            policy.authentication_method
                        ],
                        assurance=dict(ASSURANCE_LEVELS)[policy.assurance_level],
                    )
                )
            if policy.public_link_allowed and policy.assurance_level != "standard":
                raise ValidationError(
                    self.env._("Reusable public links are limited to Standard policies.")
                )

    @api.constrains("is_default", "company_id")
    def _check_single_default(self):
        for policy in self.filtered("is_default"):
            duplicate = self.search_count(
                [
                    ("company_id", "=", policy.company_id.id),
                    ("is_default", "=", True),
                    ("id", "!=", policy.id),
                ],
                limit=1,
            )
            if duplicate:
                raise ValidationError(
                    self.env._("A company can have only one default signature policy.")
                )

    @api.model
    def _ensure_company_defaults(self, companies):
        definitions = [
            {
                "name": self.env._("Standard signature"),
                "assurance_level": "standard",
                "authentication_method": "otp_email",
                "description": self.env._(
                    "For routine approvals and low-risk documents."
                ),
                "is_default": True,
                "public_link_allowed": True,
                "sequence": 10,
            },
            {
                "name": self.env._("Verified signature"),
                "assurance_level": "verified",
                "authentication_method": "identity_verification",
                "description": self.env._(
                    "For employment, contractor and material commercial agreements."
                ),
                "sequence": 20,
            },
            {
                "name": self.env._("Qualified signature"),
                "assurance_level": "qualified",
                "authentication_method": "qualified_identity",
                "description": self.env._(
                    "For documents that intentionally require qualified assurance."
                ),
                "sequence": 30,
            },
        ]
        for company in companies:
            existing_levels = set(
                self.search([("company_id", "=", company.id)]).mapped(
                    "assurance_level"
                )
            )
            for definition in definitions:
                if definition["assurance_level"] not in existing_levels:
                    self.create({**definition, "company_id": company.id})


class ResCompany(models.Model):
    _inherit = "res.company"

    @api.model_create_multi
    def create(self, vals_list):
        companies = super().create(vals_list)
        self.env["usl.sign.policy"]._ensure_company_defaults(companies)
        return companies

    def write(self, vals):
        result = super().write(vals)
        self.env["usl.sign.policy"]._ensure_company_defaults(self)
        return result
