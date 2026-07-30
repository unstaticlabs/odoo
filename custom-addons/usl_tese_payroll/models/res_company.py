from odoo import _, fields, models
from odoo.exceptions import AccessError


class ResCompany(models.Model):
    _inherit = "res.company"

    tese_payroll_journal_id = fields.Many2one(
        "account.journal",
        string="TESE Payroll Journal",
        check_company=True,
        domain="[('type', '=', 'general'), ('company_id', '=', id)]",
    )
    tese_collector_partner_id = fields.Many2one(
        "res.partner",
        string="TESE Collector",
        check_company=True,
        help="URSSAF or another provider collecting payroll social liabilities.",
    )

    def _check_tese_configuration_access(self):
        if (
            not self.env.su
            and not (
                self.env.user.has_group("hr.group_hr_manager")
                and self.env.user.has_group("account.group_account_manager")
            )
        ):
            raise AccessError(_(
                "TESE company configuration requires both HR Administrator and "
                "Accounting Administrator access.",
            ))

    def action_open_tese_configuration(self):
        self.ensure_one()
        self._check_tese_configuration_access()
        return {
            "type": "ir.actions.act_window",
            "name": _("TESE Settings"),
            "res_model": "res.company",
            "res_id": self.id,
            "view_mode": "form",
            "views": [(
                self.env.ref(
                    "usl_tese_payroll.view_company_form_tese_configuration",
                ).id,
                "form",
            )],
            "target": "current",
        }

    def write(self, values):
        if (
            {"tese_payroll_journal_id", "tese_collector_partner_id"} & set(values)
        ):
            self._check_tese_configuration_access()
        return super().write(values)
