import base64
import binascii
import os
import re

from markupsafe import Markup, escape

from odoo import Command, _, api, fields, models
from odoo.exceptions import AccessError, UserError, ValidationError

RELEASE_SHA_RE = re.compile(r"^[0-9a-f]{40}$")
MAX_ATTACHMENTS = 10
MAX_ATTACHMENT_BYTES = 10 * 1024 * 1024
MAX_SCREENSHOT_BYTES = 5 * 1024 * 1024
SCREENSHOT_MIMETYPES = {"image/jpeg", "image/png"}


class FeedbackSubmission(models.TransientModel):
    """Short-lived chat draft; the canonical feedback record is always project.task."""

    _name = "usl.feedback.submission"
    _description = "Product Feedback Conversation Draft"

    attachment_ids = fields.Many2many(
        "ir.attachment",
        "usl_feedback_submission_attachment_rel",
        "submission_id",
        "attachment_id",
        string="Attachments",
    )
    screenshot_attachment_id = fields.Many2one(
        "ir.attachment", string="Screenshot", ondelete="set null",
    )
    include_page_context = fields.Boolean(string="Share page details", default=True)
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

    def _require_internal_user(self):
        if not self.env.user._is_internal():
            raise AccessError(_("Only authenticated internal users can send product feedback."))

    @api.model
    def feedback_start(self, page_context=None):
        self._require_internal_user()
        page_context = page_context if isinstance(page_context, dict) else {}
        values = {
            "company_id": self.env.company.id,
            "source_action_id": self._safe_integer(page_context.get("action_id")) or False,
            "source_model_name": str(page_context.get("model") or "")[:128],
            "source_record_id": self._safe_integer(page_context.get("res_id")),
            "viewport_width": self._safe_integer(page_context.get("viewport_width")),
            "viewport_height": self._safe_integer(page_context.get("viewport_height")),
            "include_page_context": True,
        }
        draft = self.create(values)
        context_available = bool(
            values["source_action_id"] or values["source_model_name"] or values["source_record_id"],
        )
        return {
            "draft_id": draft.id,
            "context_available": context_available,
            "include_page_context": context_available,
            "recent": self.env["project.task"].feedback_recent(),
        }

    @staticmethod
    def _safe_integer(value):
        try:
            value = int(value or 0)
        except (TypeError, ValueError):
            return 0
        return value if value > 0 else 0

    def feedback_add_attachment(self, name, mimetype, data, is_screenshot=False):
        self.ensure_one()
        self._require_internal_user()
        self.check_access("write")
        if len(self.attachment_ids) >= MAX_ATTACHMENTS:
            raise ValidationError(_("Attach at most 10 files to one feedback item."))
        name = os.path.basename(str(name or "attachment"))[:255]
        mimetype = str(mimetype or "application/octet-stream")[:128]
        if is_screenshot and mimetype not in SCREENSHOT_MIMETYPES:
            raise ValidationError(_("Screenshots must be JPEG or PNG images."))
        try:
            raw = base64.b64decode(data or "", validate=True)
        except (binascii.Error, ValueError, TypeError) as error:
            raise ValidationError(_("The attachment data is invalid.")) from error
        maximum = MAX_SCREENSHOT_BYTES if is_screenshot else MAX_ATTACHMENT_BYTES
        if not raw or len(raw) > maximum:
            raise ValidationError(
                _("The screenshot must be 5 MB or smaller.")
                if is_screenshot
                else _("Each attachment must be 10 MB or smaller."),
            )
        attachment = (
            self.env["ir.attachment"]
            .with_user(self.env.user)
            .sudo()
            .create(
                {
                    "name": name,
                    "mimetype": mimetype,
                    "raw": raw,
                    "res_model": self._name,
                    "res_id": self.id,
                },
            )
        )
        values = {"attachment_ids": [Command.link(attachment.id)]}
        if is_screenshot:
            if self.screenshot_attachment_id:
                values["attachment_ids"].insert(0, Command.unlink(self.screenshot_attachment_id.id))
                self.screenshot_attachment_id.sudo().unlink()
            values["screenshot_attachment_id"] = attachment.id
        self.write(values)
        return {"id": attachment.id, "name": attachment.name, "mimetype": attachment.mimetype}

    def feedback_remove_attachment(self, attachment_id):
        self.ensure_one()
        self._require_internal_user()
        attachment = self.attachment_ids.filtered(lambda item: item.id == int(attachment_id or 0))
        if not attachment:
            raise AccessError(_("This attachment does not belong to your feedback draft."))
        self.write(
            {
                "attachment_ids": [Command.unlink(attachment.id)],
                **(
                    {"screenshot_attachment_id": False}
                    if attachment == self.screenshot_attachment_id
                    else {}
                ),
            },
        )
        attachment.sudo().unlink()
        return True

    def _release_sha(self):
        parameter_sha = self.env["ir.config_parameter"].sudo().get_str("usl.release.commit")
        environment_sha = os.environ.get("USL_RELEASE_COMMIT")
        valid_values = {
            value
            for value in (parameter_sha, environment_sha)
            if value and RELEASE_SHA_RE.fullmatch(value)
        }
        if len(valid_values) > 1:
            raise UserError(
                _("Feedback is unavailable because the running release identity is inconsistent."),
            )
        if not valid_values:
            raise UserError(_("Feedback is unavailable because this release has no verified identity."))
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
        if self.source_action_id.exists() and self.source_action_id.has_access("read"):
            values["usl_feedback_source_action_id"] = self.source_action_id.id
        model_name = (self.source_model_name or "").strip()
        record_id = self.source_record_id
        omitted = False
        if model_name:
            model = self.env.get(model_name)
            if model is None or model.is_transient() or not model.browse().has_access("read"):
                omitted = True
            elif record_id:
                record = model.browse(record_id).exists()
                if not record or not record.has_access("read"):
                    omitted = True
                else:
                    values.update(
                        {
                            "usl_feedback_source_model_id": self.env["ir.model"].sudo()._get_id(model_name),
                            "usl_feedback_source_res_id": record.id,
                        },
                    )
            else:
                values["usl_feedback_source_model_id"] = self.env["ir.model"].sudo()._get_id(
                    model_name,
                )
        elif record_id:
            omitted = True
        return values, omitted

    def _validated_attachments(self):
        self.ensure_one()
        if len(self.attachment_ids) > MAX_ATTACHMENTS:
            raise ValidationError(_("Attach at most 10 files to one feedback item."))
        for attachment in self.attachment_ids.sudo():
            if attachment.create_uid != self.env.user:
                raise AccessError(_("You can submit only attachments that you uploaded."))
            if attachment.res_model != self._name or attachment.res_id != self.id:
                raise AccessError(_("An attachment is already linked to another record."))
        return self.attachment_ids

    def feedback_submit_initial(self, message, include_page_context=False):
        self.ensure_one()
        self._require_internal_user()
        self.check_access("read")
        message = str(message or "").strip()
        if not message or len(message) > 8000:
            raise ValidationError(_("Describe your feedback in 8,000 characters or fewer."))
        if self.company_id != self.env.company or self.company_id not in self.env.user.company_ids:
            raise AccessError(_("Submit feedback from the active company shown in Odoo."))
        self.include_page_context = bool(include_page_context)
        context_values, context_omitted = self._validated_page_context()
        attachments = self._validated_attachments()
        project = self.env.ref("usl_feedback.project_product_feedback").sudo()
        stage = self.env.ref("usl_feedback.stage_feedback_new").sudo()
        task = (
            self.env["project.task"]
            .with_user(self.env.user)
            .sudo()
            .create(
                {
                    "name": message.splitlines()[0][:120],
                    "description": Markup("<p>%s</p>") % escape(message).replace(
                        "\n", Markup("<br>"),
                    ),
                    "project_id": project.id,
                    "stage_id": stage.id,
                    "company_id": False,
                    "priority": "0",
                    "usl_feedback_reporter_id": self.env.user.id,
                    "usl_feedback_company_id": self.env.company.id,
                    "usl_feedback_release_sha": self._release_sha(),
                    "usl_feedback_context_included": False,
                    "usl_feedback_agent_state": "waiting",
                    **context_values,
                },
            )
        )
        if attachments:
            attachments.sudo().write({"res_model": "project.task", "res_id": task.id})
        if self.screenshot_attachment_id:
            task.sudo().write(
                {"usl_feedback_screenshot_attachment_id": self.screenshot_attachment_id.id},
            )
        task.with_user(self.env.user).message_subscribe(partner_ids=[self.env.user.partner_id.id])
        task.with_user(self.env.user).message_post(
            body=escape(message),
            message_type="comment",
            subtype_xmlid="mail.mt_comment",
            attachment_ids=attachments.ids,
        )
        payload = task.with_user(self.env.user)._usl_feedback_state_payload()
        payload["context_omitted"] = context_omitted
        self.with_context(usl_feedback_keep_attachments=True).unlink()
        return payload

    def unlink(self):
        pending = self.mapped("attachment_ids").sudo().filtered(
            lambda attachment: attachment.res_model == self._name and attachment.res_id in self.ids,
        )
        result = super().unlink()
        if pending and not self.env.context.get("usl_feedback_keep_attachments"):
            pending.unlink()
        return result
