from odoo import api, fields, models


class ResCompany(models.Model):
    _inherit = "res.company"

    @api.readonly
    @api.model
    def get_fiscal_dates(self, payload):
        """Apply governed first-year bounds to Accounting spreadsheets."""
        results = super().get_fiscal_dates(payload)
        companies = self.browse(
            data["company_id"] or self.env.company.id
            for data in payload
        )
        existing_companies = companies.exists()
        for index, (data, company, result) in enumerate(
            zip(payload, companies, results),
        ):
            if not result or company not in existing_companies:
                continue
            fiscal_dates = company.compute_fiscalyear_dates(
                fields.Date.to_date(data["date"]),
            )
            results[index] = {
                "start": fiscal_dates["date_from"],
                "end": fiscal_dates["date_to"],
            }
        return results


class AccountAccount(models.Model):
    _inherit = "account.account"

    @api.model
    def _get_date_period_boundaries(self, date_period, company):
        start, end = super()._get_date_period_boundaries(
            date_period,
            company,
        )
        if date_period["range_type"] not in {"year", "day"}:
            return start, end
        fiscal_dates = company.compute_fiscalyear_dates(end)
        start = fiscal_dates["date_from"]
        if date_period["range_type"] == "year":
            end = fiscal_dates["date_to"]
        return start, end


class AccountMove(models.Model):
    _inherit = "account.move"

    def _get_sequence_date_range(self, reset):
        date_range = super()._get_sequence_date_range(reset)
        if reset not in {"year_range", "year_range_month"}:
            return date_range
        fiscal_dates = self.company_id.compute_fiscalyear_dates(self.date)
        if reset == "year_range":
            return (
                fiscal_dates["date_from"],
                fiscal_dates["date_to"],
                None,
                None,
            )
        return (
            max(date_range[0], fiscal_dates["date_from"]),
            min(date_range[1], fiscal_dates["date_to"]),
            fiscal_dates["date_from"].year,
            fiscal_dates["date_to"].year,
        )
