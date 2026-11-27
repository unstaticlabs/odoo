from collections import defaultdict
from datetime import UTC, timedelta
from zoneinfo import ZoneInfo

from odoo import _, api, fields, models, tools

from odoo.addons.project.models.project_task import CLOSED_STATES

from .task_reactivation import RETURN_ORDER, RETURN_REASONS, RETURN_WRITE, check_return_write


_SKIP_REACTIVATION = object()


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

    usl_return_reasons = fields.Json(readonly=True, copy=False)
    usl_project_return_badge = fields.Json(compute="_compute_usl_project_return_badge")
    usl_personal_return_badge = fields.Json(compute="_compute_usl_personal_return_badge")

    @api.model_create_multi
    def create(self, vals_list):
        check_return_write(self, vals_list)
        return super().create(vals_list)

    @api.depends("usl_return_reasons", "stage_id", "active", "state", "project_id.active",
                 "project_id.type_ids.sequence", "project_id.type_ids.active", "project_id.type_ids.fold")
    def _compute_usl_project_return_badge(self):
        self.usl_project_return_badge = False
        tasks = self.filtered(lambda task: task.usl_return_reasons and task.active
                              and task.project_id.active and task.state not in CLOSED_STATES)
        for project, batch in tasks.grouped("project_id").items():
            inbox = project.with_context(active_test=True)._get_default_task_stage()
            for task in batch.filtered(lambda task: task.stage_id == inbox):
                task.usl_project_return_badge = task.usl_return_reasons

    @api.depends_context("uid")
    @api.depends("personal_stage_id.stage_id", "personal_stage_id.usl_return_reasons", "active", "state")
    def _compute_usl_personal_return_badge(self):
        self.usl_personal_return_badge = False
        links = self.env["project.task.stage.personal"].search([
            ("task_id", "in", self.ids), ("user_id", "=", self.env.uid),
        ])
        if not links:
            return
        inbox = self.env["project.task.type"]._usl_personal_inboxes(self.env.user).get(self.env.uid)
        if inbox:
            reasons = {link.task_id.id: link.usl_return_reasons for link in links if link.stage_id == inbox}
            for task in self:
                if task.active and task.state not in CLOSED_STATES:
                    task.usl_personal_return_badge = reasons.get(task.id, False)

    def _get_default_personal_stage_create_vals(self, user_id):
        values = super()._get_default_personal_stage_create_vals(user_id)
        open_stage_values = [value for value in values if not value.get("fold")]
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

    def _usl_date_return_reasons(self, tz, now):
        self.ensure_one()
        reasons = set()
        if self.planned_date_begin and self.planned_date_begin <= now:
            reasons.add("start_reached")
        if self.date_deadline:
            timezone = ZoneInfo(tz or "UTC")
            today = now.replace(tzinfo=UTC).astimezone(timezone).date()
            deadline = self.date_deadline.replace(tzinfo=UTC).astimezone(timezone).date()
            days = (deadline - today).days
            if days <= 3:
                reasons.add("overdue" if days < 0 else "due_today" if days == 0 else "due_soon")
        return reasons

    def _usl_is_due_for_user(self, user, now=None):
        return self._usl_is_due_in_timezone(user.tz, now)

    def _usl_is_due_in_timezone(self, tz, now=None):
        return bool(self._usl_date_return_reasons(tz, now or fields.Datetime.now()))

    def _usl_return_reasons(self, events, date_checks, tz, now):
        reasons = events.get(self.id, set())
        if self.id in date_checks:
            reasons = reasons | self._usl_date_return_reasons(tz, now)
        return tuple(reason for reason in RETURN_ORDER if reason in reasons)

    def _usl_reactivate_later_personal_stages(self, events, date_checks, now):
        tasks = self.sudo().filtered(lambda task: task.active and task.state not in CLOSED_STATES)
        if not tasks:
            return 0
        PersonalStage = self.env["project.task.stage.personal"].sudo()
        later_links = PersonalStage.search([
            ("task_id", "in", tasks.ids),
            ("user_id.active", "=", True),
            ("user_id.share", "=", False),
            ("stage_id.active", "=", True),
            ("stage_id.usl_reactivation_role", "=", "later"),
        ])
        inboxes = self.env["project.task.type"].sudo()._usl_personal_inboxes(later_links.user_id)
        batches = defaultdict(list)
        for link in later_links:
            inbox = inboxes.get(link.user_id.id)
            if (not inbox or inbox == link.stage_id or link.stage_id.user_id != link.user_id
                    or link.user_id not in link.task_id.user_ids):
                continue
            reasons = link.task_id._usl_return_reasons(events, date_checks, link.user_id.tz, now)
            if reasons:
                batches[inbox.id, reasons].append(link.id)
        for (stage_id, reasons), ids in batches.items():
            PersonalStage.browse(ids).with_context(usl_return_write=RETURN_WRITE).write(
                {"stage_id": stage_id, RETURN_REASONS: list(reasons)},
            )
        return sum(map(len, batches.values()))

    def _usl_reactivate_later_project_stages(self, events, date_checks, now):
        tasks = self.sudo().filtered(
            lambda task: task.active and not task.is_template and task.state not in CLOSED_STATES
            and task.project_id.active and task.stage_id.active and not task.stage_id.user_id
            and task.stage_id.usl_reactivation_role == "later",
        )
        moved = 0
        for project, project_tasks in tasks.grouped("project_id").items():
            inbox = project.with_context(active_test=True)._get_default_task_stage()
            if not inbox or inbox.user_id:
                continue
            batches = defaultdict(list)
            for task in project_tasks:
                if task.stage_id == inbox or project not in task.stage_id.project_ids:
                    continue
                reasons = task._usl_return_reasons(events, date_checks, project.company_id.tz, now)
                if reasons:
                    batches[task.state, reasons].append(task.id)
            for (state, reasons), ids in batches.items():
                self.sudo().browse(ids).with_context(
                    usl_skip_task_reactivation=_SKIP_REACTIVATION, usl_return_write=RETURN_WRITE,
                ).write({"stage_id": inbox.id, "state": state, RETURN_REASONS: list(reasons)})
                moved += len(ids)
        return moved

    @api.model
    def _cron_usl_reactivate_later_tasks(self):
        now = fields.Datetime.now()
        later_links = self.env["project.task.stage.personal"].sudo().search(
            [
                ("task_id.active", "=", True),
                ("task_id.state", "not in", list(CLOSED_STATES)),
                ("user_id.active", "=", True),
                ("user_id.share", "=", False),
                ("stage_id.active", "=", True),
                ("stage_id.usl_reactivation_role", "=", "later"),
            ],
        )
        project_tasks = self.sudo().search(
            [
                ("active", "=", True),
                ("is_template", "=", False),
                ("state", "not in", list(CLOSED_STATES)),
                ("project_id.active", "=", True),
                ("stage_id.active", "=", True),
                ("stage_id.user_id", "=", False),
                ("stage_id.usl_reactivation_role", "=", "later"),
                "|",
                ("planned_date_begin", "<=", now),
                ("date_deadline", "<=", now + timedelta(days=5)),
            ],
        )
        return (
            later_links.task_id._usl_reactivate_later_personal_stages({}, set(later_links.task_id.ids), now)
            + project_tasks._usl_reactivate_later_project_stages({}, set(project_tasks.ids), now)
        )

    def _usl_reactivation_snapshot(self):
        return {
            task.id: {
                "stage_id": task.stage_id.id,
                "project_id": task.project_id.id,
                "state": task.state,
                "blocked": task.is_blocked_by_dependences(),
                "planned_date_begin": task.planned_date_begin,
                "date_deadline": task.date_deadline,
            }
            for task in self
        }

    def write(self, vals):
        check_return_write(self, [vals])
        if self.env.context.get("usl_skip_task_reactivation") is _SKIP_REACTIVATION:
            return super().write(vals)

        event_fields = {
            "stage_id",
            "project_id",
            "state",
            "depend_on_ids",
            "parent_id",
            "active",
            "planned_date_begin",
            "date_deadline",
        }
        if not event_fields.intersection(vals):
            return super().write(vals)

        tracked_tasks = self.exists()
        task_before = tracked_tasks._usl_reactivation_snapshot()
        dependent_tasks = (
            tracked_tasks.sudo().dependent_ids
            if "state" in vals
            else self.browse().sudo()
        )
        dependent_blocked_before = {
            task.id: task.is_blocked_by_dependences() for task in dependent_tasks
        }
        old_parents = tracked_tasks.sudo().parent_id
        new_parents = (
            self.sudo().browse(vals["parent_id"])
            if vals.get("parent_id")
            else self.browse().sudo()
        )
        parents = old_parents | new_parents
        parent_complete_before = {
            parent.id: parent._usl_has_completed_direct_subtasks()
            for parent in parents
        }

        result = super(
            ProjectTask,
            self.with_context(usl_skip_task_reactivation=_SKIP_REACTIVATION),
        ).write(vals)

        events = defaultdict(set)
        if "state" in vals:
            for task in tracked_tasks:
                previous = task_before[task.id]["state"]
                if task.state != previous and task.state not in CLOSED_STATES:
                    events[task.id].add("reopened" if previous in CLOSED_STATES else "status_changed")
        if "depend_on_ids" in vals:
            for task in tracked_tasks:
                if task_before[task.id]["blocked"] and not task.is_blocked_by_dependences():
                    events[task.id].add("unblocked")
        if "state" in vals:
            for task in dependent_tasks:
                if dependent_blocked_before[task.id] and not task.is_blocked_by_dependences():
                    events[task.id].add("unblocked")
        if {"state", "parent_id", "active"}.intersection(vals):
            for parent in parents | tracked_tasks.sudo().parent_id:
                if not parent_complete_before.get(parent.id, False) and parent._usl_has_completed_direct_subtasks():
                    events[parent.id].add("subtasks_closed")

        moved_tasks = tracked_tasks.filtered(
            lambda task: task.stage_id.id != task_before[task.id]["stage_id"],
        )
        closed = tracked_tasks.filtered(lambda task: not task.active or task.state in CLOSED_STATES)
        clear_project = (closed | moved_tasks | tracked_tasks.filtered(
            lambda task: task.project_id.id != task_before[task.id]["project_id"],
        )).filtered(RETURN_REASONS)
        if clear_project:
            super(ProjectTask, clear_project).write({RETURN_REASONS: False})
        if closed:
            links = self.env["project.task.stage.personal"].sudo().search([("task_id", "in", closed.ids)])
            links.filtered(RETURN_REASONS).with_context(usl_return_write=RETURN_WRITE).write({RETURN_REASONS: False})

        date_checks = {
            task.id for task in tracked_tasks
            if any(
                name in vals and task[name] != task_before[task.id][name]
                for name in ("planned_date_begin", "date_deadline")
            )
        }
        project_events = dict(events)
        for task in moved_tasks:
            events[task.id] = events[task.id] | {"project_stage_changed"}
        now = fields.Datetime.now()
        self.browse(events.keys() | date_checks)._usl_reactivate_later_personal_stages(events, date_checks, now)
        (self.browse(project_events.keys() | date_checks) - moved_tasks)._usl_reactivate_later_project_stages(
            project_events, date_checks, now,
        )
        return result

    def unlink(self):
        parents = self.sudo().parent_id
        parent_complete_before = {
            parent.id: parent._usl_has_completed_direct_subtasks()
            for parent in parents
        }
        result = super().unlink()
        candidates = parents.filtered(
            lambda parent: (
                not parent_complete_before.get(parent.id, False)
                and parent._usl_has_completed_direct_subtasks()
            ),
        )
        events = dict.fromkeys(candidates.ids, {"subtasks_closed"})
        now = fields.Datetime.now()
        candidates._usl_reactivate_later_personal_stages(events, set(), now)
        candidates._usl_reactivate_later_project_stages(events, set(), now)
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
            # The personal to-do alias identifies the inbox, not an assignee.
            # Native project mail also assigns users whose address matches To;
            # that can accidentally opt a service identity into the task.
            # Preserve explicit alias defaults and the original gateway headers.
            msg_dict = dict(msg_dict, to="")
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
