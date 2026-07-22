from odoo import api, models


USL_BENCHMARK_START = "2024-01-10"
USL_BENCHMARK_END = "2025-09-30"


def _apply_benchmark_period_defaults(wizard, values, closing_field="date_to", move_field="target_move"):
    context = wizard.env.context
    if "default_date_from" not in context:
        values["date_from"] = USL_BENCHMARK_START
    else:
        values.setdefault("date_from", context["default_date_from"])
    if f"default_{closing_field}" not in context:
        values[closing_field] = USL_BENCHMARK_END
    else:
        values.setdefault(closing_field, context[f"default_{closing_field}"])
    if f"default_{move_field}" not in context:
        values[move_field] = "posted"
    else:
        values.setdefault(move_field, context[f"default_{move_field}"])
    return values


def _default_reconcilable_account_ids(wizard):
    return wizard.env["account.account"].search([
        ("company_ids", "in", wizard.env.company.id),
        ("reconcile", "=", True),
    ]).ids


class TrialBalanceReportWizard(models.TransientModel):
    _inherit = "trial.balance.report.wizard"

    @api.model
    def default_get(self, fields_list):
        values = super().default_get(fields_list)
        return _apply_benchmark_period_defaults(self, values)

    @api.onchange("date_range_id")
    def onchange_date_range_id(self):
        if self.date_range_id:
            self.date_from = self.date_range_id.date_start
            self.date_to = self.date_range_id.date_end


class GeneralLedgerReportWizard(models.TransientModel):
    _inherit = "general.ledger.report.wizard"

    @api.model
    def default_get(self, fields_list):
        values = super().default_get(fields_list)
        return _apply_benchmark_period_defaults(self, values)


class JournalLedgerReportWizard(models.TransientModel):
    _inherit = "journal.ledger.report.wizard"

    @api.model
    def default_get(self, fields_list):
        values = super().default_get(fields_list)
        return _apply_benchmark_period_defaults(self, values, move_field="move_target")

    @api.onchange("date_range_id")
    def onchange_date_range_id(self):
        if self.date_range_id:
            self.date_from = self.date_range_id.date_start
            self.date_to = self.date_range_id.date_end


class VATReportWizard(models.TransientModel):
    _inherit = "vat.report.wizard"

    @api.model
    def default_get(self, fields_list):
        values = super().default_get(fields_list)
        return _apply_benchmark_period_defaults(self, values)

    @api.onchange("date_range_id")
    def onchange_date_range_id(self):
        if self.date_range_id:
            self.date_from = self.date_range_id.date_start
            self.date_to = self.date_range_id.date_end


class OpenItemsReportWizard(models.TransientModel):
    _inherit = "open.items.report.wizard"

    @api.model
    def default_get(self, fields_list):
        values = super().default_get(fields_list)
        values = _apply_benchmark_period_defaults(self, values, closing_field="date_at")
        if "account_ids" in fields_list and not values.get("account_ids"):
            values["account_ids"] = [(6, 0, _default_reconcilable_account_ids(self))]
        return values


class AgedPartnerBalanceReportWizard(models.TransientModel):
    _inherit = "aged.partner.balance.report.wizard"

    @api.model
    def default_get(self, fields_list):
        values = super().default_get(fields_list)
        values = _apply_benchmark_period_defaults(self, values, closing_field="date_at")
        if "account_ids" in fields_list and not values.get("account_ids"):
            values["account_ids"] = [(6, 0, _default_reconcilable_account_ids(self))]
        return values
