from odoo import models


class RebuildAccountClosingPeriod(models.Model):
    _inherit = "rebuild.account.closing.period"

    def _control_payroll(self):
        self.ensure_one()
        payslips = self.env["usl.tese.payslip"].search([
            ("company_id", "=", self.company_id.id),
            ("period_start", "<=", self.date_to),
            ("period_end", ">=", self.date_from),
            ("state", "!=", "cancelled"),
        ])
        if not payslips:
            return self._control_values(
                "payroll",
                "payroll",
                "Payroll status",
                "not_applicable",
                0,
                0.0,
                "No TESE payroll records fall in this closing period.",
                "Confirm that no TESE payroll was expected for the period.",
                owner="finance_operator",
            )

        incomplete = payslips.filtered(
            lambda payslip: (
                not payslip.attachment_id
                or not payslip.move_id
                or payslip.move_id.state != "posted"
                or payslip.state in {"draft", "prepared", "to_post"}
            ),
        )
        open_liabilities = (payslips - incomplete).filtered(
            lambda payslip: payslip.state == "to_reconcile",
        )
        affected = incomplete | open_liabilities
        if incomplete:
            status = "block"
        elif open_liabilities:
            status = "warning"
        else:
            status = "pass"
        return self._control_values(
            "payroll",
            "payroll",
            "Payroll status",
            status,
            len(affected),
            sum(affected.mapped("gross_salary")),
            (
                f"{len(incomplete)} TESE payroll record(s) remain unposted or "
                f"lack the official PDF; {len(open_liabilities)} posted "
                "record(s) retain open salary or URSSAF liabilities."
            ),
            (
                "Attach each official TESE PDF, post its payroll entry, and "
                "reconcile the salary and URSSAF liabilities."
            ),
            owner="finance_operator",
        )
