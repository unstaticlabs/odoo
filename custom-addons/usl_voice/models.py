from odoo import api, fields, models
from odoo.exceptions import AccessError, MissingError

from .speech import FORMATS, MAX_AUDIO_BYTES, SpeechUnavailable, transcribe


class VoiceMetadata(models.Model):
    _inherit = "discuss.voice.metadata"

    usl_transcription_state = fields.Selection([
        ("pending", "Transcribing"), ("done", "Transcribed"),
        ("silent", "No speech detected"), ("failed", "Transcription unavailable"),
    ], copy=False)
    usl_transcription = fields.Text(copy=False)
    usl_message_id = fields.Many2one("mail.message", ondelete="cascade", copy=False)


class MailMessage(models.Model):
    _inherit = "mail.message"

    usl_voice_notes = fields.Json(compute="_compute_usl_voice_notes", string="Voice transcripts")

    def _usl_accessible_voices(self):
        """Only serialize transcription for a reader of both message and voice."""
        self.ensure_one()
        self.check_access("read")
        attachments = self.attachment_ids.with_user(self.env.user)._filtered_access("read")
        if self.env.user.share and self.model and self.res_id:
            # mail.message._store_message_fields exposes native audio download tokens
            # to visible message recipients. Match that scope for shared chatter
            # files only after checking the recipient can still read the parent.
            parent = self.env[self.model].browse(self.res_id)
            if parent.has_access("read"):
                shared_files = self.sudo().attachment_ids.filtered(
                    lambda a: a.res_model == self.model and a.res_id == self.res_id
                )
                attachments |= shared_files.with_user(self.env.user)
        return self.env["discuss.voice.metadata"].sudo().search([
            ("attachment_id", "in", attachments.ids), ("usl_message_id", "=", self.id),
        ], order="id")

    @api.depends_context("uid")
    @api.depends("attachment_ids.voice_ids.usl_transcription_state", "attachment_ids.voice_ids.usl_transcription")
    def _compute_usl_voice_notes(self):
        for message in self:
            message.usl_voice_notes = [{
                "attachment_id": voice.attachment_id.id,
                "state": voice.usl_transcription_state,
                "text": voice.usl_transcription or "",
            } for voice in message._usl_accessible_voices()]

    def _usl_enqueue_voice(self, voice):
        self.ensure_one()
        voice.write({"usl_message_id": self.id, "usl_transcription_state": "pending"})
        self.with_delay(
            identity_key=f"usl-voice-{self.id}-{voice.id}", description="Transcribe voice note",
        )._usl_transcribe_voice(voice.id)

    def action_usl_retry_voice(self):
        self.ensure_one()
        self.check_access("write")
        for voice in self._usl_accessible_voices().filtered(
            lambda v: v.usl_transcription_state == "failed"
        ):
            self._usl_enqueue_voice(voice)
        return True

    def _usl_transcribe_voice(self, voice_id):
        if not self.exists():
            return
        # A posting user may lose access while the job waits. Keep the status
        # recoverable for a writer who still has access without reading the audio.
        voice = self.env["discuss.voice.metadata"].sudo().search([
            ("id", "=", voice_id), ("usl_message_id", "=", self.id),
            ("usl_transcription_state", "=", "pending"),
        ], limit=1)
        if not voice:
            return
        attachment = voice.attachment_id.with_user(self.env.user)
        try:
            self.check_access("read")
            attachment.check_access("read")
            if attachment not in self.attachment_ids:
                raise ValueError()
            if attachment.file_size > MAX_AUDIO_BYTES:
                raise ValueError()
            text = transcribe(bytes(attachment.raw), attachment.mimetype)
        except (SpeechUnavailable, ValueError, AccessError, MissingError):
            voice.write({"usl_transcription_state": "failed"})
            return
        voice.write({
            "usl_transcription_state": "done" if text else "silent",
            "usl_transcription": text,
        })


class MailThread(models.AbstractModel):
    _inherit = "mail.thread"

    def _message_post_after_hook(self, message, msg_values):
        result = super()._message_post_after_hook(message, msg_values)
        if self._name == "discuss.channel" or not self.env.user._is_internal():
            return result
        message = message.with_user(self.env.user)
        attachments = message.attachment_ids._filtered_access("read").filtered(
            lambda a: a.mimetype in FORMATS and a.file_size <= MAX_AUDIO_BYTES
        )
        if attachments:
            # Native voice metadata is internal. Scope elevation to verified attachments.
            voices = self.env["discuss.voice.metadata"].sudo().search([
                ("attachment_id", "in", attachments.ids), ("usl_message_id", "=", False),
            ])
            for voice in voices:
                message._usl_enqueue_voice(voice)
        return result
