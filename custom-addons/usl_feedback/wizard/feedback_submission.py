import os
import re

from odoo import Command, _, api, fields, models
from odoo.exceptions import AccessError, UserError, ValidationError
from odoo.tools.mail import is_html_empty

RELEASE_SHA_RE = re.compile(r"^[0-9a-f]{40}$")
MAX_ATTACHMENTS = 10


class FeedbackSubmission(models.TransientModel):
    _name = "usl.feedback.submission"
    _description = "Product Feedback Submission"

    summary = fields.Char(string="Summary", required=True, size=160)
    description = fields.Html(string="Description", required=True, sanitize=True)
    category = fields.Selection(
        [
            ("bug", "Bug"),
            ("improvement", "Improvement"),
            ("question", "Question"),
            ("ux", "UX"),
        ],
        required=True,
        default="improvement",
    )
    priority = fields.Selection(
        [
            ("0", "Low priority"),
            ("1", "Medium priority"),
            ("2", "High priority"),
            ("3", "Urgent"),
        ],
        required=True,
        default="0",
    )
    attachment_ids = fields.Many2many(
        "ir.attachment",
        "usl_feedback_submission_attachment_rel",
        "submission_id",
        "attachment_id",
        string="Attachments",
    )
    include_page_context = fields.Boolean(
        string="Share current page context",
        default=True,
    )
    company_id = fields.Many2one(
        "res.company",
        string="Company",
        required=True,
        readonly=True,
        default=lambda self: self.env.company,
    )
    source_action_id = fields.Many2one("ir.actions.actions", readonly=True)
    source_model_name = fields.Char(readonly=True)
    source_record_id = fields.Integer(readonly=True)
    viewport_width = fields.Integer(readonly=True)
    viewport_height = fields.Integer(readonly=True)

    @api.constrains("summary", "description")
    def _check_required_content(self):
        for submission in self:
            if not (submission.summary or "").strip():
                raise ValidationError(_("Give your feedback a short summary."))
            if is_html_empty(submission.description):
                raise ValidationError(_("Describe what happened or what would help."))

    def _release_sha(self):
        parameter_sha = self.env["ir.config_parameter"].sudo().get_str(
            "usl.release.commit",
        )
        environment_sha = os.environ.get("USL_RELEASE_COMMIT")
        valid_values = {
            value
            for value in (parameter_sha, environment_sha)
            if value and RELEASE_SHA_RE.fullmatch(value)
        }
        if len(valid_values) > 1:
            raise UserError(
                _("Feedback is temporarily unavailable because the running release identity is inconsistent."),
            )
        if not valid_values:
            raise UserError(
                _("Feedback is temporarily unavailable because this Odoo release has no verified commit identity."),
            )
        return valid_values.pop()

    def _validated_page_context(self):
        self.ensure_one()
        if not self.include_page_context:
            return {}, False

        values = {
            "usl_feedback_context_included": True,
            "usl_feedback_viewport_width": self.viewport_width or False,
            "usl_feedback_viewport_height": self.viewport_height or False,
        }
        for dimension in (self.viewport_width, self.viewport_height):
            if dimension and not 1 <= dimension <= 16384:
                raise ValidationError(_("Viewport dimensions must be between 1 and 16384 pixels."))

        if self.source_action_id and self.source_action_id.exists():
            if self.source_action_id.has_access("read"):
                values["usl_feedback_source_action_id"] = self.source_action_id.id

        model_name = (self.source_model_name or "").strip()
        record_id = self.source_record_id
        context_omitted = False
        if model_name:
            model = self.env.get(model_name)
            if model is None or model.is_transient() or not model.browse().has_access("read"):
                context_omitted = True
            elif record_id:
                record = model.browse(record_id).exists()
                if not record or not record.has_access("read"):
                    context_omitted = True
                else:
                    values.update(
                        {
                            "usl_feedback_source_model_id": self.env["ir.model"]
                            .sudo()
                            ._get_id(model_name),
                            "usl_feedback_source_res_id": record.id,
                        },
                    )
            else:
                values["usl_feedback_source_model_id"] = (
                    self.env["ir.model"].sudo()._get_id(model_name)
                )
        elif record_id:
            context_omitted = True
        return values, context_omitted

    def _validated_attachments(self):
        self.ensure_one()
        attachments = self.attachment_ids
        if len(attachments) > MAX_ATTACHMENTS:
            raise ValidationError(_("Attach at most 10 files to one feedback item."))
        for attachment in attachments.sudo():
            if attachment.create_uid != self.env.user:
                raise AccessError(_("You can submit only attachments that you uploaded."))
            if attachment.res_model not in (False, self._name) or attachment.res_id not in (
                False,
                self.id,
            ):
                raise AccessError(_("An attachment is already linked to another record."))
        return attachments

    def action_submit(self):
        self.ensure_one()
        if not self.env.user._is_internal():
            raise AccessError(_("Only authenticated internal users can send product feedback."))
        self.check_access("read")
        self._check_required_content()
        if self.company_id != self.env.company or self.company_id not in self.env.user.company_ids:
            raise AccessError(_("Submit feedback from the active company shown in Odoo."))

        project = self.env.ref("usl_feedback.project_product_feedback").sudo()
        stage = self.env.ref("usl_feedback.stage_feedback_new").sudo()
        tag = self.env.ref(f"usl_feedback.tag_feedback_{self.category}").sudo()
        release_sha = self._release_sha()
        page_context, context_omitted = self._validated_page_context()
        attachments = self._validated_attachments()

        values = {
            "name": self.summary.strip(),
            "description": self.description,
            "project_id": project.id,
            "stage_id": stage.id,
            "company_id": self.env.company.id,
            "priority": self.priority,
            "tag_ids": [Command.set(tag.ids)],
            "usl_feedback_reporter_id": self.env.user.id,
            "usl_feedback_category": self.category,
            "usl_feedback_release_sha": release_sha,
            "usl_feedback_context_included": False,
            **page_context,
        }
        task = (
            self.env["project.task"]
            .with_user(self.env.user)
            .sudo()
            .with_context(usl_feedback_submission=True)
            .create(values)
        )
        if attachments:
            attachments.sudo().write({"res_model": "project.task", "res_id": task.id})
        task.with_user(self.env.user).sudo().message_post(
            body=_("Feedback submitted from Odoo by %(reporter)s.", reporter=self.env.user.name),
            subtype_xmlid="mail.mt_note",
        )
        self.unlink()

        message = _("Feedback sent. You can follow its status and replies from My feedback.")
        if context_omitted:
            message = _(
                "Feedback sent. The source record context was omitted because it is not readable anymore.",
            )
        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {
                "title": _("Feedback sent"),
                "message": message,
                "type": "success",
                "sticky": False,
                "next": {"type": "ir.actions.act_window_close"},
            },
        }

    def unlink(self):
        submission_ids = set(self.ids)
        pending = self.mapped("attachment_ids").sudo().filtered(
            lambda attachment: attachment.res_model in (False, self._name)
            and (not attachment.res_id or attachment.res_id in submission_ids),
        )
        result = super().unlink()
        if pending:
            pending.unlink()
        return result
