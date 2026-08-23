from datetime import date

from odoo.tests import TransactionCase, tagged


@tagged("post_install", "-at_install", "usl_tese_accounting")
class TestTeseAccountingClosing(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.company = cls.env.company
        cls.employee = cls.env["hr.employee"].sudo().create({
            "name": "Closing Control Employee",
            "company_id": cls.company.id,
        })
        cls.closing = cls.env["rebuild.account.closing.period"].create({
            "name": "July 2026",
            "company_id": cls.company.id,
            "period_type": "month",
            "date_from": date(2026, 7, 1),
            "date_to": date(2026, 7, 31),
            "fiscalyear_start": date(2025, 10, 1),
            "fiscalyear_end": date(2026, 9, 30),
        })

    def test_control_reports_installed_tese_without_period_records(self):
        values = self.closing._control_payroll()

        self.assertEqual(values["status"], "not_applicable")
        self.assertIn("No TESE payroll records", values["summary"])
        self.assertNotIn("external payroll", values["summary"].lower())

    def test_control_blocks_incomplete_tese_record_in_period(self):
        self.env["usl.tese.payslip"].sudo().create({
            "company_id": self.company.id,
            "employee_id": self.employee.id,
            "pay_period": date(2026, 7, 1),
            "tese_reference": "TESE-CLOSING-2026-07",
            "gross_salary": 3000.0,
        })

        values = self.closing._control_payroll()

        self.assertEqual(values["status"], "block")
        self.assertEqual(values["record_count"], 1)
        self.assertEqual(values["amount"], 3000.0)
        self.assertIn("remain unposted", values["summary"])
