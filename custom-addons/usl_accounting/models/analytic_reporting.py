from odoo import api, fields, models


class AccountAnalyticLine(models.Model):
    _inherit = "account.analytic.line"

    rebuild_revenue = fields.Monetary(
        string="Produits",
        compute="_compute_reporting_amounts",
        store=True,
        currency_field="currency_id",
        help=(
            "Montant analytique côté produits avec le signe analytique natif. "
            "Un produit normal est positif ; une extourne réduit la mesure."
        ),
    )
    rebuild_spending = fields.Monetary(
        string="Charges",
        compute="_compute_reporting_amounts",
        store=True,
        currency_field="currency_id",
        help=(
            "Montant analytique côté charges présenté comme une consommation "
            "positive. Une extourne réduit la mesure."
        ),
    )
    rebuild_net_contribution = fields.Monetary(
        string="Contribution nette",
        compute="_compute_reporting_amounts",
        store=True,
        currency_field="currency_id",
        help=(
            "Produits moins charges ; cette valeur reste égale au montant "
            "analytique natif."
        ),
    )
    rebuild_financial_account_type = fields.Selection(
        related="general_account_id.account_type",
        string="Catégorie de compte",
        store=True,
        index=True,
        readonly=True,
    )
    rebuild_financial_account_group_id = fields.Many2one(
        "account.group",
        string="Groupe de comptes",
        compute="_compute_financial_account_group",
        store=True,
        index=True,
        readonly=True,
    )
    rebuild_document_id = fields.Many2one(
        "account.move",
        string="Document d’origine",
        related="move_line_id.move_id",
        store=True,
        index=True,
        readonly=True,
    )
    rebuild_current_fiscal_year = fields.Boolean(
        string="Exercice en cours",
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
        if operator in {"in", "not in"}:
            requested = True in value
            positive = requested if operator == "in" else not requested
        elif operator in {"=", "==", "!="} and isinstance(value, bool):
            positive = value if operator in {"=", "=="} else not value
        else:
            return NotImplemented
        company_domains = []
        for company in self.env.companies:
            fiscal_year = company.compute_fiscalyear_dates(
                fields.Date.context_today(self.with_company(company)),
            )
            company_domains.append(fields.Domain.AND([
                fields.Domain("company_id", "=", company.id),
                fields.Domain("date", ">=", fiscal_year["date_from"]),
                fields.Domain("date", "<=", fiscal_year["date_to"]),
            ]))
        domain = fields.Domain.OR(company_domains)
        return list(domain if positive else ~domain)
