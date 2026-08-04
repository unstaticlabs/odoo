from odoo import api, fields, models


def _apply_current_period_defaults(
    wizard,
    fields_list,
    values,
    closing_field="date_to",
    move_field="target_move",
):
    """Complete OCA wizards with the governed current fiscal period."""
    today = fields.Date.context_today(wizard)
    fiscal_dates = wizard.env.company.compute_fiscalyear_dates(today)
    if "date_from" in fields_list:
        values.setdefault("date_from", fiscal_dates["date_from"])
    if closing_field in fields_list:
        values.setdefault(closing_field, today)
    if move_field in fields_list:
        values.setdefault(move_field, "posted")
    return values


class TrialBalanceReportWizard(models.TransientModel):
    _inherit = "trial.balance.report.wizard"

    @api.model
    def default_get(self, fields_list):
        return _apply_current_period_defaults(
            self,
            fields_list,
            super().default_get(fields_list),
        )

    @api.onchange("date_range_id")
    def onchange_date_range_id(self):
        if self.date_range_id:
            self.date_from = self.date_range_id.date_start
            self.date_to = self.date_range_id.date_end


class JournalLedgerReportWizard(models.TransientModel):
    _inherit = "journal.ledger.report.wizard"

    @api.model
    def default_get(self, fields_list):
        return _apply_current_period_defaults(
            self,
            fields_list,
            super().default_get(fields_list),
            move_field="move_target",
        )

    @api.onchange("date_range_id")
    def onchange_date_range_id(self):
        if self.date_range_id:
            self.date_from = self.date_range_id.date_start
            self.date_to = self.date_range_id.date_end


class VATReportWizard(models.TransientModel):
    _inherit = "vat.report.wizard"

    @api.model
    def default_get(self, fields_list):
        return _apply_current_period_defaults(
            self,
            fields_list,
            super().default_get(fields_list),
        )

    @api.onchange("date_range_id")
    def onchange_date_range_id(self):
        if self.date_range_id:
            self.date_from = self.date_range_id.date_start
            self.date_to = self.date_range_id.date_end


class OpenItemsReportWizard(models.TransientModel):
    _inherit = "open.items.report.wizard"

    @api.model
    def default_get(self, fields_list):
        values = _apply_current_period_defaults(
            self,
            fields_list,
            super().default_get(fields_list),
            closing_field="date_at",
        )
        if "receivable_accounts_only" in fields_list:
            values.setdefault("receivable_accounts_only", True)
        if "payable_accounts_only" in fields_list:
            values.setdefault("payable_accounts_only", True)
        return values

    @api.onchange("receivable_accounts_only", "payable_accounts_only")
    def onchange_type_accounts_only(self):
        super().onchange_type_accounts_only()
        if self.receivable_accounts_only and self.payable_accounts_only and not self.account_ids:
            self.account_ids = self.env["account.account"].search([
                ("company_ids", "in", self.company_id.id or self.env.company.id),
                ("account_type", "in", ("asset_receivable", "liability_payable")),
                ("reconcile", "=", True),
            ])


class AgedPartnerBalanceReportWizard(models.TransientModel):
    _inherit = "aged.partner.balance.report.wizard"

    @api.model
    def default_get(self, fields_list):
        values = _apply_current_period_defaults(
            self,
            fields_list,
            super().default_get(fields_list),
            closing_field="date_at",
        )
        if "receivable_accounts_only" in fields_list:
            values.setdefault("receivable_accounts_only", True)
        if "payable_accounts_only" in fields_list:
            values.setdefault("payable_accounts_only", True)
        return values

    @api.onchange("receivable_accounts_only", "payable_accounts_only")
    def onchange_type_accounts_only(self):
        super().onchange_type_accounts_only()
        if self.receivable_accounts_only and self.payable_accounts_only and not self.account_ids:
            self.account_ids = self.env["account.account"].search([
                ("company_ids", "in", self.company_id.id or self.env.company.id),
                ("account_type", "in", ("asset_receivable", "liability_payable")),
                ("reconcile", "=", True),
            ])
