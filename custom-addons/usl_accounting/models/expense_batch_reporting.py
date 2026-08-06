from odoo import fields, models


class AccountMoveLine(models.Model):
    _inherit = "account.move.line"

    expense_batch_id = fields.Many2one(
        "usl.expense.batch",
        related="expense_id.expense_batch_id",
        string="Expense Batch",
        store=True,
        index=True,
    )
    expense_payment_mode = fields.Selection(
        related="expense_id.payment_mode",
        string="Expense Payer",
        store=True,
        index=True,
    )


class AccountAnalyticLine(models.Model):
    _inherit = "account.analytic.line"

    expense_batch_id = fields.Many2one(
        "usl.expense.batch",
        related="move_line_id.expense_batch_id",
        string="Expense Batch",
        store=True,
        index=True,
    )
    expense_payment_mode = fields.Selection(
        related="move_line_id.expense_payment_mode",
        string="Expense Payer",
        store=True,
        index=True,
    )
