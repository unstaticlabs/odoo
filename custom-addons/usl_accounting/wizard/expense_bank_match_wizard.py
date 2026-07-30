from odoo import _, api, fields, models


class UslExpenseBankMatchWizard(models.TransientModel):
    _name = "usl.expense.bank.match.wizard"
    _description = "Confirm Expense Bank Match"

    candidate_id = fields.Many2one(
        "usl.expense.bank.match.candidate",
        required=True,
        readonly=True,
        ondelete="cascade",
    )
    expense_id = fields.Many2one(
        related="candidate_id.expense_id",
        readonly=True,
    )
    bank_statement_line_id = fields.Many2one(
        related="candidate_id.bank_statement_line_id",
        readonly=True,
    )
    currency_id = fields.Many2one(
        related="candidate_id.currency_id",
        readonly=True,
    )
    bank_amount = fields.Monetary(
        related="candidate_id.bank_amount",
        currency_field="currency_id",
        readonly=True,
    )
    bank_date = fields.Date(
        related="candidate_id.bank_date",
        readonly=True,
    )
    journal_id = fields.Many2one(
        related="candidate_id.journal_id",
        readonly=True,
    )
    partner_id = fields.Many2one(
        related="candidate_id.partner_id",
        readonly=True,
    )
    evidence_summary = fields.Char(
        related="candidate_id.evidence_summary",
        readonly=True,
    )
    confirmation_message = fields.Text(
        compute="_compute_confirmation_message",
    )

    @api.depends(
        "expense_id.state",
        "expense_id.vendor_id",
        "partner_id",
    )
    def _compute_confirmation_message(self):
        for wizard in self:
            vendor_note = ""
            if (
                wizard.partner_id
                and wizard.partner_id != wizard.expense_id.vendor_id
            ):
                vendor_note = _(
                    " The vendor will change from %(before)s to %(after)s.",
                    before=(
                        wizard.expense_id.vendor_id.display_name
                        or _("Unassigned")
                    ),
                    after=wizard.partner_id.display_name or _("Unassigned"),
                )
            wizard.confirmation_message = _(
                "This changes Paid by to Company, uses the selected bank "
                "journal, completes the native submit/approve/post steps "
                "allowed for you, and reconciles the resulting company "
                "payment with this bank transaction.%(vendor_note)s",
                vendor_note=vendor_note,
            )

    def action_confirm(self):
        self.ensure_one()
        return self.candidate_id._apply_match()
