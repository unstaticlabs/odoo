from odoo import api, fields, models


class ProjectProject(models.Model):
    _inherit = "project.project"

    usl_show_company_on_card = fields.Boolean(
        compute="_compute_usl_show_company_on_card",
    )

    @api.depends_context("allowed_company_ids")
    def _compute_usl_show_company_on_card(self):
        show_company = len(self.env.companies) > 1
        for project in self:
            project.usl_show_company_on_card = bool(
                show_company and project.company_id,
            )
