from odoo import _, fields, models


class HrEmployee(models.Model):
    _inherit = "hr.employee"

    tese_payslip_count = fields.Integer(compute="_compute_tese_payslip_count")

    def _compute_tese_payslip_count(self):
        grouped = self.env["usl.tese.payslip"]._read_group(
            [("employee_id", "in", self.ids)],
            ["employee_id"],
            ["__count"],
        )
        counts = {employee.id: count for employee, count in grouped}
        for employee in self:
            employee.tese_payslip_count = counts.get(employee.id, 0)

    def action_open_tese_payslips(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": _("TESE Payroll"),
            "res_model": "usl.tese.payslip",
            "view_mode": "list,form",
            "domain": [("employee_id", "=", self.id)],
            "context": {
                "default_employee_id": self.id,
                "default_company_id": self.company_id.id,
            },
        }
