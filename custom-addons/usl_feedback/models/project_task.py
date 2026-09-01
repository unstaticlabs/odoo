import re

from markupsafe import Markup, escape

from odoo import _, api, fields, models
from odoo.exceptions import AccessError, UserError, ValidationError
from odoo.tools.mail import html2plaintext

RELEASE_SHA_RE = re.compile(r"^[0-9a-f]{40}$")
FEEDBACK_CATEGORIES = [
    ("bug", "Bug"),
    ("improvement", "Improvement"),
    ("question", "Question"),
    ("ux", "UX"),
]
AGENT_STATES = [
    ("waiting", "Needs details"),
    ("queued", "Queued"),
    ("processing", "Reviewing"),
    ("ready", "Ready to send"),
    ("error", "Needs retry"),
    ("triaged", "Sent to product team"),
]


class ProjectTask(models.Model):
    _inherit = "project.task"

    usl_feedback_reporter_id = fields.Many2one(
        "res.users", string="Reporter", readonly=True, copy=False, index=True, ondelete="restrict",
    )
    usl_feedback_company_id = fields.Many2one(
        "res.company", string="Source company", readonly=True, copy=False, index=True, ondelete="restrict",
    )
    usl_feedback_category = fields.Selection(
        FEEDBACK_CATEGORIES, string="Category", readonly=True, copy=False, index=True,
    )
    usl_feedback_context_included = fields.Boolean(
        string="Page details shared", readonly=True, copy=False,
    )
    usl_feedback_source_action_id = fields.Many2one(
        "ir.actions.actions", string="Source action", readonly=True, copy=False, ondelete="set null",
    )
    usl_feedback_source_model_id = fields.Many2one(
        "ir.model", string="Source model", readonly=True, copy=False, ondelete="set null",
    )
    usl_feedback_source_res_id = fields.Integer(
        string="Source record ID", readonly=True, copy=False,
    )
    usl_feedback_viewport_width = fields.Integer(
        string="Viewport width", readonly=True, copy=False,
    )
    usl_feedback_viewport_height = fields.Integer(
        string="Viewport height", readonly=True, copy=False,
    )
    usl_feedback_release_sha = fields.Char(
        string="Release SHA", readonly=True, copy=False, size=40, index=True,
    )
    usl_feedback_screenshot_attachment_id = fields.Many2one(
        "ir.attachment", string="Screenshot", readonly=True, copy=False, ondelete="set null",
    )
    usl_feedback_related_task_ids = fields.Many2many(
        "project.task",
        "usl_feedback_related_task_rel",
        "task_id",
        "related_task_id",
        string="Related feedback",
        readonly=True,
        copy=False,
    )
    usl_feedback_agent_state = fields.Selection(
        AGENT_STATES,
        string="Assistant status",
        readonly=True,
        copy=False,
        index=True,
    )
    usl_feedback_agent_error = fields.Char(
        string="Assistant error", readonly=True, copy=False,
    )
    usl_feedback_latest_interaction_id = fields.Char(
        string="Gemini Interaction",
        readonly=True,
        copy=False,
        groups="usl_feedback.group_feedback_maintainer,base.group_system",
    )
    usl_feedback_pending_message_id = fields.Many2one(
        "mail.message",
        string="Pending Reporter Message",
        readonly=True,
        copy=False,
        groups="usl_feedback.group_feedback_maintainer,base.group_system",
        ondelete="set null",
    )
    usl_feedback_can_manage = fields.Boolean(compute="_compute_usl_feedback_can_manage")

    @api.depends_context("uid")
    def _compute_usl_feedback_can_manage(self):
        allowed = self._usl_feedback_is_maintainer()
        for task in self:
            task.usl_feedback_can_manage = allowed

    def _usl_feedback_is_maintainer(self):
        return self.env.user.has_group("usl_feedback.group_feedback_maintainer")

    def _usl_feedback_is_task(self):
        self.ensure_one()
        return bool(self.project_id.usl_feedback_project)

    def _creation_subtype(self):
        self.ensure_one()
        if self._usl_feedback_is_task():
            return self.env["mail.message.subtype"]
        return super()._creation_subtype()

    def _creation_message(self):
        self.ensure_one()
        if not self._usl_feedback_is_task():
            return super()._creation_message()
        task = self.with_context(
            lang=self.usl_feedback_reporter_id.lang or self.env.lang,
        )
        feedback_link = task._get_html_link(
            title=task.env._("Feedback #%(feedback_id)s", feedback_id=self.id),
        )
        return task.env._(
            "%(feedback_link)s has been created in the %(project_name)s project.",
            feedback_link=feedback_link,
            project_name=task.project_id.display_name,
        )

    @api.constrains(
        "project_id",
        "stage_id",
        "company_id",
        "usl_feedback_reporter_id",
        "usl_feedback_company_id",
        "usl_feedback_category",
        "usl_feedback_context_included",
        "usl_feedback_source_action_id",
        "usl_feedback_source_model_id",
        "usl_feedback_source_res_id",
        "usl_feedback_viewport_width",
        "usl_feedback_viewport_height",
        "usl_feedback_release_sha",
        "usl_feedback_agent_state",
    )
    def _check_usl_feedback_metadata(self):
        inbox = self.env.ref("usl_feedback.stage_feedback_new", raise_if_not_found=False)
        for task in self:
            is_feedback = bool(task.project_id.usl_feedback_project)
            has_reporter = bool(task.usl_feedback_reporter_id)
            if is_feedback != has_reporter:
                raise ValidationError(
                    _("Feedback tasks must stay in the governed feedback Project and have a reporter."),
                )
            if not is_feedback:
                continue
            if task.company_id:
                raise ValidationError(_("Feedback tasks must remain company-neutral on the shared board."))
            if not task.usl_feedback_company_id:
                raise ValidationError(_("A source company is required."))
            if task.usl_feedback_company_id not in task.usl_feedback_reporter_id.company_ids:
                raise ValidationError(_("The source company must be available to the reporter."))
            if inbox and task.stage_id != inbox and not task.usl_feedback_category:
                raise ValidationError(_("A feedback category is required outside the Inbox."))
            if not RELEASE_SHA_RE.fullmatch(task.usl_feedback_release_sha or ""):
                raise ValidationError(_("Feedback must carry an exact 40-character release SHA."))
            if not task.usl_feedback_agent_state:
                raise ValidationError(_("A feedback assistant state is required."))
            if task.usl_feedback_source_res_id and not task.usl_feedback_source_model_id:
                raise ValidationError(_("A source record requires a source model."))
            if not task.usl_feedback_context_included and any(
                (
                    task.usl_feedback_source_action_id,
                    task.usl_feedback_source_model_id,
                    task.usl_feedback_source_res_id,
                    task.usl_feedback_viewport_width,
                    task.usl_feedback_viewport_height,
                ),
            ):
                raise ValidationError(_("Page context cannot be retained after the reporter opts out."))
            for value in (task.usl_feedback_viewport_width, task.usl_feedback_viewport_height):
                if value and not 1 <= value <= 16384:
                    raise ValidationError(_("Viewport dimensions must be between 1 and 16384 pixels."))

    @api.model_create_multi
    def create(self, vals_list):
        if not self.env.su and not self._usl_feedback_is_maintainer():
            project_ids = {values.get("project_id") for values in vals_list if values.get("project_id")}
            protected = self.env["project.project"].sudo().browse(project_ids).filtered(
                "usl_feedback_project",
            )
            if protected or any(values.get("usl_feedback_reporter_id") for values in vals_list):
                raise AccessError(_("Use the feedback conversation to create product feedback."))
        return super().create(vals_list)

    def write(self, values):
        if not self.env.su and not self._usl_feedback_is_maintainer():
            self.check_access("write")
            if self.sudo().filtered("usl_feedback_reporter_id") or values.get(
                "usl_feedback_reporter_id",
            ):
                raise AccessError(
                    _("Use the conversation to add details. The product team manages task fields."),
                )
            project_id = values.get("project_id")
            if project_id and self.env["project.project"].sudo().browse(project_id).usl_feedback_project:
                raise AccessError(_("Use the feedback conversation to create product feedback."))
        return super().write(values)

    def unlink(self):
        if not self.env.su and not self._usl_feedback_is_maintainer():
            self.check_access("unlink")
            if self.sudo().filtered("usl_feedback_reporter_id"):
                raise AccessError(_("Feedback cards can only be deleted by feedback maintainers."))
        return super().unlink()

    def message_subscribe(self, partner_ids=None, subtype_ids=None):
        feedback = self.filtered("usl_feedback_reporter_id")
        if feedback and not self.env.su and not self._usl_feedback_is_maintainer():
            if set(partner_ids or ()) - {self.env.user.partner_id.id}:
                raise AccessError(_("You may only follow feedback for yourself."))
        return super().message_subscribe(partner_ids=partner_ids, subtype_ids=subtype_ids)

    def message_unsubscribe(self, partner_ids=None):
        feedback = self.filtered("usl_feedback_reporter_id")
        if feedback and not self.env.su and not self._usl_feedback_is_maintainer():
            if set(partner_ids or ()) - {self.env.user.partner_id.id}:
                raise AccessError(_("You may only unfollow feedback for yourself."))
        return super().message_unsubscribe(partner_ids=partner_ids)

    def message_post(self, **kwargs):
        messages = super().message_post(**kwargs)
        if self.env.context.get("usl_feedback_skip_agent") or self.env.su:
            return messages
        if len(self) == 1:
            task = self
            if (
                task.usl_feedback_reporter_id == self.env.user
                and task.usl_feedback_agent_state != "triaged"
                and task.stage_id == self.env.ref("usl_feedback.stage_feedback_new")
                and kwargs.get("message_type", "notification") == "comment"
            ):
                self.env["usl.feedback.agent.run"].sudo()._queue_message(task.sudo(), messages.sudo())
        return messages

    def _message_get_suggested_recipients_batch(self, *args, **kwargs):
        suggested = super()._message_get_suggested_recipients_batch(*args, **kwargs)
        # Feedback chatter is an Odoo-inbox conversation. In particular, the
        # synthetic assistant author must never be proposed as an email target
        # when an employee replies to the latest assistant message.
        for task in self.filtered("usl_feedback_reporter_id"):
            suggested[task.id] = []
        return suggested

    def _notify_thread_by_email(self, message, recipients_data, **kwargs):
        if self.usl_feedback_reporter_id:
            return True
        return super()._notify_thread_by_email(message, recipients_data, **kwargs)

    def _usl_feedback_state_payload(self):
        self.ensure_one()
        self.check_access("read")
        screenshot = self.usl_feedback_screenshot_attachment_id
        return {
            "id": self.id,
            "name": self.name,
            "description": self.description or "",
            "description_text": html2plaintext(self.description or "").strip(),
            "category": (
                self.env._(dict(FEEDBACK_CATEGORIES)[self.usl_feedback_category])
                if self.usl_feedback_category
                else False
            ),
            "priority": self.priority,
            "stage": self.stage_id.name,
            "agent_state": self.usl_feedback_agent_state,
            "agent_error": self.usl_feedback_agent_error or False,
            "reporter_id": self.usl_feedback_reporter_id.id,
            "is_reporter": self.usl_feedback_reporter_id == self.env.user,
            "can_manage": self.usl_feedback_can_manage,
            "screenshot_attachment_id": screenshot.id or False,
            "screenshot_name": screenshot.name or False,
            "related_feedback": [
                {"id": task.id, "name": task.name} for task in self.usl_feedback_related_task_ids
            ],
        }

    @api.model
    def feedback_recent(self, limit=8):
        limit = min(max(int(limit), 1), 20)
        tasks = self.search(
            [
                ("project_id.usl_feedback_project", "=", True),
                ("usl_feedback_reporter_id", "=", self.env.user.id),
            ],
            order="write_date desc, id desc",
            limit=limit,
        )
        return [task._usl_feedback_state_payload() for task in tasks]

    def feedback_conversation_state(self):
        self.ensure_one()
        if not self._usl_feedback_is_task():
            raise UserError(_("This is not a feedback card."))
        return self._usl_feedback_state_payload()

    def feedback_poll_agent(self):
        self.ensure_one()
        if not self._usl_feedback_is_task():
            raise UserError(_("This is not a feedback card."))
        if self.usl_feedback_reporter_id != self.env.user and not self.usl_feedback_can_manage:
            raise AccessError(_("Only the reporter or a feedback maintainer can poll this conversation."))
        self.env["usl.feedback.agent.run"].sudo()._process_task(self.sudo())
        self.invalidate_recordset()
        return self._usl_feedback_state_payload()

    def feedback_retry_agent(self):
        self.ensure_one()
        if self.usl_feedback_reporter_id != self.env.user:
            raise AccessError(_("Only the reporter can retry this feedback conversation."))
        if self.usl_feedback_agent_state != "error":
            raise UserError(_("This feedback conversation does not need a retry."))
        last_message = self.env["mail.message"].search(
            [
                ("model", "=", self._name),
                ("res_id", "=", self.id),
                ("author_id", "=", self.env.user.partner_id.id),
                ("message_type", "=", "comment"),
            ],
            order="id desc",
            limit=1,
        )
        if not last_message:
            raise UserError(_("Add a message before retrying."))
        self.env["usl.feedback.agent.run"].sudo()._queue_message(self.sudo(), last_message.sudo())
        return self._usl_feedback_state_payload()

    def feedback_confirm_triage(self):
        self.ensure_one()
        if self.usl_feedback_reporter_id != self.env.user:
            raise AccessError(_("Only the reporter can send this feedback to the product team."))
        if self.usl_feedback_agent_state != "ready":
            raise UserError(_("This feedback is not ready to send."))
        triage = self.env.ref("usl_feedback.stage_feedback_triaged")
        self.sudo().with_context(tracking_disable=True).write(
            {
                "stage_id": triage.id,
                "usl_feedback_agent_state": "triaged",
                "usl_feedback_agent_error": False,
            },
        )
        self._track_discard()
        self.with_context(usl_feedback_skip_agent=True).message_post(
            body=_("Sent to the product team."),
            author_id=self.env.ref("usl_feedback.partner_feedback_assistant").id,
            message_type="comment",
            subtype_xmlid="mail.mt_comment",
        )
        return self._usl_feedback_state_payload()

    def _usl_feedback_apply_agent_result(self, result, interaction_id):
        """Validate and apply one provider result. This private method is not RPC-callable."""
        self.ensure_one()
        if not self.env.su or not self._usl_feedback_is_task():
            raise AccessError(_("Only the feedback service can apply an assistant result."))
        status = str(result.get("status") or "").strip().lower()
        status = {
            "ready": "ready_for_confirmation",
            "complete": "ready_for_confirmation",
            "completed": "ready_for_confirmation",
            "clarification": "needs_clarification",
            "needs_info": "needs_clarification",
            "needs_information": "needs_clarification",
        }.get(status, status)
        if status not in {"needs_clarification", "ready_for_confirmation"}:
            status = "needs_clarification"
        assistant_message = str(result.get("assistant_message") or "").strip()[:4000]
        questions = result.get("questions") or []
        if not isinstance(questions, list):
            questions = []
        questions = [
            str(question).strip()[:500]
            for question in questions[:3]
            if str(question).strip()
        ]
        category = str(result.get("category") or "").strip().lower()
        if category not in dict(FEEDBACK_CATEGORIES):
            category = False
        try:
            priority = str(int(result.get("priority", 0)))
        except (TypeError, ValueError):
            priority = "0"
        if priority not in {"0", "1", "2", "3"}:
            priority = "0"
        summary = str(result.get("summary") or "").strip()[:200]
        description = str(result.get("description") or "").strip()[:12000]
        if status == "ready_for_confirmation" and not all((summary, description, category)):
            status = "needs_clarification"
            if not questions:
                questions = [_('What happened, and what did you expect instead?')]
        if status == "needs_clarification" and not questions:
            questions = [_('What happened, and what did you expect instead?')]
        if not assistant_message:
            assistant_message = (
                _("I prepared a draft. Review it before sending.")
                if status == "ready_for_confirmation"
                else _("I need one more detail to prepare the draft.")
            )
        related_values = result.get("related_feedback_ids") or []
        if not isinstance(related_values, (list, tuple)):
            related_values = []
        related_ids = []
        for value in related_values:
            try:
                related_ids.append(int(value))
            except (TypeError, ValueError):
                continue
        related = self.env["project.task"].browse(related_ids[:5]).exists().filtered(
            lambda task: task.project_id == self.project_id and task != self,
        )
        values = {
            "usl_feedback_agent_state": "ready" if status == "ready_for_confirmation" else "waiting",
            "usl_feedback_agent_error": False,
            "usl_feedback_latest_interaction_id": interaction_id,
            "usl_feedback_pending_message_id": False,
            "usl_feedback_related_task_ids": [(6, 0, related.ids)],
            "priority": priority,
        }
        if summary:
            values["name"] = summary
        if description:
            values["description"] = Markup("<p>%s</p>") % escape(description).replace(
                "\n", Markup("<br>"))
        if category:
            values["usl_feedback_category"] = category
            tag = self.env.ref(f"usl_feedback.tag_feedback_{category}", raise_if_not_found=False)
            if tag:
                values["tag_ids"] = [(6, 0, tag.ids)]
        self.with_context(tracking_disable=True).write(values)
        self._track_discard()
        body = escape(assistant_message)
        if questions:
            body += Markup("<ul>%s</ul>") % Markup().join(
                Markup("<li>%s</li>") % escape(question) for question in questions
            )
        self.with_context(usl_feedback_skip_agent=True).message_post(
            body=body,
            author_id=self.env.ref("usl_feedback.partner_feedback_assistant").id,
            message_type="comment",
            subtype_xmlid="mail.mt_comment",
        )
