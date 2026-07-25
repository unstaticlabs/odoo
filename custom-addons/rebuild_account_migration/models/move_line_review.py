from odoo import fields, models


class RebuildAccountMoveLineReview(models.Model):
    _name = "rebuild.account.move.line.review"
    _description = "USL Source Move Line Workflow Review"
    _inherit = ["rebuild.source.trace.mixin"]
    _order = "date, source_move_id, sequence, source_move_line_id"

    name = fields.Char(required=True, index=True)
    source_move_line_id = fields.Integer(index=True, copy=False)
    source_move_id = fields.Integer(index=True, copy=False)
    imported_move_id = fields.Many2one("account.move", index=True)
    imported_move_review_id = fields.Many2one("rebuild.account.move.review", index=True)
    company_id = fields.Many2one("res.company", required=True, index=True)
    company_currency_id = fields.Many2one(
        related="company_id.currency_id",
        string="Company Currency",
        readonly=True,
    )
    journal_id = fields.Many2one("account.journal", index=True)
    partner_id = fields.Many2one("res.partner", index=True)
    account_id = fields.Many2one("account.account", index=True)
    line_currency_id = fields.Many2one("res.currency", string="Line Currency", index=True)
    date = fields.Date(required=True, index=True)
    date_maturity = fields.Date(index=True)
    source_move_name = fields.Char(copy=False)
    source_move_state = fields.Char(index=True, copy=False)
    source_move_type = fields.Char(index=True)
    sequence = fields.Integer()
    display_type = fields.Char(index=True)
    label = fields.Text()
    ref = fields.Char()
    review_status = fields.Selection(
        [
            ("represented_no_ledger_effect", "Represented - No Ledger Effect"),
            ("review_required", "Review Required"),
        ],
        default="represented_no_ledger_effect",
        required=True,
        index=True,
    )
    accounting_effect = fields.Selection(
        [
            ("none_non_account_display_line", "None - Non-account Display Line"),
            ("none_non_posted_source_line", "None - Non-posted Source Line"),
        ],
        default="none_non_account_display_line",
        required=True,
        index=True,
    )
    debit = fields.Monetary(currency_field="company_currency_id")
    credit = fields.Monetary(currency_field="company_currency_id")
    balance = fields.Monetary(currency_field="company_currency_id")
    amount_currency = fields.Monetary(currency_field="line_currency_id")
    tax_base_amount = fields.Monetary(currency_field="company_currency_id")
    source_account_id = fields.Integer(index=True, copy=False)
    source_tax_ids = fields.Text(copy=False)
    source_tax_tag_ids = fields.Text(copy=False)
    source_tax_line_id = fields.Integer(index=True, copy=False)
    source_tax_group_id = fields.Integer(index=True, copy=False)
    source_tax_repartition_line_id = fields.Integer(index=True, copy=False)
    note = fields.Text()
