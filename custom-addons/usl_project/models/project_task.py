from datetime import UTC, timedelta
from zoneinfo import ZoneInfo

from odoo import _, api, fields, models, tools
from odoo.addons.project.models.project_task import CLOSED_STATES


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

    def _get_default_personal_stage_create_vals(self, user_id):
        values = super()._get_default_personal_stage_create_vals(user_id)
        open_stage_values = [value for value in values if not value.get("fold")]
        if open_stage_values:
            open_stage_values[0]["usl_reactivation_role"] = "inbox"
        if len(open_stage_values) > 1:
            open_stage_values[-1]["usl_reactivation_role"] = "later"
        return values

    def _usl_has_completed_direct_subtasks(self):
        self.ensure_one()
        domain = [
            ("parent_id", "=", self.id),
            ("active", "=", True),
            ("recurring_task", "=", False),
        ]
        if not self.is_template:
            domain.append(("is_template", "=", False))
        subtasks = self.sudo().search(domain)
        return bool(subtasks) and all(
            subtask.state in CLOSED_STATES for subtask in subtasks
        )

    def _usl_is_due_for_user(self, user, now=None):
        self.ensure_one()
        now = now or fields.Datetime.now()
        if self.planned_date_begin and self.planned_date_begin <= now:
            return True
        if not self.date_deadline:
            return False
        timezone = ZoneInfo(user.tz or "UTC")
        utc_now = fields.Datetime.to_datetime(now).replace(tzinfo=UTC)
        utc_deadline = fields.Datetime.to_datetime(self.date_deadline).replace(
            tzinfo=UTC
        )
        today = utc_now.astimezone(timezone).date()
        local_deadline = utc_deadline.astimezone(timezone).date()
        return local_deadline <= today + timedelta(days=3)

    def _usl_reactivate_later_personal_stages(self, *, due_only=False, now=None):
        """Move validated assignee-owned Later links to their Inbox stages."""
        tasks = self.sudo().filtered(
            lambda task: task.active and task.state not in CLOSED_STATES
        )
        if not tasks:
            return 0

        PersonalStage = self.env["project.task.stage.personal"].sudo()
        later_links = PersonalStage.search(
            [
                ("task_id", "in", tasks.ids),
                ("task_id.active", "=", True),
                ("task_id.state", "not in", list(CLOSED_STATES)),
                ("user_id.active", "=", True),
                ("user_id.share", "=", False),
                ("stage_id.usl_reactivation_role", "=", "later"),
            ]
        )
        inbox_stages = self.env["project.task.type"].sudo().search(
            [
                ("user_id", "in", later_links.user_id.ids),
                ("active", "=", True),
                ("usl_reactivation_role", "=", "inbox"),
            ]
        )
        inbox_by_user = {stage.user_id.id: stage for stage in inbox_stages}
        links_by_inbox = {}
        for link in later_links:
            inbox = inbox_by_user.get(link.user_id.id)
            if (
                not inbox
                or link.user_id not in link.task_id.user_ids
                or (due_only and not link.task_id._usl_is_due_for_user(link.user_id, now))
            ):
                continue
            links_by_inbox.setdefault(inbox, PersonalStage)
            links_by_inbox[inbox] |= link
        for inbox, links in links_by_inbox.items():
            links.write({"stage_id": inbox.id})
        return sum(len(links) for links in links_by_inbox.values())

    @api.model
    def _cron_usl_reactivate_later_tasks(self):
        later_links = self.env["project.task.stage.personal"].sudo().search(
            [
                ("task_id.active", "=", True),
                ("task_id.state", "not in", list(CLOSED_STATES)),
                ("user_id.active", "=", True),
                ("user_id.share", "=", False),
                ("stage_id.usl_reactivation_role", "=", "later"),
            ]
        )
        return later_links.task_id._usl_reactivate_later_personal_stages(
            due_only=True,
            now=fields.Datetime.now(),
        )

    def _usl_reactivation_snapshot(self):
        return {
            task.id: {
                "stage_id": task.stage_id.id,
                "state": task.state,
                "blocked": task.is_blocked_by_dependences(),
                "planned_date_begin": task.planned_date_begin,
                "date_deadline": task.date_deadline,
            }
            for task in self
        }

    def write(self, values):
        if self.env.context.get("usl_skip_task_reactivation"):
            return super().write(values)

        event_fields = {
            "stage_id",
            "state",
            "depend_on_ids",
            "parent_id",
            "active",
            "planned_date_begin",
            "date_deadline",
        }
        if not event_fields.intersection(values):
            return super().write(values)

        tracked_tasks = self.exists()
        task_before = tracked_tasks._usl_reactivation_snapshot()
        dependent_tasks = (
            tracked_tasks.sudo().dependent_ids
            if "state" in values
            else self.browse().sudo()
        )
        dependent_blocked_before = {
            task.id: task.is_blocked_by_dependences() for task in dependent_tasks
        }
        old_parents = tracked_tasks.sudo().parent_id
        new_parents = (
            self.sudo().browse(values["parent_id"])
            if values.get("parent_id")
            else self.browse().sudo()
        )
        parents = old_parents | new_parents
        parent_complete_before = {
            parent.id: parent._usl_has_completed_direct_subtasks()
            for parent in parents
        }

        result = super().write(values)

        candidates = self.browse()
        if "stage_id" in values or "state" in values:
            candidates |= tracked_tasks.filtered(
                lambda task: (
                    ("stage_id" in values and task.stage_id.id != task_before[task.id]["stage_id"])
                    or ("state" in values and task.state != task_before[task.id]["state"])
                )
            )
        if "depend_on_ids" in values:
            candidates |= tracked_tasks.filtered(
                lambda task: task_before[task.id]["blocked"]
                and not task.is_blocked_by_dependences()
            )
        if "state" in values:
            candidates |= dependent_tasks.filtered(
                lambda task: dependent_blocked_before.get(task.id, False)
                and not task.is_blocked_by_dependences()
            )
        if {"state", "parent_id", "active"}.intersection(values):
            parents |= tracked_tasks.sudo().parent_id
            candidates |= parents.filtered(
                lambda parent: (
                    not parent_complete_before.get(parent.id, False)
                    and parent._usl_has_completed_direct_subtasks()
                )
            )
        if {"planned_date_begin", "date_deadline"}.intersection(values):
            changed_date_tasks = tracked_tasks.filtered(
                lambda task: any(
                    field_name in values
                    and task[field_name] != task_before.get(task.id, {}).get(field_name)
                    for field_name in ("planned_date_begin", "date_deadline")
                )
            )
            if changed_date_tasks:
                changed_date_tasks._usl_reactivate_later_personal_stages(
                    due_only=True,
                )
        candidates.with_context(
            usl_skip_task_reactivation=True
        )._usl_reactivate_later_personal_stages()
        return result

    def unlink(self):
        parents = self.sudo().parent_id
        parent_complete_before = {
            parent.id: parent._usl_has_completed_direct_subtasks()
            for parent in parents
        }
        result = super().unlink()
        parents.filtered(
            lambda parent: (
                not parent_complete_before.get(parent.id, False)
                and parent._usl_has_completed_direct_subtasks()
            )
        )._usl_reactivate_later_personal_stages()
        return result

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
