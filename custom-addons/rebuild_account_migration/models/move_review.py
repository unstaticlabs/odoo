from odoo import fields, models


class RebuildAccountMoveReview(models.Model):
    _name = "rebuild.account.move.review"
    _description = "USL Source Move Workflow Review"
    _inherit = [
        "rebuild.source.trace.mixin",
        "mail.thread.main.attachment",
    ]
    _order = "date, source_move_id"

    name = fields.Char(required=True, index=True)
    source_name = fields.Char(copy=False)
    source_move_id = fields.Integer(index=True, copy=False)
    source_state = fields.Char(index=True, copy=False)
    state = fields.Selection(
        [
            ("draft", "Draft"),
            ("cancel", "Cancelled"),
            ("unknown", "Unknown"),
        ],
        default="unknown",
        index=True,
    )
    review_status = fields.Selection(
        [
            ("represented_no_ledger_effect", "Represented - No Ledger Effect"),
            ("review_required", "Review Required"),
        ],
        default="represented_no_ledger_effect",
        index=True,
    )
    accounting_effect = fields.Selection(
        [
            ("none_non_posted_source_move", "None - Non-posted Source Move"),
        ],
        default="none_non_posted_source_move",
        required=True,
        index=True,
    )
    company_id = fields.Many2one("res.company", required=True, index=True)
    journal_id = fields.Many2one("account.journal", index=True)
    partner_id = fields.Many2one("res.partner", index=True)
    commercial_partner_id = fields.Many2one("res.partner", index=True)
    currency_id = fields.Many2one("res.currency", required=True)
    company_currency_id = fields.Many2one(
        related="company_id.currency_id",
        string="Company Currency",
        readonly=True,
    )
    date = fields.Date(required=True, index=True)
    invoice_date = fields.Date(index=True)
    invoice_date_due = fields.Date(index=True)
    move_type = fields.Char(index=True)
    ref = fields.Char()
    payment_reference = fields.Char()
    payment_state = fields.Char(index=True)
    amount_untaxed_signed = fields.Monetary(currency_field="company_currency_id")
    amount_total_signed = fields.Monetary(currency_field="company_currency_id")
    amount_residual_signed = fields.Monetary(currency_field="company_currency_id")
    source_line_count = fields.Integer(copy=False)
    source_accounting_line_count = fields.Integer(copy=False)
    move_line_review_ids = fields.One2many(
        "rebuild.account.move.line.review",
        "imported_move_review_id",
        string="Source Line Reviews",
        readonly=True,
    )
    move_line_review_count = fields.Integer(
        string="Source Line Review Count",
        compute="_compute_move_line_review_count",
    )
    source_line_debit_total = fields.Monetary(currency_field="company_currency_id")
    source_line_credit_total = fields.Monetary(currency_field="company_currency_id")
    source_line_balance_total = fields.Monetary(currency_field="company_currency_id")
    source_create_date = fields.Datetime(copy=False)
    source_write_date = fields.Datetime(copy=False)
    note = fields.Text()

    def _compute_move_line_review_count(self):
        MoveLineReview = self.env["rebuild.account.move.line.review"]
        for review in self:
            review.move_line_review_count = MoveLineReview.search_count([
                ("imported_move_review_id", "=", review.id),
            ])

    def action_open_source_line_reviews(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": "Source Move Line Workflow Review",
            "res_model": "rebuild.account.move.line.review",
            "view_mode": "list,form,pivot",
            "domain": [("imported_move_review_id", "=", self.id)],
            "context": {
                "create": False,
                "delete": False,
                "search_default_non_posted": 1,
            },
        }
