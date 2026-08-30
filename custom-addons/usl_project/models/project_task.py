from markupsafe import Markup

from odoo import _, api, fields, models
from odoo.tools.misc import format_datetime


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
    usl_stage_duration_history = fields.Html(
        string="Time Spent by Stage",
        compute="_compute_usl_stage_duration_history",
        sanitize=True,
    )

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

    @staticmethod
    def _format_duration_minutes(value):
        total_minutes = max(int(round(float(value or 0))), 0)
        days, remaining = divmod(total_minutes, 24 * 60)
        hours, minutes = divmod(remaining, 60)
        parts = []
        if days:
            parts.append(f"{days} d")
        if hours:
            parts.append(f"{hours} h")
        if minutes or not parts:
            parts.append(f"{minutes} min")
        return " ".join(parts)

    @api.depends("duration_tracking", "stage_id", "stage_id.name")
    def _compute_usl_stage_duration_history(self):
        now = fields.Datetime.now()
        for task in self:
            ledger = task.duration_tracking or {}
            stage_ids = set()
            for key in ledger:
                if key in {"d", "s"}:
                    continue
                try:
                    stage_ids.add(int(key))
                except (TypeError, ValueError):
                    continue
            current_stage_id = ledger.get("s") or task.stage_id.id
            current_stage_id = int(current_stage_id) if current_stage_id else False
            if current_stage_id:
                stage_ids.add(current_stage_id)
            if not stage_ids:
                task.usl_stage_duration_history = False
                continue

            stages = self.env["project.task.type"].with_context(
                active_test=False,
            ).browse(sorted(stage_ids)).exists()
            stages_by_id = {stage.id: stage for stage in stages}
            stage_started_at = fields.Datetime.to_datetime(ledger.get("d"))
            rows = []
            for stage_id in sorted(
                stage_ids,
                key=lambda item: (
                    stages_by_id[item].sequence if item in stages_by_id else 1_000_000,
                    item,
                ),
            ):
                stage = stages_by_id.get(stage_id)
                is_current = stage_id == current_stage_id
                minutes = float(ledger.get(str(stage_id), 0) or 0)
                if is_current and stage_started_at:
                    minutes += max(
                        (now - stage_started_at).total_seconds() / 60,
                        0,
                    )
                stage_name = (
                    stage.display_name
                    if stage
                    else _("Historical stage %(stage_id)s", stage_id=stage_id)
                )
                meta = Markup("")
                row_classes = ["o_usl_task_stage_duration_row"]
                if stage and not stage.active:
                    row_classes.append("o_usl_task_stage_duration_row_historical")
                    meta += Markup(
                        '<span class="badge rounded-pill text-bg-light">{}</span>'
                    ).format(_("Historical stage"))
                if is_current:
                    row_classes.append("o_usl_task_stage_duration_row_current")
                    meta += Markup(
                        '<span class="badge rounded-pill text-bg-primary">{}</span>'
                    ).format(_("Current stage"))
                    if stage_started_at:
                        meta += Markup(
                            '<span class="o_usl_task_stage_duration_since">{}</span>'
                        ).format(
                            _(
                                "Since %(date)s",
                                date=format_datetime(self.env, stage_started_at),
                            ),
                        )
                rows.append(
                    Markup(
                        '<div class="{}">'
                        '<div class="o_usl_task_stage_duration_identity">'
                        '<span class="o_usl_task_stage_duration_name">{}</span>{}'
                        '</div>'
                        '<strong class="o_usl_task_stage_duration_value">{}</strong>'
                        '</div>'
                    ).format(
                        " ".join(row_classes),
                        stage_name,
                        meta,
                        self._format_duration_minutes(minutes),
                    )
                )

            task.usl_stage_duration_history = Markup(
                '<div class="o_usl_task_stage_duration_rows">{}</div>'
            ).format(Markup("").join(rows))
