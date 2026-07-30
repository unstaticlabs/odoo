from odoo import fields, models


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
