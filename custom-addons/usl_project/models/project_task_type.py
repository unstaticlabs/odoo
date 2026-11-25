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

    user_id = fields.Many2one(default=lambda self: self._default_user_id())

    usl_reactivation_role = fields.Selection(
        selection=[
            ("inbox", "Inbox destination"),
            ("later", "Later source"),
        ],
        string="Reactivation Role",
        copy=False,
        help=(
            "Deferred tasks return from Later to Inbox when work becomes actionable. "
            "For a project board, set these roles on stages linked to the project "
            "and use one active Inbox. Use the Projects field to share a project stage."
        ),
    )

    _usl_reactivation_role_unique = models.Constraint(
        "UNIQUE (user_id, usl_reactivation_role)",
        "A user can have only one personal stage for each reactivation role.",
    )

    @api.model
    def _default_user_id(self):
        if self.env.context.get("usl_project_quick_create"):
            return False
        return super()._default_user_id()

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
            if not user_stages.filtered(
                lambda stage: stage.usl_reactivation_role == "inbox",
            ):
                open_stages[0].usl_reactivation_role = "inbox"
            if (
                len(open_stages) > 1
                and not user_stages.filtered(
                    lambda stage: stage.usl_reactivation_role == "later",
                )
            ):
                open_stages[-1].usl_reactivation_role = "later"
