from odoo import Command, api, fields, models
from odoo.exceptions import ValidationError


def _relation_commands(value):
    if isinstance(value, models.BaseModel):
        return [Command.set(value.ids)]
    if isinstance(value, tuple) or (value and not isinstance(value[0], (list, tuple))):
        return [Command.set(value)]
    return value or ()


class ProjectTaskType(models.Model):
    _inherit = "project.task.type"

    usl_reactivation_role = fields.Selection(
        selection=[
            ("later", "Later source"),
        ],
        string="Reactivation Role",
        copy=False,
        help=(
            "Deferred tasks return from Later to Inbox when work becomes actionable. "
            "The default task stage receives returns. Use the Projects field to share a project stage."
        ),
    )

    usl_reactivation_help = fields.Json(compute="_compute_usl_reactivation_help")

    @api.depends_context("uid", "lang", "usl_reactivation_project_id")
    def _compute_usl_reactivation_help(self):
        project_id = self.env.context.get("usl_reactivation_project_id")
        Stage = self.env["project.task.type"].with_context(active_test=True)
        if project_id:
            project = self.env["project.project"].search([("id", "=", project_id)], limit=1)
            inbox = project.with_context(active_test=True)._get_default_task_stage() if project else Stage
            stages = project.type_ids.filtered(lambda stage: stage.active and not stage.user_id)
        else:
            inbox = Stage._usl_personal_inboxes(self.env.user).get(self.env.uid, Stage)
            stages = Stage.search([("user_id", "=", self.env.uid)])
        later = stages.filtered(lambda stage: stage.usl_reactivation_role == "later") - inbox
        for stage in self:
            stage.usl_reactivation_help = (
                {"kind": "inbox", "names": later.mapped("name"), "personal": not project_id}
                if stage == inbox and later
                else {"kind": "later", "names": [inbox.name], "personal": not project_id}
                if stage in later and inbox
                else False
            )

    @api.model
    def _usl_personal_inboxes(self, users):
        # Same order and active-stage scope as _populate_missing_personal_stages.
        stages = self.with_context(active_test=True).search([("user_id", "in", users.ids)])
        return {user.id: owned[0] for user, owned in stages.grouped("user_id").items()}

    _usl_reactivation_role_unique = models.Constraint(
        "UNIQUE (user_id, usl_reactivation_role)",
        "A user can have only one personal stage for each reactivation role.",
    )

    def _usl_check_shareable(self):
        if self.filtered("user_id"):
            raise ValidationError(
                self.env._("Create or reuse a project stage to share it. Personal stages belong to their owners.")
            )

    def write(self, vals):
        if any(
            command[0] in (Command.CREATE, Command.LINK)
            or (command[0] == Command.SET and command[2])
            for command in _relation_commands(vals.get("project_ids"))
        ):
            self._usl_check_shareable()
        return super().write(vals)

    @api.model
    def _usl_initialize_reactivation_roles(self):
        """Assign stable semantics to existing native personal-stage pipelines."""
        stages = self.sudo().search(
            [
                ("user_id", "!=", False),
                ("user_id.active", "=", True),
                ("user_id.share", "=", False),
                ("active", "=", True),
            ],
            order="user_id, sequence, id",
        )
        for user in stages.user_id:
            user_stages = stages.filtered(lambda stage: stage.user_id == user)
            open_stages = user_stages.filtered(lambda stage: not stage.fold)
            if not open_stages:
                continue
            if len(open_stages) > 1 and not user_stages.filtered(
                lambda stage: stage.usl_reactivation_role == "later",
            ):
                open_stages[-1].usl_reactivation_role = "later"
