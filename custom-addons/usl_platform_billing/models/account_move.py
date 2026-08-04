from odoo import api, fields, models

from odoo.addons.account.models.account_move import PAYMENT_STATE_SELECTION

PLATFORM_PAYMENT_STATE_SELECTION = PAYMENT_STATE_SELECTION + [
    ("not_applicable", "No Payment Required"),
]


class AccountMove(models.Model):
    _inherit = "account.move"

    platform_billing_session_id = fields.Many2one(
        "usl.platform.billing.session",
        string="Platform Billing Session",
        check_company=True,
        copy=False,
        index=True,
        ondelete="set null",
        groups="usl_platform_billing.group_platform_billing_reader",
    )
    platform_billing_platform_id = fields.Many2one(
        "usl.platform.billing.platform",
        string="Content Platform",
        check_company=True,
        copy=False,
        index=True,
        ondelete="set null",
        groups="usl_platform_billing.group_platform_billing_reader",
    )
    platform_billing_payout_ids = fields.Many2many(
        "usl.platform.billing.payout",
        "usl_platform_billing_payout_move_rel",
        "move_id",
        "payout_id",
        string="Platform Payouts",
        copy=False,
        groups="usl_platform_billing.group_platform_billing_reader",
    )
    platform_billing_payment_state = fields.Selection(
        selection=PLATFORM_PAYMENT_STATE_SELECTION,
        string="Platform Payment Status",
        compute="_compute_platform_billing_payment_state",
        readonly=True,
        groups="usl_platform_billing.group_platform_billing_reader",
    )

    @api.depends("move_type", "payment_state")
    def _compute_platform_billing_payment_state(self):
        for move in self:
            move.platform_billing_payment_state = (
                "not_applicable" if move.move_type == "entry" else move.payment_state
            )
