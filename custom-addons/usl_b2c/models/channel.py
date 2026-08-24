from odoo import api, fields, models
from odoo.exceptions import ValidationError

from .constants import FULFILMENT_MODES


class B2cChannel(models.Model):
    _name = "b2c.channel"
    _description = "B2C Commercial Channel"
    _order = "company_id, sequence, name"
    _check_company_auto = True

    name = fields.Char(required=True, translate=True)
    code = fields.Char(required=True, index=True)
    sequence = fields.Integer(default=10)
    active = fields.Boolean(default=True)
    company_id = fields.Many2one(
        "res.company",
        required=True,
        default=lambda self: self.env.company,
        index=True,
        ondelete="cascade",
    )
    analytic_account_id = fields.Many2one(
        "account.analytic.account",
        string="Channel Analytic Account",
        required=True,
        check_company=True,
        ondelete="restrict",
        help="Existing Accounting-owned analytic account in the Channel plan.",
    )
    default_fulfilment_mode = fields.Selection(
        FULFILMENT_MODES,
        required=True,
        default="unknown",
    )
    notes = fields.Text()

    _company_code_unique = models.Constraint(
        "UNIQUE(company_id, code)",
        "A B2C channel code must be unique per company.",
    )
    _company_analytic_unique = models.Constraint(
        "UNIQUE(company_id, analytic_account_id)",
        "A Channel analytic account can be linked only once per company.",
    )

    @api.constrains("analytic_account_id")
    def _check_channel_plan(self):
        for channel in self:
            plan = channel.analytic_account_id.plan_id
            if plan and plan.complete_name != "Channel" and plan.name != "Channel":
                raise ValidationError(
                    self.env._("The analytic account must belong to the Channel plan."),
                )
