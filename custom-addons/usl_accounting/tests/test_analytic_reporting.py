from odoo import fields
from odoo.tests import TransactionCase, tagged


@tagged("post_install", "-at_install", "usl_accounting")
class TestAnalyticReportingMultiCompany(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.company_a = cls.env.company
        cls.company_a.write({
            "fiscalyear_last_day": 31,
            "fiscalyear_last_month": "12",
        })
        cls.company_b = cls.env["res.company"].create({
            "name": "Analytic Reporting Company B",
            "currency_id": cls.company_a.currency_id.id,
            "fiscalyear_last_day": 30,
            "fiscalyear_last_month": "9",
        })
        cls.selected_env = cls.env(context={
            **cls.env.context,
            "allowed_company_ids": [cls.company_a.id, cls.company_b.id],
        })
        plan = cls.env["account.analytic.plan"].create({
            "name": "Multi-company reporting plan",
        })
        cls.account_a = cls.env["account.analytic.account"].create({
            "name": "Company A activity",
            "plan_id": plan.id,
            "company_id": cls.company_a.id,
        })
        cls.account_b = cls.env["account.analytic.account"].create({
            "name": "Company B activity",
            "plan_id": plan.id,
            "company_id": cls.company_b.id,
        })

        today = fields.Date.context_today(cls.env["account.analytic.line"])
        fiscal_a = cls.company_a.compute_fiscalyear_dates(today)
        fiscal_b = cls.company_b.compute_fiscalyear_dates(today)
        date_a = next(
            boundary
            for boundary in (fiscal_a["date_from"], fiscal_a["date_to"])
            if not fiscal_b["date_from"] <= boundary <= fiscal_b["date_to"]
        )
        date_b = next(
            boundary
            for boundary in (fiscal_b["date_from"], fiscal_b["date_to"])
            if not fiscal_a["date_from"] <= boundary <= fiscal_a["date_to"]
        )
        cls.line_a = cls.env["account.analytic.line"].create({
            "name": "Company A current fiscal year",
            "date": date_a,
            "account_id": cls.account_a.id,
            "company_id": cls.company_a.id,
            "amount": 100,
        })
        cls.line_b = cls.selected_env["account.analytic.line"].with_company(
            cls.company_b,
        ).create({
            "name": "Company B current fiscal year",
            "date": date_b,
            "account_id": cls.account_b.id,
            "company_id": cls.company_b.id,
            "amount": 25,
        })

    def test_current_fiscal_year_uses_each_selected_company_calendar(self):
        lines = self.selected_env["account.analytic.line"].search([
            ("id", "in", [self.line_a.id, self.line_b.id]),
            ("rebuild_current_fiscal_year", "=", True),
        ])

        self.assertEqual(set(lines.ids), {self.line_a.id, self.line_b.id})

    def test_restricted_company_context_cannot_read_other_analytic_lines(self):
        restricted = self.env["account.analytic.line"].with_context(
            allowed_company_ids=self.company_a.ids,
        )

        self.assertEqual(
            restricted.search([
                ("id", "in", [self.line_a.id, self.line_b.id]),
                ("rebuild_current_fiscal_year", "=", True),
            ]),
            self.line_a,
        )
