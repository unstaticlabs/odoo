"""Fiscal-year buckets of the accounting report views.

Every value below is invented. The companies are synthetic and carry no real
fiscal calendar, amount, entry or partner.
"""

import importlib.util
from datetime import date, datetime, timedelta
from pathlib import Path

from odoo import Command
from odoo.exceptions import UserError
from odoo.tests import TransactionCase, tagged

from odoo.addons.rebuild_account_migration.models.fiscal_period import (
    EARLIER_POSTED_LABEL,
    fiscal_period_label,
    fiscal_period_sql,
)

MIGRATION_PATH = (
    Path(__file__).resolve().parents[1]
    / "migrations"
    / "saas~19.3.1.13.39"
    / "post-bound_fiscal_period_keys.py"
)


def _load_migration():
    spec = importlib.util.spec_from_file_location(
        "bound_fiscal_period_keys_migration",
        MIGRATION_PATH,
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _label(date_from, date_to):
    return fiscal_period_label(date_from, date_to)


@tagged("post_install", "-at_install", "rebuild_account_migration_unit")
class TestFiscalPeriodBuckets(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.euro = cls.env.ref("base.EUR")
        cls.france = cls.env.ref("base.fr")

    # ------------------------------------------------------------------
    # Synthetic data helpers
    # ------------------------------------------------------------------
    def _company(self, name, last_day=30, last_month="9", first=None):
        values = {
            "name": f"[QA] {name}",
            "currency_id": self.euro.id,
            "country_id": self.france.id,
            "account_fiscal_country_id": self.france.id,
            "fiscalyear_last_day": last_day,
            "fiscalyear_last_month": last_month,
        }
        if first:
            values.update({
                "rebuild_first_fiscalyear_start": first[0],
                "rebuild_first_fiscalyear_end": first[1],
            })
        return self.env["res.company"].create(values)

    def _books(self, company):
        Account = self.env["account.account"].with_company(company)

        def account(code, name, account_type, reconcile=False):
            return Account.create({
                "code": code,
                "name": name,
                "account_type": account_type,
                "reconcile": reconcile,
                "company_ids": [Command.set([company.id])],
            })

        books = {
            "company": company,
            "revenue": account("706991", "QA services", "income"),
            "cash": account("512991", "QA bank", "asset_cash"),
            "receivable": account(
                "411991", "QA customers", "asset_receivable", reconcile=True,
            ),
            "journal": self.env["account.journal"].with_company(company).create({
                "name": "QA journal",
                "code": "QAJ",
                "type": "general",
                "company_id": company.id,
            }),
            "partner": self.env["res.partner"].create({"name": "[QA] Customer"}),
        }
        return books

    def _post(self, books, on_date, amount, debit="cash", taxes=None):
        company = books["company"]
        revenue_line = {
            "name": "QA revenue",
            "account_id": books["revenue"].id,
            "credit": amount,
        }
        if taxes:
            revenue_line["tax_ids"] = [Command.set(taxes.ids)]
        move = self.env["account.move"].with_company(company).create({
            "date": on_date,
            "journal_id": books["journal"].id,
            "line_ids": [
                Command.create({
                    "name": "QA counterpart",
                    "account_id": books[debit].id,
                    "partner_id": books["partner"].id,
                    "debit": amount,
                }),
                Command.create(revenue_line),
            ],
        })
        move.action_post()
        return move

    def _rows(self, model, company, *domain):
        self.env.flush_all()
        return self.env[model].search([("company_id", "=", company.id), *domain])

    def _by_bucket(self, rows, field_name):
        result = {}
        for row in rows:
            key = (row.period_date_from or None, row.period_date_to or None)
            result[key] = round(result.get(key, 0.0) + abs(row[field_name]), 2)
        return result

    # ------------------------------------------------------------------
    # Rule equivalence
    # ------------------------------------------------------------------
    def test_sql_bucket_matches_compute_fiscalyear_dates(self):
        companies = [
            self._company("Sep 30 year end", 30, "9"),
            self._company("Dec 31 year end", 31, "12"),
            self._company("Feb 29 year end", 29, "2"),
            self._company("Feb 28 year end", 28, "2"),
        ]
        period = fiscal_period_sql("calendar.day", "company")
        for company in companies:
            self.env.cr.execute(
                f"""
                SELECT calendar.day,
                       {period.date_from},
                       {period.date_to},
                       {period.key}
                  FROM (
                        SELECT day::date AS day
                          FROM generate_series(
                              DATE '2026-01-01',
                              DATE '2029-03-31',
                              INTERVAL '1 day'
                          ) AS day
                       ) calendar
                  CROSS JOIN res_company company
                 WHERE company.id = %s
                 ORDER BY calendar.day
                """,
                [company.id],
            )
            rows = self.env.cr.fetchall()
            self.assertEqual(len(rows), (date(2029, 3, 31) - date(2026, 1, 1)).days + 1)
            for day, date_from, date_to, key in rows:
                expected = company.compute_fiscalyear_dates(day)
                self.assertEqual(
                    (date_from, date_to),
                    (expected["date_from"], expected["date_to"]),
                    f"{company.name} on {day}",
                )
                self.assertEqual(
                    key,
                    _label(expected["date_from"], expected["date_to"]),
                )

    def test_first_fiscal_year_override(self):
        company = self._company(
            "First year",
            first=("2024-01-10", "2025-09-30"),
        )
        period = fiscal_period_sql("calendar.day", "company")
        expectations = {
            date(2023, 12, 31): (
                None,
                date(2024, 1, 9),
                EARLIER_POSTED_LABEL,
            ),
            date(2024, 1, 10): (
                date(2024, 1, 10),
                date(2025, 9, 30),
                "Fiscal year 2024-01-10 to 2025-09-30",
            ),
            date(2025, 9, 30): (
                date(2024, 1, 10),
                date(2025, 9, 30),
                "Fiscal year 2024-01-10 to 2025-09-30",
            ),
            date(2025, 10, 1): (
                date(2025, 10, 1),
                date(2026, 9, 30),
                "Fiscal year 2025-10-01 to 2026-09-30",
            ),
            date(2026, 10, 1): (
                date(2026, 10, 1),
                date(2027, 9, 30),
                "Fiscal year 2026-10-01 to 2027-09-30",
            ),
        }
        for day, (date_from, date_to, key) in expectations.items():
            self.env.cr.execute(
                f"""
                SELECT {period.date_from}, {period.date_to}, {period.key}
                  FROM (SELECT %s::date AS day) calendar
                  CROSS JOIN res_company company
                 WHERE company.id = %s
                """,
                [day, company.id],
            )
            self.assertEqual(self.env.cr.fetchone(), (date_from, date_to, key), day)
            if date_from:
                expected = company.compute_fiscalyear_dates(day)
                self.assertEqual(
                    (date_from, date_to),
                    (expected["date_from"], expected["date_to"]),
                )

    # ------------------------------------------------------------------
    # Views
    # ------------------------------------------------------------------
    def test_statement_views_exclude_next_fiscal_year(self):
        company = self._company("Statements")
        books = self._books(company)
        year_end = date(2026, 9, 30)
        self._post(books, year_end, 100.0)
        self._post(books, year_end + timedelta(days=1), 7.0)
        self._post(books, year_end, 100.0, debit="receivable")
        self._post(books, year_end + timedelta(days=1), 7.0, debit="receivable")
        closed = (date(2025, 10, 1), date(2026, 9, 30))
        next_year = (date(2026, 10, 1), date(2027, 9, 30))

        financial = self._rows(
            "rebuild.account.financial.statement.line",
            company,
            ("account_id", "=", books["revenue"].id),
        )
        self.assertEqual(
            self._by_bucket(financial, "credit"),
            {closed: 200.0, next_year: 14.0},
        )
        self.assertEqual(
            {row.period_key for row in financial},
            {_label(*closed), _label(*next_year)},
        )

        french = self._rows(
            "rebuild.account.french.statement.line",
            company,
            ("line_code", "=", "CR_SERVICES"),
        )
        self.assertEqual(
            self._by_bucket(french, "amount"),
            {closed: 200.0, next_year: 14.0},
        )

        trial = self._rows(
            "rebuild.account.trial.balance.line",
            company,
            ("account_id", "=", books["revenue"].id),
        )
        self.assertEqual(
            self._by_bucket(trial, "credit"),
            {closed: 200.0, next_year: 14.0},
        )

        ledger = self._rows(
            "rebuild.account.general.ledger.line",
            company,
            ("account_id", "=", books["revenue"].id),
        )
        self.assertEqual(
            {(line.date, line.period_date_from, line.period_date_to) for line in ledger},
            {
                (year_end, *closed),
                (year_end + timedelta(days=1), *next_year),
            },
        )

        aged = self._rows("rebuild.account.aged.partner.balance.line", company)
        self.assertEqual(
            self._by_bucket(aged, "total"),
            {closed: 100.0, next_year: 7.0},
        )

        management = self._rows(
            "rebuild.account.management.summary.line",
            company,
            ("line_code", "=", "REVENUE"),
        )
        self.assertEqual(
            self._by_bucket(management, "amount"),
            {closed: 200.0, next_year: 14.0},
        )

    def test_three_fiscal_years_do_not_leak(self):
        company = self._company("Three years", first=("2024-01-10", "2025-09-30"))
        books = self._books(company)
        self._post(books, date(2023, 12, 31), 1.0)
        self._post(books, date(2024, 1, 10), 2.0)
        self._post(books, date(2025, 9, 30), 3.0)
        self._post(books, date(2025, 10, 1), 4.0)
        self._post(books, date(2026, 9, 30), 5.0)
        self._post(books, date(2026, 10, 1), 6.0)
        self._post(books, date(2027, 9, 30), 8.0)
        self._post(books, date(2027, 10, 1), 9.0)
        trial = self._rows(
            "rebuild.account.trial.balance.line",
            company,
            ("account_id", "=", books["revenue"].id),
        )
        self.assertEqual(
            self._by_bucket(trial, "credit"),
            {
                (None, date(2024, 1, 9)): 1.0,
                (date(2024, 1, 10), date(2025, 9, 30)): 5.0,
                (date(2025, 10, 1), date(2026, 9, 30)): 9.0,
                (date(2026, 10, 1), date(2027, 9, 30)): 14.0,
                (date(2027, 10, 1), date(2028, 9, 30)): 9.0,
            },
        )
        earlier = trial.filtered(lambda row: not row.period_date_from)
        self.assertEqual(earlier.period_key, EARLIER_POSTED_LABEL)

    def test_aged_balance_first_year_is_dated_at_its_end(self):
        company = self._company(
            "Aged first year",
            first=("2024-01-10", "2025-09-30"),
        )
        other = self._company("Aged other company")
        for each in (company, other):
            books = self._books(each)
            self._post(books, date(2025, 3, 1), 100.0, debit="receivable")
        self.env.flush_all()
        Aged = self.env["rebuild.account.aged.partner.balance.line"]
        self.assertEqual(
            Aged.search([("company_id", "=", company.id)]).mapped("as_of_date"),
            [date(2025, 9, 30)],
        )
        # Without an exceptional first year the position is as of today.
        (as_of,) = Aged.search([("company_id", "=", other.id)]).mapped("as_of_date")
        self.assertGreater(as_of, date(2025, 9, 30))

    def test_analytic_distribution_is_bounded_by_fiscal_year(self):
        company = self._company("Analytic")
        plan = self.env["account.analytic.plan"].create({"name": "[QA] plan"})
        analytic = self.env["account.analytic.account"].with_company(company).create({
            "name": "[QA] project",
            "plan_id": plan.id,
            "company_id": company.id,
        })
        Line = self.env["account.analytic.line"].with_company(company)
        for on_date, amount in (
            (date(2026, 9, 30), 100.0),
            (date(2026, 10, 1), 7.0),
        ):
            Line.create({
                "name": "[QA] line",
                "account_id": analytic.id,
                "date": on_date,
                "amount": amount,
                "company_id": company.id,
            })
        rows = self._rows("rebuild.account.analytic.distribution.line", company)
        self.assertEqual(
            self._by_bucket(rows, "allocated_balance"),
            {
                (date(2025, 10, 1), date(2026, 9, 30)): 100.0,
                (date(2026, 10, 1), date(2027, 9, 30)): 7.0,
            },
        )
        closed = rows.filtered(lambda row: row.period_date_to == date(2026, 9, 30))
        lines = self.env["account.analytic.line"].search(
            closed.action_open_journal_items()["domain"],
        )
        self.assertEqual(lines.mapped("amount"), [100.0])

    def test_drilldown_domain_is_bounded(self):
        company = self._company(
            "Drilldown",
            first=("2024-01-10", "2025-09-30"),
        )
        books = self._books(company)
        self._post(books, date(2023, 12, 15), 11.0)
        self._post(books, date(2025, 9, 30), 100.0)
        self._post(books, date(2025, 10, 1), 7.0)
        self._post(books, date(2026, 10, 1), 3.0)
        MoveLine = self.env["account.move.line"]
        expected = {
            (None, date(2024, 1, 9)): 11.0,
            (date(2024, 1, 10), date(2025, 9, 30)): 100.0,
            (date(2025, 10, 1), date(2026, 9, 30)): 7.0,
            (date(2026, 10, 1), date(2027, 9, 30)): 3.0,
        }
        for model in (
            "rebuild.account.financial.statement.line",
            "rebuild.account.trial.balance.line",
        ):
            rows = self._rows(
                model, company, ("account_id", "=", books["revenue"].id),
            )
            self.assertEqual(len(rows), len(expected))
            for row in rows:
                domain = row.action_open_journal_items()["domain"]
                lines = MoveLine.search(domain)
                self.assertEqual(
                    round(sum(lines.mapped("credit")), 2),
                    expected[(row.period_date_from or None, row.period_date_to)],
                    f"{model} {row.period_key}",
                )
                if row.period_date_to:
                    self.assertTrue(
                        any(clause[1] == "<=" for clause in domain
                            if isinstance(clause, tuple)),
                        f"{model} {row.period_key} has no upper bound",
                    )

    def test_multi_company_buckets_isolated_with_distinct_year_ends(self):
        sep = self._company("September", 30, "9")
        dec = self._company("December", 31, "12")
        for company in (sep, dec):
            books = self._books(company)
            self._post(books, date(2026, 12, 31), 100.0)
            self._post(books, date(2027, 1, 1), 7.0)
            company_rows = self._rows(
                "rebuild.account.trial.balance.line",
                company,
                ("account_id", "=", books["revenue"].id),
            )
            if company == sep:
                expected = {
                    (date(2026, 10, 1), date(2027, 9, 30)): 107.0,
                }
            else:
                expected = {
                    (date(2026, 1, 1), date(2026, 12, 31)): 100.0,
                    (date(2027, 1, 1), date(2027, 12, 31)): 7.0,
                }
            self.assertEqual(self._by_bucket(company_rows, "credit"), expected)

    # ------------------------------------------------------------------
    # Exports
    # ------------------------------------------------------------------
    def _eu_tax(self, company):
        group = self.env["account.tax.group"].create({
            "name": "[QA] EU group",
            "company_id": company.id,
            "country_id": self.france.id,
        })
        return self.env["account.tax"].create({
            "name": "[QA] EU DES sale",
            "amount": 0.0,
            "amount_type": "percent",
            "type_tax_use": "sale",
            "company_id": company.id,
            "country_id": self.france.id,
            "tax_group_id": group.id,
        })

    def _wizard(self, company, report_type, date_from, date_to):
        return self.env["rebuild.account.report.export.wizard"].create({
            "report_type": report_type,
            "company_id": company.id,
            "company_ids": [Command.set(company.ids)],
            "date_from": date_from,
            "date_to": date_to,
            "target_move": "posted",
        })

    def test_eu_export_respects_month_range_and_rejects_partial_month(self):
        company = self._company("EU export")
        books = self._books(company)
        tax = self._eu_tax(company)
        self._post(books, date(2026, 8, 15), 100.0, taxes=tax)
        self._post(books, date(2026, 9, 30), 5.0, taxes=tax)
        self._post(books, date(2026, 10, 5), 7.0, taxes=tax)

        def taxable(date_from, date_to):
            wizard = self._wizard(company, "ec_sales_list", date_from, date_to)
            rows = wizard._eu_tax_report_rows()
            return round(sum(float(row["taxable_amount"]) for row in rows), 2)

        self.assertEqual(taxable("2026-08-01", "2026-08-31"), 100.0)
        self.assertEqual(taxable("2026-08-01", "2026-09-30"), 105.0)
        self.assertEqual(taxable("2026-10-01", "2026-10-31"), 7.0)
        self.assertEqual(taxable("2026-07-01", "2026-12-31"), 112.0)
        self.assertEqual(taxable("2026-11-01", "2026-11-30"), 0.0)
        for date_from, date_to in (
            ("2026-08-15", "2026-08-31"),
            ("2026-08-01", "2026-08-30"),
        ):
            wizard = self._wizard(company, "ec_sales_list", date_from, date_to)
            with self.assertRaisesRegex(UserError, "mois civil complet"):
                wizard._eu_tax_report_rows()

    def test_french_tax_package_second_fiscal_year_not_empty(self):
        company = self._company(
            "Tax package",
            first=("2024-01-10", "2025-09-30"),
        )
        books = self._books(company)
        self._post(books, date(2025, 3, 1), 7.0)
        self._post(books, date(2025, 12, 1), 100.0)
        field_code = "2065_RESULTAT_FISCAL_AVANT_DEFICITS_REVIEW"

        def amount(date_from, date_to):
            wizard = self._wizard(company, "french_tax_package", date_from, date_to)
            rows = wizard._french_tax_package_rows()
            return [
                float(row["amount"])
                for row in rows
                if row["field_code"] == field_code
            ]

        self.assertEqual(amount("2024-01-10", "2025-09-30"), [7.0])
        self.assertEqual(amount("2025-10-01", "2026-09-30"), [100.0])
        wizard = self._wizard(
            company, "french_tax_package", "2025-10-01", "2026-03-31",
        )
        with self.assertRaisesRegex(UserError, "exercice comptable complet"):
            wizard._french_tax_package_rows()

    # ------------------------------------------------------------------
    # Declarations
    # ------------------------------------------------------------------
    def test_prior_year_threshold_uses_bounds(self):
        company = self._company("Threshold", first=("2024-01-10", "2025-09-30"))
        Value = self.env["rebuild.account.external.report.value"]
        Declaration = self.env["rebuild.account.declaration"]

        def value(period_key, source_key, amount):
            return Value.create({
                "name": "[QA] reviewed value",
                "company_id": company.id,
                "currency_id": company.currency_id.id,
                "period_key": period_key,
                "form_code": "CFE-PRIOR",
                "field_code": "PRIOR_LIABILITY",
                "source_key": source_key,
                "amount": amount,
                "review_status": "accepted",
            })

        def exceeds(threshold=3000.0):
            self.env.flush_all()
            return Declaration._external_value_exceeds(
                company, "CFE-PRIOR", date(2026, 9, 30), threshold,
            )

        # The year being declared starts in the prior calendar year: its own
        # label contains that year but it is not the prior fiscal year.
        value("Fiscal year 2025-10-01 to 2026-09-30", "current", 5000.0)
        self.assertFalse(exceeds())
        value("Fiscal year 2024-10-01 to 2025-09-30", "prior", 5000.0)
        self.assertTrue(exceeds())
        self.assertFalse(exceeds(threshold=6000.0))

        other = self._company("Calendar", first=("2024-01-10", "2025-09-30"))
        Value.create({
            "name": "[QA] calendar value",
            "company_id": other.id,
            "currency_id": other.currency_id.id,
            "period_key": "Calendar year 2025",
            "form_code": "CFE-PRIOR",
            "field_code": "PRIOR_LIABILITY",
            "source_key": "calendar",
            "amount": 5000.0,
            "review_status": "accepted",
        })
        self.env.flush_all()
        self.assertTrue(
            Declaration._external_value_exceeds(
                other, "CFE-PRIOR", date(2026, 9, 30), 3000.0,
            ),
        )
        Value.create({
            "name": "[QA] free text value",
            "company_id": company.id,
            "currency_id": company.currency_id.id,
            "period_key": "Reviewed in 2025",
            "form_code": "CVAE-FREE",
            "field_code": "PRIOR_LIABILITY",
            "source_key": "free",
            "amount": 5000.0,
            "review_status": "accepted",
        })
        self.env.flush_all()
        self.assertFalse(
            Declaration._external_value_exceeds(
                company, "CVAE-FREE", date(2026, 9, 30), 3000.0,
            ),
        )

    def test_declaration_tax_package_domain_follows_company_year(self):
        company = self._company(
            "Declaration bucket",
            first=("2024-01-10", "2025-09-30"),
        )
        declaration = self.env["rebuild.account.declaration"].new({
            "company_id": company.id,
        })
        self.assertEqual(
            declaration._tax_package_period_domain(date(2027, 3, 1)),
            [
                ("period_date_from", "=", date(2026, 10, 1)),
                ("period_date_to", "=", date(2027, 9, 30)),
            ],
        )
        self.assertEqual(
            declaration._tax_package_period_domain(date(2025, 9, 30)),
            [
                ("period_date_from", "=", date(2024, 1, 10)),
                ("period_date_to", "=", date(2025, 9, 30)),
            ],
        )

    # ------------------------------------------------------------------
    # Stored labels
    # ------------------------------------------------------------------
    def _stored_value(self, company, period_key, source_key, created="2026-03-01 10:00:00"):
        record = self.env["rebuild.account.external.report.value"].create({
            "name": "[QA] stored value",
            "company_id": company.id,
            "currency_id": company.currency_id.id,
            "period_key": period_key,
            "form_code": "QA-FORM",
            "field_code": "QA_FIELD",
            "source_key": source_key,
            "amount": 100.0,
        })
        self.env.flush_all()
        self.env.cr.execute(
            "UPDATE rebuild_account_external_report_value SET create_date = %s WHERE id = %s",
            [created, record.id],
        )
        self.env.invalidate_all()
        return record

    def test_relabel_helper_and_collision_raises(self):
        migration = _load_migration()
        legacy = "Fiscal year from 2025-10-01"
        created = datetime(2026, 3, 1, 10, 0)
        self.assertEqual(
            migration.relabel_target(
                legacy, created, 30, "9", date(2024, 1, 10), date(2025, 9, 30),
            ),
            "Fiscal year 2025-10-01 to 2026-09-30",
        )
        with self.assertRaisesRegex(
            migration.FiscalPeriodRelabelError, "starts inside a fiscal year",
        ):
            migration.relabel_target(legacy, created, 31, "12")
        with self.assertRaisesRegex(
            migration.FiscalPeriodRelabelError, "after the fiscal year",
        ):
            migration.relabel_target(
                legacy, datetime(2026, 10, 2, 8, 0), 30, "9",
                date(2024, 1, 10), date(2025, 9, 30),
            )
        with self.assertRaisesRegex(
            migration.FiscalPeriodRelabelError, "cannot read a start date",
        ):
            migration.relabel_target("Fiscal year from soon", created, 30, "9")

        company = self._company("Relabel", first=("2024-01-10", "2025-09-30"))
        first_label = "Fiscal year 2024-01-10 to 2025-09-30"
        kept = self._stored_value(company, first_label, "first")
        moved = self._stored_value(company, legacy, "open")
        Decision = self.env["rebuild.account.assurance.decision"]
        decision = Decision.create({
            "name": "[QA] decision",
            "company_id": company.id,
            "period_key": legacy,
        })
        self.env.flush_all()
        self.env.cr.execute(
            "UPDATE rebuild_account_assurance_decision SET create_date = %s WHERE id = %s",
            ["2026-03-01 10:00:00", decision.id],
        )
        migration.migrate(self.env.cr, "saas~19.3.1.13.38")
        self.env.invalidate_all()
        self.assertEqual(kept.period_key, first_label)
        self.assertEqual(moved.period_key, "Fiscal year 2025-10-01 to 2026-09-30")
        self.assertEqual(decision.period_key, "Fiscal year 2025-10-01 to 2026-09-30")
        # A second run finds nothing left to relabel.
        migration.migrate(self.env.cr, "saas~19.3.1.13.38")

        # A relabel that would duplicate an existing source stops everything.
        pending = self._stored_value(company, legacy, "duplicate")
        self._stored_value(
            company, "Fiscal year 2025-10-01 to 2026-09-30", "duplicate",
        )
        with self.assertRaisesRegex(
            migration.FiscalPeriodRelabelError, "would duplicate",
        ):
            migration.migrate(self.env.cr, "saas~19.3.1.13.38")
        self.env.invalidate_all()
        self.assertEqual(pending.period_key, legacy)

        # A row created after its fiscal year ended is ambiguous and stops too.
        pending.unlink()
        late = self._stored_value(
            company, legacy, "late", created="2026-10-15 09:00:00",
        )
        with self.assertRaisesRegex(
            migration.FiscalPeriodRelabelError, "after the fiscal year",
        ):
            migration.migrate(self.env.cr, "saas~19.3.1.13.38")
        self.env.invalidate_all()
        self.assertEqual(late.period_key, legacy)

    def test_report_period_fields_exist_on_every_bucketed_view(self):
        views = [
            name
            for name, model in self.env.registry.items()
            if not model._auto
            and "period_key" in model._fields
        ]
        self.assertTrue(views)
        for name in views:
            model = self.env[name]
            self.assertIn("period_date_from", model._fields, name)
            self.assertIn("period_date_to", model._fields, name)
            self.assertEqual(model._fields["period_date_from"].type, "date", name)
        self.assertIn(
            "period_month",
            self.env["rebuild.account.eu.tax.report.line"]._fields,
        )
