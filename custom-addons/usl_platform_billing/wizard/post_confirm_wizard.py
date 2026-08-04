from odoo import fields, models


class UslPlatformBillingPostConfirmWizard(models.TransientModel):
    _name = "usl.platform.billing.post.confirm.wizard"
    _description = "Confirm Incomplete Platform Coverage"

    session_id = fields.Many2one(
        "usl.platform.billing.session",
        required=True,
        readonly=True,
    )
    missing_platform_ids = fields.Many2many(
        "usl.platform.billing.platform",
        "usl_pb_post_confirm_platform_rel",
        "wizard_id",
        "platform_id",
        string="Platforms Without Payouts",
        readonly=True,
    )
    warning_message = fields.Text(readonly=True)

    def action_confirm(self):
        self.ensure_one()
        self.session_id._check_operator()
        self.session_id.with_context(
            skip_platform_coverage_warning=True,
        ).action_post_documents()
        return {"type": "ir.actions.act_window_close"}
