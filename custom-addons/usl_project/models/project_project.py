from odoo import Command, api, fields, models

from .project_task_type import _relation_commands


class ProjectProject(models.Model):
    _inherit = "project.project"

    # Explicit task commands use the normal task status workflow, even during
    # a project save that also runs the automatic dependency reset.
    tasks = fields.One2many(context={"usl_keep_waiting_reasons": False})
    task_ids = fields.One2many(context={"usl_keep_waiting_reasons": False})

    usl_show_company_on_card = fields.Boolean(
        compute="_compute_usl_show_company_on_card",
    )

    @api.model
    def name_create(self, name):
        # create() provides the quick-created project's task stages.
        project = self.create({"name": name})
        return project.id, project.display_name

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
        projects = super().create(vals_list)
        if not self.env.context.get("install_mode"):
            projects._usl_complete_task_stages()
        return projects

    def write(self, vals):
        self._usl_check_task_stages(vals.get("type_ids"))
        reactivated = self.filtered(lambda project: not project.active) if vals.get("active") else self.browse()
        if "allow_task_dependencies" in vals and not vals["allow_task_dependencies"]:
            # Preserve external waits during the native dependency reset.
            self = self.with_context(usl_keep_waiting_reasons=True)
        result = super().write(vals)
        reactivated._usl_complete_task_stages()
        return result

    def _usl_complete_task_stages(self):
        # Only configure the projects the caller has just created or reactivated.
        Stage = self.env["project.task.type"].sudo().with_context(
            active_test=True, default_project_ids=False, default_user_id=False, lang="en_US",
        )
        french_installed = "fr_FR" in dict(self.env["res.lang"].get_installed())
        for project in self.with_context(active_test=True).filtered("active"):
            first = project._get_default_task_stage()
            later = project.type_ids.filtered(
                lambda stage: stage.active and stage.usl_reactivation_role == "later",
            )
            values = {"user_id": False, "project_ids": [Command.link(project.id)]}
            if not first or first in later:
                inbox = Stage.create({
                    **values, "name": "Inbox", "sequence": first.sequence - 1 if first else 0,
                })
                if french_installed:
                    inbox.update_field_translations("name", {"fr_FR": "Boîte de réception"})
            if not later:
                stage = Stage.create({
                    **values, "name": "Later", "usl_reactivation_role": "later",
                    "sequence": max(project.type_ids.mapped("sequence"), default=0) + 1,
                })
                if french_installed:
                    stage.update_field_translations("name", {"fr_FR": "Plus tard"})

    @api.depends_context("allowed_company_ids")
    def _compute_usl_show_company_on_card(self):
        show_company = len(self.env.companies) > 1
        for project in self:
            project.usl_show_company_on_card = bool(
                show_company and project.company_id,
            )
