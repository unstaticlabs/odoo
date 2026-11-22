from unittest.mock import patch

from odoo.exceptions import AccessError
from odoo.tests import TransactionCase, new_test_user, tagged

from ..speech import SpeechUnavailable, transcribe


@tagged("post_install", "-at_install")
class TestVoice(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.writer = new_test_user(cls.env, login="voice_writer", groups="base.group_user")
        cls.portal = new_test_user(cls.env, login="voice_portal", groups="base.group_portal")

    def post_voice(self, subtype="mail.mt_note", recipient=None):
        target = self.writer.partner_id.with_user(self.writer)
        attachment = self.env["ir.attachment"].create({
            "name": "Voice.mp3", "raw": b"ID3synthetic-audio", "mimetype": "audio/mpeg",
            "res_model": target._name, "res_id": target.id,
        })
        attachment._set_voice_metadata()
        message = target.message_post(
            body="Original caption", attachment_ids=attachment.ids,
            subtype_xmlid=subtype, message_type="comment", partner_ids=(recipient.ids if recipient else []),
        )
        return message.with_user(self.writer), attachment.voice_ids

    def test_transcribed_once_on_original_internal_note(self):
        message, voice = self.post_voice()
        self.assertEqual(voice.usl_transcription_state, "pending")
        self.assertEqual(message.usl_voice_notes[0]["state"], "pending")
        caption = message.body
        text = "Bonjour à toute l’équipe."
        with patch("odoo.addons.usl_voice.models.transcribe", return_value=text) as server:
            message._usl_transcribe_voice(voice.id)
            message._usl_transcribe_voice(voice.id)
        server.assert_called_once_with(b"ID3synthetic-audio", "audio/mpeg")
        self.assertIsInstance(server.call_args.args[0], bytes)
        message.invalidate_recordset(["usl_voice_notes"])
        self.assertEqual(message.usl_voice_notes, [{
            "attachment_id": voice.attachment_id.id, "state": "done", "text": text,
        }])
        self.assertEqual(message.body, caption)
        with self.assertRaises(AccessError):
            message.with_user(self.portal).read(["usl_voice_notes"])
        with self.assertRaises(AccessError):
            message.with_user(self.portal).action_usl_retry_voice()

    def test_failure_retry_and_silence_keep_recording(self):
        message, voice = self.post_voice("mail.mt_comment")
        with patch("odoo.addons.usl_voice.models.transcribe", side_effect=SpeechUnavailable):
            message._usl_transcribe_voice(voice.id)
        self.assertEqual(voice.usl_transcription_state, "failed")
        self.assertIn(voice.attachment_id, message.attachment_ids.sudo())
        message.action_usl_retry_voice()
        with patch("odoo.addons.usl_voice.models.transcribe", return_value=""):
            message._usl_transcribe_voice(voice.id)
        self.assertEqual(voice.usl_transcription_state, "silent")
        self.assertFalse(voice.usl_transcription)

    def test_external_recipient_reads_only_accessible_voice(self):
        target = self.portal.partner_id
        attachment = self.env["ir.attachment"].create({
            "name": "Voice.mp3", "raw": b"ID3synthetic", "mimetype": "audio/mpeg",
            "res_model": target._name, "res_id": target.id,
        })
        attachment._set_voice_metadata()
        message = target.message_post(body="Shared recording", attachment_ids=attachment.ids,
                                      partner_ids=target.ids, subtype_xmlid="mail.mt_comment",
                                      message_type="comment")
        with patch("odoo.addons.usl_voice.models.transcribe", return_value="Shared words"):
            message._usl_transcribe_voice(attachment.voice_ids.id)
        reader = message.with_user(self.portal)
        self.assertEqual(reader.read(["usl_voice_notes"])[0]["usl_voice_notes"][0]["text"], "Shared words")

    def test_access_loss_before_job_preserves_audio_for_retry(self):
        message, voice = self.post_voice()
        with patch("odoo.addons.usl_voice.models.transcribe") as server:
            message.with_user(self.portal)._usl_transcribe_voice(voice.id)
        server.assert_not_called()
        self.assertEqual(voice.usl_transcription_state, "failed")
        message.action_usl_retry_voice()
        self.assertEqual(voice.usl_transcription_state, "pending")

    def test_deleted_note_cannot_be_populated_by_pending_job(self):
        message, voice = self.post_voice()
        voice_id = voice.id
        message.sudo().unlink()
        with patch("odoo.addons.usl_voice.models.transcribe") as server:
            message._usl_transcribe_voice(voice_id)
        server.assert_not_called()
        self.assertFalse(voice.exists())

    def test_backend_uses_private_gateway_and_discards_invalid_response(self):
        with patch("odoo.addons.usl_voice.speech.requests.post") as post:
            post.return_value.status_code = 200
            post.return_value.json.return_value = {"text": " Bonjour ", "language": "fr"}
            self.assertEqual(transcribe(b"audio", "audio/mpeg"), "Bonjour")
            self.assertTrue(post.call_args.args[0].endswith("/v1/audio/transcriptions"))
            self.assertEqual(post.call_args.kwargs["files"]["file"], ("voice.mp3", b"audio", "audio/mpeg"))
            post.return_value.status_code = 503
            with self.assertRaises(SpeechUnavailable):
                transcribe(b"audio", "audio/mpeg")
            with self.assertRaises(ValueError):
                transcribe(b"", "audio/mpeg")
