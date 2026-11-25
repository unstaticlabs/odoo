from odoo import Command, api, fields, models

from .project_task_type import _relation_commands


class ProjectProject(models.Model):
    _inherit = "project.project"

    usl_show_company_on_card = fields.Boolean(
        compute="_compute_usl_show_company_on_card",
    )

    @api.model
    def name_create(self, name):
        # Native quick create links a newly created, otherwise personal, "New" stage.
        return super(ProjectProject, self.with_context(usl_project_quick_create=True)).name_create(name)

    @api.model
    def _usl_check_task_stages(self, commands):
        stage_ids = set()
        for command in _relation_commands(commands):
            if command[0] == Command.LINK:
                stage_ids.add(command[1])
            elif command[0] == Command.SET:
                stage_ids.update(command[2])
        self.env["project.task.type"].browse(sorted(stage_ids))._usl_check_shareable()

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            self._usl_check_task_stages(
                vals.get("type_ids", self.env.context.get("default_type_ids")),
            )
        return super().create(vals_list)

    def write(self, vals):
        self._usl_check_task_stages(vals.get("type_ids"))
        return super().write(vals)

    @api.depends_context("allowed_company_ids")
    def _compute_usl_show_company_on_card(self):
        show_company = len(self.env.companies) > 1
        for project in self:
            project.usl_show_company_on_card = bool(
                show_company and project.company_id,
            )
