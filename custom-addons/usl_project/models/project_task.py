from odoo import _, api, fields, models, tools


class ProjectTask(models.Model):
    _inherit = "project.task"

    planned_date_begin = fields.Datetime(
        string="Planned Start",
        index=True,
        copy=False,
        tracking=True,
        help=(
            "The date and time when work is planned to start. Together with "
            "the deadline, it defines the task's planned range."
        ),
    )
    usl_dependency_date_warning = fields.Char(
        string="Dependency Schedule Warning",
        compute="_compute_usl_dependency_date_warning",
    )

    @api.model
    def _is_projectless_todo_recipient(self, recipients):
        recipient_emails = {
            tools.email_normalize(email)
            for email in tools.email_split(recipients or "")
            if tools.email_normalize(email)
        }
        if not recipient_emails:
            return False

        task_model = self.env["ir.model"]._get("project.task")
        todo_aliases = self.env["mail.alias"].sudo().search([
            ("alias_name", "=", "todo"),
            ("alias_model_id", "=", task_model.id),
            ("alias_force_thread_id", "=", 0),
            ("alias_parent_model_id", "=", False),
            ("alias_parent_thread_id", "=", 0),
        ])
        return any(
            tools.email_normalize(alias.alias_full_name) in recipient_emails
            for alias in todo_aliases
            if alias.alias_full_name
        )

    @api.model
    def message_new(self, msg_dict, custom_values=None):
        values = dict(custom_values or {})
        if (
            not values.get("project_id")
            and self._is_projectless_todo_recipient(msg_dict.get("to"))
        ):
            sender_user_ids = self._find_internal_users_from_address_mail(
                msg_dict.get("email_from"),
            )
            if len(sender_user_ids) == 1:
                values["user_ids"] = list(values.get("user_ids") or []) + [
                    sender_user_ids[0],
                ]
        return super().message_new(msg_dict, custom_values=values)

    @api.depends(
        "planned_date_begin",
        "depend_on_ids.date_deadline",
        "depend_on_ids.state",
    )
    def _compute_usl_dependency_date_warning(self):
        closed_states = {"1_done", "1_canceled"}
        for task in self:
            overlapping = task.depend_on_ids.filtered(
                lambda blocker: (
                    blocker.state not in closed_states
                    and blocker.date_deadline
                    and task.planned_date_begin
                    and blocker.date_deadline > task.planned_date_begin
                ),
            )
            task.usl_dependency_date_warning = (
                _(
                    "This task is planned to start before blocking task(s) "
                    "finish: %s",
                )
                % ", ".join(overlapping.mapped("name")[:3])
                if overlapping
                else False
            )
