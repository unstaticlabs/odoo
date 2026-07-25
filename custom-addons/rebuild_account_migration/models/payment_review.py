from odoo import fields, models


class RebuildAccountPaymentReview(models.Model):
    _name = "rebuild.account.payment.review"
    _description = "USL Source Payment Workflow Review"
    _inherit = ["rebuild.source.trace.mixin"]
    _order = "date, source_payment_id"

    name = fields.Char(required=True, index=True)
    source_payment_id = fields.Integer(index=True, copy=False)
    source_state = fields.Char(index=True, copy=False)
    state = fields.Selection(
        [
            ("draft", "Draft"),
            ("reconciled", "Reconciled"),
            ("canceled", "Canceled"),
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
    company_id = fields.Many2one("res.company", required=True, index=True)
    currency_id = fields.Many2one("res.currency", required=True)
    journal_id = fields.Many2one("account.journal", index=True)
    partner_id = fields.Many2one("res.partner", index=True)
    partner_bank_source_id = fields.Integer(index=True, copy=False)
    payment_method_line_source_id = fields.Integer(index=True, copy=False)
    paired_internal_transfer_payment_source_id = fields.Integer(index=True, copy=False)
    outstanding_account_id = fields.Many2one("account.account", index=True)
    destination_account_id = fields.Many2one("account.account", index=True)
    date = fields.Date(required=True, index=True)
    amount = fields.Monetary(currency_field="currency_id")
    amount_company_currency_signed = fields.Monetary(currency_field="company_currency_id")
    company_currency_id = fields.Many2one(
        related="company_id.currency_id",
        string="Company Currency",
        readonly=True,
    )
    payment_type = fields.Char(index=True)
    partner_type = fields.Char(index=True)
    memo = fields.Char()
    payment_reference = fields.Char()
    source_is_reconciled = fields.Boolean(copy=False)
    source_is_matched = fields.Boolean(copy=False)
    source_is_sent = fields.Boolean(copy=False)
    source_is_reconciled_raw = fields.Char(copy=False)
    source_is_matched_raw = fields.Char(copy=False)
    source_is_sent_raw = fields.Char(copy=False)
    accounting_effect = fields.Selection(
        [
            ("none_no_source_move", "None - No Source Journal Entry"),
        ],
        default="none_no_source_move",
        required=True,
        index=True,
    )
    note = fields.Text()
