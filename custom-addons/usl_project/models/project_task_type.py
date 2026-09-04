from odoo import api, fields, models


class ProjectTaskType(models.Model):
    _inherit = "project.task.type"

    usl_reactivation_role = fields.Selection(
        selection=[
            ("inbox", "Inbox destination"),
            ("later", "Later source"),
        ],
        string="Reactivation Role",
        copy=False,
        help=(
            "For a personal stage, identifies where deferred tasks wait and "
            "where they return when work becomes actionable."
        ),
    )

    _usl_reactivation_role_owner = models.Constraint(
        "CHECK (usl_reactivation_role IS NULL OR user_id IS NOT NULL)",
        "Only a personal task stage can have a reactivation role.",
    )
    _usl_reactivation_role_unique = models.Constraint(
        "UNIQUE (user_id, usl_reactivation_role)",
        "A user can have only one personal stage for each reactivation role.",
    )

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
                lambda stage: stage.usl_reactivation_role == "inbox"
            ):
                open_stages[0].usl_reactivation_role = "inbox"
            if (
                len(open_stages) > 1
                and not user_stages.filtered(
                    lambda stage: stage.usl_reactivation_role == "later"
                )
            ):
                open_stages[-1].usl_reactivation_role = "later"
