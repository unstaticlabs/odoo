from odoo import api, fields, models


class AccountAnalyticLine(models.Model):
    _inherit = "account.analytic.line"

    rebuild_revenue = fields.Monetary(
        string="Revenue",
        compute="_compute_reporting_amounts",
        store=True,
        currency_field="currency_id",
        help=(
            "Income-side analytic amount with the native analytic sign. "
            "Normal revenue is positive and reversals reduce the measure."
        ),
    )
    rebuild_spending = fields.Monetary(
        string="Spending",
        compute="_compute_reporting_amounts",
        store=True,
        currency_field="currency_id",
        help=(
            "Expense-side analytic amount shown as a positive consumption of "
            "value. Expense reversals reduce the measure."
        ),
    )
    rebuild_net_contribution = fields.Monetary(
        string="Net Contribution",
        compute="_compute_reporting_amounts",
        store=True,
        currency_field="currency_id",
        help=(
            "Revenue less spending; this remains equal to the native analytic "
            "amount."
        ),
    )
    rebuild_financial_account_type = fields.Selection(
        related="general_account_id.account_type",
        string="Account Category",
        store=True,
        index=True,
        readonly=True,
    )
    rebuild_financial_account_group_id = fields.Many2one(
        "account.group",
        string="Account Group",
        compute="_compute_financial_account_group",
        store=True,
        index=True,
        readonly=True,
    )
    rebuild_document_id = fields.Many2one(
        "account.move",
        string="Business Document",
        related="move_line_id.move_id",
        store=True,
        index=True,
        readonly=True,
    )
    rebuild_current_fiscal_year = fields.Boolean(
        string="Current Fiscal Year",
        search="_search_current_fiscal_year",
        store=False,
        exportable=False,
        export_string_translation=False,
    )

    @api.depends(
        "amount",
        "analytic_profitability",
        "general_account_id.account_type",
        "category",
    )
    def _compute_reporting_amounts(self):
        for line in self:
            profitability = line.analytic_profitability
            line.rebuild_revenue = (
                line.amount if profitability == "revenue" else 0.0
            )
            line.rebuild_spending = (
                -line.amount if profitability == "loss" else 0.0
            )
            line.rebuild_net_contribution = line.amount

    @api.depends("general_account_id", "general_account_id.code")
    def _compute_financial_account_group(self):
        for line in self:
            line.rebuild_financial_account_group_id = (
                line.general_account_id.group_id
            )

    def _search_current_fiscal_year(self, operator, value):
        if operator not in {"=", "!="} or not isinstance(value, bool):
            return NotImplemented
        fiscal_year = self.env.company.compute_fiscalyear_dates(
            fields.Date.context_today(self),
        )
        domain = [
            ("date", ">=", fiscal_year["date_from"]),
            ("date", "<=", fiscal_year["date_to"]),
        ]
        matches = (operator == "=" and value) or (operator == "!=" and not value)
        return domain if matches else [
            "|",
            ("date", "<", fiscal_year["date_from"]),
            ("date", ">", fiscal_year["date_to"]),
        ]
