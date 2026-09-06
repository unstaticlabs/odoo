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

    def _platform_billing_sessions_to_refresh(self):
        moves = self.sudo().exists()
        return (
            moves.platform_billing_session_id
            | moves.platform_billing_payout_ids.session_id
        ).exists()

    def _reverse_moves(self, default_values_list=None, cancel=False):
        sessions = self._platform_billing_sessions_to_refresh()
        result = super()._reverse_moves(default_values_list, cancel)
        sessions |= result._platform_billing_sessions_to_refresh()
        sessions._refresh_state()
        return result


class AccountPartialReconcile(models.Model):
    _inherit = "account.partial.reconcile"

    def _platform_billing_sessions_to_refresh(self):
        moves = (
            self.sudo().debit_move_id.move_id
            | self.sudo().credit_move_id.move_id
        )
        return moves._platform_billing_sessions_to_refresh()

    @api.model_create_multi
    def create(self, vals_list):
        partials = super().create(vals_list)
        partials._platform_billing_sessions_to_refresh()._refresh_state()
        return partials

    def unlink(self):
        sessions = self._platform_billing_sessions_to_refresh()
        result = super().unlink()
        sessions._refresh_state()
        return result
